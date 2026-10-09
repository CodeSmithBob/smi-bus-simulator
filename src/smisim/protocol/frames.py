"""Encoding and decoding of SMI master telegrams and drive responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .constants import (
    ANGLE_UNIT_DEG,
    CMD_CODE_MASK,
    CMD_FLAG_BYTE,
    CMD_FLAG_OPTION,
    CMD_FLAG_WORD,
    DATA_RESPONSE,
    DIAG_PAYLOAD_LEN,
    EXT_GROUP_MASK,
    EXT_KEY_ID,
    FLAG_CLEAR,
    FLAG_SET,
    LOW_NIBBLE,
    MANUFACTURER_ALL,
    MAX_SLAVE_ADDRESS,
    QUERY_PAYLOAD_LEN,
    QUERY_RESPONSE_LEN,
    RESP_LEN_FLAG,
    RESPONSE_BIT,
    SLAVE_ADDRESSING_BIT,
    TYPE_MASK,
    Command,
    DiagCode,
    QueryCode,
    TelegramType,
    code_status,
)


class FrameError(ValueError):
    """Raised for malformed or corrupted telegrams."""


# --- Checksum --------------------------------------------------------------------


def checksum(data: bytes | bytearray) -> int:
    """Two's complement of the byte sum, so that sum(frame) & 0xFF == 0."""
    return (-sum(data)) & 0xFF


def with_checksum(data: bytes | bytearray) -> bytes:
    return bytes(data) + bytes([checksum(data)])


def checksum_ok(frame: bytes | bytearray) -> bool:
    return len(frame) >= 2 and (sum(frame) & 0xFF) == 0


# --- Addressing ------------------------------------------------------------------


class AddrMode(StrEnum):
    BROADCAST = "broadcast"  # manufacturer code 0, no extension
    MANUFACTURER = "manufacturer"  # manufacturer code 1..15, no extension
    SLAVE = "slave"  # single slave address 0..15
    GROUP = "group"  # 16-bit mask of slave addresses (+ manufacturer filter)
    KEY_ID = "key_id"  # 32-bit slave ID (+ manufacturer code)


