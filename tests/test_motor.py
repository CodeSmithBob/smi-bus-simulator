from smisim.motor import FULL, BlindKind, Motor, MotorConfig
from smisim.protocol import ACK, NACK, Addressing, Command, DiagCode, MasterTelegram, QueryCode


def run(motor: Motor, seconds: float, dt: float = 0.05) -> None:
    for _ in range(int(seconds / dt)):
        motor.update(dt)


def cmd(motor: Motor, command, **kw) -> bytes:
    tel = MasterTelegram.command(Addressing.to_slave(motor.address), command, **kw)
    return motor.handle(tel).data


def test_roller_runs_down_in_travel_time():
    m = Motor(MotorConfig(kind=BlindKind.ROLLER, travel_time_s=10), 1)
    assert cmd(m, Command.DOWN) == bytes([ACK])
    run(m, 5)
    assert 0.45 * FULL < m.position < 0.55 * FULL
    assert m.direction == 1
    run(m, 6)
    assert m.position == FULL and m.direction == 0


def test_up_is_slower_than_down():
    m = Motor(MotorConfig(kind=BlindKind.ROLLER, travel_time_s=10, start_position=0xFFFF), 1)
    cmd(m, Command.UP)
    run(m, 10)
    assert m.position > 0  # up speed factor 0.9 -> 11.1 s
    run(m, 1.5)
    assert m.position == 0


def test_goto_and_position_query():
    m = Motor(MotorConfig(kind=BlindKind.ROLLER, travel_time_s=5), 1)
    cmd(m, Command.GOTO, position=0x8000)
    run(m, 6)
    assert abs(m.position - 0x8000) < 1
    tel = MasterTelegram.query(Addressing.to_slave(0), QueryCode.POSITION)
    data = m.handle(tel).data
    assert data[0] == 0xEF and int.from_bytes(data[2:4], "big") == 0x8000


def test_venetian_tilts_before_travelling():
    m = Motor(MotorConfig(kind=BlindKind.VENETIAN, travel_time_s=48, shaft_degrees=9000), 1)
    cmd(m, Command.DOWN)
    run(m, 1.0)
    assert m.position == 0  # 270 deg tilt at 187.5 deg/s takes 1.44 s
    assert 0.6 < m.tilt < 0.75
    run(m, 1.0)
    assert m.tilt == 1.0 and m.position > 0


def test_step_commands_adjust_slat_angle_at_bottom():
    m = Motor(MotorConfig(kind=BlindKind.VENETIAN, start_position=0xFFFF), 1)
    assert m.tilt == 1.0
    cmd(m, Command.UP, angle_deg=90)
    run(m, 2)
    assert abs(m.angle_deg - 180) < 1  # 270 - 90
    assert m.position == FULL


def test_store_and_recall_pos1():
    m = Motor(MotorConfig(kind=BlindKind.ROLLER, travel_time_s=4), 1)
    cmd(m, Command.POS1, position=0x4000)
    assert m.cfg.pos1 == 0x4000
    cmd(m, Command.POS1)
    run(m, 3)
    assert abs(m.position - 0x4000) < 1


def test_thermal_protection_trips_and_recovers():
    m = Motor(MotorConfig(travel_time_s=600, thermal_limit_s=3, cooldown_s=6), 1)
    cmd(m, Command.DOWN)
    run(m, 3.2)
    assert "thermal" in m.errors and m.direction == 0
    assert cmd(m, Command.DOWN) == bytes([NACK])
    run(m, 4)
    assert "thermal" not in m.errors
    assert cmd(m, Command.DOWN) == bytes([ACK])


def test_obstacle_stops_downward_run():
    m = Motor(MotorConfig(travel_time_s=10), 1)
    m.set_fault("obstacle", True)
    cmd(m, Command.DOWN)
    run(m, 12)
    assert "obstacle" in m.errors
    assert m.position < FULL


def test_diag_flags():
    m = Motor(MotorConfig(), 1)
    tel = MasterTelegram.diag(Addressing.to_slave(0), DiagCode.STATUS)
    assert m.handle(tel).data == bytes([ACK, 0xFF, 0xFF, 0xE0, 0xFF])
    cmd(m, Command.DOWN)
    run(m, 0.2)
    assert m.handle(tel).data == bytes([ACK, 0xFF, 0xE0, 0xFF, 0xFF])


def test_limits_lost_refuses_positioning_until_calibrated():
    m = Motor(MotorConfig(travel_time_s=2), 1)
    m.set_fault("limits_lost", True)
    assert cmd(m, Command.GOTO, position=100) == bytes([NACK])
    assert cmd(m, Command.DOWN) == bytes([ACK])
    m.calibrate()
    run(m, 8)
    assert m.limits_set and not m.calibrating
    assert cmd(m, Command.GOTO, position=100) == bytes([ACK])


def test_faults_affect_answers():
    m = Motor(MotorConfig(), 1)
    tel = MasterTelegram.command(Addressing.to_slave(0), Command.STOP)
    m.set_fault("drop_next", True)
    assert m.handle(tel) is None
    assert m.handle(tel).data == bytes([ACK])
    m.set_fault("offline", True)
    assert m.handle(tel) is None
    m.set_fault("offline", False)
    m.set_fault("slow", True)
    assert m.handle(tel).extra_delay_s > 0.3


def test_reversal_inserts_dead_time():
    m = Motor(MotorConfig(kind=BlindKind.ROLLER, travel_time_s=10, reversal_pause_s=0.5), 1)
    cmd(m, Command.DOWN)
    run(m, 2)
    pos = m.position
    cmd(m, Command.UP)
    run(m, 0.4)
    assert m.position == pos and m.direction == 0  # still in the reversal pause
    run(m, 0.5)
    assert m.position < pos and m.direction == -1


def test_slat_percent_and_physical_angle():
    m = Motor(MotorConfig(kind=BlindKind.VENETIAN, start_position=0xFFFF), 1)
    assert m.slat_percent == 100 and m.slat_angle == 85  # default: 0..85 deg
    m.cfg.slat_min_deg, m.cfg.slat_max_deg = -80.0, 80.0  # 180 degree blind
    assert m.slat_angle == 80
    tel = MasterTelegram.command(Addressing.to_slave(0), Command.GOTO, angle_deg=134)
    m.handle(tel)
    run(m, 3)
    assert abs(m.slat_percent - 134 / 270 * 100) < 0.5
    assert abs(m.slat_angle) < 1.5  # about horizontal
    assert m.daylight > 0.8
