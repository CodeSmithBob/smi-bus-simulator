# SMI protocol notes

This page collects everything the simulator implements about **SMI (Standard Motor
Interface)** on the wire, and where each piece of knowledge comes from.

> **Important.** The official SMI specification (currently SMI 3.0 with the basic format
> *3.0.BF* and the extended data protocol *3.0.D14*) is published only to members of
> **SMI Standard Motor Interface e.V.** This project is not affiliated with the association.
> Everything here comes from public vendor manuals, published bus captures and open-source
> code. If you have access to the specification, you can help: open a
> *protocol correction* issue that says what is wrong, without copying protected text.

Every element has one of three statuses. The same statuses appear in the code
(`src/smisim/protocol/constants.py`, `SPEC_STATUS`) and in the web UI:

| Status | Meaning |
|---|---|
| **verified** | Stated in official or vendor documentation |
| **observed** | Seen in published bus captures or in working open-source code |
| **provisional** | Simulator assumption that still needs confirmation; kept in one place so it is easy to fix |

## 1. Physical layer

| Item | Value | Status | Source |
|---|---|---|---|
| Topology | Line or star; drives in parallel on a 2-wire data pair `I+` / `I-` | verified | SMI e.V., Beckhoff, ABB |
| Cable (230 V) | 5 conductors: L, N, PE, I+, I- (standard mains cable, I+/I- polarity independent) | verified | ABB SMI planning manual |
| Cable (LoVo) | 24 V, 0 V, I+, I- | verified | Schneider Electric, WAGO 753-1631 |
| Drives per line | up to 16 (slave addresses 0 to 15) | verified | SMI e.V., Beckhoff KL6831, WAGO 753-1630 |
| Line length | up to 350 m (some datasheets say 200 m) | verified | ABB, Beckhoff, WAGO |
| Bit rate | 2400 bit/s, both directions | verified | SMI e.V., baunetzwissen, Beckhoff |
| Character format | 8N1, LSB first (UART) | observed | `ingof/smi-server` opens the port at 2400 8N1 |
| Electrical | Bus idles at about 21 V from the master through about 1 kΩ. Any participant transmits by loading the line (to about 2.7 V). Receivers compare against about 11.5 V. | observed | smiwiki "SMI-Bus" and "Selbstbau Interface" pages |
| Variants | SMI (230 V AC drives) and SMI LoVo (24 V DC drives). Do not mix them on one interface. | verified | SMI e.V., LOYTEC |

Because every participant can only *pull the line low*, the bus behaves as a **wired-AND**.
When several drives answer at the same time, the master reads the bitwise AND of their
bytes. The simulator models this exactly (`wired_and()`), so:

* an ACK from 16 drives at once is still a clean `FF`;
* diagnosis flags mean "at least one addressed drive says yes";
* two drives with the **same address** spoil each other's position answers. This is the
  real-world reason why each drive needs its own address before you can read it.

> The smiwiki "SMI-Bus" page mentions 1200 8N1. All vendor documents say 2400 bit/s, and
> so does the working `smi-server` code, so the simulator uses 2400. The rate can be
> changed per line (`serial_baud`) if you need to experiment.

## 2. Master telegrams

A telegram is 2 to 12 bytes long. Its length follows from its content, and it ends with a
checksum.

```
[address byte] [extension]* [code byte] [data]* [checksum]
```

### 2.1 Checksum (observed)

Two's complement of the sum of all bytes, so that the sum of **all** bytes, including the
checksum, is `0x00` modulo 256.

```
5C 01      -> sum 5D -> checksum A3      telegram: 5C 01 A3   ("motor 12 up", smiwiki)
```

### 2.2 Address byte (observed)

```
bit   7   6   5   4   3   2   1   0
      0   T   T   S   n   n   n   n
```

| Field | Meaning |
|---|---|
| bit 7 | `0` for master telegrams. Answers from drives have bit 7 set. |
| `TT` | Telegram type: `01` diagnosis (`0x20`), `10` drive command (`0x40`), `11` query/read (`0x60`) |
| `S` | `1` means `nnnn` is a **slave address** 0 to 15. `0` means `nnnn` is a **manufacturer code** (0 = all manufacturers). |

