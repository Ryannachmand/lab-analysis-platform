import { useState } from 'react';
import * as api from '../api';
import './CheckpointCard.css';

function cpDisplayId(checkpoint_id) {
  if (!checkpoint_id) return '';
  return checkpoint_id.includes(':') ? checkpoint_id.split(':').pop() : checkpoint_id;
}

export default function CheckpointCard({ cp, jobId, onResponded }) {
  const [response, setResponse] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(cp.status === 'completed');
  const [error, setError] = useState(null);

  const label = cpDisplayId(cp.checkpoint_id);

  async function handleRespond() {
    if (!response.trim() || !jobId) return;
    setLoading(true);
    setError(null);
    try {
      await api.respondToCheckpoint(jobId, cp.checkpoint_id, response.trim());
      setSubmitted(true);
      onResponded?.(cp.checkpoint_id);
    } catch (err) {
      console.error(err);
      setError('Failed to send response. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="checkpoint-card">
      <div className="checkpoint-card__label">{label ? `Checkpoint · ${label}` : 'Checkpoint'}</div>
      <p className="checkpoint-card__message">{cp.description || cp.findings || 'Review required before continuing.'}</p>
      {cp.status === 'completed' && cp.summary?.notes && (
        <div className="checkpoint-card__notes">
          <span className="checkpoint-card__notes-label">Notes:</span>{' '}
          {cp.summary.notes}
        </div>
      )}
      {submitted ? (
        <div className="checkpoint-card__ack">Response submitted — job continuing…</div>
      ) : (
        <>
          <div className="checkpoint-card__input-row">
            <input
              className="checkpoint-card__input"
              type="text"
              placeholder="Your response…"
              value={response}
              onChange={e => setResponse(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !loading && handleRespond()}
              disabled={loading}
            />
            <button
              className="checkpoint-card__btn"
              onClick={handleRespond}
              disabled={loading || !response.trim()}
            >
              {loading ? '…' : 'Respond'}
            </button>
          </div>
          {error && <div className="checkpoint-card__error">{error}</div>}
        </>
      )}
    </div>
  );
}
