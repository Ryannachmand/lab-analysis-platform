"""Job logs and results endpoints."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from api.models import JobResultsResponse, ResultFile

router = APIRouter(prefix="/jobs", tags=["results"])


def _jobs_dir() -> Path:
    return Path(os.getenv("JOBS_DIR", Path(__file__).parents[2] / "jobs"))


def _get_job_dir(job_id: str) -> Path:
    job_dir = _jobs_dir() / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job_dir


@router.get("/{job_id}/logs", response_class=PlainTextResponse)
async def get_job_logs(job_id: str):
    """Return the current contents of job.log as plain text."""
    job_dir = _get_job_dir(job_id)
    log_path = job_dir / "job.log"
    if not log_path.exists():
        return ""
    return log_path.read_text()


@router.get("/{job_id}/results", response_model=JobResultsResponse)
async def list_results(job_id: str):
    """Return a recursive list of files in the job's output/ directory.

    Each file's ``path`` is its relative path from output/ using forward slashes
    (e.g. ``de_sweep/scores.csv``). ``name`` is always the bare filename.
    """
    job_dir = _get_job_dir(job_id)
    output_dir = job_dir / "output"
    output_dir.mkdir(exist_ok=True)

    files = []
    for f in sorted(output_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(output_dir).as_posix()
        files.append(ResultFile(name=f.name, size=f.stat().st_size, path=rel))
    return JobResultsResponse(job_id=job_id, files=files)


@router.get("/{job_id}/results/{filename:path}")
async def download_result(job_id: str, filename: str):
    """Download a specific output file from a job.

    ``filename`` may include subdirectory segments (e.g. ``de_sweep/scores.csv``).
    """
    job_dir = _get_job_dir(job_id)
    output_dir = job_dir / "output"

    # Prevent path traversal
    try:
        file_path = (output_dir / filename).resolve()
        file_path.relative_to(output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File {filename!r} not found")

    return FileResponse(path=str(file_path), filename=file_path.name)
