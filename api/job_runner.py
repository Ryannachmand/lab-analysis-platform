"""Async job runner supporting local, docker, slurm, and lsf backends."""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
CHECKPOINT_POLL_INTERVAL = 30  # seconds between polls for checkpoint_N_response.json

_semaphore: asyncio.Semaphore | None = None


def get_semaphore() -> asyncio.Semaphore:
    """Return (or lazily create) the global concurrency semaphore."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    return _semaphore


# ── Status helpers ─────────────────────────────────────────────────────────────

def read_status(job_dir: Path) -> dict:
    """Read and return status.json for a job directory."""
    return json.loads((job_dir / "status.json").read_text())


def update_status(job_dir: Path, status: str, **kwargs) -> None:
    """Update status.json in place, merging in any extra keyword arguments."""
    data = read_status(job_dir)
    data["status"] = status
    data.update(kwargs)
    (job_dir / "status.json").write_text(json.dumps(data, indent=2))


# ── Checkpoint helpers ─────────────────────────────────────────────────────────

def find_awaiting_checkpoint(job_dir: Path) -> dict | None:
    """Scan output/ for the first checkpoint_N.json with status == 'awaiting_response'.

    Returns the parsed checkpoint dict (with an extra '_cp_file' key recording its
    path) or None if no blocking checkpoint is found.
    """
    output_dir = job_dir / "output"
    if not output_dir.exists():
        return None

    for cp_file in sorted(output_dir.glob("checkpoint_*.json")):
        if "_response" in cp_file.name:
            continue
        try:
            cp = json.loads(cp_file.read_text())
            if cp.get("status") == "awaiting_response":
                cp["_cp_file"] = str(cp_file)
                return cp
        except (json.JSONDecodeError, OSError):
            continue
    return None


async def poll_for_response(job_dir: Path, awaiting_cp: dict) -> dict | None:
    """Poll every 30 seconds for checkpoint_N_response.json until found or timeout.

    Returns the parsed response dict, or None if timeout_at is reached without
    a response appearing.
    """
    cp_number = awaiting_cp.get("checkpoint_number")
    response_path = job_dir / "output" / f"checkpoint_{cp_number}_response.json"

    timeout_at: datetime | None = None
    timeout_str = awaiting_cp.get("timeout_at")
    if timeout_str:
        try:
            timeout_at = datetime.fromisoformat(timeout_str)
        except ValueError:
            pass

    while True:
        if response_path.exists():
            try:
                return json.loads(response_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # malformed file — keep waiting

        if timeout_at and datetime.now(timezone.utc) >= timeout_at:
            return None

        await asyncio.sleep(CHECKPOINT_POLL_INTERVAL)


def _mark_checkpoint_responded(awaiting_cp: dict) -> None:
    """Set responded_at and status='completed' on the checkpoint file itself.

    Called by the runner after a response is received, since the Claude subprocess
    no longer touches the checkpoint file after writing it.
    """
    cp_file = Path(awaiting_cp.get("_cp_file", ""))
    if not cp_file.exists():
        return
    try:
        cp = json.loads(cp_file.read_text())
        cp["responded_at"] = datetime.now(timezone.utc).isoformat()
        cp["status"] = "completed"
        cp_file.write_text(json.dumps(cp, indent=2))
    except (json.JSONDecodeError, OSError):
        pass


def build_handoff_prompt(
    job_id: str, job_dir: Path, awaiting_cp: dict, response_text: str
) -> str:
    """Construct the continuation prompt for a re-invoked Claude Code session.

    Includes: prior checkpoint summary, user response, and a manifest of existing
    output files so the session can orient itself without scanning.
    """
    output_dir = job_dir / "output"
    existing_files: list[str] = []
    if output_dir.exists():
        for f in sorted(output_dir.rglob("*")):
            if f.is_file():
                existing_files.append(f"  output/{f.relative_to(output_dir)}")
    existing_str = "\n".join(existing_files) if existing_files else "  (none)"

    cp_id = awaiting_cp.get("checkpoint_id", "")
    cp_number = awaiting_cp.get("checkpoint_number", "")
    stage = awaiting_cp.get("stage", "")
    findings = awaiting_cp.get("findings", "(not recorded)")
    decision_made = awaiting_cp.get("decision_made", "(not recorded)")

    return (
        f"This is a CONTINUATION session for job {job_id}.\n\n"
        f"Prior stages have already been executed. Do NOT re-run them.\n\n"
        f"=== Checkpoint {cp_number} ({cp_id}) Summary ===\n"
        f"Stage that completed: {stage}\n\n"
        f"Findings:\n{findings}\n\n"
        f"Decision made by prior session:\n{decision_made}\n\n"
        f"=== User Response to Checkpoint {cp_number} ===\n"
        f"{response_text}\n\n"
        f"=== Output files that already exist — do not regenerate ===\n"
        f"{existing_str}\n\n"
        f"=== Your task ===\n"
        f"Read CLAUDE.md for the full pipeline specification and remaining stages.\n"
        f'Continue the pipeline from the stage immediately after "{stage}".\n'
        f"Apply the user's response to any decisions it affects.\n"
        f"For any remaining blocking checkpoints: write the checkpoint file with "
        f'status "awaiting_response", then exit cleanly (exit code 0). '
        f"Do not poll — the runner will re-invoke you with the response."
    )


def _expected_final_cp_number(job_dir: Path) -> int | None:
    """Return the highest CP number from interactive_checkpoints in job_config.json.

    Returns None in autonomous mode (no interactive checkpoints) or if
    job_config.json is missing or unparseable.
    """
    try:
        config = json.loads((job_dir / "job_config.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None

    cp_ids: list[str] = config.get("interactive_checkpoints") or []
    if not cp_ids:
        return None

    numbers: list[int] = []
    for cp_id in cp_ids:
        m = re.search(r"CP(\d+)", cp_id)
        if m:
            numbers.append(int(m.group(1)))

    return max(numbers) if numbers else None


def _pipeline_complete(job_dir: Path) -> tuple[bool, str]:
    """Check whether the pipeline ran to its expected final checkpoint.

    Returns (is_complete, failure_reason).
      (True,  "")       — pipeline completed as expected
      (False, reason)   — final checkpoint not found; likely context exhaustion
    """
    final_n = _expected_final_cp_number(job_dir)
    if final_n is None:
        # Autonomous mode — no expected final checkpoint; trust exit code alone
        return True, ""

    final_cp_file = job_dir / "output" / f"checkpoint_{final_n}.json"
    if not final_cp_file.exists():
        return False, (
            f"pipeline exited before final checkpoint "
            f"(checkpoint_{final_n}.json not found) — "
            f"likely context exhaustion mid-stage"
        )

    return True, ""


# ── Subprocess launcher ────────────────────────────────────────────────────────

async def _launch_subprocess(job_dir: Path, prompt: str, log_path: Path) -> int:
    """Launch one claude --print session, appending stdout/stderr to log_path.

    Always appends so the full log is preserved across continuation sessions.
    Returns the process exit code.
    """
    subprocess_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    with open(log_path, "a") as log_file:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN,
            "--dangerously-skip-permissions",
            "--print",
            prompt,
            cwd=str(job_dir),
            stdout=log_file,
            stderr=log_file,
            env=subprocess_env,
        )
        return await proc.wait()


# ── Backend implementations ────────────────────────────────────────────────────

_INITIAL_PROMPT = (
    "Read CLAUDE.md, analysis_brief.txt, and job_config.json. "
    "Execute the pipeline autonomously following the Autonomous Checkpoint Protocol in CLAUDE.md."
)


async def run_job_local(
    job_id: str,
    job_dir: Path,
    interactive_checkpoints: list[str] = [],
) -> None:
    """Run a job locally using Claude Code CLI with external checkpoint management.

    Execution model — a loop:
    1. Launch claude --print with the current prompt.
    2. On subprocess exit, scan output/ for a checkpoint_N.json with
       status='awaiting_response'. If found, transition to 'awaiting_response'
       and poll for the response file using Python asyncio (no Claude involvement).
    3. When the response arrives (or times out), build a handoff prompt summarising
       prior decisions and re-launch in a fresh --print session with a clean context.
    4. Repeat until no blocking checkpoint is detected on exit, then run the
       completion guard before marking the job done.
    """
    update_status(
        job_dir,
        "running",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    log_path = job_dir / "job.log"

    if os.getenv("TEST_MODE", "false").lower() == "true":
        with open(log_path, "w") as f:
            f.write("TEST_MODE: mock job complete\n")
            f.write(f"Job ID: {job_id}\n")
            f.write(f"Completed at: {datetime.now(timezone.utc).isoformat()}\n")
        update_status(
            job_dir,
            "completed",
            exit_code=0,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return

    prompt = _INITIAL_PROMPT
    session_number = 0

    while True:
        session_number += 1

        if session_number > 1:
            # Write a visible session boundary into the log
            with open(log_path, "a") as f:
                ts = datetime.now(timezone.utc).isoformat()
                f.write(
                    f"\n{'=' * 72}\n"
                    f"CONTINUATION SESSION {session_number} — {ts}\n"
                    f"{'=' * 72}\n\n"
                )

        exit_code = await _launch_subprocess(job_dir, prompt, log_path)

        if exit_code != 0:
            update_status(
                job_dir,
                "failed",
                exit_code=exit_code,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        awaiting_cp = find_awaiting_checkpoint(job_dir)

        if awaiting_cp is None:
            # No blocking checkpoint on exit — run completion guard
            is_complete, failure_reason = _pipeline_complete(job_dir)
            if is_complete:
                update_status(
                    job_dir,
                    "completed",
                    exit_code=0,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                update_status(
                    job_dir,
                    "failed",
                    exit_code=0,
                    failure_reason=failure_reason,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            return

        # Subprocess exited cleanly at a blocking checkpoint — hold in waiting state
        update_status(
            job_dir,
            "awaiting_response",
            awaiting_checkpoint=awaiting_cp.get("checkpoint_id"),
        )

        response = await poll_for_response(job_dir, awaiting_cp)

        if response is None:
            response_text = (
                "No user response received within the timeout period. "
                "Use the decision documented in checkpoint.decision_made and proceed."
            )
        else:
            response_text = response.get("input", str(response))
            _mark_checkpoint_responded(awaiting_cp)

        update_status(
            job_dir,
            "running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        prompt = build_handoff_prompt(job_id, job_dir, awaiting_cp, response_text)


async def run_job_docker(
    job_id: str,
    job_dir: Path,
    interactive_checkpoints: list[str] = [],
) -> None:
    raise NotImplementedError(
        "Docker backend not yet implemented. "
        "Set JOB_BACKEND=local in .env to use local execution."
    )


async def run_job_slurm(
    job_id: str,
    job_dir: Path,
    interactive_checkpoints: list[str] = [],
) -> None:
    raise NotImplementedError("SLURM backend not yet implemented.")


async def run_job_lsf(
    job_id: str,
    job_dir: Path,
    interactive_checkpoints: list[str] = [],
) -> None:
    raise NotImplementedError("LSF backend not yet implemented.")


# ── Dispatcher ─────────────────────────────────────────────────────────────────

async def dispatch_job(
    job_id: str,
    job_dir: Path,
    interactive_checkpoints: list[str] = [],
) -> None:
    """Acquire the concurrency semaphore and dispatch to the configured backend.

    interactive_checkpoints is forwarded to the backend runner for informational
    purposes; the checkpoint protocol is already baked into CLAUDE.md.
    """
    backend = os.getenv("JOB_BACKEND", "local").lower()

    runners = {
        "local": run_job_local,
        "docker": run_job_docker,
        "slurm": run_job_slurm,
        "lsf": run_job_lsf,
    }

    if backend not in runners:
        raise ValueError(f"Unknown JOB_BACKEND: {backend!r}. Choose from {list(runners)}")

    async with get_semaphore():
        await runners[backend](job_id, job_dir, interactive_checkpoints)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run a pre-built job directory through the pipeline runner")
    parser.add_argument("--job-id", required=True, help="Job ID (must match job directory name)")
    parser.add_argument("--job-dir", required=True, help="Path to job directory")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).expanduser().resolve()

    if not job_dir.exists():
        print(f"ERROR: job directory not found: {job_dir}")
        raise SystemExit(1)

    if not (job_dir / "status.json").exists():
        status_data = {
            "job_id": args.job_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": "IntegratePublicData"
        }
        (job_dir / "status.json").write_text(json.dumps(status_data, indent=2))
        print(f"Created status.json for {args.job_id}")

    if not (job_dir / "output").exists():
        (job_dir / "output").mkdir()
        print("Created output/ directory")

    asyncio.run(run_job_local(args.job_id, job_dir))