@dataclass(frozen=True)
class Addressing:
    mode: AddrMode
    manufacturer: int = MANUFACTURER_ALL
    slave: int = 0
    mask: int = 0
    key_id: int = 0

    # Constructors ---------------------------------------------------------------

    @classmethod
    def broadcast(cls) -> Addressing:
        return cls(AddrMode.BROADCAST)

    @classmethod
    def to_manufacturer(cls, code: int) -> Addressing:
        _check_nibble(code, "manufacturer code")
        if code == MANUFACTURER_ALL:
            return cls.broadcast()
        return cls(AddrMode.MANUFACTURER, manufacturer=code)

    @classmethod
    def to_slave(cls, address: int) -> Addressing:
        _check_nibble(address, "slave address")
        return cls(AddrMode.SLAVE, slave=address)

    @classmethod
    def to_group(cls, mask: int, manufacturer: int = MANUFACTURER_ALL) -> Addressing:
        if not 0 <= mask <= 0xFFFF:
            raise ValueError("group mask must be 16 bit")
        _check_nibble(manufacturer, "manufacturer code")
        return cls(AddrMode.GROUP, manufacturer=manufacturer, mask=mask)

    @classmethod
    def to_slaves(cls, addresses: list[int], manufacturer: int = MANUFACTURER_ALL) -> Addressing:
        mask = 0
        for a in addresses:
            _check_nibble(a, "slave address")
            mask |= 1 << a
        return cls.to_group(mask, manufacturer)

    @classmethod
    def to_key_id(cls, key_id: int, manufacturer: int) -> Addressing:
        if not 0 <= key_id <= 0xFFFFFFFF:
            raise ValueError("key ID must be 32 bit")
        _check_nibble(manufacturer, "manufacturer code")
        return cls(AddrMode.KEY_ID, manufacturer=manufacturer, key_id=key_id)

    # Encoding -------------------------------------------------------------------

    def encode(self, ttype: TelegramType) -> bytes:
        if self.mode is AddrMode.SLAVE:
            return bytes([ttype | SLAVE_ADDRESSING_BIT | self.slave])
        head = ttype | (self.manufacturer & LOW_NIBBLE)
        if self.mode is AddrMode.GROUP:
            return bytes([head, EXT_GROUP_MASK, self.mask >> 8, self.mask & 0xFF])
        if self.mode is AddrMode.KEY_ID:
            return bytes([head, EXT_KEY_ID]) + self.key_id.to_bytes(4, "big")
        return bytes([head])

    # Matching -------------------------------------------------------------------

    def matches(self, *, address: int, manufacturer: int, key_id: int) -> bool:
        """Whether a drive with the given identity is addressed."""
        if self.mode is AddrMode.SLAVE:
            return address == self.slave
        if self.manufacturer not in (MANUFACTURER_ALL, manufacturer):
            return False
        if self.mode is AddrMode.GROUP:
            return bool(self.mask & (1 << address))
        if self.mode is AddrMode.KEY_ID:
            return key_id == self.key_id
        return True

    @property
    def is_multi(self) -> bool:
        """True if the addressing can reach more than one drive."""
        if self.mode is AddrMode.GROUP:
            return self.mask.bit_count() > 1
        return self.mode in (AddrMode.BROADCAST, AddrMode.MANUFACTURER)

    def describe(self) -> str:
        if self.mode is AddrMode.SLAVE:
            return f"slave {self.slave}"
        mfr = "" if self.manufacturer == MANUFACTURER_ALL else f" mfr {self.manufacturer}"
        if self.mode is AddrMode.GROUP:
            members = [str(i) for i in range(16) if self.mask & (1 << i)]
            return f"group [{','.join(members)}]{mfr}"
        if self.mode is AddrMode.KEY_ID:
            return f"key-ID {self.key_id:08X} mfr {self.manufacturer}"
        if self.mode is AddrMode.MANUFACTURER:
            return f"all of mfr {self.manufacturer}"
        return "broadcast"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "manufacturer": self.manufacturer,
            "slave": self.slave,
            "mask": self.mask,
            "key_id": self.key_id,
        }


def _check_nibble(value: int, what: str) -> None:
    if not 0 <= value <= MAX_SLAVE_ADDRESS:
        raise ValueError(f"{what} must be 0..15, got {value}")


# --- Master telegram -------------------------------------------------------------


