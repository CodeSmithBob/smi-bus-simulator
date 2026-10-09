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

ONE_SHOT_FAULTS = {"drop_next", "corrupt_next", "overheat"}

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

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class MotorReply:
    data: bytes
    extra_delay_s: float = 0.0


@dataclass
class _Segment:
    kind: str  # "tilt" or "travel"
    goal: float


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
        self.position = float(cfg.start_position)
        self.tilt = 1.0 if cfg.start_position > 0 else 0.0  # 0 = turned up, 1 = closed down
        self.direction = 0  # -1 up, +1 down
        self.heat_s = 0.0
        self.faults: set[str] = set()
        self.errors: set[str] = set()
        self.limits_set = True
        self.calibrating = False
        self.wink_s = 0.0
        self.stats = MotorStats()
        self._plan: list[_Segment] = []
        self._budget_deg: float | None = None
        self._was_moving = False
        self._run_start = self.position
        self._pause_s = 0.0

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

    # --- kinematics ------------------------------------------------------------

    @property
    def is_venetian(self) -> bool:
        return self.cfg.kind is BlindKind.VENETIAN

    def _factor(self, direction: int) -> float:
        return self.cfg.up_speed_factor if direction < 0 else 1.0

    def _pos_rate(self, direction: int) -> float:
        return FULL / float(self.cfg.travel_time_s) * self._factor(direction)

    def _shaft_rate(self, direction: int) -> float:
        return (
            float(self.cfg.shaft_degrees) / float(self.cfg.travel_time_s) * self._factor(direction)
        )

    def _plan_move(self, goal_pos: float, final_tilt: float | None = None) -> None:
        goal_pos = min(max(goal_pos, 0.0), FULL)
        plan: list[_Segment] = []
        direction = (goal_pos > self.position) - (goal_pos < self.position)
        if self.is_venetian and direction:
            plan.append(_Segment("tilt", 1.0 if direction > 0 else 0.0))
        if direction:
            plan.append(_Segment("travel", goal_pos))
        if self.is_venetian and final_tilt is not None:
            plan.append(_Segment("tilt", min(max(final_tilt, 0.0), 1.0)))
        self._plan = plan
        self._budget_deg = None
        self._start_if_needed()

    def _plan_steps(self, direction: int, degrees: float) -> None:
        plan: list[_Segment] = []
        if self.is_venetian:
            plan.append(_Segment("tilt", 1.0 if direction > 0 else 0.0))
        plan.append(_Segment("travel", FULL if direction > 0 else 0.0))
        self._plan = plan
        self._budget_deg = float(degrees)
        self._start_if_needed()

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
        for seg in self._plan:
            current = self.tilt if seg.kind == "tilt" else self.position
            if abs(seg.goal - current) > 1e-9:
                return 1 if seg.goal > current else -1
        return 0

    def stop(self) -> None:
        self._plan = []
        self._budget_deg = None
        self._pause_s = 0.0
        self.direction = 0

    # --- test control (public, stable; see docs/api.md) ------------------------

    @property
    def target_position(self) -> float:
        """Position (0 = top, 65535 = bottom) the drive is heading for.

        Equals :attr:`position` when the drive is not moving. For an angle step it is
        where the step ends. Slat turning of venetian blinds does not change it, and an
        obstacle or thermal trip on the way is not predicted.
        """
        pos, tilt, budget = self.position, self.tilt, self._budget_deg
        for seg in self._plan:
            if seg.kind == "tilt":
                need = abs(seg.goal - tilt) * self.cfg.tilt_degrees
                if budget is not None:
                    if budget <= need:
                        break
                    budget -= need
                tilt = seg.goal
                continue
            distance = abs(seg.goal - pos)
            if budget is not None:
                reach = budget / float(self.cfg.shaft_degrees) * FULL
                if reach < distance:
                    pos += reach if seg.goal > pos else -reach
                    break
                budget -= distance / FULL * float(self.cfg.shaft_degrees)
            pos = seg.goal
        return pos

    def move_to(self, position: float) -> bool:
        """Start a move to ``position`` without a telegram (out-of-band test action).

        Behaves like a GOTO telegram, including the slat turn of venetian blinds and the
        reversal pause, but ignores whether end positions are set. Returns ``False`` and
        does nothing when the drive cannot move (offline, blocked, thermal protection).
        """
        position = _check_position(position)
        if "offline" in self.faults or not self._can_move():
            return False
        self.errors.discard("obstacle")
        self._plan_move(position)
        return True

    def set_position(self, position: float, *, tilt: float | None = None) -> None:
        """Stop the drive and put it at ``position`` at once (test setup).

        ``tilt`` (0..1, venetian blinds) sets the slat turn as well; by default the slats
        keep their current turn.
        """
        position = _check_position(position)
        if tilt is not None and not 0.0 <= tilt <= 1.0:
            raise ValueError("tilt must be 0..1")
        self.stop()
        self.position = position
        if tilt is not None:
            self.tilt = float(tilt)
        self._run_start = position

    def update(self, dt: float) -> None:
        self.wink_s = max(0.0, self.wink_s - dt)
        if "overheat" in self.faults:
            self.faults.discard("overheat")
            self.heat_s = float(self.cfg.thermal_limit_s)
            self._trip("thermal")
        if not self._plan:
            self.direction = 0
            self._cool(dt)
            self._was_moving = False
            return
        if "blocked" in self.faults:
            self._trip("blocked")
            return
        remaining = dt
        paused = 0.0
        if self._pause_s > 0:
            paused = min(self._pause_s, remaining)
            self._pause_s -= paused
            remaining -= paused
            self.direction = 0
        while remaining > 1e-9 and self._plan:
            seg = self._plan[0]
            if seg.kind == "tilt":
                remaining = self._run_tilt(seg, remaining)
            else:
                remaining = self._run_travel(seg, remaining)
        moved = dt - remaining - paused
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

    def _run_tilt(self, seg: _Segment, remaining: float) -> float:
        delta = seg.goal - self.tilt
        if abs(delta) < 1e-9:
            self._plan.pop(0)
            return remaining
        direction = 1 if delta > 0 else -1
        self.direction = direction
        rate = self._shaft_rate(direction) / self.cfg.tilt_degrees  # tilt units per second
        step = min(abs(delta), rate * remaining)
        if self._budget_deg is not None:
            step = min(step, self._budget_deg / self.cfg.tilt_degrees)
        self.tilt += direction * step
        used = step / rate if rate else remaining
        if self._budget_deg is not None:
            self._budget_deg -= step * self.cfg.tilt_degrees
            if self._budget_deg <= 1e-6:
                self.stop()
                return remaining - used
        if abs(seg.goal - self.tilt) < 1e-9:
            self.tilt = seg.goal
            self._plan.pop(0)
        return remaining - used

    def _run_travel(self, seg: _Segment, remaining: float) -> float:
        delta = seg.goal - self.position
        if abs(delta) < 1e-6:
            self.position = seg.goal
            self._plan.pop(0)
            return remaining
        direction = 1 if delta > 0 else -1
        self.direction = direction
        rate = self._pos_rate(direction)
        step = min(abs(delta), rate * remaining)
        if self._budget_deg is not None:
            step = min(step, self._budget_deg / float(self.cfg.shaft_degrees) * FULL)
        new_pos = self.position + direction * step
        if direction > 0 and "obstacle" in self.faults:
            trip_at = min(self._run_start + 0.35 * FULL, FULL - 1.0)
            if self.position < trip_at <= new_pos:
                step = trip_at - self.position
                self.position = trip_at
                self.faults.discard("obstacle")
                self._trip("obstacle")
                return remaining - (step / rate if rate else remaining)
        self.position = new_pos
        used = step / rate if rate else remaining
        if self._budget_deg is not None:
            self._budget_deg -= step / FULL * float(self.cfg.shaft_degrees)
            if self._budget_deg <= 1e-6:
                self.stop()
                return remaining - used
        if abs(seg.goal - self.position) < 1e-6:
            self.position = seg.goal
            self._plan.pop(0)
        return remaining - used

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
        plan: list[_Segment] = []
        if self.is_venetian:
            plan += [_Segment("tilt", 0.0)]
        plan += [_Segment("travel", 0.0)]
        if self.is_venetian:
            plan += [_Segment("tilt", 1.0)]
        plan += [_Segment("travel", FULL)]
        if self.is_venetian:
            plan += [_Segment("tilt", 0.0)]
        plan += [_Segment("travel", 0.0)]
        self._plan = plan
        self._budget_deg = None
        self._start_if_needed()

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
        return MotorReply(data, 0.4 if "slow" in self.faults else 0.0)

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
                self._plan_move(0.0 if direction < 0 else FULL)
            return True
        if not self.limits_set:
            return False
        final_tilt = None
        if tel.byte is not None:
            final_tilt = tel.byte * ANGLE_UNIT_DEG / self.cfg.tilt_degrees
        if code == Command.POS1:
            self._plan_move(self.cfg.pos1, final_tilt)
            return True
        if code == Command.POS2:
            self._plan_move(self.cfg.pos2, final_tilt)
            return True
        # GOTO
        if tel.word is not None:
            self._plan_move(tel.word, final_tilt)
            return True
        if final_tilt is not None:
            if self.is_venetian:
                self._plan = [_Segment("tilt", min(max(final_tilt, 0.0), 1.0))]
                self._budget_deg = None
                self._start_if_needed()
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
            value = round(self.position)
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
            value = min(0xFF, round(self.angle_deg / ANGLE_UNIT_DEG))
        elif code == QueryCode.STATUS_BITS:
            value = self.status_bits
        else:
            return bytes([NACK])
        return data_response(code, value)

    # --- state -----------------------------------------------------------------

    @property
    def angle_deg(self) -> float:
        """Slat turning progress in motor-shaft degrees (the unit of SMI angle data)."""
        return self.tilt * self.cfg.tilt_degrees if self.is_venetian else 0.0

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
        if self.position <= 0:
            bits |= STATUS_BIT_AT_TOP
        if self.position >= FULL:
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
            "tilt": round(self.tilt, 4),
            "angle_deg": round(self.angle_deg, 1),
            "slat_percent": round(self.slat_percent, 1),
            "slat_angle": round(self.slat_angle, 1),
            "daylight": round(self.daylight, 3),
            "reversing": self._pause_s > 0,
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
    pos1: int | None = None
    pos2: int | None = None

    def apply(self, motor: Motor) -> None:
        cfg = motor.cfg
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
            if cfg.kind is not BlindKind.VENETIAN:
                motor.tilt = 0.0
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
        if self.pos1 is not None:
            cfg.pos1 = max(0, min(0xFFFF, int(self.pos1)))
        if self.pos2 is not None:
            cfg.pos2 = max(0, min(0xFFFF, int(self.pos2)))
