"""Cleanup agent router: /jobs/{job_id}/cleanup and /jobs/{job_id}/cleanup/approve"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.cleanup_agent import CleanupAgent

router = APIRouter(prefix="/jobs", tags=["cleanup"])

JOBS_DIR_DEFAULT = str(Path(__file__).parent.parent.parent / "jobs")
SKILLS_DIR_DEFAULT = "./claude-skills-v2"

# In-memory store for pending proposals keyed by job_id.
# Persisted to disk as proposals_{job_id}.json in the job directory.
# (Simple approach — no DB needed at this scale.)


def _agent() -> CleanupAgent:
    return CleanupAgent(
        skills_dir=Path(os.getenv("SKILLS_DIR", SKILLS_DIR_DEFAULT)),
        jobs_dir=Path(os.getenv("JOBS_DIR", JOBS_DIR_DEFAULT)),
    )


def _proposals_path(job_id: str) -> Path:
    jobs_dir = Path(os.getenv("JOBS_DIR", JOBS_DIR_DEFAULT))
    return jobs_dir / job_id / "pending_proposals.json"


def _save_proposals(job_id: str, proposals: list[dict]) -> None:
    path = _proposals_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposals, indent=2))


def _load_proposals(job_id: str) -> list[dict]:
    path = _proposals_path(job_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


# ─── Request / Response models ────────────────────────────────────────────────

class CleanupRequest(BaseModel):
    feedback: str | None = None
    deep_interpret: bool = False


class ApproveRequest(BaseModel):
    change_ids: list[str]  # List of change_id strings to approve


class ProposedChange(BaseModel):
    change_id: str
    target_file: str
    change_type: str
    plain_english: str
    proposed_content: str
    confidence: float


class CleanupResponse(BaseModel):
    job_id: str
    file_cleanup: dict[str, Any]
    interpretation: dict[str, Any]
    proposals: list[dict[str, Any]]
    has_pending_proposals: bool


class ApproveResponse(BaseModel):
    job_id: str
    applied: list[dict[str, Any]]
    skipped_change_ids: list[str]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/{job_id}/cleanup", response_model=CleanupResponse)
async def run_cleanup(job_id: str, request: CleanupRequest = CleanupRequest()):
    """
    Run the cleanup agent for a completed job.

    Always runs:
    - Silent deletion of temp/scratch files
    - Interpretation of checkpoint summaries and job log

    If `feedback` is provided:
    - Proposes updates to the skills library based on what was learned
    - Proposals are saved and returned for user review — nothing is written until /approve is called

    Set `deep_interpret: true` for detailed biological interpretation (uses more tokens).
    """
    agent = _agent()
    result = await agent.run(
        job_id=job_id,
        feedback=request.feedback,
        deep_interpret=request.deep_interpret,
    )

    if "status" in result and result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["message"])

    proposals: list[dict] = result.get("proposals", [])

    # Persist proposals so /approve can reference them
    if proposals:
        _save_proposals(job_id, proposals)

    return CleanupResponse(
        job_id=job_id,
        file_cleanup=result["file_cleanup"],
        interpretation=result["interpretation"],
        proposals=proposals,
        has_pending_proposals=bool(proposals),
    )


@router.post("/{job_id}/cleanup/approve", response_model=ApproveResponse)
async def approve_changes(job_id: str, request: ApproveRequest):
    """
    Approve and apply a subset of proposed skills updates.

    Pass the change_ids you want to apply. All others are discarded.
    This writes directly to the skills library files — irreversible without git.

    Tip: approve incrementally if unsure — you can call /cleanup again with
    additional feedback to generate new proposals for the same job.
    """
    all_proposals = _load_proposals(job_id)
    if not all_proposals:
        raise HTTPException(
            status_code=404,
            detail=f"No pending proposals for job {job_id}. Run POST /jobs/{job_id}/cleanup with feedback first."
        )

    agent = _agent()
    approved_objs = [{"change_id": cid} for cid in request.change_ids]
    results = agent.apply_approved_changes(approved_objs, all_proposals)

    applied = [r for r in results if r["status"] == "applied"]
    skipped = [r["change_id"] for r in results if r["status"] != "applied"]

    # Clear proposals that were processed (applied or attempted)
    processed_ids = {r["change_id"] for r in results}
    remaining = [p for p in all_proposals if p["change_id"] not in processed_ids]
    if remaining:
        _save_proposals(job_id, remaining)
    else:
        # Clean up proposals file
        p = _proposals_path(job_id)
        if p.exists():
            p.unlink()

    return ApproveResponse(
        job_id=job_id,
        applied=applied,
        skipped_change_ids=skipped,
    )


@router.get("/{job_id}/cleanup/proposals")
async def get_pending_proposals(job_id: str):
    """
    Retrieve any pending (unapproved) proposals for a job.
    Useful for reviewing proposals from a previous cleanup call.
    """
    proposals = _load_proposals(job_id)
    return {
        "job_id": job_id,
        "proposals": proposals,
        "count": len(proposals),
    }
