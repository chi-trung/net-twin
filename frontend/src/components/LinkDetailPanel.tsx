/**
 * LinkDetailPanel — details + live traffic chart for one twin link.
 * Mirrors DeviceDetailPanel's layout: header, key/value facts, chart.
 */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { LinkTrafficChart, formatBps } from './LinkTrafficChart';

interface Props {
  linkId: number;
  onClose: () => void;
}

export function LinkDetailPanel({ linkId, onClose }: Props) {
  const { data: topology } = useQuery({ queryKey: ['topology', 0], queryFn: api.topology });
  const { data: traffic } = useQuery({
    queryKey: ['linkTraffic', linkId],
    queryFn: () => api.linkTraffic(linkId),
    refetchInterval: 10_000,
  });

  const link = topology?.edges.find((e) => e.id === linkId);
  const src = topology?.nodes.find((n) => n.id === link?.source_device_id);
  const dst = topology?.nodes.find((n) => n.id === link?.target_device_id);

  const points = traffic?.points ?? [];
  const last = points.at(-1);

  if (!link || !src || !dst) return <div className="detail-panel"><p className="muted">link not found</p></div>;

  return (
    <div className="detail-panel">
      <div className="detail-head">
        <div>
          <h3>
            🔗 {src.name} <span className="muted">→</span> {dst.name}
          </h3>
          <span className="muted">
            {link.protocol} · <span className={`badge h-${link.health}`}>{link.health}</span>
          </span>
        </div>
        <button className="close-btn" onClick={onClose} aria-label="close">
          ✕
        </button>
      </div>

      <div className="link-facts">
        <span className="muted">in:</span> <b>{last?.in_bps != null ? formatBps(last.in_bps) : '—'}</b>
        <span className="muted"> · out:</span> <b>{last?.out_bps != null ? formatBps(last.out_bps) : '—'}</b>
      </div>

      <h4>Throughput</h4>
      <LinkTrafficChart linkId={linkId} />
    </div>
  );
}
