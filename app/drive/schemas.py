from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DriveAuthorizationOut(BaseModel):
    authorization_url: str


class DriveFileOutcomeOut(BaseModel):
    name: str
    path: str
    # new | changed | unchanged | unsupported | failed
    status: str
    reason: str | None = None


class DriveSyncSummaryOut(BaseModel):
    documents: int = 0
    unchanged: int = 0
    pruned: int = 0
    truncated: bool = False
    unsupported: list[DriveFileOutcomeOut] = []
    failed: list[DriveFileOutcomeOut] = []
    at: datetime | None = None


class DriveStatusOut(BaseModel):
    connected: bool
    google_email: str | None = None
    connected_at: datetime | None = None
    folder_id: str | None = None
    folder_name: str | None = None
    last_synced_at: datetime | None = None
    last_sync: DriveSyncSummaryOut | None = None


class DriveFolderIn(BaseModel):
    # El enlace de la carpeta de Drive o su id.
    folder: str


class DrivePreviewOut(BaseModel):
    truncated: bool
    new: int
    changed: int
    unchanged: int
    unsupported: int
    files: list[DriveFileOutcomeOut]
