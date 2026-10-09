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
| `slack` | `0` | Gear backlash in raw position units: dead zone after every change of direction ([Drive mechanics](#drive-mechanics-and-profiles)) |
| `top_offset` | `0` | Raw units at the top end where the shaft turns but the rail does not move yet |
| `bottom_offset` | `0` | The same at the bottom end |
| `profile` | `""` | Build the drive from a [profile](#drive-mechanics-and-profiles); keys given for the drive override the profile's values |
| `pos1`, `pos2` | `49152`, `58982` | Stored intermediate positions (0 = top, 65535 = bottom) |
| `thermal_limit_s` | `240` | Accumulated run time until thermal protection trips |
| `cooldown_s` | `900` | Time to cool down completely |
| `start_position` | `0` | Position at start |

## Drive mechanics and profiles

By default every drive is ideal: the rail moves over exactly the drive's raw scale, and
it starts moving the moment the shaft turns. Real drives need calibration, and three
optional per-drive settings model why. All three are in **raw position units**, where
65535 is the drive's full travel:

| Setting | What happens |
|---|---|
| `top_offset`, `bottom_offset` | The end positions are set a bit beyond the physical travel. Over the first `top_offset` units below the top end, and the last `bottom_offset` units above the bottom end, the shaft turns but the rail does not move. |
| `slack` | Gear backlash. After every change of direction the shaft turns `slack` units before anything (slats or rail) moves. A small angle step right after a reversal can disappear in it completely. After a downward run the rail is `slack` units behind the drive's count, so a full downward run stops short unless `bottom_offset` ≥ `slack`. |
| `tilt_degrees` | Already per drive: the shaft turn the slat ladder takes (venetian). Drives on one line can differ. |

How it is modelled: the drive counts its own shaft rotation. That count is what it
reports, and what GOTO, POS1/POS2 and angle steps act on. The blind follows through a
chain: gear (play `slack`), then the slat ladder (play `tilt_degrees`: slats turn first,
then the rail moves), then the end offsets. The facade, the drive details and
`GET /api/state` show the **physical** rail and slats (`position`, `percent`, `tilt`,
`slat_percent`). The drive's own idea of its rail is `drive_position`, and the value it
sends over SMI is `reported_position`. For an ideal drive all three are the same. To set
the start state of a test, `set_position()` assumes the gear was last moved upwards.

Profiles bundle typical values, so a line can mix drives that behave differently. Use
`profile = "venetian-worn"` in a `[[bus.motor]]` entry (other keys override it),
`MotorConfig.from_profile("venetian-worn")` in Python, `smisim run --kind mixed-mechanics`
or the preset *16 drives with mixed mechanics*.

| Profile | `kind` | `tilt_degrees` | `slack` | `top_offset` | `bottom_offset` | Source |
|---|---|---|---|---|---|---|
| `venetian-tight` | venetian | `180` | `150` | `300` | `300` | **Assumption**: a new, well-adjusted drive |
| `venetian-worn` | venetian | `300` | `1500` | `1200` | `2500` | **Assumption**: an older drive with play and generous end positions |
| `roller-offset` | roller | `270` | `400` | `1500` | `3000` | **Assumption**: a roller shutter whose end positions were set beyond the travel |

No public figures for gear backlash or end-position overrun of blind drives were found, so
every value above is an assumption. The effects themselves are reported publicly:
installers describe angle pulses after a direction change that move the slats less than
expected [9]; a motorised-blind patent describes slat ladders that are only
friction-coupled to the drive shaft and slip at the end of the turn [10]; and installers
report end positions that drift from the physical stops [11]. If you have measured values
for a real drive, please open an issue.

## Where the default timing values come from

Every default timing value either has a public source or is marked **assumption**. The
SMI specification itself is not public and gives no drive speeds. Change the values per
drive or per line to match the hardware you care about.

| Setting | Default | Source |
|---|---|---|
| `travel_time_s` (roller) | `24` | **Assumption.** Travel time depends on window height and winding diameter, so there is no single public figure. |
| `travel_time_s` (venetian) | `48` | **Assumption** (as above). |
| `travel_time_s` (screen) | `36` | **Assumption** (as above). |
| `travel_time_s` (awning) | `30` | **Assumption** (as above). |
| `shaft_degrees` (roller) | `5400` | **Assumption** (15 turns over the full travel). |
| `shaft_degrees` (venetian) | `9000` | **Assumption** (25 turns). |
| `shaft_degrees` (screen) | `6480` | **Assumption** (18 turns). |
| `shaft_degrees` (awning) | `4320` | **Assumption** (12 turns). |
| `up_speed_factor` | `0.9` | **Assumption**: the drive lifts the load upwards, so it is taken as 10 % slower. No public figure found. |
| `reversal_pause_s` | `0.3` | Teco wiki, "The control of venetian blinds and roller blinds": at least 300 ms pause when the direction of an AC blind motor is switched [1]. Gira's venetian blind actuator makes this switch-over time adjustable and refers to the motor maker [2]. |
| `tilt_degrees` | `270` | elero JA Comfort SMI operating manual: slat turning range 270° of drive shaft by default, adjustable 90° to 360° [3]. |
| `thermal_limit_s` | `240` | Tubular blind motors are commonly rated for short-time duty **S2 4 min** (4 minutes of continuous running, then cool down), e.g. the Becker R12-17-E01 listing [4] and a 17 rpm 20 Nm tubular motor rated for "4 minutes operation time" [5]. The simulator simplifies this to 240 s of accumulated run time. |
| `cooldown_s` | `900` | **Assumption.** S2 only says "until cooled down"; no public cooling time found. |
| `response_delay_ms` | `8` | **Assumption.** No public figure for the SMI answer turnaround. |
| `slack` | `0` | **Assumption**: an ideal drive, so nothing changes for existing setups. Real drives have some play (see [Drive mechanics](#drive-mechanics-and-profiles)). |
| `top_offset` | `0` | **Assumption** (ideal drive, as above). |
| `bottom_offset` | `0` | **Assumption** (ideal drive, as above). |
| `slow` fault delay | `400 ms` | **Assumption**, chosen to be longer than the default timeout of the bundled master (250 ms). |

Cross-check of the speeds: published drive speeds are about **17 rpm** for roller-shutter
tubular motors (elero VariEco S5-K, Becker R12-17) [4][6] and **23–26 rpm** for venetian
blind drives (elero: all venetian motors 26 rpm; Dunkermotoren D370 SMI: 23 rpm) [7][8].
The default `travel_time_s` and `shaft_degrees` together imply faster shafts: roller
5400° / 24 s = 37.5 rpm, venetian 9000° / 48 s = 31 rpm. If shaft speed matters for your
test (angle steps are measured in shaft degrees), set `shaft_degrees = rpm × 6 × travel_time_s`,
e.g. 17 rpm × 6 × 24 s = 2448 for a roller shutter.

Sources (found via web search, October 2026):

1. Teco wiki: <https://wiki.tecomat.cz/en/article/71-the-control-of-venetian-blinds-and-roller-blinds>
2. Gira four-channel venetian blind actuator, installation instructions: <https://partner.gira.com/data3/10201290.pdf>
3. elero JA Comfort SMI operating manual: <https://ffs-bauelemente.com/wp-content/uploads/2021/03/JA-Comfort-SMI-Bedienungsanleitung-Jalousieantriebe.pdf>
4. Becker R12-17-E01 tubular motor (retailer listing with the manufacturer data): <https://derrollladen.com/products/becker-rollladenantrieb-r12-17-e01-12nm>
5. Nu Style 20 Nm 17 rpm tubular motor: <https://shuttermasters.com.au/products/nu-style-tubular-motor-for-roller-shutter>
6. elero VariEco S-K: <https://www.elero.com/en/products/electrical-drives/varieco-s-k>
7. elero venetian blind motors: <https://www.elero.com/en/products/venetian-blind-motors>
8. Dunkermotoren D370 SMI: <https://www.directindustry.com/prod/dunkermotoren-gmbh/product-14411-474601.html>
9. KNX-User-Forum, "Probleme bei Lamellenverstellung Raff. und ABB JRA/S8.230.5.1": <https://knx-user-forum.de/forum/%C3%B6ffentlicher-bereich/knx-eib-forum/828414-probleme-bei-lamellenverstellung-raff-und-abb-jra-s8-230-5-1>
10. US 7,923,948 B2, "Method for adjusting the residual light gap between slats of a motorized venetian blind": <https://patents.google.com/patent/US7923948>
11. KNX-User-Forum, "Raffstore Endlage verstellt": <https://knx-user-forum.de/forum/%C3%B6ffentlicher-bereich/knx-eib-forum/knx-einsteiger/1755712-raffstore-endlage-verstellt>
