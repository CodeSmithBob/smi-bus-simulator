# HTTP / WebSocket API

All endpoints accept and return JSON. Errors return `{"error": "..."}` with status 400 or 404.

| Method | Path | Body / query | Purpose |
|---|---|---|---|
| GET | `/api/state` | | Full state: lines, drives, statistics, test lab |
| GET | `/api/meta` | | Fault list, presets, protocol status table |
| GET | `/api/traffic?since=SEQ` | | Bus monitor entries newer than `SEQ` |
| WS | `/ws?since=SEQ` | | Pushes `{"type":"state","state":…,"traffic":[…]}` 10× per second |
| POST | `/api/presets/{16\|32\|64\|factory-16\|small}` | | Rebuild the building |
| POST | `/api/settings` | `{"time_scale": 5}` | Motor speed multiplier (0.1 to 50) |
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
