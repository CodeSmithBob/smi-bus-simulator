"""Virtual serial port (pseudo terminal) on Linux and macOS.

Controller software opens the slave side (e.g. ``/tmp/smi0``) exactly like a
USB-serial adapter at 2400 8N1.
"""

from __future__ import annotations

import asyncio
import logging
import os

from ..bus import Bus
from .base import Link, Transport

log = logging.getLogger(__name__)


class PtyTransport(Transport):
    kind = "pty"

    def __init__(self, bus: Bus, link_path: str | None = None) -> None:
        super().__init__(bus)
        self.link_path = link_path
        self.device: str | None = None
        self._master: int | None = None
        self._slave: int | None = None
        self._link: Link | None = None

    async def start(self) -> None:
        import termios
        import tty

        master, slave = os.openpty()
        tty.setraw(slave, termios.TCSANOW)
        tty.setraw(master, termios.TCSANOW)
        os.set_blocking(master, False)
        self._master, self._slave = master, slave
        self.device = os.ttyname(slave)
        if self.link_path:
            try:
                if os.path.islink(self.link_path) or os.path.exists(self.link_path):
                    os.unlink(self.link_path)
                os.symlink(self.device, self.link_path)
            except OSError as exc:
                log.warning("could not create %s -> %s: %s", self.link_path, self.device, exc)
                self.link_path = None

        async def write(data: bytes) -> None:
            if self._master is not None:
                os.write(self._master, data)

        self._link = Link(self.bus, "pty", write)
        asyncio.get_running_loop().add_reader(master, self._readable)
        log.info("bus %d: PTY %s", self.bus.index, self.link_path or self.device)

    def _readable(self) -> None:
        if self._master is None or self._link is None:
            return
        try:
            data = os.read(self._master, 1024)
        except BlockingIOError:
            return
        except OSError:
            return
        if data:
            self._link.feed(data)

    async def stop(self) -> None:
        if self._master is not None:
            asyncio.get_running_loop().remove_reader(self._master)
        if self._link is not None:
            self._link.close()
            await self._link.wait_closed()
        for fd in (self._master, self._slave):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._master = self._slave = None
        if self.link_path and os.path.islink(self.link_path):
            try:
                os.unlink(self.link_path)
            except OSError:
                pass

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "endpoint": self.link_path or self.device,
            "device": self.device,
        }
