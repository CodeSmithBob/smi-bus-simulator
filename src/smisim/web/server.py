"""HTTP + WebSocket API and the static visualization."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

from ..protocol.frames import decode_any, parse_hex
from ..simulator import Simulator

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
PUSH_INTERVAL_S = 0.1

SIM_KEY = web.AppKey("sim", Simulator)


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, default=str))


def _error(exc: Exception, status: int = 400) -> web.Response:
    return _json({"error": str(exc)}, status)


async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text=f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return data


def _guard(handler):
    async def wrapper(request: web.Request) -> web.StreamResponse:
        try:
            return await handler(request, request.app[SIM_KEY])
        except web.HTTPException:
            raise
        except KeyError as exc:
            return _error(KeyError(f"not found: {exc}"), 404)
        except (ValueError, TypeError) as exc:
            return _error(exc)

    return wrapper


# --- handlers --------------------------------------------------------------------


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


@_guard
async def get_state(request, sim: Simulator):
    sim.refresh_endpoints()
    return _json(sim.state())


@_guard
async def get_meta(request, sim: Simulator):
    return _json(sim.meta())


@_guard
async def get_traffic(request, sim: Simulator):
    since = int(request.query.get("since", 0))
    entries = [e.to_dict() for b in sim.buses for e in b.traffic if e.seq > since]
    entries.sort(key=lambda e: e["seq"])
    return _json(entries[-2000:])


@_guard
async def add_bus(request, sim: Simulator):
    body = await _body(request)
    bus = await sim.add_bus(body.get("name"), int(body.get("motors", 0)), body.get("kind", "mixed"))
    return _json({"index": bus.index})


@_guard
async def remove_bus(request, sim: Simulator):
    await sim.remove_last_bus()
    return _json({"ok": True})


@_guard
async def patch_bus(request, sim: Simulator):
    bus = sim.bus(int(request.match_info["bus"]))
    body = await _body(request)
    s = bus.settings
    if "name" in body:
        s.name = str(body["name"])[:60]
    if "variant" in body:
        if body["variant"] not in ("SMI", "SMI LoVo"):
            raise ValueError("variant must be 'SMI' or 'SMI LoVo'")
        s.variant = body["variant"]
    for key in ("echo", "realtime"):
        if key in body:
            setattr(s, key, bool(body[key]))
    if "response_delay_ms" in body:
        delay = float(body["response_delay_ms"])
        if not 0 <= delay <= 1000:
            raise ValueError("response delay must be 0..1000 ms")
        s.response_delay_ms = delay
    return _json(s.to_dict())


@_guard
async def add_motor(request, sim: Simulator):
    motor = sim.add_motor(int(request.match_info["bus"]), await _body(request))
    return _json(motor.to_state())


@_guard
async def patch_motor(request, sim: Simulator):
    sim.update_motor(int(request.match_info["uid"]), await _body(request))
    return _json({"ok": True})


@_guard
async def motor_action(request, sim: Simulator):
    body = await _body(request)
    sim.motor_action(int(request.match_info["uid"]), request.match_info["action"], body)
    return _json({"ok": True})


@_guard
async def send(request, sim: Simulator):
    result = await sim.master_send(int(request.match_info["bus"]), await _body(request))
    return _json(result)


@_guard
async def encode(request, sim: Simulator):
    return _json(sim.encode(await _body(request)))


@_guard
async def discover(request, sim: Simulator):
    body = await _body(request)
    sim.start_discovery(int(request.match_info["bus"]), int(body.get("manufacturer", 0)))
    return _json({"started": True})


@_guard
async def decode(request, sim: Simulator):
    body = await _body(request)
    return _json(decode_any(parse_hex(str(body.get("hex", "")))))


@_guard
async def preset(request, sim: Simulator):
    await sim.load_preset(request.match_info["name"])
    return _json({"ok": True})


@_guard
async def settings(request, sim: Simulator):
    body = await _body(request)
    if "time_scale" in body:
        scale = float(body["time_scale"])
        if not 0.1 <= scale <= 50:
            raise ValueError("time scale must be 0.1..50")
        sim.config.time_scale = scale
    return _json({"time_scale": sim.config.time_scale})


@_guard
async def testlab_start(request, sim: Simulator):
    body = await _body(request)
    sim.testlab.start(sim.buses, include_ui=bool(body.get("include_ui", True)))
    return _json(sim.testlab.to_state())


@_guard
async def testlab_stop(request, sim: Simulator):
    sim.testlab.stop()
    return _json(sim.testlab.to_state())


@_guard
async def testlab_arm(request, sim: Simulator):
    what = sim.testlab.arm(request.match_info["check"])
    return _json({"armed": what})


@_guard
async def testlab_report(request, sim: Simulator):
    resp = _json(sim.testlab.report())
    resp.headers["Content-Disposition"] = 'attachment; filename="smi-testlab-report.json"'
    return resp


async def websocket(request: web.Request) -> web.WebSocketResponse:
    sim: Simulator = request.app[SIM_KEY]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    last_seq = 0
    since = request.query.get("since")
    if since is not None:
        last_seq = int(since)

    async def pusher() -> None:
        nonlocal last_seq
        while not ws.closed:
            new = [e for b in sim.buses for e in b.traffic if e.seq > last_seq]
            new.sort(key=lambda e: e.seq)
            if new:
                last_seq = new[-1].seq
            traffic = [e.to_dict() for e in new[-500:]]
            await ws.send_json({"type": "state", "state": sim.state(), "traffic": traffic})
            await asyncio.sleep(PUSH_INTERVAL_S)

    task = asyncio.get_running_loop().create_task(pusher())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, ConnectionError, RuntimeError):
            pass
    return ws


# --- app -------------------------------------------------------------------------


def make_app(sim: Simulator) -> web.Application:
    app = web.Application()
    app[SIM_KEY] = sim
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC)
    app.router.add_get("/ws", websocket)
    app.router.add_get("/api/state", get_state)
    app.router.add_get("/api/meta", get_meta)
    app.router.add_get("/api/traffic", get_traffic)
    app.router.add_post("/api/decode", decode)
    app.router.add_post("/api/encode", encode)
    app.router.add_post("/api/settings", settings)
    app.router.add_post("/api/presets/{name}", preset)
    app.router.add_post("/api/buses", add_bus)
    app.router.add_delete("/api/buses/last", remove_bus)
    app.router.add_patch("/api/buses/{bus}", patch_bus)
    app.router.add_post("/api/buses/{bus}/motors", add_motor)
    app.router.add_post("/api/buses/{bus}/send", send)
    app.router.add_post("/api/buses/{bus}/discover", discover)
    app.router.add_patch("/api/motors/{uid}", patch_motor)
    app.router.add_post("/api/motors/{uid}/{action}", motor_action)
    app.router.add_post("/api/testlab/start", testlab_start)
    app.router.add_post("/api/testlab/stop", testlab_stop)
    app.router.add_post("/api/testlab/arm/{check}", testlab_arm)
    app.router.add_get("/api/testlab/report", testlab_report)
    return app


async def start_web(sim: Simulator, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(make_app(sim), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
