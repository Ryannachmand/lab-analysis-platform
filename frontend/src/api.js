const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function _json(r) {
  if (!r.ok) {
    const text = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

async function _text(r) {
  if (!r.ok) {
    const text = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status}: ${text}`);
  }
  return r.text();
}

export async function prepareJob(description, clarificationAnswers) {
  const body = { description };
  if (clarificationAnswers && clarificationAnswers.length > 0) {
    body.clarification_answers = clarificationAnswers;
  }
  return _json(await fetch(`${BASE}/jobs/prepare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function submitJob(jobConfig) {
  return _json(await fetch(`${BASE}/jobs/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      description: jobConfig.description,
      interactive_checkpoints: jobConfig.interactive_checkpoints || [],
    }),
  }));
}

export async function getJob(id) {
  return _json(await fetch(`${BASE}/jobs/${id}`));
}

export async function getCheckpoints(id) {
  return _json(await fetch(`${BASE}/jobs/${id}/checkpoints`));
}

export async function respondToCheckpoint(jobId, cpId, input) {
  return _json(await fetch(`${BASE}/jobs/${jobId}/checkpoints/${encodeURIComponent(cpId)}/response`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input }),
  }));
}

export async function getResults(id) {
  return _json(await fetch(`${BASE}/jobs/${id}/results`));
}

export async function getLogs(id) {
  return _text(await fetch(`${BASE}/jobs/${id}/logs`));
}

export async function runCleanup(id, feedback) {
  return _json(await fetch(`${BASE}/jobs/${id}/cleanup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback, deep_interpret: false }),
  }));
}

export async function getProposals(id) {
  return _json(await fetch(`${BASE}/jobs/${id}/cleanup/proposals`));
}

export async function approveProposals(id, changeIds) {
  return _json(await fetch(`${BASE}/jobs/${id}/cleanup/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ change_ids: changeIds }),
  }));
}

export async function listJobs() {
  return _json(await fetch(`${BASE}/jobs`));
}

export function resultFileUrl(jobId, filename) {
  // encodeURI preserves slashes for the {filename:path} route
  return `${BASE}/jobs/${jobId}/results/${encodeURI(filename)}`;
}
