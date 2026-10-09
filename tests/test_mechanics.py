"""Optional drive mechanics: end offsets, gear slack, per-drive slat turn, profiles.

All off by default (tests elsewhere cover that the ideal drive is unchanged).
"""

import pytest

from smisim import DRIVE_PROFILES, Bus, BusSettings, MotorConfig
from smisim.config import generate, load
from smisim.motor import FULL, Motor, MotorUpdate
from smisim.protocol import Addressing, Command, MasterTelegram, QueryCode
from smisim.protocol.frames import decode_response
from smisim.simulator import PRESETS


def run(m: Motor, seconds: float, dt: float = 0.01) -> None:
    for _ in range(round(seconds / dt)):
        m.update(dt)


def cmd(m: Motor, command: Command, **kw) -> None:
    m.handle(MasterTelegram.command(Addressing.to_slave(m.address), command, **kw))


def angle_query(m: Motor) -> int:
    tel = MasterTelegram.query(Addressing.to_slave(m.address), QueryCode.ANGLE)
    return decode_response(tel, m.handle(tel).data).value * 2


def test_end_offsets_keep_the_rail_still_at_both_ends():
    m = Motor(MotorConfig(kind="roller", top_offset=2000, bottom_offset=3000), 1)
    m.set_position(0)
    cmd(m, Command.DOWN)
    run(m, 0.5)  # raw 1365 of 2000: the shaft turns, the rail does not
    assert m.position == 0 and 1300 < m.drive_position < 1400
    assert m.reported_position == m.drive_position
    run(m, 0.5)
    assert m.position > 0
    run(m, 22.0)  # 23 s in, raw ~62800: the rail is down, the drive still has ~2700 to go
    assert m.position == FULL and m.drive_position < FULL - 2000
    run(m, 2)
    assert m.drive_position == FULL and m.direction == 0


def test_set_position_and_move_to_use_the_physical_scale():
    m = Motor(MotorConfig(kind="roller", top_offset=2000, bottom_offset=3000), 1)
    m.set_position(0x8000)
    assert m.position == pytest.approx(0x8000)
    assert m.drive_position == pytest.approx(2000 + 0x8000 / FULL * (FULL - 5000))
    assert m.move_to(0x4000)
    assert m.target_position == pytest.approx(0x4000)
    run(m, 15)
    assert m.position == pytest.approx(0x4000)


def test_slack_is_a_dead_zone_after_every_reversal():
    m = Motor(MotorConfig(kind="roller", slack=1000), 1)
    m.set_position(0x8000)  # gear in upward contact
    cmd(m, Command.DOWN)
    run(m, 0.3)  # 819 raw of shaft: still inside the 1000 dead zone
    assert m.position == 0x8000 and m.drive_position > 0x8000 + 800
    run(m, 0.7)
    assert m.position > 0x8000
    moved_to = m.position
    cmd(m, Command.UP)
    run(m, 0.5)  # 0.3 s reversal pause, then 0.2 s of slack: rail still
    assert m.position == pytest.approx(moved_to) and m.drive_position < moved_to + 1000
    run(m, 1)
    assert m.position < moved_to


def test_slack_makes_a_full_run_stop_short_unless_the_offset_covers_it():
    short = Motor(MotorConfig(kind="roller", slack=1000), 1)
    covered = Motor(MotorConfig(kind="roller", slack=1000, bottom_offset=1000), 2)
    for m in (short, covered):
        m.set_position(0)
        assert m.target_position == 0
        cmd(m, Command.DOWN)
        assert m.target_position == pytest.approx(FULL - 1000 if m is short else FULL)
        run(m, 30)
        assert m.position == pytest.approx(m.target_position)
    assert short.drive_position == covered.drive_position == FULL


