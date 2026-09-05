/**
 * TimeTravelPanel — timeline of topology snapshots with graph preview.
 *
 * Lists stored snapshots newest-first; picking one loads its graph onto the
 * canvas (handled by the parent via onPickGraph) and shows a diff against
 * the live topology: what existed then that doesn't now, and vice versa.
 * Also offers a manual "capture now" snapshot.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { SnapshotDiff, SnapshotSummary } from '../types';

const TRIGGER_ICON: Record<string, string> = {
  discovery: '🔎',
  health: '❤️',
  manual: '✋',
};

interface Props {
  onPickGraph: (graph: SnapshotGraphShape | null) => void;
  onExit: () => void;
}

// keep the graph type local-name independent of the parent's import
type SnapshotGraphShape = import('../types').SnapshotGraph;

export function TimeTravelPanel({ onPickGraph, onExit }: Props) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<SnapshotSummary | null>(null);

  const { data: snapshots } = useQuery({
    queryKey: ['snapshots'],
    queryFn: () => api.snapshots(50),
    refetchInterval: 30_000,
  });

  const { data: diff } = useQuery({
    queryKey: ['snapshot-diff', selected?.id],
    queryFn: () => api.snapshotDiff(selected!.id),
    enabled: selected !== null,
  });

  const capture = useMutation({
    mutationFn: api.createSnapshot,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['snapshots'] }),
  });

  const pick = (snap: SnapshotSummary) => {
    const next = selected?.id === snap.id ? null : snap;
    setSelected(next);
    if (next === null) {
      onPickGraph(null);
      return;
    }
    api.snapshot(next.id).then((s) => onPickGraph(s.graph));
  };

  return (
    <div className="time-travel">
      <div className="tt-head">
        <h4>🕐 Time travel</h4>
        <div className="tt-actions">
          <button
            type="button"
            className="tool-btn"
            disabled={capture.isPending}
            onClick={() => capture.mutate()}
          >
            {capture.isPending ? 'capturing…' : '📸 Capture now'}
          </button>
          <button type="button" className="tool-btn" onClick={onExit}>
            Back to live
          </button>
        </div>
      </div>

      {!snapshots || snapshots.length === 0 ? (
        <p className="muted tt-empty">
          no snapshots yet — history accrues automatically when the topology or device health
          changes
        </p>
      ) : (
        <div className="tt-timeline">
          {snapshots.map((snap) => (
            <div key={snap.id} className="tt-row">
              <button
                type="button"
                className={`tt-point ${selected?.id === snap.id ? 'active' : ''}`}
                onClick={() => pick(snap)}
                title={`${snap.node_count} nodes · ${snap.edge_count} links`}
              >
                <span className="tt-icon">{TRIGGER_ICON[snap.trigger] ?? '•'}</span>
                <span className="tt-time">{new Date(snap.taken_at).toLocaleTimeString()}</span>
                <span className="tt-meta">
                  {snap.node_count}n · {snap.edge_count}l · {snap.trigger}
                </span>
              </button>
              {selected?.id === snap.id && diff && <DiffReport diff={diff} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DiffReport({ diff }: { diff: SnapshotDiff }) {
  const s = diff.summary;
  const empty = s.added_nodes === 0 && s.removed_nodes === 0 && s.added_edges === 0
    && s.removed_edges === 0 && s.health_changes === 0;

  return (
    <div className="tt-diff">
      {empty ? (
        <p className="muted">identical to the live topology</p>
      ) : (
        <>
          <div className="tt-diff-stats">
            {s.added_nodes > 0 && <span className="wi-dev wi-add">＋{s.added_nodes} devices</span>}
            {s.removed_nodes > 0 && <span className="wi-dev wi-del">－{s.removed_nodes} devices</span>}
            {s.added_edges > 0 && <span className="wi-dev wi-add">＋{s.added_edges} links</span>}
            {s.removed_edges > 0 && <span className="wi-dev wi-del">－{s.removed_edges} links</span>}
            {s.health_changes > 0 && (
              <span className="wi-dev wi-chg">⇄ {s.health_changes} health</span>
            )}
          </div>
          {diff.added_nodes.length > 0 && (
            <div className="tt-diff-list">
              {diff.added_nodes.map((n) => (
                <span key={n.id} className="wi-dev wi-add">＋ {n.name}</span>
              ))}
            </div>
          )}
          {diff.removed_nodes.length > 0 && (
            <div className="tt-diff-list">
              {diff.removed_nodes.map((n) => (
                <span key={n.id} className="wi-dev wi-del">－ {n.name}</span>
              ))}
            </div>
          )}
          {diff.health_changes.length > 0 && (
            <div className="tt-diff-list">
              {diff.health_changes.map(([name, , oldH, newH]) => (
                <span key={`${name}-${oldH}-${newH}`} className="wi-dev wi-chg">
                  {name}: {oldH} → {newH}
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
