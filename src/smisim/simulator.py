"""Top-level simulator: SMI lines, their transports, the motion clock and the test lab."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from .bus import Bus, BusSettings
from .config import BusConfig, SimConfig, generate
from .motor import DRIVE_PROFILES, FAULTS, MotorConfig, MotorUpdate
from .protocol.constants import SPEC_STATUS, Command, DiagCode, QueryCode
from .protocol.frames import (
    Addressing,
    MasterTelegram,
    decode_any,
    decode_response,
    parse_hex,
)
from .testlab import TestLab
from .transports import PtyTransport, SerialTransport, TcpTransport, Transport

log = logging.getLogger(__name__)

PRESETS = {
    "16": {"buses": 1, "motors": 16, "label": "1 line, 16 drives (one full SMI line)"},
    "32": {"buses": 2, "motors": 16, "label": "2 lines, 32 drives"},
    "64": {"buses": 4, "motors": 16, "label": "4 lines, 64 drives"},
    "factory-16": {
        "buses": 1,
        "motors": 16,
        "factory_new": True,
        "label": "16 factory-new drives, all on address 0 (commissioning practice)",
    },
    "small": {"buses": 1, "motors": 4, "label": "1 line, 4 drives"},
    "mechanics-16": {
        "buses": 1,
        "motors": 16,
        "kind": "mixed-mechanics",
        "label": "16 drives with mixed mechanics (slack, end offsets) that need calibration",
    },
}


class Simulator:
    def __init__(self, config: SimConfig) -> None:
        self.config = config
        self.buses: list[Bus] = []
        self.transports: dict[int, list[Transport]] = {}
        self.testlab = TestLab()
        self._tick_task: asyncio.Task | None = None
        self._discovery: dict[int, asyncio.Task] = {}
        self.started = time.time()
        self._running = False

    # --- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        for bus_cfg in self.config.buses:
            await self._add_bus(bus_cfg)
        self._tick_task = asyncio.get_running_loop().create_task(self._tick())

    async def stop(self) -> None:
        self._running = False
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
        self.testlab.stop()
        for index in list(self.transports):
            await self._stop_transports(index)

    async def _tick(self) -> None:
        period = 1.0 / self.config.tick_hz
        last = time.monotonic()
        while True:
            await asyncio.sleep(period)
            now = time.monotonic()
            dt = (now - last) * self.config.time_scale
            last = now
            for bus in self.buses:
                bus.update(dt)
            self.testlab.evaluate()

    # --- lines -----------------------------------------------------------------

    async def _add_bus(self, bus_cfg: BusConfig) -> Bus:
        bus = Bus(len(self.buses), bus_cfg.settings)
        for m in bus_cfg.motors:
            bus.add_motor(m, force=True)
        self.buses.append(bus)
        await self._start_transports(bus)
        self.testlab.attach(bus)
        return bus

    async def _start_transports(self, bus: Bus) -> None:
        s = bus.settings
        transports: list[Transport] = []
        if s.tcp_port is not None:
            transports.append(TcpTransport(bus, self.config.tcp_host, s.tcp_port))
        if s.pty_link and os.name == "posix":
            transports.append(PtyTransport(bus, s.pty_link))
        if s.serial_port:
            transports.append(SerialTransport(bus, s.serial_port, s.serial_baud))
        started = []
        for t in transports:
            try:
                await t.start()
                started.append(t)
            except OSError as exc:
                log.error("bus %d: %s transport failed: %s", bus.index, t.kind, exc)
                bus.log_info(f"{t.kind} transport failed: {exc}", "sim")
        self.transports[bus.index] = started
        bus.endpoints = [t.describe() for t in started]

    async def _stop_transports(self, index: int) -> None:
        for t in self.transports.pop(index, []):
            try:
                await t.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("stopping transport: %s", exc)

    async def add_bus(self, name: str | None = None, motors: int = 0, kind: str = "mixed") -> Bus:
        index = len(self.buses)
        generated = generate(
            buses=1,
            motors_per_bus=motors,
            kind=kind,
            tcp_base_port=self.config.tcp_base_port + index if self.config.tcp_base_port else 0,
            pty_base=None,
        )[0]
        generated.settings.name = name or f"Line {index + 1}"
        if self.config.pty_base:
            generated.settings.pty_link = f"{self.config.pty_base}{index}"
        return await self._add_bus(generated)

    async def remove_last_bus(self) -> None:
        if len(self.buses) <= 1:
            raise ValueError("keep at least one line")
        bus = self.buses.pop()
        await self._stop_transports(bus.index)
        if bus in self.testlab.buses:
            self.testlab.buses.remove(bus)

    async def load_preset(self, name: str) -> None:
        preset = PRESETS.get(name)
        if preset is None:
            raise ValueError(f"unknown preset {name!r}")
        want = preset["buses"]
        while len(self.buses) < want:
            await self.add_bus()
        while len(self.buses) > want:
            await self.remove_last_bus()
        layouts = generate(
            buses=want,
            motors_per_bus=preset["motors"],
            kind=preset.get("kind", "mixed"),
            factory_new=preset.get("factory_new", False),
            tcp_base_port=0,
        )
        for bus, layout in zip(self.buses, layouts, strict=True):
            bus.motors.clear()
            for m in layout.motors:
                bus.add_motor(m)
            bus.settings.name = layout.settings.name
            bus.log_info(f"preset loaded: {preset['label']}", "sim")

    def refresh_endpoints(self) -> None:
        for bus in self.buses:
            bus.endpoints = [t.describe() for t in self.transports.get(bus.index, [])]

    # --- drives ----------------------------------------------------------------

    def bus(self, index: int) -> Bus:
        if not 0 <= index < len(self.buses):
            raise KeyError(index)
        return self.buses[index]

    def find_motor(self, uid: int):
        for bus in self.buses:
            for m in bus.motors:
                if m.uid == uid:
                    return bus, m
        raise KeyError(uid)

    def add_motor(self, bus_index: int, data: dict | None = None):
        bus = self.bus(bus_index)
        data = dict(data or {})
        used = {m.address for m in bus.motors}
        free = next((a for a in range(16) if a not in used), 0)
        data.setdefault("address", free)
        data.setdefault("name", f"Drive {bus_index + 1}.{len(bus.motors) + 1:02d}")
        if isinstance(data.get("key_id"), str):
            data["key_id"] = int(data["key_id"], 16)
        profile = data.pop("profile", "")
        if profile:
            return bus.add_motor(MotorConfig.from_profile(profile, **data))
        data.setdefault("kind", "venetian")
        return bus.add_motor(MotorConfig(**data))

    def update_motor(self, uid: int, data: dict) -> None:
        _, motor = self.find_motor(uid)
        data = dict(data)
        if isinstance(data.get("key_id"), str):
            data["key_id"] = int(data["key_id"], 16)
        allowed = MotorUpdate.__dataclass_fields__.keys()
        MotorUpdate(**{k: v for k, v in data.items() if k in allowed}).apply(motor)

    def motor_action(self, uid: int, action: str, value=None) -> None:
        bus, motor = self.find_motor(uid)
        if action == "fault":
            motor.set_fault(value["name"], bool(value.get("active", True)))
        elif action == "clear":
            motor.faults.clear()
            motor.clear_errors()
        elif action == "calibrate":
            motor.calibrate()
        elif action == "wink":
            motor.wink()
        elif action == "remove":
            bus.remove_motor(uid)
        else:
            raise ValueError(f"unknown action {action!r}")

    # --- virtual master --------------------------------------------------------

    async def master_send(self, bus_index: int, request: dict) -> dict:
        """Send a telegram from the built-in master (web UI / API)."""
        bus = self.bus(bus_index)
        if "hex" in request:
            raw = parse_hex(request["hex"])
            decoded = decode_any(raw)
            reply = await bus.transact(raw, "ui")
            out = {
                "sent": raw.hex(" ").upper(),
                "decoded": decoded,
                "reply": reply.hex(" ").upper(),
            }
            if decoded["ok"]:
                tel = MasterTelegram.decode(raw)
                out["response"] = decode_response(tel, reply).to_dict(tel)
            return out
        tel = build_telegram(request)
        raw = tel.encode()
        reply = await bus.transact(raw, "ui")
        return {
            "sent": raw.hex(" ").upper(),
            "telegram": tel.to_dict(),
            "reply": reply.hex(" ").upper(),
            "response": decode_response(tel, reply).to_dict(tel),
        }

    def encode(self, request: dict) -> dict:
        tel = build_telegram(request)
        return {"hex": tel.encode().hex(" ").upper(), "telegram": tel.to_dict()}

    def start_discovery(self, bus_index: int, manufacturer: int = 0) -> None:
        """Run the reference slave-ID discovery from the built-in master."""
        from .client import BusLink, SmiMaster

        bus = self.bus(bus_index)
        running = self._discovery.get(bus_index)
        if running and not running.done():
            raise ValueError("discovery already running on this line")

        async def job() -> None:
            bus.log_info("discovery started (binary search on slave IDs)", "ui")
            try:
                master = SmiMaster(BusLink(bus, "ui"), retries=1)
                found = await master.discover_and_address(manufacturer, first_address=1)
                text = ", ".join(f"{k:08X}->{a}" for k, a in found) or "nothing to do"
                bus.log_info(f"discovery finished: {len(found)} drive(s) addressed: {text}", "ui")
            except Exception as exc:  # noqa: BLE001 - report in the bus log
                bus.log_info(f"discovery failed: {exc}", "ui")

        self._discovery[bus_index] = asyncio.get_running_loop().create_task(job())

    def discovery_running(self, bus_index: int) -> bool:
        task = self._discovery.get(bus_index)
        return bool(task and not task.done())

    # --- state -----------------------------------------------------------------

    def state(self) -> dict:
        return {
            "uptime": time.time() - self.started,
            "time_scale": self.config.time_scale,
            "buses": [
                b.to_state() | {"discovery": self.discovery_running(b.index)} for b in self.buses
            ],
            "testlab": self.testlab.to_state(),
        }

    def meta(self) -> dict:
        return {
            "faults": FAULTS,
            "presets": {k: v["label"] for k, v in PRESETS.items()},
            "spec_status": {k: {"status": s, "source": src} for k, (s, src) in SPEC_STATUS.items()},
            "kinds": ["roller", "venetian", "screen", "awning"],
            "profiles": DRIVE_PROFILES,
        }


def build_telegram(req: dict) -> MasterTelegram:
    """Build a telegram from a JSON request (used by the web UI and the CLI)."""
    mode = req.get("mode", "slave")
    mfr = int(req.get("manufacturer", 0))
    if mode == "slave":
        addressing = Addressing.to_slave(int(req.get("address", 0)))
    elif mode == "group":
        mask = req.get("mask")
        if mask is None:
            addressing = Addressing.to_slaves([int(a) for a in req.get("addresses", [])], mfr)
        else:
            addressing = Addressing.to_group(int(mask), mfr)
    elif mode == "key_id":
        key = req.get("key_id", 0)
        addressing = Addressing.to_key_id(int(key, 16) if isinstance(key, str) else int(key), mfr)
    elif mode == "manufacturer":
        addressing = Addressing.to_manufacturer(mfr)
    else:
        addressing = Addressing.broadcast()

    ttype = req.get("type", "command")
    code = req.get("code")
    if ttype == "command":
        cmd = Command[str(code).upper()] if isinstance(code, str) else Command(int(code))
        position = req.get("position")
        angle = req.get("angle_deg")
        option = req.get("option")
        return MasterTelegram.command(
            addressing,
            cmd,
            position=None if position in (None, "") else int(position),
            angle_deg=None if angle in (None, "") else int(angle),
            option=tuple(option) if option else None,
        )
    if ttype == "diag":
        dcode = DiagCode[str(code).upper()] if isinstance(code, str) else DiagCode(int(code))
        payload = b""
        if dcode == DiagCode.KEY_ID_COMPARE:
            key = req.get("search", 0)
            payload = (int(key, 16) if isinstance(key, str) else int(key)).to_bytes(4, "big")
        elif dcode == DiagCode.WRITE_ADDRESS:
            payload = bytes([int(req.get("new_address", 0)) & 0x0F])
        return MasterTelegram.diag(addressing, dcode, payload)
    qcode = QueryCode[str(code).upper()] if isinstance(code, str) else QueryCode(int(code))
    return MasterTelegram.query(addressing, qcode)


__all__ = ["PRESETS", "Simulator", "build_telegram", "BusSettings"]
