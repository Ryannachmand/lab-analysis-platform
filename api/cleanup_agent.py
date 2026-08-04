"""Cleanup agent: reviews job outputs, interprets results, and proposes skills updates."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from api.utils import call_claude_with_retry

SKILLS_DIR_DEFAULT = "./claude-skills-v2"
JOBS_DIR_DEFAULT = str(Path(__file__).parent.parent / "jobs")

# ─── File classification ──────────────────────────────────────────────────────

# Always delete silently — these are never needed after the pipeline moves on.
ALWAYS_DELETE_PATTERNS = [
    r"^tmp_",
    r"^scratch_",
    r"\.tmp$",
    r"_partial\.rds$",
    r"_temp\.rds$",
]

# Always protect — never delete regardless of anything.
ALWAYS_KEEP_PATTERNS = [
    r"checkpoint_\d+\.json$",
    r"checkpoint_\d+_summary\.json$",
    r"job\.log$",
    r"CLAUDE\.md$",
    r"analysis_brief\.txt$",
    r"job_config\.json$",
    # Final merged/integrated objects
    r"_harmony\.rds$",
    r"_integrated\.rds$",
    r"_annotated\.rds$",
    r"_merged_raw\.rds$",
    # All plots and tables
    r"\.pdf$",
    r"\.png$",
    r"\.csv$",
    r"\.yaml$",
    r"\.yml$",
]

# Flag for user review — re-generatable but potentially large
FLAGGABLE_PATTERNS = [
    r"_filtered\.rds$",
    r"_qc\.rds$",
    r"_stage\d+\.rds$",
    r"individual_objects/.*\.rds$",
]


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(re.search(p, name, re.IGNORECASE) for p in patterns)


def _classify_files(output_dir: Path) -> dict[str, list[Path]]:
    """Walk output_dir and classify every file into delete / keep / flag buckets."""
    result: dict[str, list[Path]] = {"delete": [], "keep": [], "flag": []}
    if not output_dir.exists():
        return result

    for f in output_dir.rglob("*"):
        if not f.is_file():
            continue
        name = f.name
        rel = str(f.relative_to(output_dir))

        if _matches_any(rel, ALWAYS_KEEP_PATTERNS) or _matches_any(name, ALWAYS_KEEP_PATTERNS):
            result["keep"].append(f)
        elif _matches_any(rel, ALWAYS_DELETE_PATTERNS) or _matches_any(name, ALWAYS_DELETE_PATTERNS):
            result["delete"].append(f)
        elif _matches_any(rel, FLAGGABLE_PATTERNS) or _matches_any(name, FLAGGABLE_PATTERNS):
            result["flag"].append(f)
        else:
            # Unclassified — keep by default (conservative)
            result["keep"].append(f)

    return result


def _file_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


# ─── Summary / log reading ────────────────────────────────────────────────────

def _read_checkpoint_summaries(output_dir: Path) -> list[dict]:
    summaries = []
    for f in sorted(output_dir.glob("checkpoint_*_summary.json")):
        try:
            summaries.append(json.loads(f.read_text()))
        except Exception:
            pass
    return summaries


def _read_checkpoint_jsons(output_dir: Path) -> list[dict]:
    checkpoints = []
    for f in sorted(output_dir.glob("checkpoint_*.json")):
        if "_summary" in f.name:
            continue
        try:
            checkpoints.append(json.loads(f.read_text()))
        except Exception:
            pass
    return checkpoints


def _read_job_log(job_dir: Path, max_chars: int = 8000) -> str:
    log_path = job_dir / "job.log"
    if not log_path.exists():
        return ""
    text = log_path.read_text(errors="replace")
    # Return last max_chars — tail is most relevant
    return text[-max_chars:] if len(text) > max_chars else text


def _read_job_config(job_dir: Path) -> dict:
    cfg_path = job_dir / "job_config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


# ─── Skills file helpers ──────────────────────────────────────────────────────

def _list_skills_files(skills_dir: Path) -> list[Path]:
    """Return all .md and .yaml files in the skills directory."""
    files = []
    for ext in ("*.md", "*.yaml", "*.yml"):
        files.extend(skills_dir.rglob(ext))
    return sorted(files)


def _read_validated_examples(skills_dir: Path) -> str:
    p = skills_dir / "lab_context" / "validated_examples.yaml"
    if p.exists():
        return p.read_text()
    return ""


def _read_lab_context(skills_dir: Path) -> str:
    p = skills_dir / "lab_context.md"
    if p.exists():
        return p.read_text()
    return ""


# ─── API calls ────────────────────────────────────────────────────────────────

async def _call_claude(
    client: anthropic.AsyncAnthropic,
    system: str,
    user: str,
    max_tokens: int = 4096,
) -> str:
    message = await call_claude_with_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return message.content[0].text.strip()


# ─── Main CleanupAgent class ──────────────────────────────────────────────────

class CleanupAgent:
    def __init__(self, skills_dir: Path | None = None, jobs_dir: Path | None = None):
        self.skills_dir = Path(skills_dir or os.getenv("SKILLS_DIR", SKILLS_DIR_DEFAULT))
        self.jobs_dir = Path(jobs_dir or os.getenv("JOBS_DIR", JOBS_DIR_DEFAULT))
        self.client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _output_dir(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "output"

    # ── Step 1: File cleanup ──────────────────────────────────────────────────

    def run_file_cleanup(self, job_id: str) -> dict[str, Any]:
        """Silently delete always-unnecessary files. Return report of what happened
        and what is being flagged for user review."""
        output_dir = self._output_dir(job_id)
        classified = _classify_files(output_dir)

        deleted = []
        for f in classified["delete"]:
            try:
                f.unlink()
                deleted.append(str(f.relative_to(output_dir)))
            except OSError:
                pass

        flagged = [
            {
                "path": str(f.relative_to(output_dir)),
                "size_mb": round(_file_size_mb(f), 1),
            }
            for f in classified["flag"]
        ]

        kept = [str(f.relative_to(output_dir)) for f in classified["keep"]]

        return {
            "deleted": deleted,
            "flagged_for_review": flagged,
            "kept": kept,
            "total_flagged_mb": round(sum(x["size_mb"] for x in flagged), 1),
        }

    # ── Step 2: Result interpretation ────────────────────────────────────────

    async def interpret_results(
        self, job_id: str, deep_interpret: bool = False
    ) -> dict[str, Any]:
        """Read checkpoint summaries and job log, interpret results biologically."""
        output_dir = self._output_dir(job_id)
        job_dir = self._job_dir(job_id)

        summaries = _read_checkpoint_summaries(output_dir)
        checkpoints = _read_checkpoint_jsons(output_dir)
        job_log_tail = _read_job_log(job_dir)
        job_config = _read_job_config(job_dir)

        if not summaries and not checkpoints:
            return {
                "status": "no_data",
                "message": "No checkpoint summaries found. Was the pipeline instrumented with the summary protocol?",
            }

        depth_instruction = (
            "Provide a detailed biological interpretation, including likely cell identity, "
            "potential biological significance of marker genes, integration quality assessment, "
            "and any anomalies that warrant follow-up experiments."
            if deep_interpret
            else
            "Provide a concise summary (3-5 sentences). Highlight the most important findings "
            "and any clear red flags. Save detailed interpretation for user-requested follow-up."
        )

        system = (
            "You are an expert single-cell RNA sequencing bioinformatician. "
            "You are reviewing the outputs of an automated scRNAseq analysis pipeline. "
            "Your job is to interpret the results clearly for a researcher, identify any "
            "technical issues or concerns, and flag anything that warrants attention.\n\n"
            f"Interpretation depth: {depth_instruction}\n\n"
            "Return ONLY valid JSON with these keys:\n"
            "  pipeline_chain        — list of pipeline names that ran\n"
            "  stages_completed      — list of stage names that completed successfully\n"
            "  stages_failed         — list of stage names that failed or were skipped unexpectedly\n"
            "  final_cell_count      — integer or null\n"
            "  cell_types_found      — list of cell type strings or null\n"
            "  integration_quality   — 'good' | 'acceptable' | 'poor' | 'unknown'\n"
            "  biological_summary    — string: plain-English summary of what was found\n"
            "  technical_concerns    — list of strings: QC issues, integration problems, etc.\n"
            "  biological_flags      — list of strings: anything biologically unexpected or interesting\n"
            "  errors_encountered    — list of strings extracted from logs/checkpoints\n"
            "  recommendations       — list of strings: what to check or do next\n"
        )

        user = (
            f"Job ID: {job_id}\n"
            f"Pipeline chain: {job_config.get('pipeline_chain', 'unknown')}\n\n"
            "Checkpoint summaries (in order):\n"
            f"{json.dumps(summaries, indent=2)}\n\n"
            "Checkpoint status records:\n"
            f"{json.dumps(checkpoints, indent=2)}\n\n"
            "Job log (tail):\n"
            f"{job_log_tail}\n\n"
            "Interpret these results and return JSON as specified."
        )

        raw = await _call_claude(self.client, system, user, max_tokens=2048)

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if "```" in raw:
                raw = raw.rsplit("```", 1)[0].strip()

        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except Exception:
            return {"raw_interpretation": raw}

    # ── Step 3: Propose skills updates (requires user feedback) ───────────────

    async def propose_skills_updates(
        self, job_id: str, feedback: str, interpretation: dict
    ) -> list[dict[str, Any]]:
        """Given user feedback and interpretation, propose changes to skills files.
        Returns a list of proposed changes, each with a unique change_id."""

        output_dir = self._output_dir(job_id)
        summaries = _read_checkpoint_summaries(output_dir)
        job_config = _read_job_config(self._job_dir(job_id))

        validated_examples = _read_validated_examples(self.skills_dir)
        lab_context = _read_lab_context(self.skills_dir)

        # Load relevant methods files based on pipeline chain
        pipeline_chain: list[str] = job_config.get("pipeline_chain", [])
        methods_content = self._load_methods_files(pipeline_chain)

        system = (
            "You are a bioinformatics skills librarian. You maintain a library of reusable "
            "pipeline knowledge used by an AI analysis platform.\n\n"
            "You will be given:\n"
            "- Checkpoint summaries from a completed job\n"
            "- The researcher's feedback about the job\n"
            "- Interpretation of the results\n"
            "- Current contents of relevant skills files\n\n"
            "Your task: propose specific, concrete updates to the skills library that capture "
            "what was learned from this job. Each proposed change should be self-contained "
            "and independently approvable.\n\n"
            "Return ONLY valid JSON: a list of proposed change objects, each with:\n"
            "  change_id          — short unique slug (e.g. 'harmony-params-mouse-bm')\n"
            "  target_file        — relative path within skills dir (e.g. 'pipelines/IntegratePublicData/methods/load_formats.md')\n"
            "  change_type        — 'append' | 'update_section' | 'new_example'\n"
            "  plain_english      — 1-2 sentence description of what this change captures and why\n"
            "  proposed_content   — the exact text to append or the new example YAML block\n"
            "  confidence         — float 0.0-1.0: how confident you are this is a useful addition\n\n"
            "Rules:\n"
            "- Only propose changes backed by concrete evidence from this job.\n"
            "- Do not propose vague 'general improvements' — every change must reference "
            "specific parameters, errors, or outcomes from this job.\n"
            "- Prefer appending to existing files over creating new ones.\n"
            "- validated_examples.yaml entries must follow the existing YAML format exactly.\n"
            "- If the job had significant errors or unclear results, err on the side of fewer, "
            "more cautious proposals.\n"
            "- Return an empty list [] if no concrete learnings warrant a skills update."
        )

        user = (
            f"Job ID: {job_id}\n"
            f"Pipeline chain: {pipeline_chain}\n\n"
            f"Researcher feedback:\n{feedback}\n\n"
            f"Job interpretation:\n{json.dumps(interpretation, indent=2)}\n\n"
            f"Checkpoint summaries:\n{json.dumps(summaries, indent=2)}\n\n"
            f"Current validated_examples.yaml:\n{validated_examples}\n\n"
            f"Current lab_context.md:\n{lab_context}\n\n"
            f"Current methods files:\n{methods_content}\n\n"
            "Propose skills library updates based on what was learned. Return a JSON list."
        )

        raw = await _call_claude(self.client, system, user, max_tokens=4096)

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if "```" in raw:
                raw = raw.rsplit("```", 1)[0].strip()

        try:
            # Find outermost list
            start = raw.index("[")
            end = raw.rindex("]") + 1
            proposals = json.loads(raw[start:end])
            return proposals if isinstance(proposals, list) else []
        except Exception:
            return []

    def _load_methods_files(self, pipeline_chain: list[str]) -> str:
        sections = []
        for pipeline_name in pipeline_chain:
            methods_dir = self.skills_dir / "pipelines" / pipeline_name / "methods"
            if not methods_dir.exists():
                continue
            for f in sorted(methods_dir.glob("*.md")):
                content = f.read_text(errors="replace")
                rel = f.relative_to(self.skills_dir)
                sections.append(f"### {rel}\n{content}")
        return "\n\n".join(sections)

    # ── Step 4: Apply approved changes ────────────────────────────────────────

    def apply_approved_changes(
        self, approved_changes: list[dict], all_proposals: list[dict]
    ) -> list[dict[str, Any]]:
        """Write approved proposed changes to skills files. Returns results per change."""
        approved_ids = {c["change_id"] for c in approved_changes}
        results = []

        for proposal in all_proposals:
            change_id = proposal.get("change_id", "")
            if change_id not in approved_ids:
                continue

            target = proposal.get("target_file", "")
            content = proposal.get("proposed_content", "")
            change_type = proposal.get("change_type", "append")

            if not target or not content:
                results.append({"change_id": change_id, "status": "skipped", "reason": "missing target or content"})
                continue

            target_path = self.skills_dir / target

            try:
                if change_type == "append" or change_type == "new_example":
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with target_path.open("a") as fh:
                        fh.write(f"\n\n{content}\n")
                    results.append({"change_id": change_id, "status": "applied", "file": target})

                elif change_type == "update_section":
                    # For section updates, append with a dated note — safer than in-place editing
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    with target_path.open("a") as fh:
                        fh.write(f"\n\n<!-- Updated {timestamp} -->\n{content}\n")
                    results.append({"change_id": change_id, "status": "applied", "file": target})

                else:
                    results.append({"change_id": change_id, "status": "skipped", "reason": f"unknown change_type: {change_type}"})

            except OSError as e:
                results.append({"change_id": change_id, "status": "error", "reason": str(e)})

        return results

    # ── Orchestrator: full cleanup run ────────────────────────────────────────

    async def run(
        self,
        job_id: str,
        feedback: str | None = None,
        deep_interpret: bool = False,
    ) -> dict[str, Any]:
        """Full cleanup run. Always does file cleanup + interpretation.
        If feedback is provided, also generates skills update proposals."""

        job_dir = self._job_dir(job_id)
        if not job_dir.exists():
            return {"status": "error", "message": f"Job {job_id} not found."}

        # Always: file cleanup
        file_report = self.run_file_cleanup(job_id)

        # Always: interpret results
        interpretation = await self.interpret_results(job_id, deep_interpret=deep_interpret)

        result: dict[str, Any] = {
            "job_id": job_id,
            "file_cleanup": file_report,
            "interpretation": interpretation,
            "proposals": [],
        }

        # Only if feedback provided: propose skills updates
        if feedback:
            proposals = await self.propose_skills_updates(job_id, feedback, interpretation)
            result["proposals"] = proposals

        return result
