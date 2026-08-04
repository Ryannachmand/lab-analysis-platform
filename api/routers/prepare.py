"""Prepare and description-based submit endpoints using the DeploymentAgent."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from api.deployment_agent import DeploymentAgent
from api.job_runner import dispatch_job
from api.models import ClarificationAnswer

router = APIRouter(prefix="/jobs", tags=["prepare"])


def _jobs_dir() -> Path:
    return Path(os.getenv("JOBS_DIR", Path(__file__).parents[2] / "jobs"))


def _skills_dir() -> Path:
    return Path(os.getenv("SKILLS_DIR", "./claude-skills-v2"))


class PrepareRequest(BaseModel):
    description: str
    interactive_checkpoints: list[str] = []
    clarification_answers: list[ClarificationAnswer] | None = None


class SubmitRequest(BaseModel):
    description: str
    interactive_checkpoints: list[str] = []
    clarification_answers: list[ClarificationAnswer] | None = None


@router.post("/prepare")
async def prepare_job(request: PrepareRequest):
    """Generate pipeline files from a free-text description without launching a job.

    Runs the DeploymentAgent to select a pipeline and fill the analysis brief.
    Returns status "ready" with job_id and assumptions when confident, or
    status "needs_clarification" with a list of questions when the description
    is missing required information (GEO accessions, organism, tissue type, etc.).
    """
    job_id = str(uuid.uuid4())
    agent = DeploymentAgent(skills_dir=_skills_dir(), jobs_dir=_jobs_dir())
    return await agent.prepare(job_id, request.description, request.interactive_checkpoints, request.clarification_answers)


@router.post("/submit", status_code=202)
async def submit_job_from_description(
    request: SubmitRequest, background_tasks: BackgroundTasks
):
    """Translate a description and immediately launch the job if ready.

    Runs the DeploymentAgent to generate pipeline files. If the agent is
    confident (status == "ready"), initialises status.json and job.log, then
    dispatches the job in the background. Returns HTTP 422 with clarifying
    questions if the description is ambiguous or incomplete.
    """
    job_id = str(uuid.uuid4())
    agent = DeploymentAgent(skills_dir=_skills_dir(), jobs_dir=_jobs_dir())
    result = await agent.prepare(job_id, request.description, request.interactive_checkpoints, request.clarification_answers)

    if result["status"] == "needs_clarification":
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Description requires clarification before the job can be submitted.",
                "clarifying_questions": result["clarifying_questions"],
            },
        )

    # DeploymentAgent already wrote CLAUDE.md, analysis_brief.txt, and job_config.json.
    # Complete job initialisation: status.json and job.log, then dispatch.
    job_dir = _jobs_dir() / job_id
    (job_dir / "job.log").write_text("")

    status_data = {
        "job_id": job_id,
        "job_name": None,
        "pipeline": result.get("pipeline_selected"),
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "exit_code": None,
    }
    (job_dir / "status.json").write_text(json.dumps(status_data, indent=2))

    interactive_checkpoints = result.get("interactive_checkpoints", [])
    background_tasks.add_task(dispatch_job, job_id, job_dir, interactive_checkpoints)

    return {
        "job_id": job_id,
        "status": "queued",
        "pipeline_selected": result.get("pipeline_selected"),
        "assumptions": result.get("assumptions", []),
        "interactive_checkpoints": interactive_checkpoints,
        "mode": result.get("mode", "autonomous"),
    }
