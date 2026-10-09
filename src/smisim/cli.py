"""Command line: ``smisim run`` (simulator), ``smisim send`` (master), ``smisim decode``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys

from . import __version__
from .config import KIND_CHOICES, SimConfig, check_time_scale, generate, load
from .protocol.frames import decode_any, parse_hex


def _run_parser(sub) -> None:
    p = sub.add_parser("run", help="start the simulator with its web UI")
    p.add_argument("-c", "--config", help="TOML configuration file")
    p.add_argument("--buses", type=int, default=1, help="number of SMI lines (default 1)")
    p.add_argument("--motors", type=int, default=16, help="drives per line (default 16)")
    p.add_argument(
        "--kind",
        default="mixed",
        choices=KIND_CHOICES,
        help="blind type of generated drives",
    )
    p.add_argument("--manufacturer", type=int, default=1, help="manufacturer code (1-15)")
    p.add_argument("--factory-new", action="store_true", help="all drives on address 0")
    p.add_argument("--host", default="0.0.0.0", help="bind address for HTTP and TCP")
    p.add_argument("--http-port", type=int, default=8080)
    p.add_argument("--tcp-base-port", type=int, default=4000, help="line N listens on base+N")
    p.add_argument("--pty", metavar="PREFIX", help="create virtual serial ports PREFIX0, PREFIX1..")
    p.add_argument("--serial", metavar="DEVICE", help="attach line 1 to a physical serial port")
    p.add_argument("--shared-bus", action="store_true", help="serial port is a real SMI bus")
    p.add_argument("--echo", action="store_true", help="echo master bytes like many interfaces")
    p.add_argument("--no-realtime", action="store_true", help="answer instantly (no 2400 baud)")
    p.add_argument(
        "--time-scale",
        type=float,
        default=None,
        help="simulated seconds per real second, 0.1 to 50 (default 1; overrides the config file)",
    )
    p.add_argument("--no-web", action="store_true", help="do not start the web UI")
    p.add_argument("-v", "--verbose", action="store_true")


def _send_parser(sub) -> None:
    p = sub.add_parser("send", help="act as an SMI master and send one telegram")
    link = p.add_mutually_exclusive_group(required=True)
    link.add_argument("--tcp", metavar="HOST:PORT", help="e.g. localhost:4000")
    link.add_argument("--serial", metavar="DEVICE", help="e.g. /dev/ttyUSB0 or /tmp/smi0")
    p.add_argument("--echo", action="store_true", help="interface echoes sent bytes")
    p.add_argument("--timeout", type=float, default=0.3)
    target = p.add_mutually_exclusive_group()
    target.add_argument("-a", "--address", type=int, help="slave address 0-15")
    target.add_argument("-g", "--group", help="comma separated slave addresses, e.g. 0,1,5")
    target.add_argument("-k", "--key-id", help="32-bit slave ID in hex")
    target.add_argument("-b", "--broadcast", action="store_true")
    p.add_argument("-m", "--manufacturer", type=int, default=0, help="manufacturer code filter")
    p.add_argument(
        "action",
        help="up | down | stop | pos1 | pos2 | goto | step-up | step-down | store1 | store2 | "
        "position | read-pos1 | read-pos2 | read-address | ident | key-id | angle | status | "
        "diag | identify | write-address | discover | raw",
    )
    p.add_argument("value", nargs="?", help="percent/position/angle/new address/hex bytes")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smisim", description="SMI bus simulator")
    parser.add_argument("--version", action="version", version=f"smisim {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    _run_parser(sub)
    _send_parser(sub)
    d = sub.add_parser("decode", help="decode a telegram given in hex")
    d.add_argument("hex", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)

    if args.cmd == "decode":
        print(json.dumps(decode_any(parse_hex(" ".join(args.hex))), indent=2))
        return 0
    if args.cmd == "send":
        return asyncio.run(_send(args))
    return _run(args)


# --- run -------------------------------------------------------------------------


def _run(args) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = build_config(args)
    try:
        asyncio.run(_serve(cfg, web=not args.no_web))
    except KeyboardInterrupt:
        pass
    return 0


def build_config(args) -> SimConfig:
    """Turn ``smisim run`` arguments (and an optional config file) into a SimConfig."""
    if args.config:
        cfg = load(args.config)
    else:
        cfg = SimConfig(
            http_host=args.host,
            http_port=args.http_port,
            tcp_host=args.host,
            tcp_base_port=args.tcp_base_port,
            pty_base=args.pty,
            time_scale=1.0 if args.time_scale is None else args.time_scale,
        )
        cfg.buses = generate(
            buses=args.buses,
            motors_per_bus=args.motors,
            kind=args.kind,
            factory_new=args.factory_new,
            tcp_base_port=args.tcp_base_port,
            pty_base=args.pty,
            serial_port=args.serial,
            manufacturer=args.manufacturer,
        )
    if args.time_scale is not None:
        cfg.time_scale = args.time_scale
    try:
        cfg.time_scale = check_time_scale(cfg.time_scale)
    except ValueError as exc:
        raise SystemExit(f"smisim: {exc}") from None
    for b in cfg.buses:
        b.settings.echo = b.settings.echo or args.echo
        b.settings.realtime = b.settings.realtime and not args.no_realtime
        if args.serial and b is cfg.buses[0]:
            b.settings.serial_port = args.serial
            b.settings.shared_bus = b.settings.shared_bus or args.shared_bus
    return cfg


async def _serve(cfg: SimConfig, web: bool) -> None:
    from .simulator import Simulator

    sim = Simulator(cfg)
    await sim.start()
    runner = None
    if web:
        from .web.server import start_web

        runner = await start_web(sim, cfg.http_host, cfg.http_port)
    print(_banner(sim, cfg, web), flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop.wait()
    finally:
        if runner is not None:
            await runner.cleanup()
        await sim.stop()


def _banner(sim, cfg: SimConfig, web: bool) -> str:
    lines = ["", f"SMI bus simulator {__version__}"]
    if web:
        host = "localhost" if cfg.http_host in ("0.0.0.0", "::") else cfg.http_host
        lines.append(f"  Web UI      http://{host}:{cfg.http_port}/")
    for bus in sim.buses:
        eps = ", ".join(e["endpoint"] for e in bus.endpoints) or "no external endpoint"
        lines.append(f"  Line {bus.index + 1:<2}    {len(bus.motors):>2} drives  {eps}")
    lines.append("  Press Ctrl+C to stop.")
    return "\n".join(lines)


# --- send ------------------------------------------------------------------------


async def _send(args) -> int:
    from .client import SerialLink, SmiMaster, TcpLink
    from .protocol.constants import Command, DiagCode, QueryCode
    from .protocol.frames import Addressing, MasterTelegram

    if args.tcp:
        host, _, port = args.tcp.rpartition(":")
        link = TcpLink(host or "localhost", int(port))
    else:
        link = SerialLink(args.serial)

    if args.group:
        members = [int(a) for a in args.group.split(",")]
        addressing = Addressing.to_slaves(members, args.manufacturer)
    elif args.key_id:
        addressing = Addressing.to_key_id(int(args.key_id, 16), args.manufacturer)
    elif args.broadcast or args.address is None:
        addressing = Addressing.to_manufacturer(args.manufacturer)
    else:
        addressing = Addressing.to_slave(args.address)

    def pos(value: str | None) -> int:
        if value is None:
            raise SystemExit("this action needs a value")
        if value.endswith("%"):
            return round(float(value[:-1]) * 655.35)
        return int(value, 0)

    action = args.action.lower()
    commands = {"up": Command.UP, "down": Command.DOWN, "stop": Command.STOP,
                "pos1": Command.POS1, "pos2": Command.POS2}  # fmt: skip
    queries = {"position": QueryCode.POSITION, "read-pos1": QueryCode.POS1,
               "read-pos2": QueryCode.POS2, "read-address": QueryCode.ADDRESS,
               "ident": QueryCode.IDENT, "key-id": QueryCode.KEY_ID, "angle": QueryCode.ANGLE,
               "status": QueryCode.STATUS_BITS}  # fmt: skip

    master = SmiMaster(link, echo=args.echo, timeout=args.timeout, retries=0)
    master.log = print
    async with master:
        if action == "raw":
            raw = parse_hex(args.value or "")
            await link.write(raw)
            data = await link.read(32, args.timeout + 0.1)
            print(f"-> {raw.hex(' ').upper()}\n<- {data.hex(' ').upper() or '(nothing)'}")
            return 0
        if action == "discover":
            found = await master.discover_and_address(args.manufacturer or 0)
            for key_id, address in found:
                print(f"key ID {key_id:08X} -> address {address}")
            print(f"{len(found)} drive(s) addressed")
            return 0
        if action in commands:
            tel = MasterTelegram.command(addressing, commands[action])
        elif action == "goto":
            tel = MasterTelegram.command(addressing, Command.GOTO, position=pos(args.value))
        elif action in ("store1", "store2"):
            code = Command.POS1 if action == "store1" else Command.POS2
            tel = MasterTelegram.command(addressing, code, position=pos(args.value))
        elif action in ("step-up", "step-down"):
            code = Command.UP if action == "step-up" else Command.DOWN
            tel = MasterTelegram.command(addressing, code, angle_deg=int(args.value or 10))
        elif action in queries:
            tel = MasterTelegram.query(addressing, queries[action])
        elif action == "diag":
            tel = MasterTelegram.diag(addressing, DiagCode.STATUS)
        elif action == "identify":
            tel = MasterTelegram.diag(addressing, DiagCode.IDENTIFY)
        elif action == "write-address":
            tel = MasterTelegram.diag(addressing, DiagCode.WRITE_ADDRESS, bytes([int(args.value)]))
        else:
            print(f"unknown action {action!r}", file=sys.stderr)
            return 2
        response = await master.transact(tel)
        return 0 if response.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
