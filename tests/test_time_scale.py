"""time_scale: simulated seconds per real second (CLI, TOML, HTTP API, web UI)."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smisim.cli import build_config, make_parser
from smisim.config import SimConfig, generate, load
from smisim.simulator import Simulator
from smisim.web.server import make_app


def cfg_from(argv: list[str]) -> SimConfig:
    return build_config(make_parser().parse_args(["run", *argv]))


def test_cli_default_and_flag():
    assert cfg_from([]).time_scale == 1.0
    assert cfg_from(["--time-scale", "5"]).time_scale == 5.0


def test_config_file_value_and_cli_override(tmp_path):
    p = tmp_path / "b.toml"
    p.write_text("[simulator]\ntime_scale = 3\n[[bus]]\nname = 'x'\n")
    assert load(p).time_scale == 3.0
    assert cfg_from(["--config", str(p)]).time_scale == 3.0
    assert cfg_from(["--config", str(p), "--time-scale", "10"]).time_scale == 10.0


@pytest.mark.parametrize("bad", ["0", "-1", "51"])
def test_out_of_range_is_rejected_everywhere(tmp_path, bad):
    with pytest.raises(SystemExit):
        cfg_from(["--time-scale", bad])
    p = tmp_path / "b.toml"
    p.write_text(f"[simulator]\ntime_scale = {bad}\n")
    with pytest.raises(ValueError, match="time scale"):
        load(p)


async def test_http_setting_and_motion_speed():
    cfg = SimConfig(tcp_base_port=0, tick_hz=50)
    cfg.buses = generate(buses=1, motors_per_bus=1, kind="roller", tcp_base_port=0)
    cfg.buses[0].motors[0].travel_time_s = 10
    sim = Simulator(cfg)
    await sim.start()
    client = TestClient(TestServer(make_app(sim)))
    await client.start_server()
    try:
        r = await client.post("/api/settings", json={"time_scale": 0})
        assert r.status == 400
        r = await client.post("/api/settings", json={"time_scale": 20})
        assert (await r.json())["time_scale"] == 20
        motor = sim.buses[0].motors[0]
        motor.set_position(0)
        motor.move_to(0xFFFF)
        await asyncio.sleep(0.25)  # 0.25 real s = 5 simulated s = about half the travel
        assert 0.2 * 0xFFFF < motor.position < 0.8 * 0xFFFF
    finally:
        await client.close()
        await sim.stop()
