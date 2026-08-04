import { useState } from 'react';
import { resultFileUrl } from '../api';
import './FileTree.css';

function fileExt(name) {
  const parts = name.split('.');
  return parts.length > 1 ? parts.pop().toLowerCase() : '';
}

const EXT_ICON = {
  png: '🖼',
  jpg: '🖼',
  jpeg: '🖼',
  pdf: '📄',
  csv: '📊',
  tsv: '📊',
  rds: '📦',
  h5: '📦',
  h5ad: '📦',
  json: '{}',
  txt: '📝',
  log: '📝',
};

function fileIcon(name) {
  return EXT_ICON[fileExt(name)] || '📄';
}

function buildTree(results) {
  const root = {};
  for (const f of results) {
    const parts = (f.path || f.name).split('/');
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const part = parts[i];
      if (!node[part]) node[part] = { type: 'dir', children: {} };
      node = node[part].children;
    }
    node[parts[parts.length - 1]] = { type: 'file', file: f };
  }
  return root;
}

function sortedEntries(obj) {
  return Object.entries(obj).sort(([, a], [, b]) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
    return 0;
  });
}

function TreeNode({ name, node, depth, onFileSelect, jobId }) {
  const [open, setOpen] = useState(true);

  if (node.type === 'file') {
    const f = node.file;
    const ext = fileExt(f.name);
    return (
      <div
        className="tree-node__label tree-node__label--file"
        style={{ paddingLeft: `${depth * 14 + 14}px` }}
        onClick={() => onFileSelect({ name: f.name, type: ext, path: resultFileUrl(jobId, f.path || f.name) })}
        title={f.path || f.name}
      >
        <span className="tree-node__icon">{fileIcon(f.name)}</span>
        <span className="tree-node__name">{f.name}</span>
      </div>
    );
  }

  return (
    <div>
      <div
        className="tree-node__label tree-node__label--folder"
        style={{ paddingLeft: `${depth * 14 + 14}px` }}
        onClick={() => setOpen(o => !o)}
      >
        <span className="tree-node__caret">{open ? '▾' : '▸'}</span>
        <span className="tree-node__icon">📁</span>
        <span className="tree-node__name">{name}/</span>
      </div>
      {open && sortedEntries(node.children).map(([childName, childNode]) => (
        <TreeNode
          key={childName}
          name={childName}
          node={childNode}
          depth={depth + 1}
          onFileSelect={onFileSelect}
          jobId={jobId}
        />
      ))}
    </div>
  );
}

export default function FileTree({ results, activeJob, onFileSelect }) {
  if (!activeJob || results.length === 0) {
    return (
      <div className="file-tree">
        <div className="file-tree__header">Job Files</div>
        <div className="file-tree__empty">No output files yet.</div>
      </div>
    );
  }

  const tree = buildTree(results);

  return (
    <div className="file-tree">
      <div className="file-tree__header">Job Files</div>
      <div className="file-tree__job-label">
        <span className="tree-node__icon">🧪</span>
        <span className="tree-node__name">{activeJob.pipeline || activeJob.id.slice(0, 8)}</span>
      </div>
      <div className="file-tree__sub">
        <div className="file-tree__folder-row">
          <span className="tree-node__icon">📁</span>
          <span className="tree-node__name">output/</span>
        </div>
        {sortedEntries(tree).map(([name, node]) => (
          <TreeNode
            key={name}
            name={name}
            node={node}
            depth={1}
            onFileSelect={onFileSelect}
            jobId={activeJob.id}
          />
        ))}
      </div>
    </div>
  );
}
