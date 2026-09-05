/** AlertFeed — active + recent alerts, live-updated via twin events. */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { twinStore } from '../state/twinStore';

export function AlertFeed() {
  const queryClient = useQueryClient();
  const twin = useSyncExternalStore(twinStore.subscribe, twinStore.getSnapshot);

  // refetch whenever an alert event arrives (lastEventAt changes)
  useEffect(() => {
    queryClient.invalidateQueries({ queryKey: ['alerts'] });
  }, [twin.lastEventAt, queryClient]);

  const { data: alerts } = useQuery({
    queryKey: ['alerts'],
    queryFn: () => api.alerts(),
    refetchInterval: 15_000,
  });

  const active = (alerts ?? []).filter((a) => a.status === 'active');

  return (
    <div className="alert-feed">
      <h2>
        Alerts {active.length > 0 && <span className="alert-count">{active.length} active</span>}
      </h2>
      {active.length === 0 ? (
        <p className="muted">all clear — no active alerts</p>
      ) : (
        <ul>
          {active.map((a) => (
            <li key={a.id} className={`alert alert-${a.severity}`}>
              <span className="alert-rule">
                {a.rule.endsWith('_anomaly') && <span title="statistical anomaly">📈 </span>}
                {a.rule}
              </span>
              <span className="alert-msg">{a.message}</span>
              <span className="alert-time">
                {new Date(a.created_at).toLocaleTimeString()}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