Examples: `0x5C` is a drive command to slave 12. `0x40` is a drive command to all drives
(broadcast). `0x43` is a drive command to all drives of manufacturer 3. `0x31` is a
diagnosis for slave 1. `0x73` is a read from slave 3.

### 2.3 Addressing extensions (manufacturer mode only)

| Byte after the address | Follows | Meaning | Status |
|---|---|---|---|
| `C0` | 2 bytes, big endian | Group mask: bit *n* selects slave address *n* | observed (`43 C0 80 03 01 79` = manufacturer 3, slaves 0, 1 and 15, UP) |
| `E0` | 4 bytes, big endian | 32-bit slave ID ("key ID"), together with the manufacturer code in the address byte | **provisional**: the `E0` prefix is documented on smiwiki, the payload length is assumed from the 32-bit key ID described by Beckhoff |

### 2.4 Drive command byte (observed)

```
bit   7   6   5   4   3   2   1   0
      O   W   B   -   c   c   c   c
```

| Bits | Meaning |
|---|---|
| `cccc` | `0` STOP, `1` UP, `2` DOWN, `3` POS1, `4` POS2, `5` GOTO position |
| `O` (0x80) | 2 option bytes follow (option id, value). `smi-server` sends `22 00` with UP and `21 00` with POS1/POS2. The meaning is not documented publicly; the simulator accepts and logs them. |
| `W` (0x40) | 2 data bytes follow: a 16-bit position, `0x0000` = upper end position, `0xFFFF` = lower end position (verified, Beckhoff `FB_SMIPosRead`) |
| `B` (0x20) | 1 data byte follows: an angle in units of 2° motor-shaft rotation, 0 to 510° (verified, Beckhoff `FB_SMIUpStep`/`FB_SMIDownStep`) |

Data bytes follow in the order option, word, byte.

| Telegram | Simulator behaviour |
|---|---|
| UP / DOWN | Run to the upper / lower end position |
| UP / DOWN + angle | Turn the motor shaft by that angle (step / slat adjustment) |
| STOP | Stop |
| POS1 / POS2 | Go to the stored intermediate position |
| POS1 / POS2 + word | **provisional**: store the position (smiwiki lists "store position 1/2") |
| GOTO + word | Go to the position |
| GOTO + word + angle | **provisional**: go to the position, then turn the slats to the absolute angle |
| GOTO + angle | **provisional**: turn the slats only |

### 2.5 Diagnosis (type `0x20`)

| Code | Payload | Answer | Status |
|---|---|---|---|
| `00` STATUS | none | ACK + 4 flag bytes: *moving up*, *moving down*, *stopped*, *error* | observed (`31 00 CF`, `smi-server` parser, Beckhoff `FB_SMIDiagAll`) |
| `01` KEY_ID_COMPARE | 4-byte key ID | ACK + 4 flags: *addr 0 and ID > search*, *addr 0 and ID < search*, *addr 0 and ID = search*, *addr ≠ 0* | **provisional** (semantics from Beckhoff `FB_SMISlaveIdCompare`) |
| `02` WRITE_ADDRESS | 1 byte new address | ACK | **provisional** (semantics from Beckhoff `FB_SMISlaveAddrWrite`) |
| `03` IDENTIFY | none | ACK; the drive "winks" in the UI | **provisional**, simulator convenience |

### 2.6 Queries (type `0x60`)

| Code | Answer data | Status |
|---|---|---|
| `05` POSITION | 2 bytes | observed: `73 05 88` → `EF 45 hh ll ck` |
| `03` / `04` stored POS1 / POS2 | 2 bytes | provisional |
| `06` slave address | 1 byte | provisional |
| `07` manufacturer code + drive type | 2 bytes | provisional (Beckhoff `FB_SMISyn` reads these) |
| `08` key ID | 4 bytes | provisional |
| `09` slat angle (2° units) | 1 byte | provisional |
| `0A` status bits (see `STATUS_BIT_*` in `constants.py`) | 2 bytes | provisional |

## 3. Answers from drives

| Answer | Bytes | Status |
|---|---|---|
| ACK | `FF` (also accepted: `FE`) | observed |
| NACK | `C0` (also accepted: `E0`, `D0`) | observed (smiwiki marks `E0` with a question mark) |
| Flag asserted / clear | `E0` / `FF` | observed |
| Data answer | `EF`, `code \| length flag`, data (big endian), checksum | observed for POSITION (`EF 45 …`). The length flags `0x20`/`0x40`/`0x60` for 1/2/4 bytes are provisional. |

