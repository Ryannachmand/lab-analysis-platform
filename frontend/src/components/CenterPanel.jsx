import { useState, useEffect, useRef } from 'react';
import JobFeed from './JobFeed';
import * as api from '../api';
import './CenterPanel.css';

function LogSection({ jobId }) {
  const [open, setOpen] = useState(false);
  const [logs, setLogs] = useState('');
  const pollRef = useRef(null);

  useEffect(() => {
    if (!open || !jobId) return;
    const poll = async () => {
      try { setLogs(await api.getLogs(jobId)); }
      catch (e) { /* ignore */ }
    };
    poll();
    pollRef.current = setInterval(poll, 6000);
    return () => clearInterval(pollRef.current);
  }, [open, jobId]);

  const lines = logs.split('\n').filter(Boolean).slice(-20);

  return (
    <div className="log-section">
      <button className="log-section__toggle" onClick={() => setOpen(o => !o)}>
        {open ? '▾' : '▸'} Show details
      </button>
      {open && (
        <div className="log-section__content">
          {lines.length === 0
            ? <span className="log-section__empty">No log output yet.</span>
            : lines.map((line, i) => <div key={i} className="log-section__line">{line}</div>)
          }
        </div>
      )}
    </div>
  );
}

export default function CenterPanel({
  feed,
  promptText,
  onPromptChange,
  onSubmit,
  onConfirm,
  onCancel,
  activeJob,
  pendingPrepare,
  onCheckpointResponded,
  onRunCleanup,
  onApproveProposals,
  onSubmitClarification,
}) {
  const submitDisabled = !!pendingPrepare || !!activeJob;

  return (
    <main className="center-panel">
      <div className="center-panel__input-area">
        <textarea
          className="center-panel__textarea"
          rows={4}
          placeholder="Describe your analysis job…"
          value={promptText}
          onChange={e => onPromptChange(e.target.value)}
          disabled={submitDisabled}
        />
        <div className="center-panel__actions">
          <button
            className="center-panel__submit-btn"
            onClick={onSubmit}
            disabled={submitDisabled || !promptText.trim()}
          >
            Submit Job
          </button>
        </div>
      </div>

      <div className="center-panel__feed-area">
        {feed.length === 0 ? (
          <div className="center-panel__empty">
            <div className="center-panel__empty-icon">⚗</div>
            <p>Describe an analysis job above to get started.</p>
          </div>
        ) : (
          <>
            <JobFeed
              feed={feed}
              activeJob={activeJob}
              onConfirm={onConfirm}
              onCancel={onCancel}
              onCheckpointResponded={onCheckpointResponded}
              onRunCleanup={onRunCleanup}
              onApproveProposals={onApproveProposals}
              onSubmitClarification={onSubmitClarification}
            />
            {activeJob && <LogSection jobId={activeJob.id} />}
          </>
        )}
      </div>
    </main>
  );
}
