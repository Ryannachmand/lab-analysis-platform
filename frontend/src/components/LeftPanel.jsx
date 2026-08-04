import { useState, useEffect } from 'react';
import FileTree from './FileTree';
import './LeftPanel.css';

const SKILLS_MOCK = [
  { name: 'IntegratePublicData', desc: 'Multi-dataset integration with Harmony' },
  { name: 'LargeDataset', desc: 'Clustering, annotation, and DE for large cohorts' },
];

function SkillsList() {
  return (
    <div className="left-panel__section">
      <div className="left-panel__section-title">Available Pipelines</div>
      {SKILLS_MOCK.map(s => (
        <button key={s.name} className="skill-item">
          <span className="skill-item__name">{s.name}</span>
          <span className="skill-item__desc">{s.desc}</span>
        </button>
      ))}
    </div>
  );
}

function HistoryList({ onLoadJob }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [inputId, setInputId] = useState('');

  useEffect(() => {
    import('../api').then(api => {
      api.listJobs()
        .then(data => {
          const list = Array.isArray(data) ? data : (data.jobs || []);
          setJobs(list);
          setLoading(false);
        })
        .catch(() => {
          setError(true);
          setLoading(false);
        });
    });
  }, []);

  function statusBadgeClass(status) {
    if (status === 'completed') return 'history-badge history-badge--completed';
    if (status === 'failed') return 'history-badge history-badge--failed';
    if (status === 'running') return 'history-badge history-badge--running';
    return 'history-badge history-badge--pending';
  }

  function formatAge(isoString) {
    if (!isoString) return '';
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }

  return (
    <div className="history-list">
      <div className="left-panel__section-title">Recent Jobs</div>

      <div className="history-load-input">
        <input
          className="history-load-input__field"
          type="text"
          placeholder="Paste job ID…"
          value={inputId}
          onChange={e => setInputId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onLoadJob(inputId)}
        />
        <button
          className="history-load-input__btn"
          onClick={() => onLoadJob(inputId)}
          disabled={!inputId.trim()}
        >
          Load
        </button>
      </div>

      {loading && <div className="history-list__empty">Loading…</div>}
      {error && <div className="history-list__empty">Could not reach API.</div>}
      {!loading && !error && jobs.length === 0 && (
        <div className="history-list__empty">No jobs found.</div>
      )}
      {!loading && !error && jobs.map(job => (
        <div
          key={job.job_id || job.id}
          className="history-row"
          onClick={() => onLoadJob(job.job_id || job.id)}
        >
          <div className="history-row__top">
            <span className="history-row__id">
              {(job.job_id || job.id || '').slice(0, 8)}
            </span>
            <span className={statusBadgeClass(job.status)}>{job.status}</span>
          </div>
          <div className="history-row__bottom">
            <span className="history-row__pipeline">
              {job.pipeline_selected || job.pipeline || '—'}
            </span>
            <span className="history-row__age">
              {formatAge(job.created_at || job.started_at)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function SettingsPanel() {
  const [checkpoints, setCheckpoints] = useState(true);
  const [autoClean, setAutoClean] = useState(false);

  return (
    <div className="left-panel__section">
      <div className="left-panel__section-title">Settings</div>
      <label className="settings-toggle">
        <span className="settings-toggle__label">Interactive checkpoints</span>
        <input
          type="checkbox"
          className="settings-toggle__input"
          checked={checkpoints}
          onChange={e => setCheckpoints(e.target.checked)}
        />
        <span className="settings-toggle__pill" />
      </label>
      <label className="settings-toggle">
        <span className="settings-toggle__label">Auto-run cleanup after completion</span>
        <input
          type="checkbox"
          className="settings-toggle__input"
          checked={autoClean}
          onChange={e => setAutoClean(e.target.checked)}
        />
        <span className="settings-toggle__pill" />
      </label>
    </div>
  );
}

const NAV_ITEMS = [
  { id: 'jobs', label: 'Jobs', icon: '⚗' },
  { id: 'skills', label: 'Skills', icon: '🔬' },
  { id: 'history', label: 'History', icon: '🕐' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
];

function UserBadge({ user }) {
  if (!user) return null;
  const initials = user.name
    .split(' ')
    .map(w => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
  return (
    <div className="user-badge">
      <div className="user-badge__avatar">{initials}</div>
      <span className="user-badge__name">{user.name}</span>
    </div>
  );
}

export default function LeftPanel({ user, activeNav, onNavChange, onFileSelect, results, activeJob, onLoadJob }) {
  return (
    <aside className="left-panel">
      <div className="left-panel__top">
        <div className="left-panel__brand">
          <span className="left-panel__brand-name">Orion</span>
        </div>
        {user && <UserBadge user={user} />}
      </div>

      <nav className="left-panel__nav">
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            className={`nav-item ${activeNav === item.id ? 'nav-item--active' : ''}`}
            onClick={() => onNavChange(item.id)}
          >
            <span className="nav-item__icon">{item.icon}</span>
            <span className="nav-item__label">{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="left-panel__divider" />

      <div className="left-panel__files">
        {activeNav === 'jobs' && (
          <FileTree
            results={results}
            activeJob={activeJob}
            onFileSelect={onFileSelect}
          />
        )}
        {activeNav === 'skills' && <SkillsList />}
        {activeNav === 'history' && <HistoryList onLoadJob={onLoadJob} />}
        {activeNav === 'settings' && <SettingsPanel />}
      </div>
    </aside>
  );
}
