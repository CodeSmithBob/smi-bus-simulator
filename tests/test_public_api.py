"""The stable in-process API (docs/api.md), used exactly as an embedding program would.

Only names exported from ``smisim`` and ``smisim.protocol`` appear here. The tests are
plain functions: no event loop, no web server.
"""

import subprocess
import sys

import pytest

from smisim import Bus, BusSettings, FrameResult, MotorConfig
from smisim.protocol import (
    ACK,
    Addressing,
    Command,
    MasterTelegram,
    QueryCode,
    decode_response,
)


def make_bus() -> Bus:
    bus = Bus(settings=BusSettings(name="Lab line"))
    bus.add_motor(MotorConfig(name="Office", address=3, kind="roller", travel_time_s=10))
    bus.add_motor(MotorConfig(name="Hall", address=4, kind="venetian", travel_time_s=40))
    return bus


def read_position(bus: Bus, address: int) -> int:
    query = MasterTelegram.query(Addressing.to_slave(address), QueryCode.POSITION)
    result = bus.handle_frame(query.encode())
    response = decode_response(query, result.reply)
    assert response.kind == "data"
    return response.value


def test_documented_example_runs():
    bus = make_bus()
    reply, delay = bus.handle_frame(bytes.fromhex("53 02 AB"))  # DOWN to slave 3
    assert reply == bytes([ACK]) and delay >= 0
    for _ in range(50):
        bus.update(0.1)  # 5 simulated seconds
    assert 0.45 * 0xFFFF < read_position(bus, 3) < 0.55 * 0xFFFF
    for _ in range(60):
        bus.update(0.1)
    assert read_position(bus, 3) == 0xFFFF


def test_handle_frame_returns_frame_result():
    bus = make_bus()
    tel = MasterTelegram.command(Addressing.broadcast(), Command.STOP)
    result = bus.handle_frame(tel.encode())
    assert isinstance(result, FrameResult)
    assert result.reply == bytes([ACK])  # both drives ACK at once: wired-AND stays FF
    assert result.delay_s == pytest.approx(BusSettings().response_delay_ms / 1000)


def test_invalid_or_unaddressed_telegrams_are_silent():
    bus = make_bus()
    assert bus.handle_frame(bytes.fromhex("5C 01 A4")).reply == b""  # bad checksum
    assert bus.handle_frame(bytes.fromhex("5C 01 A3")).reply == b""  # nobody on address 12
    assert bus.handle_frame(bytearray.fromhex("53 00 AD")).reply == bytes([ACK])  # STOP 3


def test_drive_limit_and_time_validation():
    bus = Bus(settings=BusSettings(max_drives=1))
    bus.add_motor(MotorConfig())
    with pytest.raises(ValueError):
        bus.add_motor(MotorConfig(address=1))
    with pytest.raises(ValueError):
        bus.update(-1)


def test_import_does_not_pull_in_the_web_stack():
    code = (
        "import sys, smisim; "
        "assert 'aiohttp' not in sys.modules, 'aiohttp imported'; "
        "assert 'smisim.web' not in sys.modules; "
        "assert 'smisim.simulator' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_example_in_docs_runs():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs" / "api.md").read_text()
    after = text.split("<!-- example:python-api", 1)[1]
    code = after.split("```python", 1)[1].split("```", 1)[0]
    exec(compile(code, "docs/api.md", "exec"), {})


def run(bus: Bus, seconds: float, dt: float = 0.05) -> None:
    for _ in range(round(seconds / dt)):
        bus.update(dt)


def test_target_position_follows_moves():
    bus = make_bus()
    office = bus.motors[0]
    assert office.target_position == office.position == 0  # idle: its own position
    assert office.move_to(0x8000) is True
    assert office.target_position == 0x8000
    run(bus, 2)
    assert 0 < office.position < 0x8000 and office.target_position == 0x8000
    run(bus, 5)
    assert office.position == office.target_position == 0x8000


def test_target_position_of_angle_steps_and_venetian_runs():
    bus = make_bus()
    office, hall = bus.motors
    # Roller: 90 deg of a 5400 deg travel is 1/60 of the full range.
    bus.handle_frame(
        MasterTelegram.command(Addressing.to_slave(3), Command.DOWN, angle_deg=90).encode()
    )
    assert office.target_position == pytest.approx(0xFFFF / 60)
    # Venetian at the top: a small step only turns the slats, the rail stays put.
    bus.handle_frame(
        MasterTelegram.command(Addressing.to_slave(4), Command.DOWN, angle_deg=90).encode()
    )
    assert hall.target_position == 0
    hall.move_to(0xFFFF)
    assert hall.target_position == 0xFFFF


def test_set_position_stops_and_places_the_drive():
    bus = make_bus()
    office, hall = bus.motors
    office.move_to(0xFFFF)
    run(bus, 1)
    office.set_position(0x4000)
    assert office.direction == 0 and office.position == office.target_position == 0x4000
    run(bus, 1)
    assert office.position == 0x4000  # stays: the move was cancelled
    assert read_position(bus, 3) == 0x4000
    hall.set_position(0xFFFF, tilt=0.5)
    assert hall.position == 0xFFFF and hall.slat_percent == 50


def test_move_to_refuses_when_the_drive_cannot_move():
    bus = make_bus()
    office = bus.motors[0]
    office.set_fault("blocked", True)
    assert office.move_to(0xFFFF) is False
    office.set_fault("blocked", False)
    office.set_fault("offline", True)
    assert office.move_to(0xFFFF) is False
    assert office.target_position == 0
    for bad in (-1, 0x10000):
        with pytest.raises(ValueError):
            office.move_to(bad)
        with pytest.raises(ValueError):
            office.set_position(bad)
    with pytest.raises(ValueError):
        office.set_position(0, tilt=2)
    with pytest.raises(AttributeError):
        office.target_position = 5  # read-only
