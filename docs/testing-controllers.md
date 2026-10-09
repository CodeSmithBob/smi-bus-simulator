# Testing an SMI controller

Use the simulator to develop and pre-test an SMI master (gateway, KNX/BACnet/Modbus
actuator, PLC program, home-automation integration) before you go to a test house or an
official certification.

> The **official SMI certification** is run by SMI Standard Motor Interface e.V. The
> simulator's test lab is an unofficial pre-check and does not replace it.

## Sizing: how many drives?

| Building block | Drives | How to simulate |
|---|---|---|
| One SMI line (one master channel) | up to 16 | preset **16** or `smisim run --motors 16` |
| Two-channel controller | 32 | preset **32** or `--buses 2` |
| Four-channel controller | 64 | preset **64** or `--buses 4` |
| Bigger | 16 per line | `--buses N`, one TCP port per line |

Run your controller against the **worst case**: a full line of 16 drives, all addressed,
while it polls positions. At 2400 baud, one position read (3 bytes out, 5 bytes in) takes
roughly 45 ms including turnaround. Polling 16 drives therefore takes about 0.7 s per
round. The bus utilisation meter shows how much headroom you have.

## Commissioning practice

Start with `--factory-new` (or the preset *16 factory-new drives*). All drives sit on
address 0, as they are delivered. Your controller has to:

1. notice the address conflict (position reads come back garbled);
2. find the drives by key ID (search procedure);
3. give each drive its own address.

Compare your result with the reference implementation:
*Master tab → Discover & address drives*, or `smisim send --tcp localhost:4000 discover`.

## Venetian blinds: lift and tilt

A venetian drive has one motor for two movements. The simulator follows the behaviour
that KNX, Home Assistant and Shelly users describe for real external venetian blinds
(Raffstore):

1. Every run starts by **turning the slats**: closed for a downward run, open for an
   upward run. Only then does the blind travel. After *DOWN* and *STOP*, the slats are
   therefore closed, not at the angle they had before.
2. **Angle steps** (`UP`/`DOWN` with angle data, 2° of motor shaft per unit) at a fixed
   height only turn the slats. The default turning range is 270° of shaft rotation
   (`tilt_degrees`).
3. **Slat position** is reported KNX style: 0 % = open (turned up), 100 % = closed.
   The physical angle range is configurable (`slat_min_deg`, `slat_max_deg`). Blinds with
   a 180° range are horizontal at 50 %.
4. To **keep the slat angle after a move**, the master has to restore it. In SMI you send
   `GOTO` with position *and* angle data in one telegram; the drive moves, then turns the
   slats. The Drive tab does this when "Keep the slat angle" is ticked (Shelly calls this
   "retain slat position").
5. Reversing while running costs a **dead time** (`reversal_pause_s`, 0.3 s by default).
   AC motors need at least about 300 ms before they change direction.

Use *View → Front + section* to watch both movements at once.

## Calibration and end positions

Drives can lose their end positions (fault **limits_lost**). Such a drive refuses
positioning commands (`GOTO`, `POS1`, `POS2`) with NACK until it has been calibrated.
UP/DOWN still work. *Calibrate* in the Drive tab runs the teach cycle: up, down, up.
Check that your controller reports this state clearly instead of retrying forever.

## The test lab

1. Connect your controller to one or more lines.
2. Open *Test lab* and click **Start session**. Untick "count telegrams from this web UI"
   if only your controller should count.
3. Let the controller run its normal job: commission, move, poll, diagnose.
4. **Arm** the fault checks one at a time:
   * *Retry after a lost answer*: the next answer of a random drive is dropped.
   * *Retry after a checksum error*: the next answer is corrupted.
   * *No flooding when a drive is offline*: one drive disappears for 15 s.
   * *Fault detection*: one drive trips its thermal protection.
5. Download the JSON report.

| Check | Passes when |
|---|---|
| Line population | 1 to 16 drives per line, no duplicate addresses |
| Every drive commanded | each drive got at least one movement command |
| Every position read back | each drive answered a position query |
| Group / broadcast used | one telegram moved two or more drives |
| Diagnosis used | a diagnosis telegram was sent |
| Retry after lost answer / checksum error | the identical telegram was repeated within 3 s |
| No flooding | at most 1 telegram per second to an offline drive |
| Fault detection | a status read covering the tripped drive within 20 s |
| Bus load | peak 10 s utilisation below 70 % |

## Fault injection reference

| Fault | Effect |
|---|---|
| `offline` | no answer and no movement (power loss, broken I+/I-) |
| `no_response` | moves, but never answers |
| `drop_next` | the next answer is lost (one-shot) |
| `bad_checksum` | every answer has a wrong checksum |
| `corrupt_next` | the next answer has a wrong checksum (one-shot) |
| `nack` | every telegram is answered with NACK |
| `slow` | answers come 400 ms late |
| `obstacle` | the next downward run stops part-way with an obstacle error |
| `blocked` | the blind cannot move (error) |
| `overheat` | thermal protection trips now. The drive refuses movement until it has cooled to 50 %. |
| `limits_lost` | end positions are lost; positioning is refused until calibration |

Thermal protection also trips on its own after `thermal_limit_s` (default 240 s, typical
"S2 4 min" rating) of accumulated run time. The drive then cools down over `cooldown_s`.

## Automating it

Everything in the UI is also available over HTTP, so CI pipelines can drive it:

```bash
curl -X POST localhost:8080/api/presets/32
curl -X POST localhost:8080/api/testlab/start -H 'content-type: application/json' -d '{"include_ui": false}'
# ... run your controller ...
curl -X POST localhost:8080/api/testlab/arm/retry_timeout
sleep 10
curl localhost:8080/api/testlab/report > report.json
```

See [api.md](api.md) for all endpoints.
