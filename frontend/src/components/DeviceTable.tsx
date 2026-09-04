/** Device list panel — REST-backed, health badges reflect live twin events. */

import { useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { api } from '../api/client';
import { twinStore } from '../state/twinStore';
import type { HealthState } from '../types';

const HEALTH_CLASS: Record<HealthState, string> = {
  up: 'h-up',
  down: 'h-down',
  degraded: 'h-degraded',
  unknown: 'h-unknown',
};

export function DeviceTable() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['devices'],
    queryFn: api.devices,
    refetchInterval: 10_000,
  });
  const twin = useSyncExternalStore(twinStore.subscribe, twinStore.getSnapshot);

  if (isLoading) return <p className="muted">loading devices…</p>;
  if (isError) return <p className="error">cannot reach the net-twin API</p>;

  return (
    <div className="device-table">
      <h2>Devices</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Health</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((d) => {
            const live = twin.nodes.get(d.id);
            const health = live?.health ?? d.health;
            return (
              <tr key={d.id}>
                <td>
                  <span className={`dot ${HEALTH_CLASS[health]}`} />
                  {d.name}
                  <span className="ip">{d.ip_address}</span>
                </td>
                <td className="type">{d.device_type}</td>
                <td>
                  <span className={`badge ${HEALTH_CLASS[health]}`}>{health}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
