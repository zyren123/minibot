"""FastAPI application for Minibot WebUI + SDK server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .manager import AgentManager
from .models import ChatRequest, ConfigResponse, ConfigUpdate


def create_app(*, workdir: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Minibot Server", version="0.1.0")
    manager = AgentManager(workdir=Path(workdir).resolve() if workdir is not None else None)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config", response_model=ConfigResponse)
    async def get_config() -> Any:
        return manager.config_snapshot()

    @app.put("/api/config")
    async def put_config(update: ConfigUpdate) -> dict[str, str]:
        await manager.update_config(update.model_dump(exclude_unset=True))
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        sessions = manager.sessions.list_all()
        out: list[dict[str, Any]] = []
        for meta in sessions:
            out.append(
                {
                    "session_id": meta.session_id,
                    "path": str(meta.path),
                    "created_at": meta.created_at.isoformat(),
                    "modified_at": meta.modified_at.isoformat(),
                    "message_count": meta.message_count,
                    "preview": meta.preview,
                }
            )
        return out

    @app.post("/api/sessions")
    async def create_session() -> dict[str, str]:
        session_id = manager.sessions.create()
        return {"session_id": session_id}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        if not manager.sessions.exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "messages": manager.sessions.load(session_id)}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        ok = manager.sessions.delete(session_id)
        return {"deleted": ok}

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict[str, str]:
        bot = await manager.get_bot(session_id)
        bot.cancel()
        return {"status": "ok"}

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> Any:
        session_id = req.session_id or manager.sessions.create()
        bot = await manager.get_bot(session_id)
        lock = await manager.session_lock(session_id)
        async with lock:
            try:
                result = await bot.chat(req.prompt, session_id=session_id)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "session_id": result.session_id,
            "assistant_text": result.assistant_text,
            "messages": result.messages,
            "usage": result.usage,
        }

    @app.post("/api/stream")
    async def stream(req: ChatRequest, request: Request) -> EventSourceResponse:
        session_id = req.session_id or manager.sessions.create()
        bot = await manager.get_bot(session_id)
        lock = await manager.session_lock(session_id)

        async def _gen() -> AsyncIterator[dict[str, str]]:
            async with lock:
                if req.session_id is None:
                    yield {
                        "event": "system",
                        "data": json.dumps(
                            {"type": "system", "session_id": session_id, "message": "session_created"},
                            ensure_ascii=False,
                        ),
                    }
                try:
                    async for event in bot.stream(req.prompt, session_id=session_id):
                        if await request.is_disconnected():
                            bot.cancel()
                            break
                        payload = json.dumps(event, ensure_ascii=False)
                        yield {"event": event.get("type", "message"), "data": payload}
                except asyncio.CancelledError:
                    bot.cancel()
                    raise
                except Exception as exc:
                    yield {
                        "event": "system",
                        "data": json.dumps(
                            {"type": "system", "session_id": session_id, "message": str(exc)},
                            ensure_ascii=False,
                        ),
                    }

        return EventSourceResponse(_gen())

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    @app.exception_handler(HTTPException)
    async def _http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()

