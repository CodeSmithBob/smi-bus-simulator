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
