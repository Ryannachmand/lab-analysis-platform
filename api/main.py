"""Lab Analysis Platform API — FastAPI application entry point."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env from project root before any other imports read env vars
load_dotenv(Path(__file__).parents[1] / ".env")

from api.routers import jobs, prepare, results, cleanup

app = FastAPI(
    title="Lab Analysis Platform",
    description="API for submitting and monitoring scRNAseq analysis jobs powered by Claude Code.",
    version="0.1.0",
)

allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prepare.router)
app.include_router(jobs.router)
app.include_router(results.router)
app.include_router(cleanup.router)

logger = logging.getLogger(__name__)


@app.on_event("startup")
async def _check_skills_dir() -> None:
    skills_dir_env = os.environ.get("SKILLS_DIR")
    if not skills_dir_env:
        logger.warning(
            "SKILLS_DIR is not set — DeploymentAgent and CleanupAgent will run in "
            "degraded mode. See README > Prerequisites to configure the skills library."
        )
        return

    skills_dir = Path(skills_dir_env)
    required = ["pipelines", "brief_template.txt", "lab_context.md"]
    missing = [name for name in required if not (skills_dir / name).exists()]
    if missing:
        logger.warning(
            "SKILLS_DIR is set but the following expected items are missing: %s. "
            "See README > Prerequisites.",
            ", ".join(missing),
        )


@app.get("/health", tags=["health"])
async def health():
    """Return a simple liveness check."""
    return {"status": "ok"}
