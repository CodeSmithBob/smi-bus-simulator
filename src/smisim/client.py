"""A small SMI master library: talk to the simulator (or a real bus) over TCP or serial.

It doubles as a reference implementation for controller developers, including a
slave-ID based discovery and addressing routine for factory-new drives.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .protocol.constants import BYTE_TIME_S, Command, DiagCode, QueryCode
from .protocol.frames import (
    Addressing,
    MasterTelegram,
    Response,
    decode_response,
    expected_response_length,
)


class TcpLink:
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def write(self, data: bytes) -> None:
        assert self._writer is not None
        self._writer.write(data)
        await self._writer.drain()

    async def read(self, n: int, timeout: float) -> bytes:
        assert self._reader is not None
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                chunk = await asyncio.wait_for(self._reader.read(n - len(out)), left)
            except TimeoutError:
                break
            if not chunk:
                break
            out += chunk
        return bytes(out)

    async def drain_input(self) -> None:
        while await self.read(64, 0.005):
            pass


class SerialLink:
    def __init__(self, port: str, baudrate: int = 2400) -> None:
        self.port, self.baudrate = port, baudrate
        self._ser = None

    async def open(self) -> None:
        import serial

        self._ser = await asyncio.to_thread(serial.Serial, self.port, self.baudrate, timeout=0.01)

    async def close(self) -> None:
        if self._ser:
            self._ser.close()

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self._ser.write, data)

    async def read(self, n: int, timeout: float) -> bytes:
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < n and time.monotonic() < deadline:
            chunk = await asyncio.to_thread(self._ser.read, n - len(out))
            out += chunk
        return bytes(out)

    async def drain_input(self) -> None:
        await asyncio.to_thread(self._ser.reset_input_buffer)


class SmiMaster:
    """Send telegrams and decode answers. ``echo`` strips the interface's own echo."""

    def __init__(self, link, *, echo: bool = False, timeout: float = 0.25, retries: int = 2):
        self.link = link
        self.echo = echo
        self.timeout = timeout
        self.retries = retries
        self.log: Callable[[str], None] | None = None

    async def __aenter__(self) -> SmiMaster:
        await self.link.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.link.close()

    async def transact(self, tel: MasterTelegram, *, retries: int | None = None) -> Response:
        raw = tel.encode()
        expected = expected_response_length(tel)
        attempts = 1 + (self.retries if retries is None else retries)
        response = Response("none")
        for _ in range(attempts):
            await self.link.drain_input()
            await self.link.write(raw)
            if self.echo:
                await self.link.read(len(raw), self.timeout + len(raw) * BYTE_TIME_S)
            data = await self.link.read(expected, self.timeout + expected * BYTE_TIME_S)
            response = decode_response(tel, data)
            if self.log:
                self.log(f"-> {raw.hex(' ').upper():<26} {tel.describe()}")
                self.log(f"<- {data.hex(' ').upper():<26} {response.describe(tel)}")
            if response.ok or response.kind == "nack":
                return response
        return response

    # Convenience wrappers -------------------------------------------------------

    async def command(self, addressing: Addressing, cmd: Command, **kw) -> Response:
        return await self.transact(MasterTelegram.command(addressing, cmd, **kw))

    async def up(self, address: int) -> Response:
        return await self.command(Addressing.to_slave(address), Command.UP)

    async def down(self, address: int) -> Response:
        return await self.command(Addressing.to_slave(address), Command.DOWN)

    async def stop(self, address: int) -> Response:
        return await self.command(Addressing.to_slave(address), Command.STOP)

    async def goto(self, address: int, position: int, angle_deg: int | None = None) -> Response:
        return await self.command(
            Addressing.to_slave(address), Command.GOTO, position=position, angle_deg=angle_deg
        )

    async def read(self, address: int, code: QueryCode) -> Response:
        return await self.transact(MasterTelegram.query(Addressing.to_slave(address), code))

    async def read_position(self, address: int) -> int | None:
        r = await self.read(address, QueryCode.POSITION)
        return r.value if r.kind == "data" else None

    async def diag(self, addressing: Addressing) -> Response:
        return await self.transact(MasterTelegram.diag(addressing, DiagCode.STATUS))

    async def compare_key_id(self, search: int, manufacturer: int = 0) -> Response:
        tel = MasterTelegram.diag(
            Addressing.to_manufacturer(manufacturer),
            DiagCode.KEY_ID_COMPARE,
            search.to_bytes(4, "big"),
        )
        return await self.transact(tel)

    async def write_address(self, addressing: Addressing, new_address: int) -> Response:
        tel = MasterTelegram.diag(addressing, DiagCode.WRITE_ADDRESS, bytes([new_address]))
        return await self.transact(tel)

    async def discover_and_address(
        self, manufacturer: int, first_address: int = 1, max_drives: int = 16
    ) -> list[tuple[int, int]]:
        """Give every drive that still sits on address 0 its own address.

        Binary search on the 32-bit slave ID using the KEY_ID_COMPARE flags
        (``addr0 & ID < search``, ``addr0 & ID = search``). The smallest key ID among
        the drives on address 0 is found in 32 steps, then that drive is addressed by
        key ID and moved to the next free address. Addresses that already answer a
        position query count as taken. When no free address is left, the remaining
        drive stays on 0. Returns ``[(key_id, address), ...]``.
        """
        free: list[int] = []
        for a in range(max(1, first_address), 16):
            probe = await self.transact(
                MasterTelegram.query(Addressing.to_slave(a), QueryCode.POSITION), retries=0
            )
            if probe.kind == "none":
                free.append(a)
        assigned: list[tuple[int, int]] = []
        while len(assigned) < max_drives and free:
            address = free[0]
            lo, hi = 0, 0xFFFFFFFF
            probe = await self.compare_key_id(hi, manufacturer)
            if probe.kind != "flags" or not (probe.flags[1] or probe.flags[2]):
                break  # no drive left on address 0
            while lo < hi:
                mid = (lo + hi) // 2
                r = await self.compare_key_id(mid, manufacturer)
                if r.kind != "flags":
                    raise RuntimeError(f"discovery failed at {mid:08X}: {r.describe()}")
                # Is there a drive with ID <= mid?
                if r.flags[1] or r.flags[2]:
                    hi = mid
                else:
                    lo = mid + 1
            key_id = lo
            r = await self.write_address(Addressing.to_key_id(key_id, manufacturer), address)
            if not r.ok:
                raise RuntimeError(f"could not address drive {key_id:08X}: {r.describe()}")
            assigned.append((key_id, address))
            free.pop(0)
        return assigned


class BusLink:
    """In-process link to a simulated line (the web UI uses it to run the master library)."""

    def __init__(self, bus, source: str = "ui") -> None:
        self.bus = bus
        self.source = source
        self._pending = b""

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def write(self, data: bytes) -> None:
        self._pending = await self.bus.transact(data, self.source)

    async def read(self, n: int, timeout: float) -> bytes:
        out, self._pending = self._pending[:n], self._pending[n:]
        return out

    async def drain_input(self) -> None:
        self._pending = b""