Drive answers start after a short turnaround (configurable, 8 ms by default) and are sent
at 2400 baud (about 4.17 ms per byte). Many real interfaces echo the master's own bytes
back. Enable `echo = true` if your controller expects that.

## 4. Addressing and commissioning

* Factory-new drives are delivered on **slave address 0** (mikrocontroller.net,
  ABB). Movement commands work with duplicate addresses, but reads collide.
* Each drive has a **32-bit slave ID** (key ID), unique per manufacturer, plus a 4-bit
  **manufacturer code**. Manufacturer code 0 addresses all drives (broadcast). Beckhoff
  says codes 0 and 14 are special.
* A master can find drives with a **search procedure** on the key ID and then give each
  drive a slave address. The simulator ships a reference implementation:
  `SmiMaster.discover_and_address()` in `src/smisim/client.py` does a binary search with
  KEY_ID_COMPARE (32 telegrams per drive), then a WRITE_ADDRESS addressed by key ID.
  In the web UI: *Master → Discover & address drives*.

## 5. Not modelled (yet)

* SMI 3.0.D14 *properties* (up to 65 standardised read-out values) and parameter
  read/write of 1-, 2- and 4-byte values (Beckhoff `FB_SMIParValueRead*`).
* Manufacturer-specific commands.
* Firmware update over SMI.
* Exact electrical waveforms and timing limits from the specification.

Pull requests are welcome. Add new codes in `constants.py` with a status and a source.

## Sources

* SMI Standard Motor Interface e.V.: <https://standard-motor-interface.com/en/>,
  "Die Technik des SMI": <https://standard-motor-interface.com/technik-des-smi/>, FAQ:
  <https://standard-motor-interface.com/frequently-asked-questions/>
* Beckhoff KL6831/KL6841 introduction:
  <https://infosys.beckhoff.com/content/1033/kl6831_kl6841/4001149323.html>
* Beckhoff TwinCAT 3 PLC library Tc2_SMI manual:
  <https://download.beckhoff.com/download/document/automation/twincat3/TwinCAT_3_PLC_Lib_Tc2_SMI_EN.pdf>
  (device addressing, `FB_SMIPosRead`, `FB_SMIUpStep`, `FB_SMIDiagAll`,
  `FB_SMISlaveIdCompare`, `FB_SMISlaveAddrWrite`, error codes)
* WAGO 753-1630 SMI master manual: <https://www.manualslib.com/manual/1948181/Wago-753-1630.html>
* ABB SMI planning manual (German):
  <https://library.e.abb.com/public/2314c39350654d9a8f1cab5c19f50fc8/SJRS_42421_PLHB_FL_DE_V1-0_SMI-PLANUNGSHANDBUCH_2011-07.pdf>
* "Neuer SMI-Standard für Jalousieantriebe" (SMI 3.0), de 4/2020:
  <https://www.elektro.net/file/show/80995/573df2/DE_2020_4_44-45_GV12_LOW.pdf>
* LOYTEC L-SMI: <https://www.loytec.com/products/interfaces/l-smi>
* SMI-Wiki (community reverse engineering): telegrams
  <https://smiwiki.thefischer.net/doku.php?id=wiki%3Asmi%3Atelegramme>, bus
  <https://smiwiki.thefischer.net/doku.php?id=wiki:smi:smi-bus>, interface
  <https://smiwiki.thefischer.net/doku.php?id=wiki:smi:selbstbint>
* mikrocontroller.net thread "Gibt es irgendwo brauchbare Infos zu SMI?":
  <https://www.mikrocontroller.net/topic/273846>
* `ingof/smi-server`, Linux server for the SMI bus: <https://github.com/ingof/smi-server>
  (the simulator re-implements the observed formats; no code was copied)
* Vestamatic IF SMI RS-485, a gateway with its own serial protocol (not the SMI wire format):
  <https://vestamatic.com/externally_provided_media/IF%20SMI%20RS-485%20DIN%20RAIL_3060%20001%20GB.pdf>

Retrieved October 2026.
