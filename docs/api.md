# API

The simulator has two APIs:

* the **Python in-process API**: embed drives and SMI lines in your own program or test
  suite, without the web server and without asyncio. It is **stable** (see below);
* the **HTTP / WebSocket API** of `smisim run`, used by the web UI and handy for scripts.

## Python in-process API (stable)

Install the package (`pip install git+https://github.com/CodeSmithBob/smi-bus-simulator`) and drive a line
directly. A `Bus` is one SMI line. `handle_frame()` takes raw telegram bytes (checksum
included) and returns what the master would read back. `update(dt)` moves simulated time
forward by `dt` seconds. Nothing runs in the background, so your program decides how time
passes.

<!-- example:python-api (executed by tests/test_public_api.py) -->
```python
from smisim import Bus, BusSettings, MotorConfig
from smisim.protocol import Addressing, MasterTelegram, QueryCode, decode_response

bus = Bus(settings=BusSettings(name="Lab line"))
bus.add_motor(MotorConfig(name="Office", address=3, kind="roller", travel_time_s=10))

reply, delay = bus.handle_frame(bytes.fromhex("53 02 AB"))  # DOWN to slave 3
assert reply == b"\xff"  # ACK

for _ in range(50):  # 5 simulated seconds
    bus.update(0.1)

query = MasterTelegram.query(Addressing.to_slave(3), QueryCode.POSITION)
answer = decode_response(query, bus.handle_frame(query.encode()).reply)
print(answer.value)  # about 32768 (50 %)
```

### Stable names

