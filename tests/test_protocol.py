import pytest

from smisim.protocol import (
    Addressing,
    AddrMode,
    Command,
    DiagCode,
    FrameAssembler,
    FrameError,
    MasterTelegram,
    QueryCode,
    checksum,
    checksum_ok,
    data_response,
    decode_response,
    expected_length,
    parse_hex,
    wired_and,
)

# Published captures (see docs/protocol.md for sources).
MOTOR_12_UP = bytes.fromhex("5C01A3")  # smiwiki: "Motor 12 Hoch"
ELERO_GROUP_UP = bytes.fromhex("43C0800301 79".replace(" ", ""))  # forum capture
DIAG_MOTOR_1 = bytes.fromhex("3100CF")  # forum capture


def test_checksum_of_published_captures():
    for frame in (MOTOR_12_UP, ELERO_GROUP_UP, DIAG_MOTOR_1):
        assert checksum(frame[:-1]) == frame[-1]
        assert checksum_ok(frame)


def test_decode_single_slave_command():
    tel = MasterTelegram.decode(MOTOR_12_UP)
    assert tel.addressing == Addressing.to_slave(12)
    assert tel.code == Command.UP
    assert tel.encode() == MOTOR_12_UP


def test_decode_group_capture():
    tel = MasterTelegram.decode(ELERO_GROUP_UP)
    assert tel.addressing.mode is AddrMode.GROUP
    assert tel.addressing.manufacturer == 3
    assert tel.addressing.mask == 0x8003  # slaves 0, 1 and 15
    assert tel.code == Command.UP
    assert tel.encode() == ELERO_GROUP_UP


def test_decode_diag_capture():
    tel = MasterTelegram.decode(DIAG_MOTOR_1)
    assert tel.addressing == Addressing.to_slave(1)
    assert tel.code == DiagCode.STATUS


def test_position_query_matches_smi_server():
    tel = MasterTelegram.query(Addressing.to_slave(3), QueryCode.POSITION)
    assert tel.encode() == bytes([0x73, 0x05, (-(0x73 + 0x05)) & 0xFF])
    answer = data_response(QueryCode.POSITION, 0x1234)
    assert answer[:4] == bytes([0xEF, 0x45, 0x12, 0x34])
    assert checksum_ok(answer)
    r = decode_response(tel, answer)
    assert r.kind == "data" and r.value == 0x1234


def test_goto_with_position_and_angle_roundtrip():
    tel = MasterTelegram.command(Addressing.to_slave(5), Command.GOTO, position=40000, angle_deg=90)
    raw = tel.encode()
    assert raw[1] == 0x40 | 0x20 | Command.GOTO
    back = MasterTelegram.decode(raw)
    assert back.word == 40000 and back.angle_deg == 90


def test_option_bytes_like_smi_server_group_up():
    # smi-server sends "40 C0 mm mm 81 22 00 ck" for group UP.
    raw = parse_hex("40 C0 00 05 81 22 00")
    raw += bytes([checksum(raw)])
    tel = MasterTelegram.decode(raw)
    assert tel.code == Command.UP and tel.option == (0x22, 0x00)
    assert tel.encode() == raw


@pytest.mark.parametrize(
    "addressing",
    [
        Addressing.broadcast(),
        Addressing.to_manufacturer(7),
        Addressing.to_slave(15),
        Addressing.to_slaves([0, 3, 9]),
        Addressing.to_key_id(0xDEADBEEF, 4),
    ],
)
def test_addressing_roundtrip(addressing):
    for tel in (
        MasterTelegram.command(addressing, Command.STOP),
        MasterTelegram.diag(addressing, DiagCode.STATUS),
        MasterTelegram.query(addressing, QueryCode.POSITION),
    ):
        assert MasterTelegram.decode(tel.encode()) == tel


def test_addressing_matches():
    kw = dict(address=4, manufacturer=2, key_id=0x1000)
    assert Addressing.broadcast().matches(**kw)
    assert Addressing.to_manufacturer(2).matches(**kw)
    assert not Addressing.to_manufacturer(3).matches(**kw)
    assert Addressing.to_slave(4).matches(**kw)
    assert Addressing.to_slaves([1, 4]).matches(**kw)
    assert not Addressing.to_slaves([1, 4], manufacturer=9).matches(**kw)
    assert Addressing.to_key_id(0x1000, 2).matches(**kw)
    assert not Addressing.to_key_id(0x1001, 2).matches(**kw)


def test_expected_length_incremental():
    raw = MasterTelegram.command(Addressing.to_slaves([1, 2]), Command.GOTO, position=1).encode()
    assert expected_length(raw[:1]) is None
    assert expected_length(raw[:4]) is None
    assert expected_length(raw[:5]) == len(raw)
    with pytest.raises(FrameError):
        expected_length(b"\xef")


def test_bad_checksum_rejected():
    with pytest.raises(FrameError):
        MasterTelegram.decode(b"\x5c\x01\xa4")


def test_wired_and_combines_answers():
    assert wired_and([b"\xff", b"\xff"]) == b"\xff"
    assert wired_and([b"\xff\xff\xe0", b"\xff\xe0\xff"]) == b"\xff\xe0\xe0"
    assert wired_and([]) == b""


def test_assembler_handles_chunks_and_garbage():
    asm = FrameAssembler(clock=lambda: 0.0)
    frames = asm.feed(b"\xaa" + MOTOR_12_UP[:2])
    assert [f.valid for f in frames] == [False]
    frames = asm.feed(MOTOR_12_UP[2:] + DIAG_MOTOR_1)
    assert [f.data for f in frames if f.valid] == [MOTOR_12_UP, DIAG_MOTOR_1]


def test_assembler_resyncs_after_corruption():
    asm = FrameAssembler(clock=lambda: 0.0)
    frames = asm.feed(b"\x5c\x01\x00" + MOTOR_12_UP)
    assert frames[-1].valid and frames[-1].data == MOTOR_12_UP
    assert any(not f.valid for f in frames)


def test_assembler_flushes_on_idle_gap():
    t = [0.0]
    asm = FrameAssembler(idle_timeout_s=0.05, clock=lambda: t[0])
    assert asm.feed(MOTOR_12_UP[:2]) == []
    t[0] = 1.0
    frames = asm.feed(MOTOR_12_UP)
    assert not frames[0].valid
    assert frames[1].valid
