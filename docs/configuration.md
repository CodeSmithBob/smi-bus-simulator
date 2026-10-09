# Configuration

Without a file, `smisim run` builds lines from command-line options:

```bash
smisim run --buses 2 --motors 16 --kind mixed --pty /tmp/smi --time-scale 1
smisim run --factory-new --motors 16      # commissioning practice
smisim run --help                         # all options
```

For a fixed building, use a TOML file: `smisim run --config building.toml`.
[examples/two-floors.toml](../examples/two-floors.toml) is a complete example.

## `[simulator]`

| Key | Default | Meaning |
|---|---|---|
| `http_host` | `0.0.0.0` | Bind address of the web UI |
| `http_port` | `8080` | Web UI port |
| `tcp_host` | `0.0.0.0` | Bind address of the line sockets |
| `tcp_base_port` | `4000` | Line *n* listens on `tcp_base_port + n` unless it sets `tcp_port` |
| `tick_hz` | `20` | Motion update rate |
| `time_scale` | `1.0` | Simulated seconds per real second, 0.1 to 50 (see [Simulation speed](#simulation-speed-time_scale)) |
| `pty_base` | none | Prefix for virtual serial ports of lines added at runtime |

## Simulation speed (`time_scale`)

`time_scale` is how many simulated seconds pass per real second. At `1` the drives move in
real time. At `10` a 48 s venetian run takes 4.8 s, which helps when you watch long runs or
the thermal protection. Allowed range: **0.1 to 50**, checked the same way everywhere.

| Where | How | Notes |
|---|---|---|
| Command line | `smisim run --time-scale 10` | Overrides the value from `--config` |
| Config file | `[simulator]` `time_scale = 10` | |
| HTTP API | `POST /api/settings` with `{"time_scale": 10}` | Answers `{"time_scale": 10}`; out-of-range values give status 400. The current value is in `GET /api/state` → `time_scale`. |
| Web UI | *Speed* list in the top bar (1×, 2×, 5×, 10×, 25×) | Takes effect at once. A value set elsewhere (e.g. 4×) is added to the list. |

What it speeds up: everything the drives do over time, which is travel, slat turning,
angle steps, the reversal pause, calibration runs, heating and cooling of the thermal
protection, and the identify blink.

What it does **not** change: the wire. Telegram bytes are still paced at 2400 baud, the
answer delay (`response_delay_ms`) and the `slow` fault (400 ms) stay the same, and so do
your controller's timeouts and the test-lab windows (3 s retry, 15 s offline, 20 s thermal)
and the bus-load meter. At a high scale, a controller that polls positions therefore sees
bigger jumps between two reads.

The in-process Python API has no `time_scale`: you choose the `dt` you pass to
`Bus.update(dt)`.

## `[[bus]]` (one per SMI line)

| Key | Default | Meaning |
|---|---|---|
| `name` | `"SMI line"` | Display name |
| `variant` | `"SMI"` | `"SMI"` (230 V AC) or `"SMI LoVo"` (24 V DC). Informational. |
| `tcp_port` | base + index | Raw TCP socket |
| `pty_link` | none | Path of a symlink to a new pseudo terminal, e.g. `/tmp/smi0` |
| `serial_port` | none | Physical serial port, e.g. `/dev/ttyUSB0` or `COM3` |
| `serial_baud` | `2400` | Baud rate of the physical port |
| `shared_bus` | `false` | The serial port is a transceiver on a real SMI line |
| `echo` | `false` | Echo the master's bytes |
| `response_delay_ms` | `8` | Answer turnaround |
| `realtime` | `true` | Pace answers at 2400 baud |
| `max_drives` | `16` | Limit for drives added at runtime |

## `[[bus.motor]]` (one per drive)

| Key | Default | Meaning |
|---|---|---|
| `name` | `""` | Display name |
| `address` | `0` | Slave address 0 to 15 |
| `manufacturer` | `1` | Manufacturer code 0 to 15 |
| `key_id` | derived | 32-bit slave ID, hex string such as `"1A2B3C4D"` |
| `kind` | `"roller"` | `roller`, `venetian`, `screen` or `awning` |
| `drive_type` | `1` | Reported with the IDENT query |
| `travel_time_s` | by kind | Full downward travel time |
| `up_speed_factor` | `0.9` | Upward speed relative to downward |
| `shaft_degrees` | by kind | Motor shaft rotation over the full travel. Used for angle steps. |
| `tilt_degrees` | `270` | Venetian slat turning range in shaft degrees |
| `slat_min_deg` | `0` | Physical slat angle at 0 % slat position (0 = horizontal, + = outer edge down) |
| `slat_max_deg` | `85` | Physical slat angle at 100 % (closed). Use `-80` / `80` for 180° blinds. |
| `reversal_pause_s` | `0.3` | Dead time when the direction is reversed while running |
| `tilt_in_position` | `false` | Venetian only, **provisional**: the reported position counts slat turning as drive-shaft rotation, so angle steps move it ([protocol.md §2.7](protocol.md#27-position-scale-of-venetian-drives-optional-provisional)) |
| `pos1`, `pos2` | `49152`, `58982` | Stored intermediate positions (0 = top, 65535 = bottom) |
| `thermal_limit_s` | `240` | Accumulated run time until thermal protection trips |
| `cooldown_s` | `900` | Time to cool down completely |
| `start_position` | `0` | Position at start |
