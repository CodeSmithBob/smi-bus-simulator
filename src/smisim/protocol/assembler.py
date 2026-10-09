"""Split a raw byte stream into master telegrams.

Real SMI masters send each telegram as one burst of back-to-back bytes, but a
simulator can receive the bytes in arbitrary chunks (PTY, TCP, USB-serial). The
assembler uses the self-describing telegram structure to find frame boundaries,
resynchronises after garbage by dropping one byte at a time, and flushes partial
frames after an idle gap.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .constants import BYTE_TIME_S
from .frames import FrameError, checksum_ok, expected_length


@dataclass
class AssembledFrame:
    data: bytes
    valid: bool  # structure + checksum ok
    error: str | None = None


class FrameAssembler:
    def __init__(
        self,
        idle_timeout_s: float = 20 * BYTE_TIME_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.idle_timeout_s = idle_timeout_s
        self._clock = clock
        self._buf = bytearray()
        self._last_rx = 0.0
        self._ignore_until = 0.0

    def ignore_for(self, seconds: float) -> None:
        """Discard incoming bytes for a while (own echo / other drives on a shared bus)."""
        self._ignore_until = max(self._ignore_until, self._clock() + seconds)
        self._buf.clear()

    def feed(self, data: bytes) -> list[AssembledFrame]:
        now = self._clock()
        if now < self._ignore_until:
            return []
        out: list[AssembledFrame] = []
        if self._buf and now - self._last_rx > self.idle_timeout_s:
            out.append(AssembledFrame(bytes(self._buf), False, "incomplete telegram (idle gap)"))
            self._buf.clear()
        self._last_rx = now
        self._buf += data
        out.extend(self._drain())
        return out

    def flush_if_idle(self) -> list[AssembledFrame]:
        if self._buf and self._clock() - self._last_rx > self.idle_timeout_s:
            frame = AssembledFrame(bytes(self._buf), False, "incomplete telegram (idle gap)")
            self._buf.clear()
            return [frame]
        return []

    def _drain(self) -> list[AssembledFrame]:
        out: list[AssembledFrame] = []
        junk = bytearray()
        while self._buf:
            try:
                n = expected_length(self._buf)
            except FrameError:
                junk.append(self._buf.pop(0))
                continue
            if n is None or len(self._buf) < n:
                break
            if junk:
                out.append(AssembledFrame(bytes(junk), False, "unexpected bytes"))
                junk.clear()
            candidate = bytes(self._buf[:n])
            if checksum_ok(candidate):
                out.append(AssembledFrame(candidate, True))
                del self._buf[:n]
                continue
            # Corrupted telegram. If a valid telegram starts inside it, only drop the
            # bytes before that one; otherwise drop the whole candidate.
            k = _find_sync(self._buf, 1, n)
            cut = k if k is not None else n
            out.append(AssembledFrame(bytes(self._buf[:cut]), False, "checksum error"))
            del self._buf[:cut]
        if junk:
            out.append(AssembledFrame(bytes(junk), False, "unexpected bytes"))
        return out


def _find_sync(buf: bytearray, start: int, stop: int) -> int | None:
    """Offset in [start, stop) where a complete valid telegram begins."""
    for k in range(start, min(stop, len(buf))):
        try:
            m = expected_length(buf[k:])
        except FrameError:
            continue
        if m is not None and len(buf) - k >= m and checksum_ok(buf[k : k + m]):
            return k
    return None
