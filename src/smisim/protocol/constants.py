"""SMI wire-level constants.

The official SMI specification is only available to members of SMI Standard Motor
Interface e.V. Everything in this module comes from public sources: vendor manuals,
bus captures published by the community and the open-source ``ingof/smi-server``
project. Each element carries a verification status in :data:`SPEC_STATUS` so
contributors can see what is confirmed and what is a simulator assumption.
See ``docs/protocol.md`` for the full source list.
"""

from __future__ import annotations

from enum import IntEnum

# --- Physical / UART layer -------------------------------------------------------

#: Bit rate of the SMI bus (both directions). Source: SMI e.V., Beckhoff KL6831, WAGO.
BAUDRATE = 2400
#: 8 data bits, no parity, 1 stop bit -> 10 bit times per byte on the wire.
BITS_PER_BYTE = 10
BYTE_TIME_S = BITS_PER_BYTE / BAUDRATE  # ~4.17 ms

#: Maximum number of drives on one SMI line (slave addresses 0..15).
MAX_DRIVES_PER_BUS = 16
MAX_SLAVE_ADDRESS = 15

# --- Master telegram, byte 0 (address byte) --------------------------------------
#
#   bit 7    : 0 = master telegram (responses from drives have bit 7 set)
#   bits 6-5 : telegram type (01 diagnosis, 10 drive command, 11 query)
#   bit 4    : 1 = low nibble is a slave address, 0 = low nibble is a manufacturer code
#   bits 3-0 : slave address 0..15 or manufacturer code (0 = all manufacturers)

RESPONSE_BIT = 0x80
TYPE_MASK = 0x60
SLAVE_ADDRESSING_BIT = 0x10
LOW_NIBBLE = 0x0F


class TelegramType(IntEnum):
    DIAG = 0x20
    COMMAND = 0x40
    QUERY = 0x60


# --- Addressing extension (manufacturer mode only) -------------------------------

#: Followed by a 16-bit big-endian bit mask of slave addresses (bit n = address n).
EXT_GROUP_MASK = 0xC0
#: Followed by a 32-bit big-endian slave ID ("key ID"). PROVISIONAL, see SPEC_STATUS.
EXT_KEY_ID = 0xE0

#: Manufacturer code 0 addresses all drives regardless of manufacturer (broadcast).
MANUFACTURER_ALL = 0

# --- Drive command byte ----------------------------------------------------------
#
#   bit 7    : 2 option bytes follow (option id, option value)
#   bit 6    : 2 data bytes follow (16-bit position, big endian)
#   bit 5    : 1 data byte follows (angle in units of 2 degrees, 0..510 deg)
#   bits 3-0 : command code

CMD_CODE_MASK = 0x0F
CMD_FLAG_OPTION = 0x80
CMD_FLAG_WORD = 0x40
CMD_FLAG_BYTE = 0x20


class Command(IntEnum):
    STOP = 0
    UP = 1
    DOWN = 2
    POS1 = 3
    POS2 = 4
    GOTO = 5


#: One angle unit in the 1-byte data field equals 2 degrees of motor shaft rotation.
ANGLE_UNIT_DEG = 2
MAX_ANGLE_DEG = 0xFF * ANGLE_UNIT_DEG  # 510

#: Position scale: 0 = upper end position, 0xFFFF = lower end position.
POSITION_TOP = 0x0000
POSITION_BOTTOM = 0xFFFF


# --- Diagnosis and query codes ---------------------------------------------------


class DiagCode(IntEnum):
    STATUS = 0x00  # observed: "31 00 CF" = diagnosis for slave 1
    KEY_ID_COMPARE = 0x01  # provisional encoding
    WRITE_ADDRESS = 0x02  # provisional encoding
    IDENTIFY = 0x03  # provisional encoding (short jog so the installer can see the drive)


class QueryCode(IntEnum):
    POS1 = 0x03  # provisional encoding
    POS2 = 0x04  # provisional encoding
    POSITION = 0x05  # observed: "7n 05 ck" -> "EF 45 hh ll ck"
    ADDRESS = 0x06  # provisional encoding
    IDENT = 0x07  # provisional encoding: manufacturer code + drive type
    KEY_ID = 0x08  # provisional encoding
    ANGLE = 0x09  # provisional encoding
    STATUS_BITS = 0x0A  # provisional encoding


#: Number of payload bytes after the code byte of a diagnosis telegram.
DIAG_PAYLOAD_LEN: dict[int, int] = {
    DiagCode.STATUS: 0,
    DiagCode.KEY_ID_COMPARE: 4,
    DiagCode.WRITE_ADDRESS: 1,
    DiagCode.IDENTIFY: 0,
}

#: Number of payload bytes after the code byte of a query telegram.
QUERY_PAYLOAD_LEN: dict[int, int] = {code: 0 for code in QueryCode}

#: Number of data bytes in the drive's answer to a query.
QUERY_RESPONSE_LEN: dict[int, int] = {
    QueryCode.POS1: 2,
    QueryCode.POS2: 2,
    QueryCode.POSITION: 2,
    QueryCode.ADDRESS: 1,
    QueryCode.IDENT: 2,
    QueryCode.KEY_ID: 4,
    QueryCode.ANGLE: 1,
    QueryCode.STATUS_BITS: 2,
}

