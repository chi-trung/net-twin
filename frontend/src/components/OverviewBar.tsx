/**
 * OverviewBar — fleet KPIs for the dashboard header.
 *
 * Form choices (dataviz method): the data's job is "a handful of headline
 * numbers" → a KPI row of stat tiles; the health mix is part-to-whole → a
 * single horizontal stacked bar (not a donut) using the project's reserved
 * status colors, with a 2px surface gap between segments. Identity is never
 * color-alone: each segment has a visible count label + the legend names it.
 */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { HealthState } from '../types';

const HEALTH_ORDER: HealthState[] = ['up', 'down', 'degraded', 'unknown'];

export function OverviewBar() {
  const { data: ov } = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: 10_000,
  });
  if (!ov) return null;

  const total = Math.max(ov.total_devices, 1);
  const segments = HEALTH_ORDER.filter((h) => ov[h] > 0);

  return (
    <div className="overview-bar">
      <div className="kpi-row">
        <StatTile label="Devices" value={ov.total_devices} />
        <StatTile
          label="Reachable"
          value={ov.up}
          sub={`${Math.round((ov.up / total) * 100)}% of fleet`}
        />
        <StatTile
          label="Down"
          value={ov.down}
          tone={ov.down > 0 ? 'critical' : undefined}
        />
        <StatTile label="Links" value={ov.total_links} />
        <StatTile
          label="Active alerts"
          value={ov.active_alerts}
          tone={ov.critical_alerts > 0 ? 'critical' : ov.active_alerts > 0 ? 'warning' : undefined}
          sub={ov.critical_alerts > 0 ? `${ov.critical_alerts} critical` : undefined}
        />
        <StatTile
          label="Avg latency"
          value={ov.avg_latency_ms !== null ? `${ov.avg_latency_ms.toFixed(1)} ms` : '—'}
        />
      </div>

      <div
        className="health-mix"
        role="img"
        aria-label={`Fleet health: ${ov.up} up, ${ov.down} down, ${ov.degraded} degraded, ${ov.unknown} unknown`}
      >
        {segments.map((h) => (
          <div
            key={h}
            className={`hm-seg hm-${h}`}
            style={{ flexGrow: ov[h] }}
            title={`${h}: ${ov[h]}`}
          >
            {ov[h]}
          </div>
        ))}
      </div>
      <div className="hm-legend">
        {segments.map((h) => (
          <span key={h} className="hm-key">
            <span className={`hm-dot hm-${h}`} aria-hidden />
            {h} {ov[h]}
          </span>
        ))}
      </div>
    </div>
  );
}

function StatTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: number | string;
  sub?: string;
  tone?: 'critical' | 'warning';
}) {
  return (
    <div className={`stat-tile ${tone ? `stat-${tone}` : ''}`}>
      <span className="st-label">{label}</span>
      <span className="st-value">{value}</span>
      {sub && <span className="st-sub">{sub}</span>}
    </div>
  );
}
