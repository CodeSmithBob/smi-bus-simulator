"""Bus monitor, CSV export and `smisim decode`: raw position values plus physical state."""

import csv
import io
import json

from aiohttp.test_utils import TestClient, TestServer

from smisim import Bus, BusSettings, MotorConfig
from smisim.bus import TRAFFIC_CSV_COLUMNS, traffic_csv
from smisim.cli import main
from smisim.config import SimConfig, generate
from smisim.protocol import Addressing, Command, MasterTelegram, QueryCode
from smisim.protocol.frames import data_response, decode_any, decode_response
from smisim.simulator import Simulator
from smisim.web.server import make_app


def read(bus: Bus, address: int, code: QueryCode = QueryCode.POSITION):
    bus.handle_frame(MasterTelegram.query(Addressing.to_slave(address), code).encode())
    return bus.traffic[-1]


def test_position_answers_are_shown_raw():
    tel = MasterTelegram.query(Addressing.to_slave(1), QueryCode.POSITION)
    assert decode_response(tel, data_response(QueryCode.POSITION, 41321)).describe(tel) == (
        "pos 41321 / 65535"
    )
    goto = MasterTelegram.command(Addressing.to_slave(1), Command.GOTO, position=32768)
    assert "pos 32768 / 65535" in goto.describe() and "%" not in goto.describe()


def test_venetian_with_slat_turn_in_position_shows_raw_and_physical():
    bus = Bus(settings=BusSettings())
    drive = bus.add_motor(MotorConfig(address=2, kind="venetian", tilt_in_position=True))
    drive.set_position(0.6 * 0xFFFF, tilt=1.0)  # rail 60 %, slats closed
    entry = read(bus, 2)
    raw = round(drive.reported_position)
    assert raw != round(0.6 * 0xFFFF)  # the raw value also counts the slat turn
    assert entry.text == f"pos {raw} / 65535  · rail 60.0 %, slats 100 %"
    assert (entry.raw_position, entry.rail_percent, entry.slat_percent) == (raw, 60.0, 100.0)


def test_roller_answer_has_no_slats_and_other_answers_no_physical_state():
    bus = Bus(settings=BusSettings())
    drive = bus.add_motor(MotorConfig(address=1, kind="roller"))
    drive.set_position(0x4000)
    entry = read(bus, 1)
    assert entry.text == "pos 16384 / 65535  · rail 25.0 %"
    assert entry.slat_percent is None
    entry = read(bus, 1, QueryCode.POS1)
    assert entry.text == "pos1 49152 / 65535"
    assert entry.raw_position is None and entry.rail_percent is None


def test_answer_from_several_drives_is_raw_only():
    bus = Bus(settings=BusSettings())
    for _ in range(2):  # same address, same position: the wired-AND answer stays valid
        bus.add_motor(MotorConfig(address=0, kind="roller")).set_position(0x8000)
    entry = read(bus, 0)
    assert entry.raw_position == 0x8000 and entry.rail_percent is None
    assert "rail" not in entry.text and "2 drives answered" in entry.text


def test_csv_has_separate_columns():
    bus = Bus(settings=BusSettings())
    bus.add_motor(MotorConfig(address=2, kind="venetian")).set_position(0x8000, tilt=0.5)
    read(bus, 2)
    rows = list(csv.DictReader(io.StringIO(traffic_csv(bus.traffic))))
    assert list(rows[0]) == TRAFFIC_CSV_COLUMNS
    answer = rows[-1]
    assert answer["direction"] == "S"
    assert (answer["raw_position"], answer["rail_percent"], answer["slat_percent"]) == (
        "32768",
        "50.0",
        "50.0",
    )
    assert rows[0]["raw_position"] == ""  # the master telegram has none


async def test_csv_endpoint_respects_since():
    cfg = SimConfig(tcp_base_port=0)
    cfg.buses = generate(buses=1, motors_per_bus=1, kind="roller", tcp_base_port=0)
    sim = Simulator(cfg)
    await sim.start()
    client = TestClient(TestServer(make_app(sim)))
    await client.start_server()
    try:
        bus = sim.buses[0]
        read(bus, 0)
        last = bus.traffic[-1].seq
        read(bus, 0)
        r = await client.get(f"/api/traffic.csv?since={last}")
        assert r.headers["Content-Type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(await r.text())))
        assert [row["direction"] for row in rows] == ["M", "S"]
        assert rows[1]["rail_percent"] == "0.0"
    finally:
        await client.close()
        await sim.stop()


def test_decode_shows_raw_value_only(capsys):
    answer = data_response(QueryCode.POSITION, 41321)
    assert decode_any(answer)["answer"]["text"] == "answer to READ POSITION: pos 41321 / 65535"
    assert main(["decode", answer.hex(" ")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["answer"]["value"] == 41321 and "rail" not in out["answer"]["text"]
    assert decode_any(answer[:-1] + b"\x00")["ok"] is False  # bad checksum
