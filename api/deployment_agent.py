"""Deployment agent: translates a free-text description into pipeline files."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import logging

import anthropic
from fastapi import HTTPException

from api.utils import call_claude_with_retry

logger = logging.getLogger(__name__)

SKILLS_DIR_DEFAULT = "./claude-skills-v2"
JOBS_DIR_DEFAULT = str(Path(__file__).parent.parent / "jobs")


_CHECKPOINT_SUMMARY_PROTOCOL = """
## Checkpoint Summary Protocol

**At every checkpoint** (blocking and non-blocking), in addition to writing `checkpoint_N.json`,
you MUST also write `output/checkpoint_N_summary.json` using the schema below.

This file is read by the cleanup agent for result interpretation and skills refinement.
Write it with as much detail as the pipeline has produced at that stage — leave fields `null`
if genuinely not yet available, but never omit the file.

### checkpoint_N_summary.json schema

```json
{
  "checkpoint_id": "IntegratePublicData:CP2",
  "pipeline": "IntegratePublicData",
  "stage": "Quality Control",
  "timestamp": "<ISO timestamp>",

  "cell_counts": {
    "before": 120000,
    "after": 87376,
    "removed": 32624,
    "removal_pct": 27.2
  },

  "sample_breakdown": {
    "GSE183320": {"before": 40000, "after": 32000},
    "GSE236566": {"before": 50000, "after": 38000}
  },

  "qc_thresholds_applied": {
    "min_genes": 200,
    "max_genes": 6000,
    "max_pct_mt": 10.0
  },

  "metrics": {
    "median_genes_per_cell": 2100,
    "median_umi_per_cell": 5400,
    "median_pct_mt": 4.2,
    "n_variable_features": 3000,
    "n_pcs_used": 30,
    "clustering_resolution": 0.3
  },

  "clusters": {
    "n_clusters": 18,
    "sizes": {"0": 4200, "1": 3800}
  },

  "cell_types_identified": ["Arteriolar EC", "Capillary EC", "Sinusoidal EC"],

  "top_markers": {
    "cluster_0": ["Pecam1", "Cdh5", "Kdr"],
    "cluster_1": ["Ly6c1", "Cxcl12", "Gja5"]
  },

  "integration": {
    "method": "Harmony",
    "batch_vars": ["sample_id", "dataset"],
    "n_datasets": 3,
    "dataset_names": ["GSE183320", "GSE236566", "GSE210543"]
  },

  "label_transfer": {
    "method": "Seurat",
    "reference_used": "HumanBoneMarrow_reference.rds",
    "confidence_threshold": 0.75,
    "low_confidence_pct": 8.3
  },

  "warnings": [
    "Sample GSE183320 had low median gene count (800) — may reflect low quality"
  ],

  "errors": [],

  "notes": "Free text: anything unusual, decisions made, parameters tuned at this stage."
}
```

**Rules:**
- Use `null` for fields not yet applicable at this stage (e.g. `clusters` is null at a QC stage).
- `warnings` and `errors` must always be arrays — empty array if none.
- `cell_counts.before` reflects the count entering this stage; `after` reflects post-filter/processing.
- For integration stages, populate the `integration` block fully.
- For annotation stages, populate `cell_types_identified` and `top_markers` fully.
- File must be valid JSON — no comments, no trailing commas.
"""


def _build_checkpoint_protocol(job_config: dict) -> str:
    """Generate the Autonomous Checkpoint Protocol section to append to CLAUDE.md.

    The output varies based on whether interactive (blocking) checkpoints are configured
    in job_config. Blocking checkpoints cause the agent to write a checkpoint file and
    exit; the runner handles the wait and re-invocation. Non-blocking checkpoints write
    JSON and continue immediately.
    """
    blocking_cps: list[str] = job_config.get("interactive_checkpoints", [])
    intents: dict[str, str] = job_config.get("interactive_checkpoint_intents", {})

    if blocking_cps:
        lines = []
        for cp in blocking_cps:
            intent = intents.get(cp, "")
            if intent:
                lines.append(f"  - **{cp}** — {intent}")
            else:
                lines.append(f"  - **{cp}**")
        blocking_section = "\n".join(lines)
    else:
        blocking_section = "  None — all checkpoints run non-blocking (autonomous mode)."

    return """\

## Autonomous Checkpoint Protocol

**At startup:** Read `job_config.json` to determine the execution mode and which
checkpoints require interactive review before proceeding.

