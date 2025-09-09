from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class UpdateStartRequest(BaseModel):
    """Request to start an update."""

    target_version: Optional[str] = Field(default=None)
    channel: Optional[Literal["stable", "beta"]] = Field(default=None)
    force: Optional[bool] = Field(default=False)


class UpdateStartResponse(BaseModel):
    """Accepted response for a started update."""

    update_id: str
    state: Literal[
        "preflight",
        "backup",
        "pull",
        "migrate",
        "recreate",
        "healthcheck",
        "completed",
        "failed",
    ]


class UpdateStatusResponse(BaseModel):
    """Current status and progress details for an update session."""

    update_id: str
    state: str
    progress: int = Field(ge=0, le=100)
    phase: str
    log_tail: List[str] = []
    started_at: Optional[datetime] = None


class UpdateCancelRequest(BaseModel):
    """Request to signal best-effort cancellation for an update session."""

    update_id: str


class VersionInfo(BaseModel):
    """Version payload for GET /version."""

    current: Dict[str, Dict[str, Any]]
    latest: Dict[str, Any]
    update_available: bool
    update_in_progress: bool = False
    channel: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_result: Optional[str] = None


class UpdateEvent(BaseModel):
    """Event payload used for SSE streaming of update progress."""

    event: Literal["phase", "progress", "log", "completed", "failed"] = "phase"
    state: str
    message: str
    ts: datetime


class BackupCancelRequest(BaseModel):
    """Request to cancel a running backup job."""

    backup_id: str


class BackupStartRequest(BaseModel):
    """Request to start a backup job."""

    scope: List[Literal["postgres", "influx", "config"]] = Field(default_factory=lambda: ["postgres", "influx", "config"])
    label: Optional[str] = None


class BackupStartResponse(BaseModel):
    """Accepted response for a started backup."""

    backup_id: str
    state: Literal["backup", "completed", "failed"]


class BackupStatusResponse(BaseModel):
    """Current status and progress for a backup session."""

    backup_id: str
    state: str
    progress: int = Field(ge=0, le=100)
    phase: str
    log_tail: List[str] = []
    started_at: Optional[datetime] = None


class BackupListItem(BaseModel):
    backup_id: str
    created_at: datetime
    size_bytes: Optional[int] = None
    scope: List[str] = []


class BackupListResponse(BaseModel):
    items: List[BackupListItem]


class BackupRestoreRequest(BaseModel):
    backup_id: str
    scope: List[Literal["postgres", "influx", "config"]] = Field(default_factory=lambda: ["postgres", "influx", "config"])