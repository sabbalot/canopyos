from __future__ import annotations

import asyncio
import json
from datetime import datetime, UTC
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Response, status
from starlette.responses import StreamingResponse

from .schemas import (
    UpdateCancelRequest,
    UpdateEvent,
    UpdateStartRequest,
    UpdateStartResponse,
    UpdateStatusResponse,
    VersionInfo,
)
from .orchestrator import orchestrator

app = FastAPI(title="CanopyOS Updater")


@app.post("/update/start", response_model=UpdateStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_update(body: UpdateStartRequest) -> Response:
    # Proactively clear any stale locks before checking activity
    await orchestrator.cleanup_stale()
    if orchestrator.is_effectively_active():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Update already in progress")
    update_id = f"upd-{int(datetime.now(UTC).timestamp())}"
    orchestrator.create_session(update_id)
    asyncio.create_task(orchestrator.run_update(update_id))
    payload = UpdateStartResponse(update_id=update_id, state="preflight")
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_202_ACCEPTED)


@app.get("/update/status", response_model=UpdateStatusResponse)
async def get_status(update_id: str = Query(...)) -> Response:
    payload = orchestrator.status(update_id)
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_200_OK)


@app.get("/update/stream")
async def stream(update_id: str = Query(...)) -> StreamingResponse:
    # Ensure the session exists; otherwise return 404
    if orchestrator.get_session(update_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown update_id")
    async def gen() -> AsyncIterator[str]:
        # Initial init event to establish the SSE stream reliably
        yield f"data: {json.dumps({'event': 'init', 'update_id': update_id})}\n\n"
        async for evt in orchestrator.stream(update_id):
            yield f"data: {evt.model_dump_json()}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Helps with some reverse proxies (ignored if not present)
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/update/cancel")
async def cancel(body: UpdateCancelRequest) -> Response:
    orchestrator.cancel(body.update_id)
    return Response(content=json.dumps({"ok": True}), media_type="application/json", status_code=status.HTTP_200_OK)


@app.get("/version", response_model=VersionInfo)
async def version(refresh: bool = Query(False)) -> Response:
    # Keep the lock view fresh for callers
    await orchestrator.cleanup_stale()
    in_progress = orchestrator.is_effectively_active()
    payload = VersionInfo(
        current={
            "app": {"tag": "latest", "digest": "sha256:unknown"},
            "backend": {"tag": "latest", "digest": "sha256:unknown"},
        },
        latest={"version": "latest", "services": {}},
        update_available=False,
        update_in_progress=in_progress,
        last_checked_at=datetime.now(UTC),
        last_result="ok",
    )
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_200_OK)
