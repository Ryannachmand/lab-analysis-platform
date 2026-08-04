# Lab Analysis Platform

Backend API infrastructure for a lab bioinformatics platform. Wraps
Claude Code agent execution of scRNAseq analysis pipelines defined in
`~/claude-skills-v2/`.

## What this is

A FastAPI service that accepts analysis job submissions, creates per-job
working directories, and runs Claude Code agents asynchronously. All job
state is file-based — no database required at this stage.

## Prerequisites

- Python 3.11+
- Anaconda (for R pipeline execution via `conda run -n r-env`)
- Claude Code CLI (run `which claude` to find the path, then set `CLAUDE_BIN` in `.env`)
- Docker (optional — for containerised deployment)

## Quickstart

1. Copy the example env file and review settings:
   ```bash
   cp .env.example .env
   ```

2. Start the API:
   ```bash
   bash scripts/start_api.sh
   ```

3. In a second terminal, submit a test job:
   ```bash
   bash scripts/test_submit.sh
   ```

   With `TEST_MODE=true` (the default), jobs complete instantly with a
   mock log — no real Claude Code process is launched.

## Endpoint reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jobs` | Submit a new analysis job |
| `GET` | `/jobs` | List all jobs (newest first) |
| `GET` | `/jobs/{job_id}` | Get status for a specific job |
| `GET` | `/jobs/{job_id}/logs` | Stream the job log |
| `GET` | `/jobs/{job_id}/results` | List output files |
| `GET` | `/jobs/{job_id}/results/{filename}` | Download an output file |
| `GET` | `/health` | Liveness check |
| `GET` | `/docs` | Interactive API docs (Swagger UI) |

Full request/response schemas are on the `/docs` page.

## Switching from TEST_MODE to real execution

Edit `.env` and set:
```
TEST_MODE=false
```

On the next job submission the API will run the Claude Code CLI
(`CLAUDE_BIN`) inside the job directory with `--dangerously-skip-permissions`.
Ensure Claude Code is authenticated before running real jobs.

## Job directory structure

Each submitted job gets its own directory under `jobs/`:

```
jobs/{job_id}/
├── analysis_brief.txt   ← text submitted as brief_content
├── CLAUDE.md            ← copied from SKILLS_DIR/PROJECT_CLAUDE_TEMPLATE.md
├── job.log              ← stdout/stderr from the Claude Code process
├── status.json          ← machine-readable status + timestamps
└── output/              ← files written here are served via /results
```

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `JOB_BACKEND` | `local` | Execution backend (`local` only for now) |
| `MAX_CONCURRENT_JOBS` | `2` | Semaphore limit on parallel jobs |
| `CLAUDE_BIN` | (required) run `which claude` | Path to Claude Code binary |
| `SKILLS_DIR` | (required) | Skills library (read-only) |
| `JOBS_DIR` | `<repo>/jobs` | Where job directories are created |
| `API_HOST` | `0.0.0.0` | Bind address |
| `API_PORT` | `8000` | Bind port |
| `TEST_MODE` | `true` | Mock jobs when `true` |

## Docker deployment

Build and start with Docker Compose:
```bash
docker compose up --build api
```

The worker service (for containerised R execution) is currently a stub.
To enable it:
1. Export the r-env conda environment: `conda env export -n r-env > worker/r-env.yml`
2. Update `worker/Dockerfile` to install from `r-env.yml`
3. Start with: `docker compose --profile worker up`

## Future work

- Docker worker with full r-env environment
- HPC backends: SLURM and LSF job submission
- Web frontend with job browser and log viewer
- Database layer for job history persistence