@dataclass(frozen=True)
class MasterTelegram:
    ttype: TelegramType
    addressing: Addressing
    code: int
    option: tuple[int, int] | None = None  # command option id + value
    word: int | None = None  # 16-bit data (position)
    byte: int | None = None  # 8-bit data (angle units of 2 degrees)
    payload: bytes = field(default=b"")  # diagnosis/query payload

    # Constructors ---------------------------------------------------------------

    @classmethod
    def command(
        cls,
        addressing: Addressing,
        command: Command | int,
        *,
        position: int | None = None,
        angle_deg: int | None = None,
        option: tuple[int, int] | None = None,
    ) -> MasterTelegram:
        byte = None
        if angle_deg is not None:
            if not 0 <= angle_deg <= 0xFF * ANGLE_UNIT_DEG:
                raise ValueError("angle must be 0..510 degrees")
            byte = angle_deg // ANGLE_UNIT_DEG
        if position is not None and not 0 <= position <= 0xFFFF:
            raise ValueError("position must be 0..65535")
        return cls(TelegramType.COMMAND, addressing, int(command), option, position, byte)

    @classmethod
    def diag(
        cls, addressing: Addressing, code: DiagCode | int, payload: bytes = b""
    ) -> MasterTelegram:
        return cls(TelegramType.DIAG, addressing, int(code), payload=bytes(payload))

    @classmethod
    def query(cls, addressing: Addressing, code: QueryCode | int) -> MasterTelegram:
        return cls(TelegramType.QUERY, addressing, int(code))

    # Properties -----------------------------------------------------------------

    @property
    def angle_deg(self) -> int | None:
        return None if self.byte is None else self.byte * ANGLE_UNIT_DEG

    @property
    def command_byte(self) -> int:
        value = self.code & CMD_CODE_MASK
        if self.option is not None:
            value |= CMD_FLAG_OPTION
        if self.word is not None:
            value |= CMD_FLAG_WORD
        if self.byte is not None:
            value |= CMD_FLAG_BYTE
        return value

    @property
    def status(self) -> str:
        return code_status(self.ttype, self.code)

    # Encoding / decoding --------------------------------------------------------

    def encode(self) -> bytes:
        out = bytearray(self.addressing.encode(self.ttype))
        if self.ttype is TelegramType.COMMAND:
            out.append(self.command_byte)
            if self.option is not None:
                out += bytes(self.option)
            if self.word is not None:
                out += self.word.to_bytes(2, "big")
            if self.byte is not None:
                out.append(self.byte)
        else:
            out.append(self.code)
            out += self.payload
        return with_checksum(out)

    @classmethod
    def decode(cls, frame: bytes | bytearray) -> MasterTelegram:
        frame = bytes(frame)
        n = expected_length(frame)
        if n is None or len(frame) < n:
            raise FrameError("incomplete telegram")
        if len(frame) != n:
            raise FrameError(f"telegram length {len(frame)} does not match expected {n}")
        if not checksum_ok(frame):
            raise FrameError("checksum error")
        b0 = frame[0]
        ttype = TelegramType(b0 & TYPE_MASK)
        i = 1
        if b0 & SLAVE_ADDRESSING_BIT:
            addressing = Addressing.to_slave(b0 & LOW_NIBBLE)
        else:
            mfr = b0 & LOW_NIBBLE
            ext = frame[1]
            if ext == EXT_GROUP_MASK:
                addressing = Addressing.to_group((frame[2] << 8) | frame[3], mfr)
                i = 4
            elif ext == EXT_KEY_ID:
                addressing = Addressing.to_key_id(int.from_bytes(frame[2:6], "big"), mfr)
                i = 6
            else:
                addressing = Addressing.to_manufacturer(mfr)
        code_byte = frame[i]
        i += 1
        if ttype is TelegramType.COMMAND:
            option = word = byte = None
            if code_byte & CMD_FLAG_OPTION:
                option = (frame[i], frame[i + 1])
                i += 2
            if code_byte & CMD_FLAG_WORD:
                word = (frame[i] << 8) | frame[i + 1]
                i += 2
            if code_byte & CMD_FLAG_BYTE:
                byte = frame[i]
                i += 1
            return cls(ttype, addressing, code_byte & CMD_CODE_MASK, option, word, byte)
        payload = frame[i:-1]
        return cls(ttype, addressing, code_byte, payload=payload)

    # Presentation ---------------------------------------------------------------

    def describe(self) -> str:
        target = self.addressing.describe()
        if self.ttype is TelegramType.COMMAND:
            try:
                name = Command(self.code).name
            except ValueError:
                name = f"CMD{self.code}"
            parts = [name]
            if self.word is not None:
                verb = "store" if self.code in (Command.POS1, Command.POS2) else "pos"
                parts.append(f"{verb}={self.word} ({self.word / 655.35:.1f}%)")
            if self.byte is not None:
                parts.append(f"angle={self.angle_deg}deg")
            if self.option is not None:
                parts.append(f"option={self.option[0]:02X}:{self.option[1]:02X}")
            return f"{' '.join(parts)} -> {target}"
        if self.ttype is TelegramType.DIAG:
            try:
                name = DiagCode(self.code).name
            except ValueError:
                name = f"DIAG{self.code:02X}"
            extra = f" data={self.payload.hex(' ').upper()}" if self.payload else ""
            return f"DIAG {name}{extra} -> {target}"
        try:
            name = QueryCode(self.code).name
        except ValueError:
            name = f"QUERY{self.code:02X}"
        return f"READ {name} -> {target}"

    def to_dict(self) -> dict:
        return {
            "type": self.ttype.name,
            "addressing": self.addressing.to_dict(),
            "code": self.code,
            "option": list(self.option) if self.option else None,
            "word": self.word,
            "byte": self.byte,
            "payload": self.payload.hex(),
            "status": self.status,
            "text": self.describe(),
        }


