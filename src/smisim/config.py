"""Simulator configuration: TOML files and quick generated setups."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .bus import BusSettings
from .motor import BlindKind, MotorConfig

ROOMS = [
    "Living", "Kitchen", "Dining", "Office", "Bedroom", "Kids", "Guest", "Bath",
    "Hall", "Studio", "Library", "Lounge", "Meeting", "Lobby", "Stairs", "Gym",
]  # fmt: skip

#: Allowed range of the simulation speed multiplier (CLI, TOML, HTTP API, web UI).
TIME_SCALE_MIN = 0.1
TIME_SCALE_MAX = 50.0


def check_time_scale(value: float) -> float:
    """Validate a time scale: how many simulated seconds pass per real second."""
    scale = float(value)
    if not TIME_SCALE_MIN <= scale <= TIME_SCALE_MAX:
        raise ValueError(f"time scale must be {TIME_SCALE_MIN:g}..{TIME_SCALE_MAX:g}, got {value}")
    return scale


KIND_CYCLE = [BlindKind.VENETIAN, BlindKind.ROLLER, BlindKind.SCREEN, BlindKind.VENETIAN]


@dataclass
class BusConfig:
    settings: BusSettings
    motors: list[MotorConfig] = field(default_factory=list)


@dataclass
class SimConfig:
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    tcp_host: str = "0.0.0.0"
    tick_hz: float = 20.0
    time_scale: float = 1.0  # simulated seconds per real second (0.1..50)
    buses: list[BusConfig] = field(default_factory=list)
    tcp_base_port: int = 4000
    pty_base: str | None = None


def _pick(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {', '.join(sorted(unknown))}")
    return data


def load(path: str | Path) -> SimConfig:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    sim = raw.get("simulator", {})
    cfg = SimConfig(**_pick(SimConfig, {k: v for k, v in sim.items() if k != "buses"}))
    cfg.time_scale = check_time_scale(cfg.time_scale)
    for i, b in enumerate(raw.get("bus", [])):
        b = dict(b)
        motors = b.pop("motor", [])
        settings = BusSettings(**_pick(BusSettings, b))
        if settings.tcp_port is None and cfg.tcp_base_port:
            settings.tcp_port = cfg.tcp_base_port + i
        bus = BusConfig(settings)
        for m in motors:
            m = dict(m)
            if isinstance(m.get("key_id"), str):
                m["key_id"] = int(m["key_id"], 16)
            bus.motors.append(MotorConfig(**_pick(MotorConfig, m)))
        cfg.buses.append(bus)
    return cfg


def generate(
    buses: int = 1,
    motors_per_bus: int = 16,
    kind: str = "mixed",
    factory_new: bool = False,
    tcp_base_port: int = 4000,
    pty_base: str | None = None,
    serial_port: str | None = None,
    manufacturer: int = 1,
) -> list[BusConfig]:
    """Build a building with ``buses`` SMI lines of ``motors_per_bus`` drives each."""
    out: list[BusConfig] = []
    for b in range(buses):
        settings = BusSettings(
            name=f"Line {b + 1}" + (f" - Floor {b}" if buses > 1 else ""),
            tcp_port=tcp_base_port + b if tcp_base_port else None,
            pty_link=f"{pty_base}{b}" if pty_base else None,
            serial_port=serial_port if b == 0 else None,
        )
        bus = BusConfig(settings)
        for i in range(motors_per_bus):
            k = KIND_CYCLE[i % len(KIND_CYCLE)] if kind == "mixed" else BlindKind(kind)
            bus.motors.append(
                MotorConfig(
                    name=f"{ROOMS[i % len(ROOMS)]} {b + 1}.{i + 1:02d}",
                    address=0 if factory_new else i % 16,
                    manufacturer=manufacturer,
                    kind=k,
                    key_id=None,
                )
            )
            if not factory_new:
                # Spread start positions a little so the facade looks lived-in.
                bus.motors[-1].start_position = [0, 0, 0x4000, 0xFFFF][(i + b) % 4]
        out.append(bus)
    return out
