"""Common plumbing for the ways a controller can reach a simulated SMI line."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..bus import Bus
from ..protocol.assembler import FrameAssembler
from ..protocol.constants import BYTE_TIME_S

log = logging.getLogger(__name__)

Writer = Callable[[bytes], Awaitable[None]]


class Link:
    """One byte stream between a master and the bus (a TCP client, a PTY, a serial port)."""

    def __init__(self, bus: Bus, source: str, write: Writer) -> None:
        self.bus = bus
        self.source = source
        self._write = write
        self.assembler = FrameAssembler()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def feed(self, data: bytes) -> None:
        self._queue.put_nowait(data)

    def close(self) -> None:
        self._queue.put_nowait(None)

    async def wait_closed(self) -> None:
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        settings = self.bus.settings
        while True:
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=0.05)
            except TimeoutError:
                for frame in self.assembler.flush_if_idle():
                    self.bus.log_error(frame.data, frame.error or "error", self.source)
                continue
            if data is None:
                return
            for frame in self.assembler.feed(data):
                if not frame.valid:
                    self.bus.log_error(frame.data, frame.error or "error", self.source)
                    continue
                try:
                    await self._transaction(frame.data, settings)
                except (ConnectionError, OSError) as exc:
                    log.info("%s: write failed: %s", self.source, exc)

    async def _transaction(self, raw: bytes, settings) -> None:
        async with self.bus.lock:
            if settings.echo:
                await self._write(raw)
            reply, delay = self.bus.handle_frame(raw, self.source)
            if settings.shared_bus:
                # On a real bus we would read our own answer and other drives' answers back.
                window = delay + (len(reply) or 8) * BYTE_TIME_S + 0.03
                self.assembler.ignore_for(window)
            if not reply:
                return
            await asyncio.sleep(delay)
            if settings.realtime:
                for b in reply:
                    await self._write(bytes([b]))
                    await asyncio.sleep(BYTE_TIME_S)
            else:
                await self._write(reply)


class Transport:
    kind = "base"

    def __init__(self, bus: Bus) -> None:
        self.bus = bus

    async def start(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict:  # pragma: no cover - interface
        raise NotImplementedError