**Checkpoint ID format — REQUIRED:** When writing `checkpoint_id` to any JSON
file (blocking or non-blocking), you MUST prefix it with the pipeline name and
a colon. Use the pipeline name exactly as it appears in `pipeline_chain` in
this CLAUDE.md. Examples:
- `"checkpoint_id": "IntegratePublicData:CP3"` ← correct
- `"checkpoint_id": "LargeDataset:CP1"` ← correct
- `"checkpoint_id": "CP3"` ← WRONG — missing pipeline prefix

This rule applies to ALL checkpoint JSON writes. Prefixed IDs prevent
checkpoint_id collisions when multiple pipelines in a chain share the same
CP numbering (e.g. both use CP1, CP2, CP3).

Match on the CP suffix only when determining whether to block — ignore the
pipeline prefix for filename numbering (`checkpoint_3.json`, not
`checkpoint_IntegratePublicData_3.json`).

### Blocking checkpoints (halt and wait for human response)

""" + blocking_section + """

For each **blocking checkpoint** listed above, when you reach it:
1. Write `output/checkpoint_N.json` with `"status": "awaiting_response"` and all fields below.
   Set `timeout_at` to `written_at + 36 hours`.
2. Write `output/checkpoint_N_summary.json` using the Checkpoint Summary Protocol schema above.
3. **Exit immediately with exit code 0.** Do not poll. Do not wait for a response.
   The pipeline runner will detect this checkpoint, wait for the user\'s response,
   and re-invoke you in a fresh session. When re-invoked, you will receive a handoff
   prompt that summarises prior decisions and contains the user\'s response — read it,
   then continue from the stage immediately after this checkpoint.

### Non-blocking checkpoints (all other checkpoints)

For each **non-blocking checkpoint**, when you reach it:
1. Write `output/checkpoint_N.json` with `"status": "completed"`.
2. Write `output/checkpoint_N_summary.json` using the Checkpoint Summary Protocol schema above.
3. Document your findings and decision made.
4. Continue immediately — do NOT pause or wait.

### checkpoint_N.json schema

```json
{
  "checkpoint_number": 3,
  "checkpoint_id": "LargeDataset:CP3",
  "stage": "<stage name>",
  "status": "awaiting_response | completed | timed_out | skipped",
  "findings": "<what the agent found>",
  "decision_made": "<what it decided or will decide>",
  "alternatives_considered": ["..."],
  "data_files": ["<relative paths to relevant output files for this checkpoint>"],
  "written_at": "<ISO timestamp>",
  "responded_at": null,
  "timeout_at": "<ISO timestamp, written_at + 36h>"
}
```

Use sequential integers for N matching the pipeline\'s CP numbering (CP1 → checkpoint_1.json, etc.).
""" + _CHECKPOINT_SUMMARY_PROTOCOL


def _read_file_safe(path: Path) -> str:
    """Read a file, returning empty string if missing."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


