"""Full stack: simulator + TCP transport + the bundled master library."""

import asyncio

import pytest

from smisim.client import SmiMaster, TcpLink
from smisim.config import SimConfig, generate
from smisim.protocol import Addressing, Command, QueryCode
from smisim.simulator import Simulator


async def _sim(**kw) -> Simulator:
    cfg = SimConfig(tcp_host="127.0.0.1", time_scale=20.0)
    cfg.buses = generate(tcp_base_port=0, **kw)
    for b in cfg.buses:
        b.settings.tcp_port = 0  # ephemeral
        b.settings.realtime = False
    sim = Simulator(cfg)
    await sim.start()
    return sim


def _port(sim: Simulator, line: int = 0) -> int:
    return sim.transports[line][0].port


async def test_command_and_readback_over_tcp():
    sim = await _sim(buses=1, motors_per_bus=4, kind="roller")
    try:
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim))) as master:
            r = await master.goto(2, 0x8000)
            assert r.kind == "ack"
            await asyncio.sleep(1.5)  # 24 s travel at 20x time scale
            pos = await master.read_position(2)
            assert pos is not None and abs(pos - 0x8000) < 50
            r = await master.read(2, QueryCode.IDENT)
            assert r.value == (1 << 8) | 1
    finally:
        await sim.stop()


async def test_group_command_moves_only_members():
    sim = await _sim(buses=1, motors_per_bus=4, kind="roller")
    try:
        bus = sim.buses[0]
        for m in bus.motors:
            m.set_position(0)
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim))) as master:
            r = await master.command(Addressing.to_slaves([1, 3]), Command.DOWN)
            assert r.kind == "ack"
            await asyncio.sleep(0.3)
        moving = sorted(m.address for m in bus.motors if m.direction == 1)
        assert moving == [1, 3]
    finally:
        await sim.stop()


async def test_duplicate_addresses_spoil_reads():
    sim = await _sim(buses=1, motors_per_bus=3, factory_new=True, kind="roller")
    try:
        bus = sim.buses[0]
        bus.motors[0].set_position(0x1234)
        bus.motors[1].set_position(0xFF00)
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim)), retries=0) as master:
            r = await master.read(0, QueryCode.POSITION)
            assert r.kind == "garbled"
    finally:
        await sim.stop()


async def test_discovery_addresses_factory_new_drives():
    sim = await _sim(buses=1, motors_per_bus=6, factory_new=True, kind="venetian")
    try:
        bus = sim.buses[0]
        assert bus.duplicate_addresses() == [0]
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim))) as master:
            found = await master.discover_and_address(manufacturer=1, first_address=1)
        assert len(found) == 6
        assert sorted(m.address for m in bus.motors) == [1, 2, 3, 4, 5, 6]
        assert bus.duplicate_addresses() == []
        keys = {m.key_id: m.address for m in bus.motors}
        for key_id, address in found:
            assert keys[key_id] == address
    finally:
        await sim.stop()


async def test_lost_answer_is_retried_by_master():
    sim = await _sim(buses=1, motors_per_bus=2, kind="roller")
    try:
        sim.buses[0].motors[1].set_fault("drop_next", True)
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim)), timeout=0.1, retries=1) as master:
            r = await master.up(1)
        assert r.kind == "ack"
        assert sim.buses[0].stats["no_answer"] == 1
    finally:
        await sim.stop()


async def test_virtual_master_api():
    sim = await _sim(buses=1, motors_per_bus=2, kind="roller")
    try:
        out = await sim.master_send(
            0, {"mode": "slave", "address": 1, "type": "query", "code": "position"}
        )
        assert out["response"]["kind"] == "data"
        out = await sim.master_send(0, {"hex": "51 02 AD"})
        assert out["response"]["kind"] == "ack"
        with pytest.raises(ValueError):
            await sim.master_send(
                0, {"mode": "slave", "address": 99, "type": "command", "code": "up"}
            )
    finally:
        await sim.stop()


async def test_testlab_retry_check_passes_with_retrying_master():
    sim = await _sim(buses=1, motors_per_bus=1, kind="roller")
    try:
        sim.testlab.start(sim.buses, include_ui=True)
        sim.testlab.arm("retry_timeout")
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim)), timeout=0.1, retries=2) as master:
            await master.read_position(0)
        sim.testlab.evaluate()
        assert sim.testlab.checks["retry_timeout"].status == "pass"
        assert sim.testlab.checks["poll_all"].status == "pass"
    finally:
        await sim.stop()


async def test_discovery_skips_addresses_already_in_use():
    sim = await _sim(buses=1, motors_per_bus=4, kind="roller")
    try:
        bus = sim.buses[0]
        for m, addr in zip(bus.motors, [0, 0, 1, 2], strict=True):
            m.cfg.address = addr
        async with SmiMaster(TcpLink("127.0.0.1", _port(sim)), timeout=0.05) as master:
            found = await master.discover_and_address(manufacturer=0)
        assert sorted(a for _, a in found) == [3, 4]
        assert bus.duplicate_addresses() == []
    finally:
        await sim.stop()
