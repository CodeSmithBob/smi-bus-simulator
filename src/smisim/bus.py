"""One SMI line: up to 16 drives sharing a two-wire, wired-AND data bus.

:class:`Bus`, :class:`BusSettings` and :class:`FrameResult` are part of the stable
in-process API (see docs/api.md). A ``Bus`` needs neither the web server nor a running
asyncio event loop: feed it raw telegrams with :meth:`Bus.handle_frame` and advance time
with :meth:`Bus.update`.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from .motor import Motor, MotorConfig
from .protocol.constants import BYTE_TIME_S, MAX_DRIVES_PER_BUS
from .protocol.frames import (
    FrameError,
    MasterTelegram,
    Response,
    decode_any,
    decode_response,
    wired_and,
)

_seq = itertools.count(1)
_uid = itertools.count(1)


@dataclass
class BusSettings:
    name: str = "SMI line"
    variant: str = "SMI"  # "SMI" (230 V AC drives) or "SMI LoVo" (24 V DC drives)
    echo: bool = False  # send the master's own bytes back, like many real interfaces do
    # Turnaround between telegram end and answer. Assumption: no public figure (see
    # docs/configuration.md, "Where the default timing values come from").
    response_delay_ms: float = 8.0
    realtime: bool = True  # pace answers at 2400 baud
    tcp_port: int | None = None
    pty_link: str | None = None
    serial_port: str | None = None
    serial_baud: int = 2400
    shared_bus: bool = False  # serial port is attached to a real bus with real drives
    max_drives: int = MAX_DRIVES_PER_BUS

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class TrafficEntry:
    seq: int
    t: float
    bus: int
    direction: str  # "M" master telegram, "S" drive answer, "E" error, "I" info
    source: str
    hex: str
    text: str
    status: str = ""
    ok: bool = True
    tx: int = 0  # transaction number shared by a telegram and its answer

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Transaction:
    bus: Bus
    telegram: MasterTelegram
    raw: bytes
    reply: bytes
    response: Response
    responders: list[Motor]
    silent: list[Motor]  # addressed drives that did not answer
    source: str
    t: float = field(default_factory=time.time)


Listener = Callable[[Transaction], None]


class FrameResult(NamedTuple):
    """Outcome of :meth:`Bus.handle_frame`. Unpacks as ``reply, delay_s``."""

    reply: bytes  # what the master reads back (wired-AND of all answers); b"" = silence
    delay_s: float  # turnaround before the first answer byte, in seconds


class Bus:
    def __init__(self, index: int = 0, settings: BusSettings | None = None) -> None:
        self.index = index
        self.settings = settings if settings is not None else BusSettings()
        self.motors: list[Motor] = []
        self.traffic: deque[TrafficEntry] = deque(maxlen=4000)
        self.listeners: list[Listener] = []
        self.lock = asyncio.Lock()
        self.stats = {"telegrams": 0, "answers": 0, "no_answer": 0, "errors": 0, "bytes": 0}
        self._busy: deque[tuple[float, float]] = deque()
        self._tx = itertools.count(1)
        self.endpoints: list[dict] = []

    # --- drives ----------------------------------------------------------------

    def add_motor(self, cfg: MotorConfig, *, force: bool = False) -> Motor:
        if len(self.motors) >= self.settings.max_drives and not force:
            raise ValueError(
                f"an SMI line carries at most {self.settings.max_drives} drives; add another bus"
            )
        motor = Motor(cfg, next(_uid))
        self.motors.append(motor)
        return motor

    def remove_motor(self, uid: int) -> None:
        self.motors = [m for m in self.motors if m.uid != uid]

    def motor(self, uid: int) -> Motor:
        for m in self.motors:
            if m.uid == uid:
                return m
        raise KeyError(uid)

    def update(self, dt: float) -> None:
        """Advance simulated time by ``dt`` seconds for every drive on the line."""
        if dt < 0:
            raise ValueError("dt must not be negative")
        for m in self.motors:
            m.update(dt)

    # --- telegram handling -----------------------------------------------------

    def handle_frame(self, raw: bytes | bytearray, source: str = "api") -> FrameResult:
        """Process one complete master telegram, checksum included.

        Every addressed drive executes it at once. Returns the bytes the master would
        read (empty if no drive answers or the telegram is invalid) and the answer delay.
        Invalid telegrams are logged and ignored, like real drives do.
        """
        raw = bytes(raw)
        now = time.time()
        try:
            tel = MasterTelegram.decode(raw)
        except (FrameError, ValueError, IndexError) as exc:
            self.log_error(raw, str(exc), source)
            return FrameResult(b"", 0.0)
        tx = next(self._tx)
        self.stats["telegrams"] += 1
        self._log("M", source, raw, tel.describe(), status=tel.status, tx=tx, t=now)
        addressed = [m for m in self.motors if m.is_addressed(tel)]
        replies = []
        responders = []
        silent = []
        for m in addressed:
            r = m.handle(tel)
            if r is None:
                silent.append(m)
            else:
                replies.append(r)
                responders.append(m)
        reply = wired_and([r.data for r in replies])
        delay = self.settings.response_delay_ms / 1000.0
        if replies:
            delay += max(r.extra_delay_s for r in replies)
        response = decode_response(tel, reply)
        if reply:
            self.stats["answers"] += 1
            who = ",".join(str(m.address) for m in responders)
            text = response.describe(tel)
            if len(responders) > 1:
                text += f"  [{len(responders)} drives answered: {who}]"
            self._log("S", "drive", reply, text, ok=response.ok, tx=tx, t=now + delay)
        else:
            self.stats["no_answer"] += 1
            why = "no drive addressed" if not addressed else "addressed drive(s) silent"
            self._log("I", "bus", b"", f"no answer ({why})", ok=False, tx=tx, t=now + delay)
        self.stats["bytes"] += len(raw) + len(reply)
        self._busy.append((now, (len(raw) + len(reply)) * BYTE_TIME_S))
        txn = Transaction(self, tel, raw, reply, response, responders, silent, source, now)
        for listener in list(self.listeners):
            listener(txn)
        return FrameResult(reply, delay)

    async def transact(self, raw: bytes, source: str = "ui") -> bytes:
        """Used by in-process masters (web UI, test lab): send and wait like on the wire."""
        async with self.lock:
            if self.settings.realtime:
                await asyncio.sleep(len(raw) * BYTE_TIME_S)
            reply, delay = self.handle_frame(raw, source)
            if self.settings.realtime and reply:
                await asyncio.sleep(delay + len(reply) * BYTE_TIME_S)
            return reply

    def log_error(self, raw: bytes, error: str, source: str) -> None:
        self.stats["errors"] += 1
        decoded = decode_any(raw)
        hint = decoded.get("hint")
        text = error + (f" ({hint})" if hint else "")
        self._log("E", source, raw, text, ok=False)

    def log_info(self, text: str, source: str = "sim") -> None:
        self._log("I", source, b"", text)

    def _log(
        self,
        direction: str,
        source: str,
        raw: bytes,
        text: str,
        *,
        status: str = "",
        ok: bool = True,
        tx: int = 0,
        t: float | None = None,
    ) -> None:
        self.traffic.append(
            TrafficEntry(
                seq=next(_seq),
                t=t if t is not None else time.time(),
                bus=self.index,
                direction=direction,
                source=source,
                hex=raw.hex(" ").upper(),
                text=text,
                status=status,
                ok=ok,
                tx=tx,
            )
        )

    # --- statistics ------------------------------------------------------------

    def utilization(self, window_s: float = 10.0) -> float:
        now = time.time()
        while self._busy and self._busy[0][0] < now - window_s:
            self._busy.popleft()
        busy = sum(d for _, d in self._busy)
        return min(1.0, busy / window_s)

    def duplicate_addresses(self) -> list[int]:
        seen: dict[int, int] = {}
        for m in self.motors:
            seen[m.address] = seen.get(m.address, 0) + 1
        return sorted(a for a, n in seen.items() if n > 1)

    def to_state(self) -> dict:
        return {
            "index": self.index,
            "settings": self.settings.to_dict(),
            "endpoints": self.endpoints,
            "stats": dict(self.stats),
            "utilization": round(self.utilization(), 4),
            "duplicates": self.duplicate_addresses(),
            "motors": [m.to_state() for m in self.motors],
        }