class DeploymentAgent:
    """Translates a free-text analysis description into a ready-to-run job directory."""

    def __init__(self, skills_dir: Path | None = None, jobs_dir: Path | None = None):
        self.skills_dir = Path(skills_dir or os.getenv("SKILLS_DIR", SKILLS_DIR_DEFAULT))
        self.jobs_dir = Path(jobs_dir or os.getenv("JOBS_DIR", JOBS_DIR_DEFAULT))
        self.client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _load_pipeline_mds(self, pipeline_chain: list[str]) -> str:
        """Load pipeline.md for each pipeline in the chain, concatenated with headers.

        Returns a string with each pipeline's content prefixed by a section header:
        "## Pipeline: IntegratePublicData\\n{content}\\n\\n## Pipeline: LargeDataset\\n{content}"

        Tries an exact directory name match per pipeline, then a case-insensitive
        fallback. If a pipeline is not found, a placeholder comment is included so
        the resolver still sees the pipeline name in context.
        """
        pipelines_dir = self.skills_dir / "pipelines"
        sections: list[str] = []

        for pipeline_name in pipeline_chain:
            pipeline_md = ""
            candidate = pipelines_dir / pipeline_name
            if candidate.is_dir():
                pipeline_md = _read_file_safe(candidate / "pipeline.md")
            else:
                try:
                    for d in pipelines_dir.iterdir():
                        if d.is_dir() and d.name.lower() == pipeline_name.lower():
                            pipeline_md = _read_file_safe(d / "pipeline.md")
                            break
                except FileNotFoundError:
                    pass

            if pipeline_md:
                sections.append(self._extract_checkpoint_table(pipeline_md, pipeline_name))
            else:
                sections.append(
                    f"## Pipeline: {pipeline_name} checkpoints\n"
                    f"# (pipeline.md not found for {pipeline_name})"
                )

        return "\n\n".join(sections)

    def _extract_checkpoint_table(self, pipeline_md: str, pipeline_name: str) -> str:
        """Extract only the checkpoint summary table from a pipeline.md string.

        Looks for a markdown table containing CP | Stage | User action headers.
        If found, returns just that table with a pipeline header above it.
        If not found, falls back to extracting any line containing 'CP' followed
        by a digit (e.g. 'CP1', 'CP2') from the full text, returning those lines
        with context. This ensures the resolver always gets something useful even
        if the table format changes.
        """
        lines = pipeline_md.splitlines()

        # Find the table header line containing all three markers
        header_idx = None
        for i, line in enumerate(lines):
            if "CP" in line and "Stage" in line and "User action" in line:
                header_idx = i
                break

        if header_idx is not None:
            # Collect header + all immediately following "|" lines (separator + data rows)
            table_lines = [lines[header_idx]]
            for line in lines[header_idx + 1:]:
                if line.startswith("|"):
                    table_lines.append(line)
                else:
                    break
            return (
                f"## Pipeline: {pipeline_name} checkpoints\n"
                + "\n".join(table_lines)
            )

        # Fallback: extract any line containing CPn (e.g. CP1, CP2)
        cp_lines = [line for line in lines if re.search(r'CP\d', line)]
        if cp_lines:
            return (
                f"## Pipeline: {pipeline_name} checkpoints\n"
                "# (extracted from pipeline text, no table found)\n"
                + "\n".join(cp_lines)
            )

        return f"## Pipeline: {pipeline_name} checkpoints\n# (no checkpoint information found)"

    async def _resolve_checkpoints(
        self,
        pipeline_chain: list[str],
        interactive_checkpoints: list[str],
    ) -> dict[str, str]:
        """Map plain-English checkpoint intents to prefixed CP IDs across the pipeline chain.

        Makes a small focused API call against all pipeline checkpoint tables in the chain.
        Returns a dict with prefixed CP IDs as keys:
        {"IntegratePublicData:CP3": "quality control", "LargeDataset:CP7": "cluster annotation"}.
        Returns {} if interactive_checkpoints is empty, the chain is empty, or
        the API call fails for any reason.
        """
        if not interactive_checkpoints:
            return {}

        if not pipeline_chain:
            return {}

        pipeline_docs = self._load_pipeline_mds(pipeline_chain)

        system = (
            "You are a checkpoint resolver for a bioinformatics pipeline chain.\n\n"
            "You will be given documentation for one or more pipelines (each section "
            "prefixed with ## Pipeline: <name>) and a list of plain-English intents the "
            "user wants as interactive checkpoints.\n\n"
            "Your task: match each plain-English intent to the most appropriate CP ID, "
            "prefixed with the pipeline name it belongs to.\n\n"
            "CP ID format: \"<PipelineName>:<CPID>\" "
            "(e.g. \"IntegratePublicData:CP3\", \"LargeDataset:CP7\"). "
            "Always include the pipeline prefix — bare CP IDs are not valid return values.\n\n"
            "Return ONLY valid JSON — a single object with two keys:\n"
            "  \"resolved\": object where keys are prefixed CP IDs "
            "(e.g. \"IntegratePublicData:CP3\") and values are the matching intent strings "
            "from the input list\n"
            "  \"clarifying_questions\": list of strings for any intents that are genuinely "
            "ambiguous and cannot be resolved (empty list if all resolved cleanly)\n\n"
            "Rules:\n"
            "- Only use CP IDs that actually appear in the provided pipeline documentation.\n"
            "- Always prefix the CP ID with the pipeline name it came from.\n"
            "- If an intent clearly belongs to a later pipeline in the chain, map it to "
            "that pipeline's CP ID with the correct prefix.\n"
            "- If an intent is ambiguous between two checkpoints, add a clarifying question.\n"
            "- Do not invent CP IDs that are not in the documentation.\n"
            "- The pipeline chain runs in order: the first pipeline listed runs first, "
            "the second runs after. When an intent could match checkpoints in multiple "
            "pipelines, prefer the earlier pipeline unless the intent clearly describes "
            "a later-pipeline activity (e.g. 'cluster annotation' and 'marker extraction' "
            "belong to post-integration analysis, not data loading).\n"
            "- 'Quality control', 'filtering', 'cell filtering', 'QC', and similar intents "
            "refer to data loading and filtering steps in the FIRST pipeline, not QC "
            "plots in downstream pipelines.\n"
            "- 'Cluster annotation', 'cell type annotation', 'marker review', and similar "
            "intents refer to post-clustering annotation steps, typically in the LAST "
            "pipeline in the chain."
        )

        user = (
            f"Pipeline chain: {' → '.join(pipeline_chain)}\n\n"
            "Checkpoint tables for each pipeline in the chain:\n"
            f"{pipeline_docs}\n\n"
            "User's requested interactive checkpoints (plain English):\n"
            + "\n".join(f"- {intent}" for intent in interactive_checkpoints)
            + "\n\nMatch each intent to the most appropriate checkpoint. "
            "Return JSON with keys: resolved (object) and clarifying_questions (list)."
        )

        try:
            message = await self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception:
            return {}

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if "```" in raw:
                raw = raw.rsplit("```", 1)[0].strip()

        try:
            start = raw.index('{')
            end = raw.rindex('}') + 1
            data = json.loads(raw[start:end])
            return data.get("resolved") or {}
        except Exception:
            return {}

    def _load_pipeline_context(self) -> str:
        """Load all pipeline brief_templates and pipeline.md files from the skills directory."""
        pipelines_dir = self.skills_dir / "pipelines"
        sections: list[str] = []

        for pipeline_dir in sorted(pipelines_dir.iterdir()):
            if not pipeline_dir.is_dir():
                continue

            name = pipeline_dir.name
            pipeline_md = _read_file_safe(pipeline_dir / "pipeline.md")
            brief_template = _read_file_safe(pipeline_dir / "brief_template.txt")

            if not (pipeline_md or brief_template):
                continue

            section = f"## Pipeline: {name}\n\n"
            if pipeline_md:
                section += f"### pipeline.md\n{pipeline_md}\n\n"
            if brief_template:
                section += f"### brief_template.txt\n{brief_template}\n\n"
            sections.append(section)

        lab_context = _read_file_safe(self.skills_dir / "lab_context.md")
        if lab_context:
            sections.append(f"## Lab Context and Conventions\n\n{lab_context}\n\n")

        validated_examples = _read_file_safe(
            self.skills_dir / "lab_context" / "validated_examples.yaml"
        )
        if validated_examples:
            sections.append(f"## Validated Job Examples\n\n{validated_examples}\n\n")

        return "\n".join(sections)

    def _system_prompt(self) -> str:
        return (
            "You are a bioinformatics pipeline deployment assistant. Your job is to translate "
            "a free-text analysis description into: (1) a selected pipeline, (2) a filled "
            "analysis_brief.txt, and (3) a CLAUDE.md for the job.\n\n"
            "You must return ONLY valid JSON with no markdown fences, no preamble, no explanation.\n\n"
            "Required JSON keys:\n"
            "  pipeline_selected    — string or array: single pipeline name (e.g. \"LargeDataset\") "
            "or ordered array for a chained job (e.g. [\"IntegratePublicData\", \"LargeDataset\"])\n"
            "  claude_md            — string (REQUIRED, MUST be a non-empty string): full CLAUDE.md content. "
            "Never return null, an empty string, or omit this key.\n"
            "  analysis_brief       — string (REQUIRED, MUST be a non-empty string): filled brief_template.txt content. "
            "Never return null, an empty string, or omit this key.\n"
            "  assumptions          — list of strings: what you assumed that was not explicitly stated\n"
            "  clarifying_questions — list of strings: questions you must ask before proceeding\n"
            "  confidence           — float 0.0–1.0: confidence that the generated files are correct\n"
            "  handoff_checkpoint   — string or null: CP ID where the first pipeline hands off to the "
            "second (e.g. \"CP6\"). Null if pipeline_selected is a single string.\n"
            "  summary              — string: 2-3 sentence plain-English description of what this job "
            "will do, written for a non-technical user. Never mention pipeline names, checkpoint IDs, "
            "or technical implementation details.\n\n"
            "HARD RULES — never violate these:\n"
            "1. output_dir must always be exactly: jobs/{job_id}/output/ "
            "(substitute the actual job_id provided in the user message).\n"
            "2. Never reference external paths, prior run directories, or specific "
            "file-system locations in the generated files.\n"
            "3. If the description is ambiguous about which pipeline to use, add a "
            "clarifying question — do not guess.\n"
            "5. If the description implies a sequence of pipelines (e.g. integrate public data "
            "then run downstream analysis), set pipeline_selected to a JSON array of pipeline names "
            "in order (e.g. [\"IntegratePublicData\", \"LargeDataset\"]). Determine the handoff "
            "checkpoint — the checkpoint in the first pipeline after which the second pipeline begins. "
            "Document the chain in assumptions, not in clarifying_questions. Never mention pipeline "
            "names to the user in any user-facing text.\n"
            "6. When the description provides GEO accession IDs, organism, and implies a full\n"
            "   workflow (integrate + downstream analysis), this is sufficient information to\n"
            "   proceed with confidence >= 0.85. Do not penalize confidence for details the\n"
            "   pipeline detects automatically (file formats, cell type lists, metadata fields).\n"
            "   A chained pipeline job with accession IDs and organism specified is a confident case.\n\n"
            "## Clarifying question policy\n\n"
            "Only add a question to clarifying_questions if ALL THREE of the following are true:\n"
            "1. The information cannot be reasonably inferred from the job description.\n"
            "2. A wrong assumption would cause the pipeline to fail or produce incorrect output "
            "(it is a hard blocker, not a preference).\n"
            "3. It cannot be resolved at runtime by the pipeline itself.\n\n"
            "If any one of those three conditions is not met, make the most reasonable assumption, "
            "proceed with confidence >= 0.75, and document the assumption in the assumptions list.\n\n"
            "NEVER ask about:\n"
            "- File formats (the pipeline detects these automatically)\n"
            "- Cell type lists or metadata contents (the pipeline reads these from the data)\n"
            "- Output file formats (the pipeline determines these)\n"
            "- Scope or extent of analysis when the description already implies it — interpret generously\n"
            "- Any technical execution detail the pipeline handles itself\n\n"
            "ONLY ask if ALL THREE conditions above are met (examples):\n"
            "- GEO accession IDs that are genuinely absent and required to fetch data\n"
            "- Organism / species when truly ambiguous and the pipeline cannot detect it\n"
            "- Any field in the brief_template marked required that cannot be inferred at all\n\n"
            "Default posture: assume, document, proceed. A question is a last resort."
        )

    def _user_message(self, job_id: str, description: str, clarification_answers=None) -> str:
        pipeline_context = self._load_pipeline_context()
        msg = (
            f"Job ID: {job_id}\n"
            f"output_dir for this job: jobs/{job_id}/output/\n\n"
            f"User description:\n{description}\n\n"
        )
        if clarification_answers:
            msg += "---\n\nClarifications from user:\n"
            for qa in clarification_answers:
                msg += f"Q: {qa.question}\nA: {qa.answer}\n"
            msg += "\n"
        msg += (
            "---\n\n"
            "Available pipeline templates:\n\n"
            f"{pipeline_context}\n\n"
            "Select the appropriate pipeline, fill the brief, and generate CLAUDE.md. "
            "Return only valid JSON with keys: "
            "pipeline_selected, claude_md, analysis_brief, assumptions, clarifying_questions, confidence."
        )
        return msg

    async def prepare(
        self,
        job_id: str,
        description: str,
        interactive_checkpoints: list[str] = [],
        clarification_answers=None,
    ) -> dict:
        """Run the deployment agent and optionally create the job directory.

        If confidence >= 0.75 and clarifying_questions is empty:
          - Creates jobs/{job_id}/ and jobs/{job_id}/output/
          - Normalizes pipeline_selected (string or list) into a pipeline_chain list
          - Resolves interactive_checkpoints plain-English intents to CP IDs (second API call)
          - Writes CLAUDE.md (with generated Checkpoint Protocol), analysis_brief.txt, job_config.json
          - Returns {"status": "ready", "job_id": ..., "pipeline_selected": ...,
                     "pipeline_chain": [...], "handoff_checkpoint": ..., "assumptions": [...],
                     "interactive_checkpoints": [...], "mode": "...", "summary": "...",
                     "confidence": ...}

        Otherwise returns {"status": "needs_clarification", "clarifying_questions": [...], "confidence": ...}
        without writing any files.
        """
        message = await call_claude_with_retry(
            self.client,
            model="claude-sonnet-4-6",
            max_tokens=32000,
            system=self._system_prompt(),
            messages=[{"role": "user", "content": self._user_message(job_id, description, clarification_answers)}],
        )

        stop_reason = message.stop_reason
        logger.info("[deployment_agent] prepare() stop_reason=%s job_id=%s", stop_reason, job_id)

        raw = message.content[0].text.strip()

        # Strip markdown fences if the model returned them despite instructions
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if "```" in raw:
                raw = raw.rsplit("```", 1)[0].strip()

        start = raw.index('{')
        end = raw.rindex('}') + 1
        try:
            result = json.loads(raw[start:end])
        except json.JSONDecodeError as e:
            if stop_reason == "max_tokens":
                raise HTTPException(
                    status_code=422,
                    detail="Deployment agent response truncated at max_tokens. Try a shorter description or increase max_tokens.",
                )
            raise HTTPException(
                status_code=422,
                detail=f"Deployment agent returned malformed JSON: {str(e)[:200]}",
            )

        # Validate required string fields immediately after parse — fail loudly rather than
        # propagating None or empty strings into job files.
        required_str_keys = ["claude_md", "analysis_brief"]
        for key in required_str_keys:
            value = result.get(key)
            if value is None or not isinstance(value, str) or not value.strip():
                logger.debug(
                    "[deployment_agent] full result dict (prepare validation failure):\n%s",
                    json.dumps(result, indent=2)[:5000],
                )
                logger.error(
                    "[deployment_agent] required field %r is invalid: value=%r keys_present=%s job_id=%s",
                    key,
                    str(value)[:500] if value is not None else None,
                    list(result.keys()),
                    job_id,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Deployment agent response missing required field: {key}. "
                        f"The model returned keys: {list(result.keys())}. "
                        "Check api.log for the full response."
                    ),
                )

        confidence = float(result.get("confidence", 0.0))
        clarifying_questions: list[str] = result.get("clarifying_questions") or []

        if clarifying_questions:
            return {
                "status": "needs_clarification",
                "clarifying_questions": clarifying_questions,
                "confidence": confidence,
            }

        low_confidence = confidence < 0.75
        if low_confidence:
            logger.warning("[deployment_agent] proceeding with low confidence %s for job %s", confidence, job_id)

        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "output").mkdir(exist_ok=True)

        # Normalize pipeline_selected: may be a string or a list for chained pipelines
        raw_pipeline = result.get("pipeline_selected") or ""
        if isinstance(raw_pipeline, list):
            pipeline_chain: list[str] = [p for p in raw_pipeline if p]
        else:
            pipeline_chain = [raw_pipeline] if raw_pipeline else []
        pipeline_name: str = pipeline_chain[0] if pipeline_chain else ""
        handoff_checkpoint: str | None = result.get("handoff_checkpoint") or None

        # Resolve plain-English intents to prefixed CP IDs across the full pipeline chain
        resolved_map = await self._resolve_checkpoints(pipeline_chain, interactive_checkpoints)

        mode = "interactive" if resolved_map else "autonomous"
        job_config: dict = {
            "mode": mode,
            "interactive_checkpoints": list(resolved_map.keys()),
            "interactive_checkpoint_intents": resolved_map,
            "pipeline_chain": pipeline_chain,
            "handoff_checkpoint": handoff_checkpoint,
        }

        protocol = _build_checkpoint_protocol(job_config)
        claude_md = result["claude_md"] + "\n" + protocol
        (job_dir / "CLAUDE.md").write_text(claude_md)
        (job_dir / "analysis_brief.txt").write_text(result["analysis_brief"])
        (job_dir / "job_config.json").write_text(json.dumps(job_config, indent=2))

        ret: dict = {
            "status": "ready",
            "job_id": job_id,
            "pipeline_selected": pipeline_name,
            "pipeline_chain": pipeline_chain,
            "handoff_checkpoint": handoff_checkpoint,
            "assumptions": result.get("assumptions") or [],
            "interactive_checkpoints": list(resolved_map.keys()),
            "mode": mode,
            "summary": result.get("summary") or "",
            "confidence": confidence,
        }
        if low_confidence:
            ret["low_confidence"] = True
            ret["confidence_warning"] = (
                f"Proceeding despite low confidence ({confidence:.2f}). "
                "Review assumptions carefully before running."
            )
        return ret
