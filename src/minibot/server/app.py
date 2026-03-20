"""FastAPI application for Minibot WebUI + SDK server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .manager import AgentManager
from .models import (
    AvailableModelResponse,
    BotConfigResponse,
    BotConfigUpdate,
    BotCreateRequest,
    BotMetaResponse,
    ChatRequest,
    ConfigResponse,
    ConfigUpdate,
    DashboardResponse,
    FetchedModelResponse,
    MCPServerInfo,
    MessageDeleteResponse,
    MessageRegenerateRequest,
    MessageRegenerateResponse,
    SessionMessagesResponse,
    SkillCreateRequest,
    SkillDeleteResponse,
    ModelCreateRequest,
    ModelDeleteRequest,
    ModelDeleteResponse,
    ModelResponse,
    ModelUpdateRequest,
    ProviderCreateRequest,
    ProviderResponse,
    ProviderUpdateRequest,
    SkillInfo,
    SubagentCandidateResponse,
)


def _resolve_static_dir(server_dir: Path) -> Path | None:
    candidates = [
        server_dir / "static",
        server_dir.parents[2] / "webui" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None


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

    @app.get("/api/dashboard", response_model=DashboardResponse)
    async def get_dashboard() -> Any:
        return manager.dashboard_snapshot()

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
        try:
            await manager.update_bot_config(bot_id, update.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok"}

    @app.get("/api/bots/{bot_id}/subagent-candidates", response_model=list[SubagentCandidateResponse])
    async def get_subagent_candidates(bot_id: str) -> Any:
        return manager.subagent_candidates(bot_id)

    @app.get("/api/skills", response_model=list[SkillInfo])
    async def list_skills() -> Any:
        return manager.skills_snapshot()

    @app.post("/api/skills", response_model=SkillInfo)
    async def create_skill(req: SkillCreateRequest) -> Any:
        try:
            return await manager.create_skill(req.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/skills/{scope}/{folder_name}", response_model=SkillDeleteResponse)
    async def delete_skill(scope: str, folder_name: str) -> Any:
        try:
            return await manager.delete_skill(scope=scope, folder_name=folder_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/mcp/servers", response_model=list[MCPServerInfo])
    async def list_mcp_servers() -> Any:
        return manager.mcp_servers_snapshot()

    @app.get("/api/models/available", response_model=list[AvailableModelResponse])
    async def list_available_models() -> Any:
        return manager.active_models_snapshot()

    @app.post("/api/providers", response_model=ProviderResponse)
    async def create_provider(req: ProviderCreateRequest) -> Any:
        return manager.create_provider(req.model_dump(exclude_unset=True))

    @app.put("/api/providers/{provider_id}", response_model=ProviderResponse)
    async def update_provider(provider_id: str, req: ProviderUpdateRequest) -> Any:
        try:
            return manager.update_provider(provider_id, req.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc

    @app.get("/api/providers/{provider_id}/models", response_model=list[ModelResponse])
    async def list_provider_models(provider_id: str) -> Any:
        return manager.list_provider_models(provider_id)

    @app.post("/api/providers/{provider_id}/fetch-models", response_model=list[FetchedModelResponse])
    async def fetch_provider_models(provider_id: str) -> Any:
        try:
            return await manager.fetch_provider_models(provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/providers/{provider_id}/models", response_model=list[ModelResponse])
    async def create_provider_models(provider_id: str, req: ModelCreateRequest) -> Any:
        try:
            return manager.create_models_for_provider(provider_id, req.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc

    @app.post("/api/providers/{provider_id}/models/delete", response_model=ModelDeleteResponse)
    async def delete_provider_models(provider_id: str, req: ModelDeleteRequest) -> Any:
        try:
            return manager.delete_models_for_provider(provider_id, req.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc

    @app.put("/api/models/{model_id}", response_model=ModelResponse)
    async def update_model(model_id: str, req: ModelUpdateRequest) -> Any:
        try:
            return manager.update_model(model_id, req.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Model not found") from exc

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

    @app.get("/api/sessions/{session_id}", response_model=SessionMessagesResponse)
    async def get_session(session_id: str) -> dict[str, Any]:
        if not manager.sessions_for("default").exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "messages": manager.session_messages("default", session_id)}

    @app.get("/api/bots/{bot_id}/sessions/{session_id}", response_model=SessionMessagesResponse)
    async def get_bot_session(bot_id: str, session_id: str) -> dict[str, Any]:
        if not manager.sessions_for(bot_id).exists(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"session_id": session_id, "messages": manager.session_messages(bot_id, session_id)}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        ok = await manager.delete_session("default", session_id)
        return {"deleted": ok}

    @app.delete("/api/bots/{bot_id}/sessions/{session_id}")
    async def delete_bot_session(bot_id: str, session_id: str) -> dict[str, Any]:
        ok = await manager.delete_session(bot_id, session_id)
        return {"deleted": ok}

    @app.delete(
        "/api/bots/{bot_id}/sessions/{session_id}/messages/{message_id}",
        response_model=MessageDeleteResponse,
    )
    async def delete_bot_session_message(bot_id: str, session_id: str, message_id: str) -> dict[str, Any]:
        try:
            lock = await manager.session_lock(bot_id, session_id)
            async with lock:
                return await manager.delete_message_turn(bot_id, session_id, message_id)
        except ValueError as exc:
            detail = str(exc)
            if detail == "Session not found":
                raise HTTPException(status_code=404, detail=detail) from exc
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.post(
        "/api/bots/{bot_id}/sessions/{session_id}/messages/{message_id}/regenerate",
        response_model=MessageRegenerateResponse,
    )
    async def regenerate_bot_session_message(bot_id: str, session_id: str, message_id: str) -> dict[str, Any]:
        try:
            lock = await manager.session_lock(bot_id, session_id)
            async with lock:
                return await manager.regenerate_message_turn(bot_id, session_id, message_id)
        except ValueError as exc:
            detail = str(exc)
            if detail == "Session not found":
                raise HTTPException(status_code=404, detail=detail) from exc
            raise HTTPException(status_code=400, detail=detail) from exc

    @app.post("/api/bots/{bot_id}/sessions/{session_id}/messages/{message_id}/regenerate/stream")
    async def stream_regenerate_bot_session_message(
        bot_id: str,
        session_id: str,
        message_id: str,
        req: MessageRegenerateRequest,
        request: Request,
    ) -> StreamingResponse:
        lock = await manager.session_lock(bot_id, session_id)
        await lock.acquire()
        try:
            prompt = manager.prepare_regenerate_message_turn(bot_id, session_id, message_id)
            bot = await manager.get_bot(bot_id, session_id)
        except ValueError as exc:
            if lock.locked():
                lock.release()
            detail = str(exc)
            if detail == "Session not found":
                raise HTTPException(status_code=404, detail=detail) from exc
            raise HTTPException(status_code=400, detail=detail) from exc
        except Exception:
            if lock.locked():
                lock.release()
            raise

        def _frame(*, event: str, data: str) -> str:
            return f"event: {event}\ndata: {data}\n\n"

        async def _gen() -> AsyncIterator[str]:
            try:
                async for event in bot.stream(
                    prompt,
                    session_id=session_id,
                    reasoning_effort=req.reasoning_effort,
                ):
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
            finally:
                if lock.locked():
                    lock.release()

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict[str, str]:
        try:
            bot = await manager.get_bot("default", session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        bot.cancel()
        return {"status": "ok"}

    @app.post("/api/bots/{bot_id}/sessions/{session_id}/cancel")
    async def cancel_bot_session(bot_id: str, session_id: str) -> dict[str, str]:
        try:
            bot = await manager.get_bot(bot_id, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        bot.cancel()
        return {"status": "ok"}

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> Any:
        session_id = req.session_id or manager.sessions_for("default").create()
        try:
            bot = await manager.get_bot("default", session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        lock = await manager.session_lock("default", session_id)
        async with lock:
            try:
                result = await bot.chat(
                    req.prompt,
                    session_id=session_id,
                    reasoning_effort=req.reasoning_effort,
                )
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
        try:
            bot = await manager.get_bot(bot_id, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        lock = await manager.session_lock(bot_id, session_id)
        async with lock:
            try:
                result = await bot.chat(
                    req.prompt,
                    session_id=session_id,
                    reasoning_effort=req.reasoning_effort,
                )
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
        try:
            bot = await manager.get_bot("default", session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
                    async for event in bot.stream(
                        req.prompt,
                        session_id=session_id,
                        reasoning_effort=req.reasoning_effort,
                    ):
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
        try:
            bot = await manager.get_bot(bot_id, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
                    async for event in bot.stream(
                        req.prompt,
                        session_id=session_id,
                        reasoning_effort=req.reasoning_effort,
                    ):
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

    static_dir = _resolve_static_dir(Path(__file__).resolve().parent)
    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    else:
        @app.get("/", include_in_schema=False)
        async def ui_unavailable() -> HTMLResponse:
            return HTMLResponse(
                """
                <html>
                  <body style="font-family: sans-serif; padding: 2rem; line-height: 1.5;">
                    <h1>Minibot WebUI is not built</h1>
                    <p>
                      Build the frontend first:
                      <code>cd webui &amp;&amp; npm install &amp;&amp; npm run build</code>
                    </p>
                  </body>
                </html>
                """.strip(),
                status_code=503,
            )

    @app.exception_handler(HTTPException)
    async def _http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