def test_slack_can_swallow_an_angle_step_on_a_venetian_drive():
    m = Motor(MotorConfig(kind="venetian", slack=1000), 1)  # 1000 raw = 137 deg of shaft
    m.set_position(0x8000, tilt=0)
    cmd(m, Command.DOWN)
    run(m, 60)
    assert m.tilt == 1.0 and angle_query(m) == 270
    cmd(m, Command.UP, angle_deg=90)
    run(m, 2)
    assert m.tilt == 1.0  # the whole step went into the slack: nothing moved
    assert angle_query(m) == 180  # but the drive counted it
    cmd(m, Command.UP, angle_deg=90)
    run(m, 2)
    assert 0.8 < m.tilt < 0.9  # 43 deg beyond the slack turned the slats


def test_slat_turn_differs_per_drive(tmp_path):
    p = tmp_path / "b.toml"
    p.write_text(
        "[[bus]]\nname = 'x'\n"
        "[[bus.motor]]\naddress = 1\nkind = 'venetian'\n"
        "tilt_degrees = 180\nstart_position = 65535\n"
        "[[bus.motor]]\naddress = 2\nkind = 'venetian'\n"
        "tilt_degrees = 300\nstart_position = 65535\n"
    )
    bus = Bus(settings=BusSettings())
    for m in load(p).buses[0].motors:
        bus.add_motor(m)
    bus.handle_frame(
        MasterTelegram.command(Addressing.broadcast(), Command.UP, angle_deg=90).encode()
    )
    for _ in range(300):
        bus.update(0.01)
    assert [round(m.slat_percent) for m in bus.motors] == [50, 70]


def test_profiles():
    assert set(DRIVE_PROFILES) == {"venetian-tight", "venetian-worn", "roller-offset"}
    worn = MotorConfig.from_profile("venetian-worn", name="Hall", slack=900)
    assert worn.profile == "venetian-worn" and worn.kind == "venetian"
    assert worn.slack == 900 and worn.top_offset == DRIVE_PROFILES["venetian-worn"]["top_offset"]
    with pytest.raises(ValueError, match="unknown drive profile"):
        MotorConfig.from_profile("nope")
    with pytest.raises(ValueError, match="unknown drive profile"):
        MotorConfig(profile="nope")
    tight, worn = (MotorConfig.from_profile(n) for n in ("venetian-tight", "venetian-worn"))
    assert tight.slack < worn.slack and tight.tilt_degrees != worn.tilt_degrees


def test_profiles_in_toml_generator_and_preset(tmp_path):
    p = tmp_path / "b.toml"
    p.write_text(
        "[[bus]]\nname = 'x'\n"
        "[[bus.motor]]\nname = 'A'\nprofile = 'venetian-worn'\nslack = 800\n"
        "[[bus.motor]]\nname = 'B'\nprofile = 'roller-offset'\n"
    )
    a, b = load(p).buses[0].motors
    assert (a.kind, a.slack, a.profile) == ("venetian", 800, "venetian-worn")
    assert (b.kind, b.top_offset) == ("roller", DRIVE_PROFILES["roller-offset"]["top_offset"])
    motors = generate(motors_per_bus=8, kind="mixed-mechanics", tcp_base_port=0)[0].motors
    assert {m.profile for m in motors} == {"venetian-tight", "venetian-worn", "roller-offset", ""}
    assert PRESETS["mechanics-16"]["kind"] == "mixed-mechanics"


def test_validation_and_editing_keep_the_blind_where_it_is():
    for bad in ({"slack": -1}, {"slack": FULL}, {"top_offset": 20000, "bottom_offset": 20000}):
        with pytest.raises(ValueError):
            MotorConfig(**bad)
    m = Motor(MotorConfig(kind="venetian"), 1)
    m.set_position(0x6000, tilt=0.5)
    MotorUpdate(slack=500, top_offset=1000, bottom_offset=1000).apply(m)
    assert (m.cfg.slack, m.cfg.top_offset) == (500, 1000)
    assert m.position == pytest.approx(0x6000) and m.tilt == pytest.approx(0.5)
    with pytest.raises(ValueError):
        MotorUpdate(slack=FULL).apply(m)
