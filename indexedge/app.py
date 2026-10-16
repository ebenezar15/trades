"""FastAPI application factory: REST API, WebSocket fan-out of bus events, and the built PWA."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from indexedge.api.auth import AuthStore, auth_middleware, load_or_create_token, request_authorised
from indexedge.api.auth_routes import router as auth_router
from indexedge.api.routes import router
from indexedge.api.serialize import jsonable
from indexedge.core import bus as topics

log = logging.getLogger(__name__)
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
WS_TOPICS = ("agent", topics.TICK, topics.CANDLE, topics.SIGNAL, topics.CONTEXT, topics.NEWS, topics.ORDER, topics.TRADE,
             topics.HEALTH, topics.ALERT)


def ws_payload(rt: Any, topic: str, payload: Any) -> dict | None:
    """Convert an internal bus payload into the WebSocket message contract."""
    if topic == topics.CANDLE:
        bar = payload.get("bar") or {}
        return {"underlying": payload.get("underlying"), "tf": payload.get("tf", "5m"), **bar}
    if topic == topics.CONTEXT:
        from indexedge.context.engine import snapshot_to_dict

        return snapshot_to_dict(payload)
    if topic == topics.NEWS:
        return {"id": payload.id, "ts": payload.ts, "source": payload.source, "title": payload.title,
                "url": payload.url, "sentiment": payload.sentiment, "relevance": payload.relevance,
                "impact": payload.impact}
    if topic == topics.SIGNAL and not isinstance(payload, dict):
        return rt.signals.to_json(payload)
    if topic == topics.ORDER and not isinstance(payload, dict):
        r = payload.request
        return {"broker_id": payload.broker_id, "symbol": r.instrument.symbol, "side": r.side.value,
                "qty": r.quantity, "type": r.order_type.value, "price": r.price, "trigger": r.trigger_price,
                "status": payload.status.value, "filled": payload.filled_qty, "avg_price": payload.avg_price,
                "message": payload.message}
    if topic == topics.TRADE and hasattr(payload, "to_dict"):
        return payload.to_dict()
    if topic == topics.ALERT and isinstance(payload, dict):
        return {"level": payload.get("level", "info"), "title": payload.get("title", ""),
                "message": payload.get("message") or payload.get("body") or ""}
    return payload


def create_app(runtime: Any = None, start_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from indexedge.runtime import Runtime

        rt = runtime or Runtime(start_background=start_background)
        await rt.start()
        app.state.rt = rt
        app.state.access_token = load_or_create_token(rt.data_dir)
        app.state.auth = AuthStore(rt.data_dir)
        app.state.sockets = set()

        async def reload() -> None:
            """Rebuild the runtime in place (e.g. after connecting Angel One). Clients reconnect automatically."""
            old = app.state.rt
            start_bg = old.start_background
            await old.stop()
            from indexedge.runtime import Runtime

            new = Runtime(start_background=start_bg)
            await new.start()
            app.state.rt = new
            for sock in list(app.state.sockets):
                try:
                    await sock.close(code=1012)
                except Exception:  # noqa: BLE001
                    pass

        app.state.reload = reload
        try:
            yield
        finally:
            await app.state.rt.stop()

    app = FastAPI(title="IndexEdge", version="1.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.middleware("http")(auth_middleware)
    app.include_router(auth_router)
    app.include_router(router)

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        log.exception("API error on %s", request.url.path)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if not request_authorised(socket, app.state.auth, app.state.access_token):
            await socket.close(code=4401)
            return
        await socket.accept()
        rt = app.state.rt
        app.state.sockets.add(socket)
        queues = {t: rt.bus.queue(t, maxsize=500) for t in WS_TOPICS}
        merged: asyncio.Queue = asyncio.Queue(maxsize=2000)

        async def pump(topic: str, q: asyncio.Queue) -> None:
            while True:
                item = await q.get()
                if merged.full():
                    merged.get_nowait()
                merged.put_nowait((topic, item))

        pumps = [asyncio.create_task(pump(t, q)) for t, q in queues.items()]
        try:
            while True:
                topic, item = await merged.get()
                try:
                    data = ws_payload(rt, topic, item)
                except Exception:
                    log.exception("ws serialise failed for %s", topic)
                    continue
                if data is None:
                    continue
                await socket.send_json({"type": topic, "data": jsonable(data)})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for p in pumps:
                p.cancel()
            for t, q in queues.items():
                rt.bus.drop_queue(t, q)
            app.state.sockets.discard(socket)

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")
        if (DIST / "icons").exists():
            app.mount("/icons", StaticFiles(directory=DIST / "icons"), name="icons")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            f = (DIST / path).resolve()
            if path and f.is_file() and DIST in f.parents:
                return FileResponse(f)
            return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return JSONResponse({"message": "IndexEdge API is running. Frontend not built: run "
                                            "`npm ci && npm run build` in frontend/."})

    return app