def expected_length(buf: bytes | bytearray) -> int | None:
    """Total length of the master telegram starting at ``buf[0]``.

    Returns ``None`` when more bytes are needed to decide. Raises :class:`FrameError`
    if ``buf[0]`` cannot start a master telegram.
    """
    if not buf:
        return None
    b0 = buf[0]
    if b0 & RESPONSE_BIT or (b0 & TYPE_MASK) == 0:
        raise FrameError(f"0x{b0:02X} cannot start a master telegram")
    ttype = b0 & TYPE_MASK
    n = 1
    if not b0 & SLAVE_ADDRESSING_BIT:
        if len(buf) < 2:
            return None
        if buf[1] == EXT_GROUP_MASK:
            n += 3
        elif buf[1] == EXT_KEY_ID:
            n += 5
    if len(buf) <= n:
        return None
    code = buf[n]
    n += 1
    if ttype == TelegramType.COMMAND:
        if code & CMD_FLAG_OPTION:
            n += 2
        if code & CMD_FLAG_WORD:
            n += 2
        if code & CMD_FLAG_BYTE:
            n += 1
    elif ttype == TelegramType.DIAG:
        n += DIAG_PAYLOAD_LEN.get(code, 0)
    else:
        n += QUERY_PAYLOAD_LEN.get(code, 0)
    return n + 1  # checksum


# --- Responses -------------------------------------------------------------------


def flag(value: bool) -> int:
    return FLAG_SET if value else FLAG_CLEAR


def is_ack(b: int) -> bool:
    return (b & 0xFE) == 0xFE


def is_nack(b: int) -> bool:
    return (b & 0xDF) == 0xC0 or b == 0xD0


def is_flag_set(b: int) -> bool:
    return b != FLAG_CLEAR


def data_response(code: int, value: int) -> bytes:
    """Answer to a query: EF, code|length flag, big-endian data, checksum."""
    length = QUERY_RESPONSE_LEN.get(code, 2)
    body = bytes([DATA_RESPONSE, code | RESP_LEN_FLAG[length]]) + value.to_bytes(length, "big")
    return with_checksum(body)


def expected_response_length(tel: MasterTelegram) -> int:
    if tel.ttype is TelegramType.COMMAND:
        return 1
    if tel.ttype is TelegramType.DIAG:
        return 5 if tel.code in (DiagCode.STATUS, DiagCode.KEY_ID_COMPARE) else 1
    return 3 + QUERY_RESPONSE_LEN.get(tel.code, 2)


def wired_and(responses: list[bytes]) -> bytes:
    """Combine simultaneous answers the way the open-collector bus does."""
    responses = [r for r in responses if r]
    if not responses:
        return b""
    length = max(len(r) for r in responses)
    out = bytearray([0xFF] * length)
    for r in responses:
        for i, b in enumerate(r):
            out[i] &= b
    return bytes(out)


