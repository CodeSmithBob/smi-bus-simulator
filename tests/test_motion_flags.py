"""moving_rail / moving_slats: what a person watching the blind would see."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from smisim import MotorConfig
from smisim.config import SimConfig, generate
from smisim.motor import Motor
from smisim.protocol import Addressing, Command, MasterTelegram
from smisim.simulator import Simulator
from smisim.web.server import make_app


def run(m: Motor, seconds: float, dt: float = 0.05) -> None:
    for _ in range(round(seconds / dt)):
        m.update(dt)


def cmd(m: Motor, command: Command) -> None:
    m.handle(MasterTelegram.command(Addressing.to_slave(m.address), command))


def flags(m: Motor) -> tuple[bool, bool]:
    return m.moving_rail, m.moving_slats


def test_venetian_turns_slats_first_then_moves_the_rail():
    m = Motor(MotorConfig(kind="venetian"), 1)  # top, slats open
    assert flags(m) == (False, False)
    cmd(m, Command.DOWN)
    run(m, 0.1)
    assert m.direction == 1 and flags(m) == (False, True)
    run(m, 2)  # the 270 deg slat turn takes 1.44 s
    assert flags(m) == (True, False)
    run(m, 60)
    assert m.direction == 0 and flags(m) == (False, False)


def test_nothing_visible_moves_during_the_reversal_pause():
    m = Motor(MotorConfig(kind="roller", reversal_pause_s=0.5), 1)
    cmd(m, Command.DOWN)
    run(m, 1)
    assert flags(m) == (True, False)
    cmd(m, Command.UP)
    run(m, 0.2)
    assert m.to_state()["reversing"] and flags(m) == (False, False)
    run(m, 0.5)
    assert m.direction == -1 and flags(m) == (True, False)


def test_motor_runs_but_rail_stays_inside_slack_and_end_offset():
    slack = Motor(MotorConfig(kind="roller", slack=1000), 1)
    slack.set_position(0x8000)
    offset = Motor(MotorConfig(kind="roller", top_offset=2000), 2)
    for m in (slack, offset):
        cmd(m, Command.DOWN)
        run(m, 0.3)  # inside the 1000 slack / 2000 offset
        assert m.direction == 1 and m.drive_position > m.position
        assert flags(m) == (False, False)
    run(slack, 0.2)
    run(offset, 0.5)
    assert slack.moving_rail and offset.moving_rail


def test_set_position_and_stop_clear_the_flags():
    m = Motor(MotorConfig(kind="venetian"), 1)
    cmd(m, Command.DOWN)
    run(m, 2)
    assert m.moving_rail
    m.stop()
    assert flags(m) == (False, False)
    cmd(m, Command.DOWN)
    run(m, 0.1)
    m.set_position(0x4000)
    assert flags(m) == (False, False)


async def test_flags_in_api_state():
    cfg = SimConfig(tcp_base_port=0, tick_hz=50)
    cfg.buses = generate(buses=1, motors_per_bus=1, kind="roller", tcp_base_port=0)
    sim = Simulator(cfg)
    await sim.start()
    client = TestClient(TestServer(make_app(sim)))
    await client.start_server()
    try:

        async def drive() -> dict:
            state = await (await client.get("/api/state")).json()
            return state["buses"][0]["motors"][0]

        motor = sim.buses[0].motors[0]
        motor.set_position(0)
        first = await drive()
        assert (first["moving_rail"], first["moving_slats"]) == (False, False)
        motor.move_to(0xFFFF)
        await asyncio.sleep(0.2)
        moving = await drive()
        assert moving["moving_rail"] is True and moving["moving_slats"] is False
        motor.stop()
        await asyncio.sleep(0.1)
        assert (await drive())["moving_rail"] is False
    finally:
        await client.close()
        await sim.stop()
