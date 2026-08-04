import { useState, useEffect, useRef } from 'react';
import Papa from 'papaparse';
import './RightPanel.css';

function useBlobUrl(filePath) {
  const [blobUrl, setBlobUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!filePath) return;
    setLoading(true);
    setError(false);
    setBlobUrl(prev => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });

    fetch(filePath)
      .then(r => {
        if (!r.ok) throw new Error(r.status);
        return r.blob();
      })
      .then(blob => {
        setBlobUrl(URL.createObjectURL(blob));
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });

    return () => {
      setBlobUrl(prev => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, [filePath]);

  return { blobUrl, loading, error };
}

function ImageViewer({ file }) {
  const { blobUrl, loading, error } = useBlobUrl(file.path);

  if (loading) return <div className="file-viewer__loading">Loading…</div>;

  if (error || !blobUrl) {
    return (
      <div className="file-viewer__img-placeholder">
        <div className="file-viewer__img-icon">📷</div>
        <div className="file-viewer__img-name">{file.name}</div>
        <div className="file-viewer__img-note">Preview unavailable</div>
      </div>
    );
  }

  return (
    <div className="file-viewer__img-wrap">
      <img
        src={blobUrl}
        alt={file.name}
        className="file-viewer__img"
      />
      <div className="file-viewer__img-name">{file.name}</div>
    </div>
  );
}

function CsvViewer({ file }) {
  const [headers, setHeaders] = useState(null);
  const [rows, setRows] = useState(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!file.path) return;
    setHeaders(null);
    setRows(null);
    setTruncated(false);
    setError(false);
    fetch(file.path)
      .then(r => {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(text => {
        const result = Papa.parse(text, {
          header: true,
          preview: 100,
          skipEmptyLines: true,
        });
        setHeaders(result.meta.fields || []);
        setRows(result.data);
        setTruncated(result.data.length === 100);
      })
      .catch(() => setError(true));
  }, [file.path]);

  if (error) {
    return (
      <div className="file-viewer__img-placeholder">
        <div className="file-viewer__img-icon">📊</div>
        <div className="file-viewer__img-name">{file.name}</div>
        <div className="file-viewer__img-note">Could not load CSV preview</div>
      </div>
    );
  }

  if (!rows || !headers) {
    return <div className="file-viewer__loading">Loading…</div>;
  }

  return (
    <div className="file-viewer__csv">
      <div className="file-viewer__csv-name">{file.name}</div>
      <div className="file-viewer__csv-scroll">
        <table className="file-viewer__table">
          <thead>
            <tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {headers.map((h, j) => <td key={j}>{row[h] ?? ''}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && (
        <div className="file-viewer__csv-footer">
          Showing first 100 rows. Download for full file.
        </div>
      )}
    </div>
  );
}

function PdfViewer({ file }) {
  const { blobUrl, loading, error } = useBlobUrl(file.path);

  if (loading) return <div className="file-viewer__loading">Loading…</div>;

  if (error || !blobUrl) {
    return (
      <div className="file-viewer__img-placeholder">
        <div className="file-viewer__img-icon">📄</div>
        <div className="file-viewer__img-name">{file.name}</div>
        <div className="file-viewer__img-note">Preview unavailable</div>
      </div>
    );
  }

  return (
    <div className="file-viewer__pdf-wrap">
      <div className="file-viewer__img-name">{file.name}</div>
      <iframe
        src={blobUrl}
        title={file.name}
        className="file-viewer__pdf-frame"
      />
    </div>
  );
}

function TextViewer({ file }) {
  const [text, setText] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!file.path) return;
    setText(null);
    setError(false);
    fetch(file.path)
      .then(r => {
        if (!r.ok) throw new Error(r.status);
        return r.text();
      })
      .then(t => setText(t))
      .catch(() => setError(true));
  }, [file.path]);

  if (error) return (
    <div className="file-viewer__img-placeholder">
      <div className="file-viewer__img-icon">📝</div>
      <div className="file-viewer__img-name">{file.name}</div>
      <div className="file-viewer__img-note">Could not load file</div>
    </div>
  );

  if (text === null) return <div className="file-viewer__loading">Loading…</div>;

  return (
    <div className="file-viewer__text">
      <div className="file-viewer__img-name">{file.name}</div>
      <pre className="file-viewer__pre">{text}</pre>
    </div>
  );
}

function FilePreview({ file }) {
  const isImage = ['png', 'jpg', 'jpeg', 'svg', 'gif', 'webp'].includes(file?.type);
  const isPdf = file?.type === 'pdf';
  const isCsv = ['csv', 'tsv'].includes(file?.type);
  const isText = ['txt', 'log', 'md'].includes(file?.type);

  if (!file) return null;
  if (isImage) return <ImageViewer file={file} />;
  if (isPdf) return <PdfViewer file={file} />;
  if (isCsv) return <CsvViewer file={file} />;
  if (isText) return <TextViewer file={file} />;
  return (
    <div className="file-viewer__img-placeholder">
      <div className="file-viewer__img-icon">📄</div>
      <div className="file-viewer__img-name">{file.name}</div>
      <div className="file-viewer__img-note">No preview for this file type</div>
    </div>
  );
}

export default function RightPanel({ selectedFile }) {
  const [expanded, setExpanded] = useState(false);

  function handleDownload() {
    const a = document.createElement('a');
    a.href = selectedFile.path;
    a.download = selectedFile.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  return (
    <aside className="right-panel">
      <div className="right-panel__header">
        <span>File Viewer</span>
        {selectedFile && (
          <div className="right-panel__header-actions">
            <button
              className="right-panel__download-btn"
              onClick={handleDownload}
              title="Download"
            >
              ⬇
            </button>
            <button
              className="right-panel__expand-btn"
              onClick={() => setExpanded(true)}
              title="Expand"
            >
              ⛶
            </button>
          </div>
        )}
      </div>
      <div className="right-panel__body">
        {!selectedFile ? (
          <div className="right-panel__empty">
            <div className="right-panel__empty-icon">🗂</div>
            <p>Select a file from the job tree to preview</p>
          </div>
        ) : (
          <FilePreview file={selectedFile} />
        )}
      </div>

      {expanded && (
        <div
          className="file-viewer__modal-backdrop"
          onClick={() => setExpanded(false)}
        >
          <div
            className="file-viewer__modal"
            onClick={e => e.stopPropagation()}
          >
            <button
              className="file-viewer__modal-close"
              onClick={() => setExpanded(false)}
            >
              ×
            </button>
            <FilePreview file={selectedFile} />
          </div>
        </div>
      )}
    </aside>
  );
}
