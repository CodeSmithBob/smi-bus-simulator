"""Behavioural model of one SMI drive (tubular motor or venetian blind drive)."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from enum import StrEnum

from .protocol.constants import (
    ACK,
    ANGLE_UNIT_DEG,
    NACK,
    POSITION_BOTTOM,
    STATUS_BIT_AT_BOTTOM,
    STATUS_BIT_AT_TOP,
    STATUS_BIT_BLOCKED,
    STATUS_BIT_ERROR,
    STATUS_BIT_LIMITS_NOT_SET,
    STATUS_BIT_MOVING_DOWN,
    STATUS_BIT_MOVING_UP,
    STATUS_BIT_OBSTACLE,
    STATUS_BIT_THERMAL,
    Command,
    DiagCode,
    QueryCode,
    TelegramType,
)
from .protocol.frames import MasterTelegram, data_response, flag

FULL = float(POSITION_BOTTOM)


class BlindKind(StrEnum):
    ROLLER = "roller"  # roller shutter (Rollladen)
    VENETIAN = "venetian"  # external venetian blind with tiltable slats (Raffstore/Jalousie)
    SCREEN = "screen"  # fabric screen / ZIP screen
    AWNING = "awning"  # folding-arm awning (Markise)


#: Faults that can be injected into a drive, with a description for the UI.
FAULTS: dict[str, str] = {
    "offline": "No power or broken data line: the drive neither answers nor moves.",
    "no_response": "The drive executes commands but never answers.",
    "drop_next": "The next answer is lost (one-shot).",
    "bad_checksum": "Every answer carries a corrupted checksum.",
    "corrupt_next": "The next answer carries a corrupted checksum (one-shot).",
    "nack": "The drive rejects every telegram with NACK.",
    "slow": "The drive answers 400 ms late, after typical master timeouts.",
    "obstacle": "Obstacle detection trips during the next downward run.",
    "blocked": "The blind is mechanically blocked and cannot move.",
    "overheat": "Thermal protection trips immediately.",
    "limits_lost": "End positions are lost; positioning is refused until calibrated.",
}

#: Extra answer delay of the "slow" fault (assumption, see docs/configuration.md).
SLOW_FAULT_DELAY_S = 0.4

ONE_SHOT_FAULTS = {"drop_next", "corrupt_next", "overheat"}

# Sources / assumptions for every default timing value: docs/configuration.md,
# "Where the default timing values come from" (tests/test_defaults_doc.py keeps it in sync).
KIND_DEFAULTS: dict[BlindKind, dict] = {
    BlindKind.ROLLER: {"travel_time_s": 24.0, "shaft_degrees": 5400.0},
    BlindKind.VENETIAN: {"travel_time_s": 48.0, "shaft_degrees": 9000.0},
    BlindKind.SCREEN: {"travel_time_s": 36.0, "shaft_degrees": 6480.0},
    BlindKind.AWNING: {"travel_time_s": 30.0, "shaft_degrees": 4320.0},
}


def _check_position(position: float) -> float:
    value = float(position)
    if not 0.0 <= value <= FULL:
        raise ValueError("position must be 0..65535")
    return value


def make_key_id(seed: object) -> int:
    """Deterministic, unique-looking 32-bit slave ID."""
    return random.Random(str(seed)).randrange(0x00100000, 0xFFFFFFFF)


#: Drive mechanics presets ("profiles"). All values are ASSUMPTIONS: no public figures for
#: gear backlash or end-position overrun of blind drives were found (docs/configuration.md,
#: "Drive mechanics"). Offsets and slack are in raw position units (65535 = full travel).
DRIVE_PROFILES: dict[str, dict] = {
    "venetian-tight": {
        "kind": "venetian",
        "tilt_degrees": 180.0,
        "slack": 150,
        "top_offset": 300,
        "bottom_offset": 300,
    },
    "venetian-worn": {
        "kind": "venetian",
        "tilt_degrees": 300.0,
        "slack": 1500,
        "top_offset": 1200,
        "bottom_offset": 2500,
    },
    "roller-offset": {
        "kind": "roller",
        "slack": 400,
        "top_offset": 1500,
        "bottom_offset": 3000,
    },
}

#: Upper limits for the mechanics options (raw position units).
MAX_SLACK = int(FULL) // 4
MAX_OFFSETS = int(FULL) // 2


@dataclass
class MotorConfig:
    name: str = ""
    address: int = 0
    manufacturer: int = 1
    key_id: int | None = None
    kind: BlindKind = BlindKind.ROLLER
    drive_type: int = 1
    travel_time_s: float | None = None  # full downward travel
    up_speed_factor: float = 0.9  # upward speed relative to downward speed
    shaft_degrees: float | None = None  # shaft rotation over the full travel
    tilt_degrees: float = 270.0  # venetian slat turning range (shaft degrees)
    # Physical slat angle at 0 % and 100 % slat position (0 = horizontal, + = outer edge
    # down). Default: a common "90 degree" external venetian blind, 0 % open, 100 % closed
    # (KNX convention). Blinds with a 180 degree range: use -80 / 80 (50 % = horizontal).
    slat_min_deg: float = 0.0
    slat_max_deg: float = 85.0
    reversal_pause_s: float = 0.3  # dead time when the direction is reversed while running
    # Venetian only, PROVISIONAL (see SPEC_STATUS "tilt_in_position"): the position the
    # drive reports and accepts counts drive-shaft rotation including slat turning, so
    # angle steps move the position. Off = position is the height of the bottom rail.
    tilt_in_position: bool = False
    # Drive mechanics (all 0 = ideal drive, the behaviour before these options existed).
    # Raw position units (0..65535 = the drive's full travel):
    top_offset: int = 0  # at the top end the shaft turns this far before the rail moves
    bottom_offset: int = 0  # same at the bottom end
    slack: int = 0  # gear backlash: dead zone after every change of direction
    profile: str = ""  # name of the DRIVE_PROFILES entry this drive was built from (label)
    pos1: int = 0xC000  # stored intermediate position 1 (e.g. sun protection)
    pos2: int = 0xE666  # stored intermediate position 2 (e.g. privacy)
    thermal_limit_s: float = 240.0  # continuous run time until thermal protection (S2 4 min)
    cooldown_s: float = 900.0  # time to cool down completely
    start_position: int = 0

    def __post_init__(self) -> None:
        self.kind = BlindKind(self.kind)
        defaults = KIND_DEFAULTS[self.kind]
        if self.travel_time_s is None:
            self.travel_time_s = defaults["travel_time_s"]
        if self.shaft_degrees is None:
            self.shaft_degrees = defaults["shaft_degrees"]
        if not 0 <= self.address <= 15:
            raise ValueError("address must be 0..15")
        if not 0 <= self.manufacturer <= 15:
            raise ValueError("manufacturer code must be 0..15")
        if not 30 <= float(self.tilt_degrees) <= 720:
            raise ValueError("tilt range must be 30..720 degrees")
        check_mechanics(self.top_offset, self.bottom_offset, self.slack)
        if self.profile and self.profile not in DRIVE_PROFILES:
            raise ValueError(f"unknown drive profile {self.profile!r}")

    @classmethod
    def from_profile(cls, profile: str, **overrides) -> MotorConfig:
        """A config from a DRIVE_PROFILES entry; keyword arguments override its values."""
        if profile not in DRIVE_PROFILES:
            raise ValueError(
                f"unknown drive profile {profile!r}; known: {', '.join(DRIVE_PROFILES)}"
            )
        return cls(**(DRIVE_PROFILES[profile] | {"profile": profile} | overrides))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


def check_mechanics(top_offset: int, bottom_offset: int, slack: int) -> None:
    if top_offset < 0 or bottom_offset < 0 or top_offset + bottom_offset > MAX_OFFSETS:
        raise ValueError(f"offsets must be >= 0 and together at most {MAX_OFFSETS}")
    if not 0 <= slack <= MAX_SLACK:
        raise ValueError(f"slack must be 0..{MAX_SLACK}")


@dataclass
class MotorReply:
    data: bytes
    extra_delay_s: float = 0.0


@dataclass
class _Chain:
    """Shaft-to-blind mechanics, all in shaft degrees.

    ``sigma`` is the drive shaft as the drive counts it (0 = upper end, slats open). The
    gear follows it with play ``slack``; the bottom rail follows the gear with play
    ``tilt`` (the slat ladder: while the play is taken up, the slats turn). With
    ``slack = 0`` and no offsets this is the ideal drive. ``drive_rail`` is the rail as
    the drive itself models it (ladder play only, no slack).
    """

    sigma: float
    gear: float
    rail: float
    drive_rail: float

    def advance(self, sigma: float, tilt: float, slack: float, span: float) -> None:
        """Move the shaft monotonically to ``sigma`` and let the mechanics follow."""
        self.sigma = sigma
        self.drive_rail = min(max(self.drive_rail, sigma - tilt), sigma, span)
        self.drive_rail = max(self.drive_rail, 0.0)
        self.gear = min(max(self.gear, sigma - slack), sigma)
        self.rail = min(max(self.rail, self.gear - tilt), self.gear, span)
        self.rail = max(self.rail, 0.0)


@dataclass
class MotorStats:
    telegrams: int = 0
    commands: int = 0
    queries: int = 0
    answers: int = 0
    runs: int = 0
    run_time_s: float = 0.0
    last_command: str = ""


class Motor:
    def __init__(self, cfg: MotorConfig, uid: int) -> None:
        self.cfg = cfg
        self.uid = uid
        if cfg.key_id is None:
            cfg.key_id = make_key_id(("motor", uid, cfg.name))
        self.direction = 0  # -1 up, +1 down (drive shaft)
        self.heat_s = 0.0
        self.faults: set[str] = set()
        self.errors: set[str] = set()
        self.limits_set = True
        self.calibrating = False
        self.wink_s = 0.0
        self.stats = MotorStats()
        self._plan: list[float] = []  # shaft targets (degrees), in order
        self._was_moving = False
        self._pause_s = 0.0
        self._moving_rail = False
        self._moving_slats = False
        self._chain = _Chain(0.0, 0.0, 0.0, 0.0)
        start = _check_position(cfg.start_position)
        self._place(start, 1.0 if start > 0 else 0.0)  # tilt: 0 = turned up, 1 = closed
        self._run_start = self.position

    # --- identity --------------------------------------------------------------

    @property
    def address(self) -> int:
        return self.cfg.address

    @property
    def key_id(self) -> int:
        return int(self.cfg.key_id or 0)

    def is_addressed(self, tel: MasterTelegram) -> bool:
        return tel.addressing.matches(
            address=self.cfg.address, manufacturer=self.cfg.manufacturer, key_id=self.key_id
        )

    # --- geometry --------------------------------------------------------------

    @property
    def is_venetian(self) -> bool:
        return self.cfg.kind is BlindKind.VENETIAN

    @property
    def _span(self) -> float:
        """Shaft degrees of rail travel between the drive's end positions."""
        return float(self.cfg.shaft_degrees)

    @property
    def _tilt_span(self) -> float:
        """Shaft degrees of the slat turn (0 for blinds without slats)."""
        return float(self.cfg.tilt_degrees) if self.is_venetian else 0.0

    @property
    def _slack_deg(self) -> float:
        return self.cfg.slack / FULL * self._span

    @property
    def _sigma_max(self) -> float:
        return self._tilt_span + self._span

    def _raw_to_physical(self, raw: float) -> float:
        """Drive raw rail value -> physical rail position (offsets applied)."""
        top, bottom = float(self.cfg.top_offset), float(self.cfg.bottom_offset)
        travel = FULL - top - bottom
        return min(max((raw - top) / travel, 0.0), 1.0) * FULL

    def _physical_to_raw(self, position: float) -> float:
        if position <= 0:
            return 0.0
        if position >= FULL:
            return FULL
        top, bottom = float(self.cfg.top_offset), float(self.cfg.bottom_offset)
        return top + position / FULL * (FULL - top - bottom)

    def _place(self, position: float, tilt: float) -> None:
        """Put the blind at a physical position and slat turn (gear in upward contact)."""
        rail = self._physical_to_raw(position) / FULL * self._span
        sigma = rail + (tilt * self._tilt_span)
        self._chain = _Chain(sigma, sigma, rail, rail)

    def _advance(self, sigma: float) -> None:
        self._chain.advance(sigma, self._tilt_span, self._slack_deg, self._span)

    # --- kinematics ------------------------------------------------------------

    def _factor(self, direction: int) -> float:
        return self.cfg.up_speed_factor if direction < 0 else 1.0

    def _shaft_rate(self, direction: int) -> float:
        return self._span / float(self.cfg.travel_time_s) * self._factor(direction)

    def _drive_tilt(self) -> float:
        """Slat turn as the drive models it (0..1)."""
        if not self.is_venetian:
            return 0.0
        c = self._chain
        return min(max((c.sigma - c.drive_rail) / self._tilt_span, 0.0), 1.0)

    def _go(self, targets: list[float]) -> None:
        """Run the shaft through ``targets`` (degrees), clipped to the end positions."""
        self._plan = [min(max(t, 0.0), self._sigma_max) for t in targets]
        self._start_if_needed()

    def _plan_rail(self, raw: float, final_tilt: float | None = None) -> None:
        """GOTO a raw rail value the way the drive does it: slats first, then the rail."""
        c = self._chain
        goal = min(max(raw, 0.0), FULL) / FULL * self._span
        targets: list[float] = []
        rail_after = c.drive_rail
        if goal > c.drive_rail + 1e-9:  # down: close the slats, then lower the rail
            targets.append(goal + self._tilt_span)
            rail_after = goal
        elif goal < c.drive_rail - 1e-9:  # up: open the slats, then raise the rail
            targets.append(goal)
            rail_after = goal
        if final_tilt is not None and self.is_venetian:
            targets.append(rail_after + min(max(final_tilt, 0.0), 1.0) * self._tilt_span)
        self._go(targets)

    def _plan_reported(self, target: float, final_tilt: float | None = None) -> None:
        """Plan a move to a position on the scale the drive reports over SMI."""
        if not self.counts_tilt_in_position:
            self._plan_rail(target, final_tilt)
            return
        # The target is a shaft angle measured from the top end with open slats.
        c = self._chain
        sigma = min(max(target, 0.0), FULL) / FULL * self._sigma_max
        targets = [sigma]
        if final_tilt is not None:
            if sigma > c.sigma:
                rail_after = max(c.drive_rail, sigma - self._tilt_span)
            else:
                rail_after = min(c.drive_rail, sigma)
            targets.append(rail_after + min(max(final_tilt, 0.0), 1.0) * self._tilt_span)
        self._go(targets)

    def _plan_steps(self, direction: int, degrees: float) -> None:
        self._go([self._chain.sigma + direction * degrees])

    def _start_if_needed(self) -> None:
        self._run_start = self.position
        new_dir = self._plan_direction()
        if self.direction and new_dir == -self.direction:
            # AC motors need a dead time before reversing (at least ~300 ms).
            self._pause_s = float(self.cfg.reversal_pause_s)
            self.direction = 0
        if self._plan and not self._was_moving:
            self.stats.runs += 1

    def _plan_direction(self) -> int:
        sigma = self._chain.sigma
        for target in self._plan:
            if abs(target - sigma) > 1e-9:
                return 1 if target > sigma else -1
        return 0

    def stop(self) -> None:
        self._plan = []
        self._pause_s = 0.0
        self.direction = 0
        self._moving_rail = self._moving_slats = False

    # --- test control (public, stable; see docs/api.md) ------------------------

    @property
    def target_position(self) -> float:
        """Physical position (0 = top, 65535 = bottom) the drive is heading for.

        Equals :attr:`position` when the drive is not moving. For an angle step it is
        where the step ends. It includes the effects of slack and end offsets, but an
        obstacle or thermal trip on the way is not predicted.
        """
        chain = _Chain(**asdict(self._chain))
        for target in self._plan:
            chain.advance(target, self._tilt_span, self._slack_deg, self._span)
        return self._raw_to_physical(chain.rail / self._span * FULL)

    def move_to(self, position: float) -> bool:
        """Start a move to the physical ``position`` without a telegram (test action).

        Behaves like a GOTO telegram to the matching raw value, including the slat turn of
        venetian blinds, the reversal pause and any slack, but ignores whether end
        positions are set. Returns ``False`` and does nothing when the drive cannot move
        (offline, blocked, thermal protection).
        """
        position = _check_position(position)
        if "offline" in self.faults or not self._can_move():
            return False
        self.errors.discard("obstacle")
        self._plan_rail(self._physical_to_raw(position))
        return True

    def set_position(self, position: float, *, tilt: float | None = None) -> None:
        """Stop the drive and put it at the physical ``position`` at once (test setup).

        ``tilt`` (0..1, venetian blinds) sets the slat turn as well; by default the slats
        keep their current turn. Any gear slack is taken up in the upward direction.
        """
        position = _check_position(position)
        if tilt is not None and not 0.0 <= tilt <= 1.0:
            raise ValueError("tilt must be 0..1")
        self.stop()
        self._place(position, self.tilt if tilt is None else float(tilt))
        self._run_start = self.position

    def update(self, dt: float) -> None:
        self.wink_s = max(0.0, self.wink_s - dt)
        if "overheat" in self.faults:
            self.faults.discard("overheat")
            self.heat_s = float(self.cfg.thermal_limit_s)
            self._trip("thermal")
        if not self._plan:
            self.direction = 0
            self._moving_rail = self._moving_slats = False
            self._cool(dt)
            self._was_moving = False
            return
        if "blocked" in self.faults:
            self._trip("blocked")
            return
        rail_before, slats_before = self.position, self.tilt  # physical, offsets applied
        remaining = dt
        paused = 0.0
        if self._pause_s > 0:
            paused = min(self._pause_s, remaining)
            self._pause_s -= paused
            remaining -= paused
            self.direction = 0
        while remaining > 1e-9 and self._plan:
            remaining = self._run(remaining)
        moved = dt - remaining - paused
        # What a person watching would see during this step (not the motor running).
        self._moving_rail = abs(self.position - rail_before) > 1e-6
        self._moving_slats = abs(self.tilt - slats_before) > 1e-9
        self.heat_s += moved
        self.stats.run_time_s += moved
        self._was_moving = True
        if self.heat_s >= float(self.cfg.thermal_limit_s):
            self._trip("thermal")
        if not self._plan:
            self.direction = 0
            if self.calibrating:
                self.calibrating = False
                self.limits_set = True
                self.errors.discard("limits")
                self.faults.discard("limits_lost")

    def _run(self, remaining: float) -> float:
        """Turn the shaft toward the next target for up to ``remaining`` seconds."""
        target = self._plan[0]
        sigma = self._chain.sigma
        delta = target - sigma
        if abs(delta) < 1e-9:
            self._plan.pop(0)
            return remaining
        direction = 1 if delta > 0 else -1
        self.direction = direction
        rate = self._shaft_rate(direction)
        step = min(abs(delta), rate * remaining)
        new_sigma = sigma + direction * step
        if direction > 0 and "obstacle" in self.faults and self._obstacle(new_sigma):
            return remaining - step / rate
        self._advance(new_sigma)
        if abs(target - new_sigma) < 1e-9:
            self._plan.pop(0)
        return remaining - step / rate

    def _obstacle(self, new_sigma: float) -> bool:
        """Stop where the rail meets the obstacle, if it does before ``new_sigma``."""
        trip_at = min(self._run_start + 0.35 * FULL, FULL - 1.0)
        args = (self._tilt_span, self._slack_deg, self._span)
        probe = _Chain(**asdict(self._chain))
        probe.advance(new_sigma, *args)
        if not self.position < trip_at <= self._raw_to_physical(probe.rail / self._span * FULL):
            return False
        lo, hi = self._chain.sigma, new_sigma
        for _ in range(40):  # bisect the shaft angle where the rail reaches the obstacle
            mid = (lo + hi) / 2
            probe = _Chain(**asdict(self._chain))
            probe.advance(mid, *args)
            if self._raw_to_physical(probe.rail / self._span * FULL) < trip_at:
                lo = mid
            else:
                hi = mid
        self._advance(hi)
        self.faults.discard("obstacle")
        self._trip("obstacle")
        return True

    def _trip(self, error: str) -> None:
        self.errors.add(error)
        self.stop()

    def _cool(self, dt: float) -> None:
        if self.heat_s > 0:
            rate = float(self.cfg.thermal_limit_s) / float(self.cfg.cooldown_s)
            self.heat_s = max(0.0, self.heat_s - rate * dt)
        if "thermal" in self.errors and self.heat_s <= 0.5 * float(self.cfg.thermal_limit_s):
            self.errors.discard("thermal")

    # --- simulator actions (out of band) ---------------------------------------

    def set_fault(self, name: str, active: bool) -> None:
        if name not in FAULTS:
            raise ValueError(f"unknown fault {name!r}")
        if active:
            self.faults.add(name)
            if name == "limits_lost":
                self.limits_set = False
                self.errors.add("limits")
        else:
            self.faults.discard(name)
            if name == "blocked":
                self.errors.discard("blocked")

    def clear_errors(self) -> None:
        self.errors -= {"obstacle", "blocked"}
        if self.limits_set:
            self.errors.discard("limits")

    def calibrate(self) -> None:
        """Teach the end positions: run to the top, the bottom and back to the top."""
        self.faults.discard("blocked")
        self.errors -= {"blocked", "obstacle"}
        self.calibrating = True
        self._go([0.0, self._sigma_max, 0.0])

    def wink(self, seconds: float = 3.0) -> None:
        self.wink_s = seconds

    # --- protocol --------------------------------------------------------------

    def handle(self, tel: MasterTelegram) -> MotorReply | None:
        """Execute a telegram addressed to this drive and build its answer."""
        if "offline" in self.faults:
            return None
        self.stats.telegrams += 1
        if "nack" in self.faults:
            data = bytes([NACK])
        else:
            data = self._execute(tel)
        if data is None:
            return None
        if "no_response" in self.faults:
            return None
        if "drop_next" in self.faults:
            self.faults.discard("drop_next")
            return None
        if "bad_checksum" in self.faults or "corrupt_next" in self.faults:
            self.faults.discard("corrupt_next")
            data = data[:-1] + bytes([data[-1] ^ 0x5A]) if len(data) > 1 else bytes([0x5A])
        self.stats.answers += 1
        return MotorReply(data, SLOW_FAULT_DELAY_S if "slow" in self.faults else 0.0)

    def _can_move(self) -> bool:
        return "thermal" not in self.errors and "blocked" not in self.faults

    def _execute(self, tel: MasterTelegram) -> bytes | None:
        if tel.ttype is TelegramType.COMMAND:
            self.stats.commands += 1
            self.stats.last_command = tel.describe()
            return bytes([ACK if self._command(tel) else NACK])
        if tel.ttype is TelegramType.DIAG:
            return self._diag(tel)
        self.stats.queries += 1
        return self._query(tel)

    def _command(self, tel: MasterTelegram) -> bool:
        code = tel.code
        if code == Command.STOP:
            self.stop()
            return True
        if code in (Command.POS1, Command.POS2) and tel.word is not None:
            if code == Command.POS1:
                self.cfg.pos1 = tel.word
            else:
                self.cfg.pos2 = tel.word
            return True
        if code not in (Command.UP, Command.DOWN, Command.POS1, Command.POS2, Command.GOTO):
            return False
        if not self._can_move():
            return False
        self.errors.discard("obstacle")
        if code in (Command.UP, Command.DOWN):
            direction = -1 if code == Command.UP else 1
            if tel.byte is not None:
                self._plan_steps(direction, tel.byte * ANGLE_UNIT_DEG)
            else:
                self._go([0.0 if direction < 0 else self._sigma_max])
            return True
        if not self.limits_set:
            return False
        final_tilt = None
        if tel.byte is not None:
            final_tilt = tel.byte * ANGLE_UNIT_DEG / self.cfg.tilt_degrees
        if code == Command.POS1:
            self._plan_reported(self.cfg.pos1, final_tilt)
            return True
        if code == Command.POS2:
            self._plan_reported(self.cfg.pos2, final_tilt)
            return True
        # GOTO
        if tel.word is not None:
            self._plan_reported(tel.word, final_tilt)
            return True
        if final_tilt is not None:
            if self.is_venetian:
                tilt = min(max(final_tilt, 0.0), 1.0)
                self._go([self._chain.drive_rail + tilt * self._tilt_span])
            return True
        return False

    def _diag(self, tel: MasterTelegram) -> bytes | None:
        if tel.code == DiagCode.STATUS:
            return bytes(
                [
                    ACK,
                    flag(self.direction < 0),
                    flag(self.direction > 0),
                    flag(self.direction == 0),
                    flag(bool(self.errors)),
                ]
            )
        if tel.code == DiagCode.KEY_ID_COMPARE and len(tel.payload) == 4:
            search = int.from_bytes(tel.payload, "big")
            a0 = self.cfg.address == 0
            kid = self.key_id
            return bytes(
                [
                    ACK,
                    flag(a0 and kid > search),
                    flag(a0 and kid < search),
                    flag(a0 and kid == search),
                    flag(not a0),
                ]
            )
        if tel.code == DiagCode.WRITE_ADDRESS and len(tel.payload) == 1:
            self.cfg.address = tel.payload[0] & 0x0F
            return bytes([ACK])
        if tel.code == DiagCode.IDENTIFY:
            self.wink()
            return bytes([ACK])
        return bytes([NACK])

    def _query(self, tel: MasterTelegram) -> bytes | None:
        code = tel.code
        if code == QueryCode.POSITION:
            value = round(self.reported_position)
        elif code == QueryCode.POS1:
            value = self.cfg.pos1
        elif code == QueryCode.POS2:
            value = self.cfg.pos2
        elif code == QueryCode.ADDRESS:
            value = self.cfg.address
        elif code == QueryCode.IDENT:
            value = (self.cfg.manufacturer << 8) | (self.cfg.drive_type & 0xFF)
        elif code == QueryCode.KEY_ID:
            value = self.key_id
        elif code == QueryCode.ANGLE:
            drive_angle = self._drive_tilt() * self._tilt_span  # the drive's own count
            value = min(0xFF, round(drive_angle / ANGLE_UNIT_DEG))
        elif code == QueryCode.STATUS_BITS:
            value = self.status_bits
        else:
            return bytes([NACK])
        return data_response(code, value)

    # --- state -----------------------------------------------------------------

    @property
    def position(self) -> float:
        """Physical height of the bottom rail: 0 = top, 65535 = bottom (what you see)."""
        return self._raw_to_physical(self._chain.rail / self._span * FULL)

    @property
    def tilt(self) -> float:
        """Physical slat turn: 0 = turned up/open, 1 = closed (0 for blinds without slats)."""
        if not self.is_venetian:
            return 0.0
        c = self._chain
        return min(max((c.gear - c.rail) / self._tilt_span, 0.0), 1.0)

    @property
    def moving_rail(self) -> bool:
        """True while the bottom rail physically moves (during the last ``update``).

        False during the reversal pause, while gear slack is taken up, while only the
        slats turn and while the rail stays at an end inside an end offset.
        """
        return self._moving_rail

    @property
    def moving_slats(self) -> bool:
        """True while the slats of a venetian blind physically turn (last ``update``)."""
        return self._moving_slats

    @property
    def drive_position(self) -> float:
        """The bottom rail as the drive itself counts it, raw 0..65535.

        Equals :attr:`position` for an ideal drive. With slack or end offsets the drive's
        count and the physical blind differ, as on a drive that needs calibration.
        """
        return self._chain.drive_rail / self._span * FULL

    @property
    def angle_deg(self) -> float:
        """Physical slat turn in motor-shaft degrees (the unit of SMI angle data)."""
        return self.tilt * self._tilt_span

    @property
    def counts_tilt_in_position(self) -> bool:
        return self.is_venetian and bool(self.cfg.tilt_in_position)

    @property
    def reported_position(self) -> float:
        """The position this drive reports over SMI (0 = top, 65535 = bottom).

        Equals :attr:`drive_position` (the bottom rail as the drive counts it) unless the
        venetian option ``tilt_in_position`` is on; then it is the drive-shaft rotation from
        the top end with open slats (0) to the bottom end with closed slats (65535). For an
        ideal drive (no slack, no offsets) ``drive_position`` equals :attr:`position`.
        """
        if not self.counts_tilt_in_position:
            return self.drive_position
        return self._chain.sigma / self._sigma_max * FULL

    @property
    def slat_percent(self) -> float:
        """KNX-style slat position: 0 % turned up/open, 50 % horizontal, 100 % closed."""
        return self.tilt * 100.0 if self.is_venetian else 0.0

    @property
    def slat_angle(self) -> float:
        """Physical slat angle in degrees: 0 = horizontal, positive = outer edge down."""
        lo, hi = self.cfg.slat_min_deg, self.cfg.slat_max_deg
        return lo + self.tilt * (hi - lo) if self.is_venetian else 0.0

    @property
    def daylight(self) -> float:
        """Rough share of daylight that reaches the room (0..1), for the visualization."""
        covered = self.position / FULL
        if self.cfg.kind is BlindKind.VENETIAN:
            openness = max(0.0, math.cos(math.radians(self.slat_angle))) ** 1.5
            return (1 - covered) + covered * openness * 0.85
        if self.cfg.kind is BlindKind.SCREEN:
            return (1 - covered) + covered * 0.25
        if self.cfg.kind is BlindKind.AWNING:
            return 1 - covered * 0.45
        return (1 - covered) + covered * 0.03

    @property
    def status_bits(self) -> int:
        bits = 0
        if self.direction < 0:
            bits |= STATUS_BIT_MOVING_UP
        if self.direction > 0:
            bits |= STATUS_BIT_MOVING_DOWN
        if self.errors:
            bits |= STATUS_BIT_ERROR
        if "thermal" in self.errors:
            bits |= STATUS_BIT_THERMAL
        if "obstacle" in self.errors:
            bits |= STATUS_BIT_OBSTACLE
        if "blocked" in self.errors:
            bits |= STATUS_BIT_BLOCKED
        if not self.limits_set:
            bits |= STATUS_BIT_LIMITS_NOT_SET
        if self.drive_position <= 0:
            bits |= STATUS_BIT_AT_TOP
        if self.drive_position >= FULL:
            bits |= STATUS_BIT_AT_BOTTOM
        return bits

    def to_state(self) -> dict:
        return {
            "uid": self.uid,
            "name": self.cfg.name,
            "address": self.cfg.address,
            "manufacturer": self.cfg.manufacturer,
            "key_id": f"{self.key_id:08X}",
            "kind": self.cfg.kind.value,
            "drive_type": self.cfg.drive_type,
            "position": round(self.position),
            "percent": round(self.position / FULL * 100, 1),
            "reported_position": round(self.reported_position),
            "drive_position": round(self.drive_position),
            "tilt": round(self.tilt, 4),
            "angle_deg": round(self.angle_deg, 1),
            "slat_percent": round(self.slat_percent, 1),
            "slat_angle": round(self.slat_angle, 1),
            "daylight": round(self.daylight, 3),
            "reversing": self._pause_s > 0,
            "moving_rail": self._moving_rail,
            "moving_slats": self._moving_slats,
            "direction": self.direction,
            "errors": sorted(self.errors),
            "faults": sorted(self.faults),
            "heat": round(min(1.0, self.heat_s / float(self.cfg.thermal_limit_s)), 3),
            "limits_set": self.limits_set,
            "calibrating": self.calibrating,
            "wink": self.wink_s > 0,
            "config": self.cfg.to_dict() | {"key_id": f"{self.key_id:08X}"},
            "stats": asdict(self.stats),
        }