@dataclass
class Response:
    kind: str  # none, ack, nack, flags, data, garbled
    raw: bytes = b""
    flags: list[bool] | None = None
    value: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.kind in ("ack", "flags", "data")

    def describe(self, tel: MasterTelegram | None = None) -> str:
        if self.kind == "none":
            return "no response"
        if self.kind == "ack":
            return "ACK"
        if self.kind == "nack":
            return "NACK"
        if self.kind == "garbled":
            return f"garbled ({self.error})"
        if self.kind == "flags" and self.flags is not None:
            names = FLAG_NAMES.get((tel.ttype, tel.code), None) if tel else None
            names = names or [f"f{i}" for i in range(len(self.flags))]
            on = [n for n, f in zip(names, self.flags, strict=False) if f]
            return "ACK flags: " + (", ".join(on) if on else "none")
        if self.kind == "data" and self.value is not None:
            if tel and tel.code in (QueryCode.POSITION, QueryCode.POS1, QueryCode.POS2):
                return f"{self.value} ({self.value / 655.35:.1f}%)"
            if tel and tel.code == QueryCode.ANGLE:
                return f"{self.value * ANGLE_UNIT_DEG} deg"
            if tel and tel.code == QueryCode.KEY_ID:
                return f"{self.value:08X}"
            if tel and tel.code == QueryCode.IDENT:
                return f"mfr {self.value >> 8}, type {self.value & 0xFF}"
            return str(self.value)
        return self.kind

    def to_dict(self, tel: MasterTelegram | None = None) -> dict:
        return {
            "kind": self.kind,
            "raw": self.raw.hex(" ").upper(),
            "flags": self.flags,
            "value": self.value,
            "error": self.error,
            "text": self.describe(tel),
        }


FLAG_NAMES: dict[tuple[int, int], list[str]] = {
    (TelegramType.DIAG, DiagCode.STATUS): ["moving up", "moving down", "stopped", "error"],
    (TelegramType.DIAG, DiagCode.KEY_ID_COMPARE): [
        "addr0 & ID>search",
        "addr0 & ID<search",
        "addr0 & ID=search",
        "addr!=0",
    ],
}


def decode_response(tel: MasterTelegram, raw: bytes) -> Response:
    """Interpret the bytes a master received after sending ``tel``."""
    raw = bytes(raw)
    if not raw:
        return Response("none")
    expected = expected_response_length(tel)
    if tel.ttype is TelegramType.QUERY:
        if len(raw) < expected:
            if len(raw) == 1 and is_nack(raw[0]):
                return Response("nack", raw)
            return Response("garbled", raw, error="short data response")
        if raw[0] != DATA_RESPONSE:
            return Response("garbled", raw, error=f"unexpected start byte {raw[0]:02X}")
        if not checksum_ok(raw[:expected]):
            return Response("garbled", raw, error="checksum error")
        return Response("data", raw, value=int.from_bytes(raw[2 : expected - 1], "big"))
    first = raw[0]
    if is_nack(first) and expected == 1:
        return Response("nack", raw)
    if not is_ack(first):
        if is_nack(first):
            return Response("nack", raw)
        return Response("garbled", raw, error=f"unexpected byte {first:02X}")
    if expected == 1:
        return Response("ack", raw)
    if len(raw) < expected:
        return Response("garbled", raw, error="short flag response")
    return Response("flags", raw, flags=[is_flag_set(b) for b in raw[1:expected]])


def parse_hex(text: str) -> bytes:
    cleaned = text.replace(",", " ").replace("0x", " ").replace("0X", " ").split()
    if len(cleaned) == 1 and len(cleaned[0]) > 2:
        return bytes.fromhex(cleaned[0])
    return bytes(int(tok, 16) for tok in cleaned)


def decode_any(raw: bytes) -> dict:
    """Best-effort decoding of a byte string for the monitor and the CLI."""
    try:
        tel = MasterTelegram.decode(raw)
        return {"ok": True, "telegram": tel.to_dict()}
    except (FrameError, ValueError, IndexError) as exc:
        hint = None
        if raw and raw[0] == DATA_RESPONSE:
            hint = "looks like a data response from a drive"
        elif raw and is_ack(raw[0]):
            hint = "looks like an ACK (with flags) from a drive"
        return {"ok": False, "error": str(exc), "hint": hint}
