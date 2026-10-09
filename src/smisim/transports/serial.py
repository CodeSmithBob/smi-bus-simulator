"""Physical serial port (USB-UART adapter, RS-232, or an SMI-to-UART transceiver).

Two use cases:

* Point-to-point: a controller under test talks UART to this PC through a
  null-modem / crossed TX-RX USB-UART cable. The simulator plays all drives.
* Shared bus (``shared_bus = true``): the port is an SMI transceiver attached to a
  real SMI line. Simulated drives answer next to real drives and a real master.
  Never connect mains-voltage SMI wiring to anything not designed for it.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from ..bus import Bus
from .base import Link, Transport

log = logging.getLogger(__name__)


class SerialTransport(Transport):
    kind = "serial"

    def __init__(self, bus: Bus, port: str, baudrate: int = 2400) -> None:
        super().__init__(bus)
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._link: Link | None = None
        self.error: str | None = None

    async def start(self) -> None:
        import serial  # pyserial

        try:
            self._serial = serial.Serial(
                self.port,
                self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.02,
            )
        except (serial.SerialException, OSError) as exc:
            self.error = str(exc)
            log.error("bus %d: cannot open %s: %s", self.bus.index, self.port, exc)
            self.bus.log_info(f"serial port {self.port} unavailable: {exc}", "serial")
            return
        loop = asyncio.get_running_loop()
        ser = self._serial

        async def write(data: bytes) -> None:
            await asyncio.to_thread(ser.write, data)

        self._link = Link(self.bus, "serial", write)
        link = self._link

        def reader() -> None:
            while not self._stop.is_set():
                try:
                    data = ser.read(64)
                except Exception as exc:  # noqa: BLE001 - report and stop reading
                    loop.call_soon_threadsafe(
                        self.bus.log_info, f"serial read error: {exc}", "serial"
                    )
                    return
                if data:
                    loop.call_soon_threadsafe(link.feed, data)

        self._thread = threading.Thread(target=reader, name=f"smi-serial-{self.port}", daemon=True)
        self._thread.start()
        log.info("bus %d: serial %s @ %d", self.bus.index, self.port, self.baudrate)

    async def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1.0)
        if self._link is not None:
            self._link.close()
            await self._link.wait_closed()
        if self._serial is not None:
            self._serial.close()

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "endpoint": f"{self.port} @ {self.baudrate} 8N1",
            "error": self.error,
            "shared_bus": self.bus.settings.shared_bus,
        }
