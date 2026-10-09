"""Raw TCP socket per SMI line (compatible with ser2net, socat, com0com/hub4com, etc.)."""

from __future__ import annotations

import asyncio
import logging

from ..bus import Bus
from .base import Link, Transport

log = logging.getLogger(__name__)


class TcpTransport(Transport):
    kind = "tcp"

    def __init__(self, bus: Bus, host: str, port: int) -> None:
        super().__init__(bus)
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        sock = self._server.sockets[0] if self._server.sockets else None
        if sock is not None:
            self.port = sock.getsockname()[1]
        log.info("bus %d: TCP on %s:%d", self.bus.index, self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for w in list(self._clients):
                w.close()
            await self._server.wait_closed()
            self._server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self._clients.add(writer)
        self.bus.log_info(f"TCP client connected: {peer}", "tcp")

        async def write(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        link = Link(self.bus, "tcp", write)
        try:
            while data := await reader.read(256):
                link.feed(data)
        except (ConnectionError, OSError):
            pass
        finally:
            link.close()
            await link.wait_closed()
            self._clients.discard(writer)
            writer.close()
            self.bus.log_info(f"TCP client disconnected: {peer}", "tcp")

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "endpoint": f"tcp://{self.host}:{self.port}",
            "port": self.port,
            "clients": len(self._clients),
        }
