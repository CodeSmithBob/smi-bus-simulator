# Connecting a controller

The simulator plays the **drives**. Your controller (a PC, a Raspberry Pi or another ARM
board, a PLC, a microcontroller) plays the **SMI master**. There are four ways to connect them.

```
 ┌──────────────┐  TCP / PTY / UART   ┌─────────────────────────────────────┐
 │ your SMI     │◄───────────────────►│ smisim                              │
 │ master       │  SMI telegrams      │  line 1: 16 virtual drives   ┐      │
 │ (PC, ARM,    │  2400 8N1           │  line 2: 16 virtual drives   ├─ web │
 │  PLC, MCU)   │                     │  ...                         ┘  UI  │
 └──────────────┘                     └─────────────────────────────────────┘
```

Every line gets its own endpoint, so a controller with 2 SMI channels connects to 2
endpoints, one per channel.

## 1. TCP socket (any OS, Docker)

Each line listens on `tcp_base_port + line index`, by default `4000`, `4001`, …
The stream carries the raw SMI bytes, without any framing.

* Software that can talk to a TCP socket: connect directly.
* Software that needs a serial port:
  * Linux/macOS: `socat pty,link=/tmp/smi0,raw,echo=0 tcp:localhost:4000`
  * Windows: com0com + hub4com (`--use-driver=tcp`), or any "serial over TCP" driver.
  * ser2net-style tools work the same way.

## 2. Virtual serial port (Linux/macOS)

```bash
smisim run --pty /tmp/smi        # creates /tmp/smi0, /tmp/smi1, ...
```

Open `/tmp/smi0` at 2400 8N1, like a USB adapter. Baud-rate settings on the PTY are
ignored, so any setting works.

## 3. Physical serial port, point to point

Use this to test firmware on a real board that has a UART-level SMI master, before you
connect it to an SMI transceiver.

```
 controller TX ──────► USB-UART RX ┐
 controller RX ◄────── USB-UART TX ├── PC running: smisim run --serial /dev/ttyUSB0
 controller GND ─────── USB-UART GND┘
```

Match the voltage levels (3.3 V and 5 V). Many SMI transceivers invert the signal. If
your firmware expects inverted levels, invert them in the firmware or with a
single-transistor inverter.

## 4. Attach virtual drives to a real SMI line (advanced)

```bash
smisim run --serial /dev/ttyUSB0 --shared-bus --motors 4
```

With an **SMI-to-UART transceiver** you can add virtual drives to a real line, next to
real drives and a real (certified) master, for example a KNX/SMI actuator. With
`shared_bus`, the simulator ignores its own echo and the other drives' answers.

Make sure:

* the virtual drives use addresses that no real drive on the line uses;
* the transceiver is suitable for the line. The community "Selbstbau Interface"
  (smiwiki) is a reference circuit: an optocoupler-isolated comparator at about 11.5 V and
  a 150 Ω load switched by the transmit line.

> ⚠️ **Mains voltage.** SMI 230 V drives share one cable with L/N/PE. The I+/I- pair needs
> basic insulation against mains, and it is not a SELV circuit. Only qualified electricians
> should work on installed 230 V wiring. Never connect a USB device to I+/I- without a
> suitable isolated interface. SMI LoVo (24 V) is the safer choice for a lab bench.

## 5. Docker

```bash
docker compose up                         # web UI on :8080, lines on :4000/:4001
docker run --rm -p 8080:8080 -p 4000-4003:4000-4003 smi-bus-simulator run --buses 4
docker run --rm --device /dev/ttyUSB0 -p 8080:8080 smi-bus-simulator run --serial /dev/ttyUSB0
```

The image runs as a non-root user in the `dialout` group. Your host may need
`--group-add $(stat -c %g /dev/ttyUSB0)` to give that user access to the device.
PTY endpoints exist only inside the container, so use TCP or a passed-through device.

## Echo, timing and realtime

| Setting | Default | Use |
|---|---|---|
| `echo` | `false` | Many hardware interfaces receive their own bytes. Turn this on if your driver expects them. |
| `response_delay_ms` | `8` | Turnaround between the end of the telegram and the answer. Raise it to test timeouts. |
| `realtime` | `true` | Answers are paced at 2400 baud (about 4.17 ms per byte). Turn it off for very fast CI tests. |

You can change all three at runtime with `PATCH /api/buses/{n}`.
