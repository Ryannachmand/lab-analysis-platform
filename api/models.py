"""Pydantic models for the Lab Analysis Platform API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Pipeline(str, Enum):
    large_dataset = "LargeDataset"
    integrate_public_data = "IntegratePublicData"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    awaiting_response = "awaiting_response"
    completed = "completed"
    failed = "failed"


# ── Request models ─────────────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    pipeline: Pipeline
    brief_content: str = Field(..., description="Full text of analysis_brief.txt")
    job_name: Optional[str] = Field(None, description="Optional human-readable name")
    interactive_checkpoints: list[str] = Field(
        default_factory=list,
        description="Plain-English checkpoint intents, e.g. 'quality control', 'cluster annotation'",
    )


# ── Response models ────────────────────────────────────────────────────────────

class JobSubmitResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    job_name: Optional[str]
    pipeline: str
    status: JobStatus
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    exit_code: Optional[int]
    awaiting_checkpoint: Optional[str] = None
    failure_reason: Optional[str] = None


class ResultFile(BaseModel):
    name: str
    size: int
    path: str


class JobResultsResponse(BaseModel):
    job_id: str
    files: list[ResultFile]


class ClarificationAnswer(BaseModel):
    question: str
    answer: str
