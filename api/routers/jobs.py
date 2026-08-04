"""Job submission, status, and checkpoint endpoints."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict

from api.models import JobStatus, JobSubmitRequest, JobSubmitResponse, JobStatusResponse
from api.job_runner import dispatch_job

# Matches checkpoint_N.json but NOT checkpoint_N_response.json
_CHECKPOINT_FILE_RE = re.compile(r'^checkpoint_(\d+)\.json$')


class CheckpointResponseBody(BaseModel):
    """Request body for POST /jobs/{job_id}/checkpoints/{checkpoint_id}/response."""

    model_config = ConfigDict(extra="allow")
    input: str

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _jobs_dir() -> Path:
    return Path(os.getenv("JOBS_DIR", Path(__file__).parents[2] / "jobs"))


def _skills_dir() -> Path:
    return Path(os.getenv("SKILLS_DIR", "./claude-skills-v2"))


def _get_job_dir(job_id: str) -> Path:
    job_dir = _jobs_dir() / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job_dir


def _read_status(job_dir: Path) -> dict:
    return json.loads((job_dir / "status.json").read_text())


@router.post("", response_model=JobSubmitResponse, status_code=202)
async def submit_job(request: JobSubmitRequest, background_tasks: BackgroundTasks):
    """Submit a new analysis job.

    Creates the job directory, writes the analysis brief and CLAUDE.md,
    initialises status.json, then dispatches the job to run in the background.
    """
    job_id = str(uuid.uuid4())
    job_dir = _jobs_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Write analysis brief
    (job_dir / "analysis_brief.txt").write_text(request.brief_content)

    # Copy CLAUDE.md from skills template
    template_path = _skills_dir() / "PROJECT_CLAUDE_TEMPLATE.md"
    if template_path.exists():
        shutil.copy(template_path, job_dir / "CLAUDE.md")
    else:
        (job_dir / "CLAUDE.md").write_text(
            "# Job CLAUDE.md\n# Skills template not found — no shared skills available.\n"
        )

    # Create output directory
    (job_dir / "output").mkdir(exist_ok=True)

    # Initialise empty log
    (job_dir / "job.log").write_text("")

    # Write initial status
    status_data = {
        "job_id": job_id,
        "job_name": request.job_name,
        "pipeline": request.pipeline.value,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "exit_code": None,
    }
    (job_dir / "status.json").write_text(json.dumps(status_data, indent=2))

    background_tasks.add_task(dispatch_job, job_id, job_dir)
    return JobSubmitResponse(job_id=job_id, status=JobStatus.queued)


@router.get("", response_model=list[JobStatusResponse])
async def list_jobs():
    """Return all jobs, most recent first."""
    jobs_dir = _jobs_dir()
    jobs_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for status_file in sorted(jobs_dir.glob("*/status.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(status_file.read_text())
            results.append(JobStatusResponse(**data))
        except Exception:
            continue
    return results


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str):
    """Return the current status of a specific job."""
    job_dir = _get_job_dir(job_id)
    data = _read_status(job_dir)
    return JobStatusResponse(**data)


@router.get("/{job_id}/checkpoints")
async def list_checkpoints(job_id: str):
    """Return all checkpoint files for a job, sorted by checkpoint_number.

    Scans jobs/{job_id}/output/ for files matching checkpoint_N.json.
    Response files (checkpoint_N_response.json) are excluded.
    Returns an empty list if no checkpoints have been written yet.

    Each entry always includes at least checkpoint_id (prefixed, e.g.
    "LargeDataset:CP3") and checkpoint_number so callers can target the
    POST .../checkpoints/{checkpoint_id}/response endpoint unambiguously.
    """
    job_dir = _get_job_dir(job_id)
    output_dir = job_dir / "output"

    if not output_dir.exists():
        return []

    checkpoints = []
    for path in output_dir.iterdir():
        m = _CHECKPOINT_FILE_RE.match(path.name)
        if not m:
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue

        # Merge selected fields from the sibling _summary.json if it exists
        summary_path = output_dir / f"checkpoint_{m.group(1)}_summary.json"
        if summary_path.exists():
            try:
                raw = json.loads(summary_path.read_text())
                summary = {
                    k: v for k, v in raw.items()
                    if k in ("notes", "warnings", "errors", "cell_types_identified", "stage")
                    and v is not None
                }
                if summary:
                    data["summary"] = summary
            except Exception:
                pass

        # Guarantee both identity fields are always present in the response
        data.setdefault("checkpoint_id", None)
        data.setdefault("checkpoint_number", None)
        checkpoints.append(data)

    return sorted(checkpoints, key=lambda c: (c.get("checkpoint_number") or 0))


@router.post("/{job_id}/checkpoints/{checkpoint_id}/response")
async def respond_to_checkpoint(
    job_id: str,
    checkpoint_id: str,
    body: CheckpointResponseBody,
):
    """Write a human response to a blocking checkpoint.

    Resolution order:
    1. Exact match on the checkpoint_id field (e.g. "LargeDataset:CP3").
    2. Fallback: if checkpoint_id parses as an integer, match on the
       checkpoint_number field (e.g. "3" → checkpoint_number == 3).
    3. If neither matches, returns 404 listing all present checkpoint IDs
       and numbers.

    Writes checkpoint_{N}_response.json to jobs/{job_id}/output/ (the file
    the pipeline agent is polling for) and updates responded_at + status in
    checkpoint_N.json.

    Returns 400 if the checkpoint is not in "awaiting_response" status.
    """
    job_dir = _get_job_dir(job_id)
    output_dir = job_dir / "output"

    if not output_dir.exists():
        raise HTTPException(status_code=404, detail=f"Checkpoint {checkpoint_id!r} not found — no output directory exists")

    def _iter_checkpoint_files():
        for path in output_dir.iterdir():
            if not _CHECKPOINT_FILE_RE.match(path.name):
                continue
            try:
                yield path, json.loads(path.read_text())
            except Exception:
                continue

    # Pass 1: exact match on checkpoint_id field
    target_file: Path | None = None
    target_data: dict | None = None

    for path, data in _iter_checkpoint_files():
        if data.get("checkpoint_id") == checkpoint_id:
            target_file = path
            target_data = data
            break

    # Pass 2: fallback — match on checkpoint_number if checkpoint_id is an integer string
    if target_file is None:
        try:
            cp_num = int(checkpoint_id)
        except ValueError:
            cp_num = None

        if cp_num is not None:
            for path, data in _iter_checkpoint_files():
                if data.get("checkpoint_number") == cp_num:
                    target_file = path
                    target_data = data
                    break

    if target_file is None or target_data is None:
        present = [
            f"{d.get('checkpoint_id')!r} (number {d.get('checkpoint_number')})"
            for _, d in _iter_checkpoint_files()
        ]
        detail = f"Checkpoint {checkpoint_id!r} not found."
        if present:
            detail += f" Present checkpoints: {', '.join(sorted(present))}"
        else:
            detail += " No checkpoints found in this job."
        raise HTTPException(status_code=404, detail=detail)

    if target_data.get("status") != "awaiting_response":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Checkpoint {checkpoint_id!r} is not awaiting a response "
                f"(current status: {target_data.get('status')!r})"
            ),
        )

    checkpoint_number = (
        target_data.get("checkpoint_number")
        or target_data.get("cp_number")
        or int(target_file.stem.split("_")[-1])
    )
    now = datetime.now(timezone.utc).isoformat()

    # Write the response file the pipeline agent is polling for
    response_path = output_dir / f"checkpoint_{checkpoint_number}_response.json"
    response_payload = body.model_dump()
    response_payload["checkpoint_id"] = checkpoint_id
    response_payload["responded_at"] = now
    response_path.write_text(json.dumps(response_payload, indent=2))

    # Update the original checkpoint file
    target_data["responded_at"] = now
    target_data["status"] = "completed"
    target_file.write_text(json.dumps(target_data, indent=2))

    return target_data
