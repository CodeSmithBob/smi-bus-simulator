import asyncio
import os
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smisim.config import SimConfig, generate, load
from smisim.simulator import Simulator
from smisim.web.server import make_app

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads():
    cfg = load(ROOT / "examples" / "two-floors.toml")
    assert len(cfg.buses) == 2
    assert cfg.buses[0].settings.tcp_port == 4000
    assert cfg.buses[1].settings.tcp_port == 4001
    assert cfg.buses[0].motors[0].key_id == 0x1A2B3C01
    assert all(m.address == 0 for m in cfg.buses[1].motors)


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("[[bus]]\nnmae = 'typo'\n")
    with pytest.raises(ValueError, match="nmae"):
        load(p)


async def _client():
    cfg = SimConfig(tcp_base_port=0)
    cfg.buses = generate(buses=1, motors_per_bus=4, kind="venetian", tcp_base_port=0)
    sim = Simulator(cfg)
    await sim.start()
    client = TestClient(TestServer(make_app(sim)))
    await client.start_server()
    return sim, client


async def test_api_roundtrip():
    sim, client = await _client()
    try:
        r = await client.get("/api/meta")
        meta = await r.json()
        assert "offline" in meta["faults"] and "16" in meta["presets"]

        r = await client.post(
            "/api/buses/0/send", json={"mode": "broadcast", "type": "command", "code": "down"}
        )
        out = await r.json()
        assert out["sent"] == "40 02 BE" and out["response"]["kind"] == "ack"

        r = await client.post(
            "/api/encode", json={"mode": "slave", "address": 12, "type": "command", "code": "up"}
        )
        assert (await r.json())["hex"] == "5C 01 A3"

        uid = sim.buses[0].motors[0].uid
        r = await client.post(f"/api/motors/{uid}/fault", json={"name": "offline", "active": True})
        assert r.status == 200
        r = await client.patch(f"/api/motors/{uid}", json={"address": 99})
        assert r.status == 400

        r = await client.post("/api/presets/32")
        assert r.status == 200
        state = await (await client.get("/api/state")).json()
        assert [len(b["motors"]) for b in state["buses"]] == [16, 16]

        r = await client.post("/api/testlab/start", json={"include_ui": True})
        assert (await r.json())["active"]
        r = await client.get("/api/testlab/report")
        assert "disclaimer" in await r.json()

        async with client.ws_connect("/ws") as ws:
            msg = await asyncio.wait_for(ws.receive_json(), 2)
            assert msg["type"] == "state" and msg["traffic"]
    finally:
        await client.close()
        await sim.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX only")
async def test_pty_transport(tmp_path):
    link = str(tmp_path / "smi0")
    cfg = SimConfig()
    cfg.buses = generate(buses=1, motors_per_bus=2, kind="roller", tcp_base_port=0, pty_base=None)
    cfg.buses[0].settings.pty_link = link
    cfg.buses[0].settings.realtime = False
    sim = Simulator(cfg)
    await sim.start()
    try:
        fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
        try:
            os.write(fd, bytes.fromhex("7105 8A"))  # read position of slave 1
            await asyncio.sleep(0.3)
            data = os.read(fd, 16)
            assert data[:2] == b"\xef\x45" and (sum(data) & 0xFF) == 0
        finally:
            os.close(fd)
    finally:
        await sim.stop()
    assert not os.path.exists(link)