From version **0.3.0** on, the names below follow [Semantic Versioning](https://semver.org/).
While the version is below 1.0, a breaking change to any of them bumps the **minor**
version (0.3 → 0.4) and is listed under *Breaking* in [CHANGELOG.md](../CHANGELOG.md).
Patch releases (0.3.0 → 0.3.1) never break them. Additions (new optional parameters,
new names) are not breaking.

| Name | Stable part |
|---|---|
| `smisim.Bus` | `Bus(index=0, settings=None)`, `add_motor(cfg, *, force=False) -> Motor`, `remove_motor(uid)`, `motor(uid)`, `motors` (list), `handle_frame(raw, source="api") -> FrameResult`, `update(dt)`, `settings`, `stats`, `duplicate_addresses()` |
| `smisim.FrameResult` | named tuple `(reply: bytes, delay_s: float)`; `reply` is empty when no drive answers |
| `smisim.BusSettings` | all fields and their meaning ([configuration.md](configuration.md)) |
| `smisim.MotorConfig` | all fields and their meaning ([configuration.md](configuration.md)) |
| `smisim.Motor` | read: `uid`, `cfg`, `address`, `key_id`, `position` (bottom-rail height, 0 = top, 65535 = bottom), `reported_position` (what the drive reports over SMI; differs from `position` only with `tilt_in_position`), `tilt` (0..1), `direction` (-1 up, 0, +1 down), `errors`, `faults`, `limits_set`, `slat_percent`, `slat_angle`, `angle_deg`; act: `set_fault(name, active)`, `clear_errors()`, `calibrate()`, `stop()`; test control: `target_position`, `move_to(position)`, `set_position(position, *, tilt=None)` |
| `smisim.BlindKind`, `smisim.FAULTS` | values and keys |
| `smisim.protocol` | `Addressing`, `AddrMode`, `MasterTelegram`, `Command`, `DiagCode`, `QueryCode`, `Response`, `decode_response`, `checksum`, `checksum_ok`, `expected_response_length`, `ACK`, `NACK` |

Wire encodings marked *provisional* in [protocol.md](protocol.md) may still change when
better sources appear. Such a change is a protocol fix, not an API break, and is listed in
the changelog. Everything else (`smisim.simulator`, `smisim.transports`, `smisim.web`,
`smisim.testlab`, `smisim.client`, the dictionaries returned by `to_state()`, traffic log
entries) is internal and may change in any release.

### Test control

Tests often need a drive in a known state, or moving, without composing telegrams. Use these
instead of private fields:

| Member | Meaning |
|---|---|
| `motor.target_position` | Read-only. Where the drive is heading (0 = top, 65535 = bottom), or its current `position` when it is not moving. For an angle step it is where the step ends. Slat turning does not change it. |
| `motor.move_to(position)` | Starts a move without a telegram, like a GOTO (venetian slat turn and reversal pause included). It ignores whether end positions are set. Returns `False` and does nothing when the drive cannot move (offline, blocked, thermal protection). |
| `motor.set_position(position, *, tilt=None)` | Stops the drive and puts it at `position` at once. `tilt` (0..1) also sets the slat turn of a venetian blind. |

```python
drive = bus.motors[0]
drive.set_position(0x4000)  # start the test at 25 %
drive.move_to(0xFFFF)  # then let it run down
while drive.position != drive.target_position:
    bus.update(0.1)
```

Notes:

* `handle_frame()` and `update()` are synchronous and do not need an event loop. The
  `Bus` is not thread-safe: call it from one thread.
* `handle_frame()` ignores invalid telegrams (bad checksum, wrong length), like real drives,
  and returns an empty reply. They are counted in `bus.stats["errors"]`.
* Answers from several drives are combined as on the real wire (bitwise AND), so a read
  addressed to two drives with the same address comes back garbled.
* `delay_s` is the turnaround the drive would wait before answering. The `Bus` itself never
  sleeps. Pace your own transport with it if you need realistic timing.

## HTTP / WebSocket API

All endpoints accept and return JSON. Errors return `{"error": "..."}` with status 400 or 404.

| Method | Path | Body / query | Purpose |
|---|---|---|---|
| GET | `/api/state` | | Full state: lines, drives, statistics, test lab |
| GET | `/api/meta` | | Fault list, presets, protocol status table |
| GET | `/api/traffic?since=SEQ` | | Bus monitor entries newer than `SEQ`. Position answers carry `raw_position` and, when one drive answered, `rail_percent` / `slat_percent` (where that drive physically was) |
| GET | `/api/traffic.csv?since=SEQ` | | The same entries as CSV, with `raw_position`, `rail_percent` and `slat_percent` in their own columns (the *Export CSV* button) |
| WS | `/ws?since=SEQ` | | Pushes `{"type":"state","state":…,"traffic":[…]}` 10× per second |
| POST | `/api/presets/{16\|32\|64\|factory-16\|small}` | | Rebuild the building |
| POST | `/api/settings` | `{"time_scale": 5}` | Simulated seconds per real second, 0.1 to 50 ([details](configuration.md#simulation-speed-time_scale)) |
| POST | `/api/buses` | `{"name": "...", "motors": 16, "kind": "mixed"}` | Add an SMI line |
| DELETE | `/api/buses/last` | | Remove the last line |
| PATCH | `/api/buses/{n}` | `{"name", "variant", "echo", "realtime", "response_delay_ms"}` | Line settings |
| POST | `/api/buses/{n}/motors` | MotorConfig fields | Add a drive |
| POST | `/api/buses/{n}/send` | telegram request or `{"hex": "5C 01 A3"}` | Send from the built-in master |
| POST | `/api/buses/{n}/discover` | `{"manufacturer": 0}` | Run the reference discovery |
| POST | `/api/encode` | telegram request | Encode without sending |
| POST | `/api/decode` | `{"hex": "..."}` | Decode bytes |
| PATCH | `/api/motors/{uid}` | `name, address, manufacturer, key_id, kind, travel_time_s, tilt_degrees, pos1, pos2` | Reconfigure a drive |
| POST | `/api/motors/{uid}/fault` | `{"name": "offline", "active": true}` | Inject or clear a fault |
| POST | `/api/motors/{uid}/{calibrate\|wink\|clear\|remove}` | | Commissioning actions |
| POST | `/api/testlab/start` | `{"include_ui": false}` | Start a test session |
| POST | `/api/testlab/stop` | | Stop the session |
| POST | `/api/testlab/arm/{check}` | | Arm `retry_timeout`, `retry_checksum`, `offline` or `thermal` |
| GET | `/api/testlab/report` | | JSON report |

## Telegram request

```json
{
  "mode": "slave | group | broadcast | manufacturer | key_id",
  "address": 3,
  "addresses": [0, 1, 5],
  "manufacturer": 0,
  "key_id": "1A2B3C4D",
  "type": "command | query | diag",
  "code": "up | down | stop | goto | pos1 | pos2 | position | ident | status | key_id_compare | ...",
  "position": 32768,
  "angle_deg": 90,
  "search": "80000000",
  "new_address": 4
}
```

Answer from `/send`:

```json
{
  "sent": "53 02 AB",
  "reply": "FF",
  "telegram": {"type": "COMMAND", "text": "DOWN -> slave 3", "status": "observed"},
  "response": {"kind": "ack", "text": "ACK"}
}
```
