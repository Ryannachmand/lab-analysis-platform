import { useState, useEffect, useRef } from 'react';
import LeftPanel from '../components/LeftPanel';
import CenterPanel from '../components/CenterPanel';
import RightPanel from '../components/RightPanel';
import * as api from '../api';
import './Workspace.css';

export default function Workspace({ user }) {
  const [activeNav, setActiveNav] = useState('jobs');
  const [selectedFile, setSelectedFile] = useState(null);
  const [promptText, setPromptText] = useState('');
  const [activeJob, setActiveJob] = useState(null);
  const [feed, setFeed] = useState([]);
  const [checkpoints, setCheckpoints] = useState([]);
  const [results, setResults] = useState([]);
  const [pendingPrepare, setPendingPrepare] = useState(null);

  const jobPollRef = useRef(null);
  const resultsPollRef = useRef(null);
  const jobIdRef = useRef(null);
  const checkpointsRef = useRef([]);
  const completedCpIdsRef = useRef(new Set());

  useEffect(() => { jobIdRef.current = activeJob?.id ?? null; }, [activeJob?.id]);
  useEffect(() => { checkpointsRef.current = checkpoints; }, [checkpoints]);

  useEffect(() => () => {
    clearInterval(jobPollRef.current);
    clearInterval(resultsPollRef.current);
  }, []);

  function pushFeed(item) {
    setFeed(f => [...f, { ...item, _key: Math.random().toString(36).slice(2) }]);
  }

  function stopPolling() {
    clearInterval(jobPollRef.current);
    clearInterval(resultsPollRef.current);
    jobPollRef.current = null;
    resultsPollRef.current = null;
  }

  async function doPollJobOnce(jobId) {
    try {
      const [jobData, cpList] = await Promise.all([
        api.getJob(jobId),
        api.getCheckpoints(jobId),
      ]);

      setActiveJob(prev => prev ? { ...prev, status: jobData.status } : prev);

      const known = checkpointsRef.current;
      const newPending = cpList.filter(
        cp => cp.status === 'awaiting_response' &&
              !known.some(k => k.checkpoint_id === cp.checkpoint_id)
      );
      if (newPending.length > 0) {
        const updated = [...known, ...newPending];
        checkpointsRef.current = updated;
        setCheckpoints(updated);
        newPending.forEach(cp =>
          setFeed(f => [...f, { type: 'checkpoint', cp, _key: Math.random().toString(36).slice(2) }])
        );
      }

      // Mark checkpoints resolved when they transition to completed
      const justResolved = cpList.filter(
        cp => cp.status === 'completed' &&
              known.some(k => k.checkpoint_id === cp.checkpoint_id && k.status === 'awaiting_response')
      );
      if (justResolved.length > 0) {
        setCheckpoints(prev =>
          prev.map(k => {
            const match = justResolved.find(r => r.checkpoint_id === k.checkpoint_id);
            return match ? { ...k, status: 'completed' } : k;
          })
        );
      }

      // Push a progress status item for each newly completed checkpoint
      cpList.forEach(cp => {
        if (cp.status === 'completed' && !completedCpIdsRef.current.has(cp.checkpoint_id)) {
          completedCpIdsRef.current.add(cp.checkpoint_id);
          const label = cp.checkpoint_id.includes(':')
            ? cp.checkpoint_id.split(':').pop()
            : cp.checkpoint_id;
          setFeed(f => [...f, {
            type: 'progress',
            message: `✓ Checkpoint ${label}`,
            _key: `cp-done-${cp.checkpoint_id}`,
          }]);
        }
      });

      if (jobData.status === 'completed') {
        stopPolling();
        setFeed(f => [
          ...f,
          { type: 'status', message: 'Analysis complete ✓', _key: 'done' },
          { type: 'cleanup_prompt', _key: 'cleanup-prompt' },
        ]);
      } else if (jobData.status === 'failed') {
        stopPolling();
        setFeed(f => [...f, {
          type: 'error',
          message: 'Job failed. Expand "Show details" to see the log.',
          _key: 'fail',
        }]);
      }
    } catch (err) {
      console.error('Poll error:', err);
    }
  }

  function startPolling(jobId) {
    doPollJobOnce(jobId);
    jobPollRef.current = setInterval(() => doPollJobOnce(jobIdRef.current), 4000);

    resultsPollRef.current = setInterval(async () => {
      const id = jobIdRef.current;
      if (!id) return;
      try {
        const data = await api.getResults(id);
        setResults(data.files || []);
      } catch (err) {
        console.error('Results poll error:', err);
      }
    }, 8000);
  }

  async function handlePrepare() {
    if (!promptText.trim() || pendingPrepare || activeJob) return;
    setFeed([{ type: 'status', message: 'Reviewing your request…', _key: 'preparing' }]);
    try {
      const res = await api.prepareJob(promptText.trim());
      if (res.status === 'needs_clarification') {
        setFeed(f => [...f, {
          type: 'clarification',
          questions: res.clarifying_questions,
          originalDescription: promptText.trim(),
          _key: `clarify-${Date.now()}`,
        }]);
        return;
      }
      const prepare = { ...res, description: promptText.trim() };
      setPendingPrepare(prepare);
      setFeed(f => [...f, { type: 'confirm', prepare, _key: 'confirm' }]);
    } catch (err) {
      console.error(err);
      setFeed(f => [...f, {
        type: 'error',
        message: 'Failed to prepare job. Is the API running?',
        _key: 'prep-err',
      }]);
    }
  }

  async function handleConfirm() {
    if (!pendingPrepare) return;
    const prepare = pendingPrepare;
    setPendingPrepare(null);
    setFeed(f => f.filter(item => item.type !== 'confirm'));
    try {
      const submitRes = await api.submitJob(prepare);
      const jobId = submitRes.job_id;
      setActiveJob({ id: jobId, status: submitRes.status, pipeline: submitRes.pipeline_selected });
      setFeed(f => [...f, {
        type: 'status',
        message: "Job started — you can safely leave and return. We'll notify you at each checkpoint.",
        _key: 'started',
      }]);
      startPolling(jobId);
    } catch (err) {
      console.error(err);
      setFeed(f => [...f, {
        type: 'error',
        message: 'Failed to start job. Please try again.',
        _key: 'submit-err',
      }]);
    }
  }

  function handleCancel() {
    setPendingPrepare(null);
    setFeed([]);
  }

  function handleCheckpointResponded(cpId) {
    setCheckpoints(prev =>
      prev.map(cp => cp.checkpoint_id === cpId ? { ...cp, status: 'completed' } : cp)
    );
    const label = cpId.includes(':') ? cpId.split(':').pop() : cpId;
    setFeed(f => [...f, {
      type: 'status',
      message: `Response sent for ${label} — job continuing…`,
      _key: `cp-resp-${cpId}`,
    }]);
  }

  async function handleSubmitClarification(originalDescription, clarificationAnswers) {
    setFeed(f => [...f, { type: 'status', message: 'Reviewing your answers…', _key: `clarify-reviewing-${Date.now()}` }]);
    try {
      const res = await api.prepareJob(originalDescription, clarificationAnswers);
      if (res.status === 'needs_clarification') {
        setFeed(f => [...f, {
          type: 'clarification',
          questions: res.clarifying_questions,
          originalDescription,
          _key: `clarify-${Date.now()}`,
        }]);
        return;
      }
      const prepare = { ...res, description: originalDescription };
      setPendingPrepare(prepare);
      setFeed(f => [...f, { type: 'confirm', prepare, _key: `confirm-${Date.now()}` }]);
    } catch (err) {
      console.error(err);
      setFeed(f => [...f, {
        type: 'error',
        message: 'Failed to process answers. Please try again.',
        _key: `clarify-err-${Date.now()}`,
      }]);
    }
  }

  async function handleRunCleanup(feedbackText) {
    if (!activeJob) return;
    setFeed(f => [...f, { type: 'status', message: 'Reviewing job results…', _key: 'cleanup-running' }]);
    try {
      await api.runCleanup(activeJob.id, feedbackText);
      const proposalsData = await api.getProposals(activeJob.id);
      setFeed(f => [...f, {
        type: 'cleanup',
        proposals: proposalsData.proposals || [],
        _key: 'cleanup-proposals',
      }]);
    } catch (err) {
      console.error(err);
      setFeed(f => [...f, {
        type: 'error',
        message: 'Cleanup failed. Check the console for details.',
        _key: 'cleanup-err',
      }]);
    }
  }

  async function handleLoadJob(jobId) {
    const id = jobId.trim();
    if (!id) return;

    stopPolling();
    setFeed([]);
    setResults([]);
    setCheckpoints([]);
    completedCpIdsRef.current = new Set();
    setPendingPrepare(null);

    setFeed([{ type: 'status', message: `Loading job ${id.slice(0, 8)}…`, _key: 'loading' }]);

    try {
      const [jobData, cpList, resultsData] = await Promise.all([
        api.getJob(id),
        api.getCheckpoints(id),
        api.getResults(id),
      ]);

      setActiveJob({
        id,
        status: jobData.status,
        pipeline: jobData.pipeline_selected || jobData.pipeline,
      });
      jobIdRef.current = id;

      setResults(resultsData.files || []);

      const feedItems = [];

      feedItems.push({
        type: 'status',
        message: `Job loaded — pipeline: ${jobData.pipeline_selected || jobData.pipeline || 'unknown'}`,
        _key: 'loaded-status',
      });

      const completedCps = cpList.filter(cp => cp.status === 'completed');
      completedCps.forEach(cp => {
        completedCpIdsRef.current.add(cp.checkpoint_id);
        const label = cp.checkpoint_id.includes(':')
          ? cp.checkpoint_id.split(':').pop()
          : cp.checkpoint_id;
        feedItems.push({
          type: 'progress',
          message: `✓ Checkpoint ${label}`,
          _key: `cp-done-${cp.checkpoint_id}`,
        });
      });

      const awaitingCps = cpList.filter(cp => cp.status === 'awaiting_response');
      setCheckpoints([...completedCps, ...awaitingCps]);
      awaitingCps.forEach(cp => {
        feedItems.push({
          type: 'checkpoint',
          cp,
          _key: `cp-await-${cp.checkpoint_id}`,
        });
      });

      if (jobData.status === 'completed') {
        feedItems.push({ type: 'status', message: 'Analysis complete ✓', _key: 'done' });
        feedItems.push({ type: 'cleanup_prompt', _key: 'cleanup-prompt' });
      } else if (jobData.status === 'failed') {
        feedItems.push({
          type: 'error',
          message: 'Job failed. Expand "Show details" to see the log.',
          _key: 'fail',
        });
      }

      setFeed(feedItems);
      setActiveNav('jobs');

      if (jobData.status === 'running') {
        startPolling(id);
      }
    } catch (err) {
      console.error(err);
      setFeed([{
        type: 'error',
        message: `Could not load job ${id.slice(0, 8)}: ${err.message}`,
        _key: 'load-err',
      }]);
    }
  }

  async function handleApproveProposals(changeIds) {
    if (!activeJob) return;
    try {
      await api.approveProposals(activeJob.id, changeIds);
      setFeed(f => [...f, { type: 'status', message: 'Skills library updated ✓', _key: 'approved' }]);
    } catch (err) {
      console.error(err);
      setFeed(f => [...f, {
        type: 'error',
        message: 'Failed to apply proposals.',
        _key: 'approve-err',
      }]);
    }
  }

  return (
    <div className="workspace">
      <LeftPanel
        user={user}
        activeNav={activeNav}
        onNavChange={setActiveNav}
        onFileSelect={setSelectedFile}
        results={results}
        activeJob={activeJob}
        onLoadJob={handleLoadJob}
      />
      <CenterPanel
        feed={feed}
        promptText={promptText}
        onPromptChange={setPromptText}
        onSubmit={handlePrepare}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
        activeJob={activeJob}
        pendingPrepare={pendingPrepare}
        onCheckpointResponded={handleCheckpointResponded}
        onRunCleanup={handleRunCleanup}
        onApproveProposals={handleApproveProposals}
        onSubmitClarification={handleSubmitClarification}
      />
      <RightPanel selectedFile={selectedFile} activeJob={activeJob} />
    </div>
  );
}
