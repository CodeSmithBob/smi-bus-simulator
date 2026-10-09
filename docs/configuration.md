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
| `time_scale` | `1.0` | Motor speed multiplier |
| `pty_base` | none | Prefix for virtual serial ports of lines added at runtime |

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
| `pos1`, `pos2` | `49152`, `58982` | Stored intermediate positions (0 = top, 65535 = bottom) |
| `thermal_limit_s` | `240` | Accumulated run time until thermal protection trips |
| `cooldown_s` | `900` | Time to cool down completely |
| `start_position` | `0` | Position at start |
