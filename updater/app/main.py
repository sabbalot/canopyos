from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, UTC
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Response, status
from starlette.responses import StreamingResponse

from .schemas import (
    BackupListResponse,
    BackupRestoreRequest,
    BackupStartRequest,
    BackupStartResponse,
    BackupStatusResponse,
    BackupCancelRequest,
    UpdateCancelRequest,
    UpdateEvent,
    UpdateStartRequest,
    UpdateStartResponse,
    UpdateStatusResponse,
    VersionInfo,
)
from .orchestrator import orchestrator
from .version import get_version_info

app = FastAPI(title="CanopyOS Updater")


@app.post("/update/start", response_model=UpdateStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_update(body: UpdateStartRequest) -> Response:
    # Proactively clear any stale locks before checking activity
    await orchestrator.cleanup_stale()
    if orchestrator.is_effectively_active():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Update already in progress")
    update_id = f"upd-{int(datetime.now(UTC).timestamp())}"
    orchestrator.create_session(update_id)
    # Pass target params to orchestrator via env or dedicated setters; for now, run with defaults
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
    await orchestrator.cleanup_stale()
    # Always recompute 'current'; cache only 'latest' inside get_version_info
    payload = await get_version_info(refresh=refresh)
    # Reflect live in-progress flag from orchestrator
    if orchestrator.is_effectively_active():
        payload.update_in_progress = True
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_200_OK)


# Backup APIs

@app.post("/backup/start", response_model=BackupStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def backup_start(body: BackupStartRequest) -> Response:
    backup_id = f"bak-{int(datetime.now(UTC).timestamp())}"
    orchestrator.create_backup_session(backup_id)
    asyncio.create_task(orchestrator.run_backup(backup_id, body.scope))
    payload = BackupStartResponse(backup_id=backup_id, state="backup")
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_202_ACCEPTED)


@app.get("/backup/status", response_model=BackupStatusResponse)
async def backup_status(backup_id: str = Query(...)) -> Response:
    payload = orchestrator.backup_status(backup_id)
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_200_OK)


@app.get("/backup/stream")
async def backup_stream(backup_id: str = Query(...)) -> StreamingResponse:
    if orchestrator.get_backup_session(backup_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown backup_id")
    async def gen() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'event': 'init', 'backup_id': backup_id})}\n\n"
        async for evt in orchestrator.stream_backup(backup_id):
            yield f"data: {evt.model_dump_json()}\n\n"
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/backup/list", response_model=BackupListResponse)
async def backup_list() -> Response:
    payload = orchestrator.list_backups()
    return Response(content=payload.model_dump_json(), media_type="application/json", status_code=status.HTTP_200_OK)


@app.post("/backup/restore")
async def backup_restore(body: BackupRestoreRequest) -> Response:
    restore_id = f"rst-{int(datetime.now(UTC).timestamp())}"
    orchestrator.create_backup_session(restore_id)
    asyncio.create_task(orchestrator.run_restore(restore_id, body.backup_id, body.scope))
    return Response(content=json.dumps({"restore_id": restore_id, "state": "restore"}), media_type="application/json", status_code=status.HTTP_202_ACCEPTED)


@app.post("/backup/cancel")
async def backup_cancel(body: BackupCancelRequest) -> Response:
    # Best-effort: mark session for cancel; restore/backup steps check periodically
    sess = orchestrator.get_backup_session(body.backup_id)
    if not sess:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown backup_id")
    sess.cancel_requested = True
    return Response(content=json.dumps({"ok": True}), media_type="application/json", status_code=status.HTTP_200_OK)