@dataclass
class MotorUpdate:
    """Editable fields from the UI/API."""

    name: str | None = None
    address: int | None = None
    manufacturer: int | None = None
    key_id: int | None = None
    kind: str | None = None
    travel_time_s: float | None = None
    tilt_degrees: float | None = None
    slat_min_deg: float | None = None
    slat_max_deg: float | None = None
    reversal_pause_s: float | None = None
    tilt_in_position: bool | None = None
    top_offset: int | None = None
    bottom_offset: int | None = None
    slack: int | None = None
    pos1: int | None = None
    pos2: int | None = None

    def apply(self, motor: Motor) -> None:
        cfg = motor.cfg
        position, tilt = motor.position, motor.tilt
        geometry = (cfg.kind, cfg.tilt_degrees, cfg.top_offset, cfg.bottom_offset, cfg.slack)
        if self.name is not None:
            cfg.name = str(self.name)[:40]
        if self.address is not None:
            if not 0 <= int(self.address) <= 15:
                raise ValueError("address must be 0..15")
            cfg.address = int(self.address)
        if self.manufacturer is not None:
            if not 0 <= int(self.manufacturer) <= 15:
                raise ValueError("manufacturer code must be 0..15")
            cfg.manufacturer = int(self.manufacturer)
        if self.key_id is not None:
            if not 0 <= int(self.key_id) <= 0xFFFFFFFF:
                raise ValueError("key ID must be 32 bit")
            cfg.key_id = int(self.key_id)
        if self.kind is not None:
            cfg.kind = BlindKind(self.kind)
        if self.travel_time_s is not None:
            if not 2 <= float(self.travel_time_s) <= 600:
                raise ValueError("travel time must be 2..600 s")
            cfg.travel_time_s = float(self.travel_time_s)
        if self.tilt_degrees is not None:
            if not 30 <= float(self.tilt_degrees) <= 720:
                raise ValueError("tilt range must be 30..720 degrees")
            cfg.tilt_degrees = float(self.tilt_degrees)
        if self.slat_min_deg is not None or self.slat_max_deg is not None:
            lo = float(cfg.slat_min_deg if self.slat_min_deg is None else self.slat_min_deg)
            hi = float(cfg.slat_max_deg if self.slat_max_deg is None else self.slat_max_deg)
            if not -90 <= lo < hi <= 90:
                raise ValueError("slat angles must satisfy -90 <= min < max <= 90")
            cfg.slat_min_deg, cfg.slat_max_deg = lo, hi
        if self.reversal_pause_s is not None:
            if not 0 <= float(self.reversal_pause_s) <= 5:
                raise ValueError("reversal pause must be 0..5 s")
            cfg.reversal_pause_s = float(self.reversal_pause_s)
        if self.tilt_in_position is not None:
            cfg.tilt_in_position = bool(self.tilt_in_position)
        if self.pos1 is not None:
            cfg.pos1 = max(0, min(0xFFFF, int(self.pos1)))
        if self.pos2 is not None:
            cfg.pos2 = max(0, min(0xFFFF, int(self.pos2)))
        if any(v is not None for v in (self.top_offset, self.bottom_offset, self.slack)):
            top = cfg.top_offset if self.top_offset is None else int(self.top_offset)
            bottom = cfg.bottom_offset if self.bottom_offset is None else int(self.bottom_offset)
            slack = cfg.slack if self.slack is None else int(self.slack)
            check_mechanics(top, bottom, slack)
            cfg.top_offset, cfg.bottom_offset, cfg.slack = top, bottom, slack
        new_geometry = (cfg.kind, cfg.tilt_degrees, cfg.top_offset, cfg.bottom_offset, cfg.slack)
        if new_geometry != geometry:
            # The mechanics changed: rebuild the shaft state around the blind as it is now.
            motor.set_position(position, tilt=tilt if motor.is_venetian else 0.0)
