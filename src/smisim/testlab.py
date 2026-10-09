"""Controller test lab: observe an SMI master under test and score its behaviour.

This is NOT the official SMI certification (that is run by SMI Standard Motor
Interface e.V.). It is a pre-check tool that watches the telegrams of a controller
connected to the simulator, injects faults on request and reports what it saw.
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field

from .bus import Bus, Transaction
from .motor import Motor
from .protocol.constants import Command, DiagCode, QueryCode, TelegramType

PENDING, RUNNING, PASS, FAIL, INFO = "pending", "running", "pass", "fail", "info"

RETRY_WINDOW_S = 3.0
ARM_TIMEOUT_S = 30.0
OFFLINE_DURATION_S = 15.0
THERMAL_WINDOW_S = 20.0

MOVE_CODES = {Command.UP, Command.DOWN, Command.POS1, Command.POS2, Command.GOTO}


@dataclass
class Check:
    id: str
    title: str
    description: str
    armable: bool = False
    status: str = PENDING
    detail: str = ""
    updated: float = 0.0

    def set(self, status: str, detail: str = "") -> None:
        if self.status != status or self.detail != detail:
            self.status, self.detail, self.updated = status, detail, time.time()


@dataclass
class _Armed:
    check: str
    motor: Motor
    bus: Bus
    t0: float
    awaiting: bytes | None = None
    awaiting_t: float = 0.0
    hits: int = 0
    extra: dict = field(default_factory=dict)


def _checks() -> list[Check]:
    return [
        Check(
            "population",
            "Line population and unique addresses",
            "Every line has 1 to 16 drives and no two drives share a slave address. "
            "Factory-new drives all start at address 0, so a controller must commission them.",
        ),
        Check(
            "reach_all",
            "Every drive commanded",
            "Each drive received at least one movement command (single, group or broadcast).",
        ),
        Check(
            "poll_all",
            "Every drive's position read back",
            "Each drive answered at least one position query.",
        ),
        Check(
            "group",
            "Group or broadcast telegram used",
            "At least one movement telegram reached two or more drives at once.",
        ),
        Check(
            "diag",
            "Diagnosis used",
            "The controller sent at least one diagnosis telegram.",
        ),
        Check(
            "retry_timeout",
            "Retry after a lost answer",
            "Arm it: the next answer of a random drive is dropped. The controller should "
            f"repeat the telegram within {RETRY_WINDOW_S:.0f} s.",
            armable=True,
        ),
        Check(
            "retry_checksum",
            "Retry after a checksum error",
            "Arm it: the next answer of a random drive has a broken checksum. The controller "
            f"should repeat the telegram within {RETRY_WINDOW_S:.0f} s.",
            armable=True,
        ),
        Check(
            "offline",
            "No flooding when a drive is offline",
            f"Arm it: a random drive goes offline for {OFFLINE_DURATION_S:.0f} s. The controller "
            "should not send more than 1 telegram per second to it on average.",
            armable=True,
        ),
        Check(
            "thermal",
            "Fault detection (thermal protection)",
            "Arm it: a random drive trips its thermal protection. The controller should run "
            f"a diagnosis or status read that covers this drive within {THERMAL_WINDOW_S:.0f} s.",
            armable=True,
        ),
        Check(
            "bus_load",
            "Bus load",
            "Peak bus utilisation over a 10 s window stays below 70 % (2400 baud is slow).",
        ),
    ]


class TestLab:
    def __init__(self) -> None:
        self.checks = {c.id: c for c in _checks()}
        self.active = False
        self.started = 0.0
        self.include_ui = True
        self.buses: list[Bus] = []
        self._commanded: set[int] = set()
        self._polled: set[int] = set()
        self._group = False
        self._diag = False
        self._peak_util = 0.0
        self._armed: dict[str, _Armed] = {}
        self._telegrams = 0

    # --- session ---------------------------------------------------------------

    def start(self, buses: list[Bus], include_ui: bool = True) -> None:
        self.stop()
        self.checks = {c.id: c for c in _checks()}
        self.active = True
        self.started = time.time()
        self.include_ui = include_ui
        self.buses = list(buses)
        self._commanded.clear()
        self._polled.clear()
        self._group = self._diag = False
        self._peak_util = 0.0
        self._telegrams = 0
        for bus in self.buses:
            bus.listeners.append(self.on_transaction)

    def stop(self) -> None:
        for bus in self.buses:
            if self.on_transaction in bus.listeners:
                bus.listeners.remove(self.on_transaction)
        for armed in list(self._armed.values()):
            self._disarm(armed)
        self._armed.clear()
        self.active = False

    def attach(self, bus: Bus) -> None:
        if self.active and bus not in self.buses:
            self.buses.append(bus)
            bus.listeners.append(self.on_transaction)

    def arm(self, check_id: str) -> str:
        if not self.active:
            raise ValueError("start a test session first")
        check = self.checks.get(check_id)
        if check is None or not check.armable:
            raise ValueError(f"{check_id} cannot be armed")
        candidates = [(b, m) for b in self.buses for m in b.motors if not m.faults]
        if not candidates:
            raise ValueError("no healthy drive available")
        bus, motor = random.choice(candidates)
        armed = _Armed(check_id, motor, bus, time.time())
        if check_id == "retry_timeout":
            motor.set_fault("drop_next", True)
        elif check_id == "retry_checksum":
            motor.set_fault("corrupt_next", True)
        elif check_id == "offline":
            motor.set_fault("offline", True)
        elif check_id == "thermal":
            motor.set_fault("overheat", True)
        self._armed[check_id] = armed
        what = f"drive '{motor.cfg.name}' (line {bus.index + 1}, address {motor.address})"
        check.set(RUNNING, f"Armed on {what}.")
        bus.log_info(f"test lab: {check.title} armed on {what}", "testlab")
        return what

    def _disarm(self, armed: _Armed) -> None:
        for name in ("drop_next", "corrupt_next", "offline"):
            armed.motor.set_fault(name, False)
        self._armed.pop(armed.check, None)

    # --- observation -----------------------------------------------------------

    def on_transaction(self, txn: Transaction) -> None:
        if not self.include_ui and txn.source in ("ui", "testlab"):
            return
        self._telegrams += 1
        tel = txn.telegram
        addressed = txn.responders + txn.silent
        if tel.ttype is TelegramType.COMMAND and tel.code in MOVE_CODES:
            for m in addressed:
                self._commanded.add(m.uid)
            if len(addressed) >= 2:
                self._group = True
        elif tel.ttype is TelegramType.QUERY and tel.code == QueryCode.POSITION:
            if txn.response.kind == "data" and len(txn.responders) == 1:
                self._polled.add(txn.responders[0].uid)
        elif tel.ttype is TelegramType.DIAG and tel.code == DiagCode.STATUS:
            self._diag = True
        for armed in list(self._armed.values()):
            self._observe_armed(armed, txn, addressed)

    def _observe_armed(self, armed: _Armed, txn: Transaction, addressed: list[Motor]) -> None:
        check = self.checks[armed.check]
        target = armed.motor
        if armed.check in ("retry_timeout", "retry_checksum"):
            if armed.awaiting is None:
                lost = (
                    target in txn.silent
                    if armed.check == "retry_timeout"
                    else (target in txn.responders and txn.response.kind == "garbled")
                )
                if lost:
                    armed.awaiting = txn.raw
                    armed.awaiting_t = txn.t
                    check.set(
                        RUNNING,
                        f"Answer to '{txn.telegram.describe()}' was spoiled; waiting for a retry.",
                    )
            elif txn.raw == armed.awaiting:
                dt = txn.t - armed.awaiting_t
                check.set(PASS, f"Controller repeated the telegram after {dt:.2f} s.")
                self._disarm(armed)
        elif armed.check == "offline":
            if target in addressed:
                armed.hits += 1
        elif armed.check == "thermal":
            tel = txn.telegram
            covers = target in addressed
            is_status = (tel.ttype is TelegramType.DIAG and tel.code == DiagCode.STATUS) or (
                tel.ttype is TelegramType.QUERY and tel.code == QueryCode.STATUS_BITS
            )
            if covers and is_status and txn.t - armed.t0 > 0.05:
                dt = txn.t - armed.t0
                check.set(PASS, f"Controller checked the drive's status {dt:.1f} s after the trip.")
                self._disarm(armed)

    # --- evaluation (called every tick) ----------------------------------------

    def evaluate(self) -> None:
        if not self.active:
            return
        now = time.time()
        motors = [m for b in self.buses for m in b.motors]
        n = len(motors)
        c = self.checks
        dups = {b.index + 1: b.duplicate_addresses() for b in self.buses}
        bad = {k: v for k, v in dups.items() if v}
        sizes_ok = all(1 <= len(b.motors) <= 16 for b in self.buses)
        if not sizes_ok:
            c["population"].set(FAIL, "A line has no drives or more than 16 drives.")
        elif bad:
            text = "; ".join(f"line {k}: address {', '.join(map(str, v))}" for k, v in bad.items())
            c["population"].set(RUNNING, f"Duplicate addresses: {text}.")
        else:
            c["population"].set(PASS, f"{n} drives on {len(self.buses)} line(s), all unique.")
        self._progress(c["reach_all"], self._commanded, motors, "commanded")
        self._progress(c["poll_all"], self._polled, motors, "read back")
        if self._group:
            c["group"].set(PASS, "Seen.")
        if self._diag:
            c["diag"].set(PASS, "Seen.")
        util = max((b.utilization() for b in self.buses), default=0.0)
        self._peak_util = max(self._peak_util, util)
        status = PASS if self._peak_util < 0.7 else FAIL
        c["bus_load"].set(
            status if self._telegrams else PENDING,
            f"Now {util * 100:.0f} %, peak {self._peak_util * 100:.0f} %.",
        )
        for armed in list(self._armed.values()):
            check = c[armed.check]
            if armed.check in ("retry_timeout", "retry_checksum"):
                if armed.awaiting is not None and now - armed.awaiting_t > RETRY_WINDOW_S:
                    check.set(FAIL, f"No retry within {RETRY_WINDOW_S:.0f} s.")
                    self._disarm(armed)
                elif armed.awaiting is None and now - armed.t0 > ARM_TIMEOUT_S:
                    check.set(
                        FAIL,
                        f"The controller did not address the drive within "
                        f"{ARM_TIMEOUT_S:.0f} s, so nothing could be tested.",
                    )
                    self._disarm(armed)
            elif armed.check == "offline":
                elapsed = now - armed.t0
                rate = armed.hits / max(elapsed, 1e-6)
                if elapsed >= OFFLINE_DURATION_S:
                    ok = rate <= 1.0
                    check.set(
                        PASS if ok else FAIL,
                        f"{armed.hits} telegrams to the offline drive in "
                        f"{OFFLINE_DURATION_S:.0f} s ({rate:.2f}/s).",
                    )
                    self._disarm(armed)
                else:
                    check.set(
                        RUNNING,
                        f"{armed.hits} telegrams so far ({rate:.2f}/s), "
                        f"{OFFLINE_DURATION_S - elapsed:.0f} s left.",
                    )
            elif armed.check == "thermal" and now - armed.t0 > THERMAL_WINDOW_S:
                check.set(FAIL, f"No status check of the drive within {THERMAL_WINDOW_S:.0f} s.")
                self._disarm(armed)

    @staticmethod
    def _progress(check: Check, seen: set[int], motors: list[Motor], verb: str) -> None:
        if not motors:
            check.set(PENDING, "No drives.")
            return
        done = sum(1 for m in motors if m.uid in seen)
        if done == len(motors):
            check.set(PASS, f"All {done} drives {verb}.")
        elif done:
            check.set(RUNNING, f"{done} of {len(motors)} drives {verb}.")
        else:
            check.set(PENDING, f"0 of {len(motors)} drives {verb}.")

    def to_state(self) -> dict:
        return {
            "active": self.active,
            "started": self.started,
            "include_ui": self.include_ui,
            "telegrams": self._telegrams,
            "checks": [asdict(c) for c in self.checks.values()],
        }

    def report(self) -> dict:
        state = self.to_state()
        state["generated"] = time.time()
        state["disclaimer"] = (
            "Unofficial pre-check produced by smi-bus-simulator. It is not an SMI e.V. "
            "certification and does not replace it."
        )
        state["lines"] = [
            {
                "line": b.index + 1,
                "name": b.settings.name,
                "drives": len(b.motors),
                "stats": dict(b.stats),
            }
            for b in self.buses
        ]
        return state
