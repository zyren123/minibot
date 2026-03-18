"""FastAPI application for Minibot WebUI + SDK server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .manager import AgentManager
from .models import (
    BotConfigResponse,
    BotConfigUpdate,
    BotCreateRequest,
    BotMetaResponse,
    ChatRequest,
    ConfigResponse,
    ConfigUpdate,
    MCPServerInfo,
    SkillInfo,
)


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

    @app.get("/api/bots", response_model=list[BotMetaResponse])
    async def list_bots() -> list[dict[str, Any]]:
        return manager.list_bots()

    @app.post("/api/bots", response_model=BotMetaResponse)
    async def create_bot(req: BotCreateRequest) -> dict[str, Any]:
        return await manager.create_bot(name=req.name)

    @app.delete("/api/bots/{bot_id}")
    async def delete_bot(bot_id: str) -> dict[str, Any]:
        try:
            ok = await manager.delete_bot(bot_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": ok}

    @app.get("/api/bots/{bot_id}/config", response_model=BotConfigResponse)
    async def get_bot_config(bot_id: str) -> Any:
        return manager.bot_config_snapshot(bot_id)

    @app.put("/api/bots/{bot_id}/config")
    async def put_bot_config(bot_id: str, update: BotConfigUpdate) -> dict[str, str]:
        await manager.update_bot_config(bot_id, update.model_dump(exclude_unset=True))
        return {"status": "ok"}

    @app.get("/api/skills", response_model=list[SkillInfo])
    async def list_skills() -> Any:
        return manager.skills_snapshot()

    @app.get("/api/mcp/servers", response_model=list[MCPServerInfo])
    async def list_mcp_servers() -> Any:
        return manager.mcp_servers_snapshot()

    def _session_meta(bot_id: str) -> list[dict[str, Any]]:
        sessions = manager.sessions_for(bot_id).list_all()
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

    @app.get("/api/sessions")
    async def list_sessions_default() -> list[dict[str, Any]]:
        return _session_meta("default")

    @app.get("/api/bots/{bot_id}/sessions")
    async def list_sessions(bot_id: str) -> list[dict[str, Any]]:
        return _session_meta(bot_id)

    @app.post("/api/sessions")
    async def create_session() -> dict[str, str]:
        session_id = manager.sessions_for("default").create()
        return {"session_id": session_id}

    @app.post("/api/bots/{bot_id}/sessions")
    async def create_bot_session(bot_id: str) -> dict[str, str]:
        session_id = manager.sessions_for(bot_id).create()
        return {"session_id": session_id}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        if not manager.sessions_for("default").exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "messages": manager.sessions_for("default").load(session_id)}

    @app.get("/api/bots/{bot_id}/sessions/{session_id}")
    async def get_bot_session(bot_id: str, session_id: str) -> dict[str, Any]:
        if not manager.sessions_for(bot_id).exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "messages": manager.sessions_for(bot_id).load(session_id)}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        ok = await manager.delete_session("default", session_id)
        return {"deleted": ok}

    @app.delete("/api/bots/{bot_id}/sessions/{session_id}")
    async def delete_bot_session(bot_id: str, session_id: str) -> dict[str, Any]:
        ok = await manager.delete_session(bot_id, session_id)
        return {"deleted": ok}

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict[str, str]:
        bot = await manager.get_bot("default", session_id)
        bot.cancel()
        return {"status": "ok"}

    @app.post("/api/bots/{bot_id}/sessions/{session_id}/cancel")
    async def cancel_bot_session(bot_id: str, session_id: str) -> dict[str, str]:
        bot = await manager.get_bot(bot_id, session_id)
        bot.cancel()
        return {"status": "ok"}

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> Any:
        session_id = req.session_id or manager.sessions_for("default").create()
        bot = await manager.get_bot("default", session_id)
        lock = await manager.session_lock("default", session_id)
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

    @app.post("/api/bots/{bot_id}/chat")
    async def bot_chat(bot_id: str, req: ChatRequest) -> Any:
        session_id = req.session_id or manager.sessions_for(bot_id).create()
        bot = await manager.get_bot(bot_id, session_id)
        lock = await manager.session_lock(bot_id, session_id)
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
    async def stream(req: ChatRequest, request: Request) -> StreamingResponse:
        session_id = req.session_id or manager.sessions_for("default").create()
        bot = await manager.get_bot("default", session_id)
        lock = await manager.session_lock("default", session_id)

        def _frame(*, event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        async def _gen() -> AsyncIterator[str]:
            async with lock:
                if req.session_id is None:
                    yield _frame(
                        event="system",
                        data=json.dumps(
                            {"type": "system", "session_id": session_id, "message": "session_created"},
                            ensure_ascii=False,
                        ),
                    )
                try:
                    async for event in bot.stream(req.prompt, session_id=session_id):
                        if await request.is_disconnected():
                            bot.cancel()
                            break
                        payload = json.dumps(event, ensure_ascii=False)
                        yield _frame(event=event.get("type", "message"), data=payload)
                except asyncio.CancelledError:
                    bot.cancel()
                    raise
                except Exception as exc:
                    yield _frame(
                        event="system",
                        data=json.dumps(
                            {"type": "system", "session_id": session_id, "message": str(exc)},
                            ensure_ascii=False,
                        ),
                    )

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post("/api/bots/{bot_id}/stream")
    async def bot_stream(bot_id: str, req: ChatRequest, request: Request) -> StreamingResponse:
        session_id = req.session_id or manager.sessions_for(bot_id).create()
        bot = await manager.get_bot(bot_id, session_id)
        lock = await manager.session_lock(bot_id, session_id)

        def _frame(*, event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        async def _gen() -> AsyncIterator[str]:
            async with lock:
                if req.session_id is None:
                    yield _frame(
                        event="system",
                        data=json.dumps(
                            {"type": "system", "session_id": session_id, "message": "session_created"},
                            ensure_ascii=False,
                        ),
                    )
                try:
                    async for event in bot.stream(req.prompt, session_id=session_id):
                        if await request.is_disconnected():
                            bot.cancel()
                            break
                        payload = json.dumps(event, ensure_ascii=False)
                        yield _frame(event=event.get("type", "message"), data=payload)
                except asyncio.CancelledError:
                    bot.cancel()
                    raise
                except Exception as exc:
                    yield _frame(
                        event="system",
                        data=json.dumps(
                            {"type": "system", "session_id": session_id, "message": str(exc)},
                            ensure_ascii=False,
                        ),
                    )

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    @app.exception_handler(HTTPException)
    async def _http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