# --- Responses -------------------------------------------------------------------
#
# The bus idles high and every participant transmits by pulling it low (wired-AND).
# When several drives answer at once, the master reads the bitwise AND of their bytes.
# A "flag" byte is therefore asserted when at least one drive pulls it low.

ACK = 0xFF
ACK_ALT = 0xFE
NACK = 0xC0
NACK_ALT = 0xE0
FLAG_SET = 0xE0
FLAG_CLEAR = 0xFF
#: First byte of a data response (followed by echo code | length flag, data, checksum).
DATA_RESPONSE = 0xEF

#: Length flags OR-ed onto the echoed query code in a data response.
RESP_LEN_FLAG = {1: 0x20, 2: 0x40, 4: 0x60}

# Bits of the 16-bit STATUS_BITS query answer (simulator definition).
STATUS_BIT_MOVING_UP = 1 << 0
STATUS_BIT_MOVING_DOWN = 1 << 1
STATUS_BIT_ERROR = 1 << 2
STATUS_BIT_THERMAL = 1 << 3
STATUS_BIT_OBSTACLE = 1 << 4
STATUS_BIT_BLOCKED = 1 << 5
STATUS_BIT_LIMITS_NOT_SET = 1 << 6
STATUS_BIT_AT_TOP = 1 << 8
STATUS_BIT_AT_BOTTOM = 1 << 9


# --- Verification status ---------------------------------------------------------

VERIFIED = "verified"  # stated in official/vendor documentation
OBSERVED = "observed"  # seen in published bus captures or working open-source code
PROVISIONAL = "provisional"  # simulator assumption, needs confirmation from the spec

SPEC_STATUS: dict[str, tuple[str, str]] = {
    "baudrate": (VERIFIED, "2400 bit/s: SMI e.V., Beckhoff KL6831, WAGO 753-1630"),
    "max_drives": (VERIFIED, "16 drives per line, addresses 0..15: SMI e.V., Beckhoff"),
    "uart_8n1": (OBSERVED, "8N1 framing: ingof/smi-server opens the port at 2400 8N1"),
    "checksum": (OBSERVED, "Two's complement of the byte sum: smiwiki, mikrocontroller.net"),
    "address_byte": (OBSERVED, "Type bits, slave/manufacturer bit: smi-server, forum captures"),
    "group_mask": (OBSERVED, "0xC0 + 16-bit mask: '43 C0 80 03 01 79' capture"),
    "key_id_ext": (PROVISIONAL, "0xE0 extension exists; 32-bit payload length is assumed"),
    "cmd_codes": (OBSERVED, "0 stop, 1 up, 2 down, 3 pos1, 4 pos2, 5 goto: smi-server"),
    "cmd_flags": (OBSERVED, "0x80 option, 0x40 word, 0x20 angle byte: smi-server"),
    "angle_unit": (VERIFIED, "2 degree resolution, 0..510 deg: Beckhoff FB_SMIUpStep"),
    "position_scale": (VERIFIED, "0 = top, 65535 = bottom: Beckhoff FB_SMIPosRead"),
    "pos_store": (PROVISIONAL, "POS1/POS2 with word data stores the position"),
    "ack_nack": (OBSERVED, "ACK 0xFF/0xFE, NACK 0xC0/0xE0: smi-server, smiwiki"),
    "diag_status": (OBSERVED, "ACK + up/down/stop/error flag bytes: smi-server"),
    "query_position": (OBSERVED, "'7n 05 ck' -> 'EF 45 hh ll ck': smi-server"),
    "query_other": (PROVISIONAL, "Other query codes follow the position pattern"),
    "key_id_compare": (PROVISIONAL, "Semantics from Beckhoff FB_SMISlaveIdCompare"),
    "write_address": (PROVISIONAL, "Semantics from Beckhoff FB_SMISlaveAddrWrite"),
    "identify": (PROVISIONAL, "Simulator convenience for commissioning"),
    "tilt_in_position": (
        PROVISIONAL,
        "Optional venetian mode (off by default): position counts slat turning as shaft "
        "rotation. KNX-User-Forum: slat angles can be reached via absolute SMI positions",
    ),
    "wired_and": (OBSERVED, "Bus idles ~21 V, drives pull it low: smiwiki hardware page"),
}

CODE_STATUS: dict[tuple[int, int], str] = {
    (TelegramType.DIAG, DiagCode.STATUS): "diag_status",
    (TelegramType.DIAG, DiagCode.KEY_ID_COMPARE): "key_id_compare",
    (TelegramType.DIAG, DiagCode.WRITE_ADDRESS): "write_address",
    (TelegramType.DIAG, DiagCode.IDENTIFY): "identify",
    (TelegramType.QUERY, QueryCode.POSITION): "query_position",
}


def code_status(ttype: int, code: int) -> str:
    """Verification status of a diagnosis/query code."""
    if ttype == TelegramType.COMMAND:
        return OBSERVED
    key = CODE_STATUS.get((ttype, code))
    if key is None:
        return PROVISIONAL
    return SPEC_STATUS[key][0]
