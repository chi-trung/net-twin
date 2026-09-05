/**
 * RcaPanel — ranked root-cause hypotheses for an unhealthy device.
 *
 * Renders the /analysis/rca response: each hypothesis as a card with its
 * score, headline reason, supporting evidence chips and the full reason
 * list. Hidden entirely when the device is healthy (no symptom, no panel).
 */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { RcaResult } from '../types';

export function RcaPanel({ deviceId }: { deviceId: number }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['rca', deviceId],
    queryFn: () => api.rca(deviceId),
    refetchInterval: 20_000,
    retry: 1,
  });

  if (isLoading || isError || !data) return null;
  if (data.hypotheses.length === 0) return null;

  return <RcaView data={data} />;
}

function RcaView({ data }: { data: RcaResult }) {
  const top = data.hypotheses[0];
  return (
    <div className="rca-panel">
      <h4>🧠 Root-cause analysis</h4>
      {data.hypotheses.map((h, i) => (
        <div key={h.device.id} className={`rca-card ${i === 0 ? 'rca-top' : ''}`}>
          <div className="rca-card-head">
            <span className="rca-rank">{i + 1}</span>
            <b>{h.device.name}</b>
            <span className="rca-score" title="evidence score">
              {h.score.toFixed(1)}
            </span>
          </div>
          <div className="rca-reasons">
            {h.reasons.map((r) => (
              <div key={r} className="rca-reason">
                {r}
              </div>
            ))}
          </div>
          {h.evidence.length > 0 && (
            <div className="rca-evidence">
              {h.evidence.map((e) => (
                <span key={e.alert_id} className={`chip chip-${e.severity}`}>
                  {e.severity}: {e.rule}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
      <p className="rca-note muted">
        Likely cause: <b>{top.device.name}</b> — {top.headline.toLowerCase()}
      </p>
    </div>
  );
}
