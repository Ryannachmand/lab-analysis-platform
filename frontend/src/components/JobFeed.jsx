import { useState } from 'react';
import CheckpointCard from './CheckpointCard';
import './JobFeed.css';

function confidenceLabel(confidence) {
  if (confidence >= 0.85) return { text: 'High confidence', cls: 'conf-high' };
  if (confidence >= 0.65) return { text: 'Looks right — double-check the pipeline', cls: 'conf-mid' };
  return { text: 'Low confidence — please review carefully', cls: 'conf-low' };
}

function ConfirmCard({ prepare, onConfirm, onCancel }) {
  const chain = prepare.pipeline_chain || (prepare.pipeline_selected ? [prepare.pipeline_selected] : []);
  const pipelineDisplay = chain.length > 1 ? chain.join(' → ') : chain[0] || '—';
  const conf = confidenceLabel(prepare.confidence ?? 0);

  return (
    <div className="confirm-card">
      <div className="confirm-card__label">Ready to run</div>
      {prepare.summary && <p className="confirm-card__summary">{prepare.summary}</p>}
      <div className="confirm-card__meta">
        <span className="confirm-card__pipeline">Pipeline: {pipelineDisplay}</span>
        <span className={`confirm-card__conf ${conf.cls}`}>{conf.text}</span>
      </div>
      {prepare.assumptions?.length > 0 && (
        <ul className="confirm-card__assumptions">
          {prepare.assumptions.map((a, i) => <li key={i}>{a}</li>)}
        </ul>
      )}
      <div className="confirm-card__actions">
        <button className="confirm-card__run" onClick={onConfirm}>Looks good, run it</button>
        <button className="confirm-card__cancel" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function CleanupPromptCard({ onRunCleanup }) {
  const [feedback, setFeedback] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  async function handle() {
    setLoading(true);
    await onRunCleanup(feedback.trim());
    setLoading(false);
    setDone(true);
  }

  if (done) return null;

  return (
    <div className="cleanup-prompt-card">
      <div className="cleanup-prompt-card__label">Post-job cleanup</div>
      <textarea
        className="cleanup-prompt-card__textarea"
        rows={3}
        placeholder="Optional: add notes about this job for the skills library…"
        value={feedback}
        onChange={e => setFeedback(e.target.value)}
        disabled={loading}
      />
      <button
        className="cleanup-prompt-card__btn"
        onClick={handle}
        disabled={loading}
      >
        {loading ? 'Running cleanup…' : 'Run Cleanup'}
      </button>
    </div>
  );
}

function CleanupProposalsCard({ proposals, onApproveProposals }) {
  const allIds = proposals.map(p => p.change_id);
  const [checked, setChecked] = useState(new Set(allIds));
  const [done, setDone] = useState(false);

  function toggle(id) {
    setChecked(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function handleApprove() {
    await onApproveProposals([...checked]);
    setDone(true);
  }

  if (done) return <div className="cleanup-done">Skills library updated ✓</div>;

  if (proposals.length === 0) {
    return <div className="cleanup-done">No proposals generated.</div>;
  }

  return (
    <div className="cleanup-proposals">
      <div className="cleanup-proposals__label">Proposed skills updates</div>
      {proposals.map(p => (
        <label key={p.change_id} className="cleanup-proposal">
          <input
            type="checkbox"
            checked={checked.has(p.change_id)}
            onChange={() => toggle(p.change_id)}
          />
          <div className="cleanup-proposal__body">
            <span className="cleanup-proposal__desc">
              {p.plain_english || p.description || p.change_type}
            </span>
            <span className="cleanup-proposal__file">
              {p.target_file?.split('/').pop() ?? p.target_file}
            </span>
          </div>
        </label>
      ))}
      <button
        className="cleanup-proposals__approve"
        disabled={checked.size === 0}
        onClick={handleApprove}
      >
        Approve Selected
      </button>
    </div>
  );
}

function ClarificationCard({ item, onSubmitClarification }) {
  const [answers, setAnswers] = useState(item.questions.map(() => ''));
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  function setAnswer(i, val) {
    setAnswers(prev => prev.map((a, j) => j === i ? val : a));
  }

  async function handleSubmit() {
    setLoading(true);
    setSubmitted(true);
    await onSubmitClarification(
      item.originalDescription,
      item.questions.map((q, i) => ({ question: q, answer: answers[i] })),
    );
    setLoading(false);
  }

  if (submitted) {
    return <div className="feed-status">Answers submitted…</div>;
  }

  return (
    <div className="clarification-card">
      <div className="clarification-card__label">Clarification needed</div>
      {item.questions.map((q, i) => (
        <div key={i} className="clarification-card__row">
          <div className="clarification-card__question">{q}</div>
          <input
            className="clarification-card__input"
            type="text"
            placeholder="Your answer…"
            value={answers[i]}
            onChange={e => setAnswer(i, e.target.value)}
            disabled={loading}
          />
        </div>
      ))}
      <button
        className="clarification-card__btn"
        onClick={handleSubmit}
        disabled={loading || answers.some(a => !a.trim())}
      >
        Submit answers
      </button>
    </div>
  );
}

function FeedItem({ item, activeJob, onConfirm, onCancel, onCheckpointResponded, onRunCleanup, onApproveProposals, onSubmitClarification }) {
  switch (item.type) {
    case 'status':
      return <div className="feed-status">{item.message}</div>;

    case 'progress':
      return <div className="feed-progress">{item.message}</div>;

    case 'error':
      return <div className="feed-error">{item.message}</div>;

    case 'confirm':
      return (
        <ConfirmCard
          prepare={item.prepare}
          onConfirm={onConfirm}
          onCancel={onCancel}
        />
      );

    case 'checkpoint':
      return (
        <CheckpointCard
          cp={item.cp}
          jobId={activeJob?.id}
          onResponded={onCheckpointResponded}
        />
      );

    case 'cleanup_prompt':
      return <CleanupPromptCard onRunCleanup={onRunCleanup} />;

    case 'cleanup':
      return (
        <CleanupProposalsCard
          proposals={item.proposals}
          onApproveProposals={onApproveProposals}
        />
      );

    case 'clarification':
      return (
        <ClarificationCard
          item={item}
          onSubmitClarification={onSubmitClarification}
        />
      );

    case 'log':
      return <div className="feed-log">{item.text}</div>;

    default:
      return null;
  }
}

export default function JobFeed({
  feed,
  activeJob,
  onConfirm,
  onCancel,
  onCheckpointResponded,
  onRunCleanup,
  onApproveProposals,
  onSubmitClarification,
}) {
  return (
    <div className="job-feed">
      {feed.map(item => (
        <FeedItem
          key={item._key}
          item={item}
          activeJob={activeJob}
          onConfirm={onConfirm}
          onCancel={onCancel}
          onCheckpointResponded={onCheckpointResponded}
          onRunCleanup={onRunCleanup}
          onApproveProposals={onApproveProposals}
          onSubmitClarification={onSubmitClarification}
        />
      ))}
    </div>
  );
}
