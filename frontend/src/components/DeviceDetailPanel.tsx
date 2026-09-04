/** DeviceDetailPanel — identity, interfaces and live metric chart for one node. */

import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { MetricsChart } from './MetricsChart';

interface Props {
  deviceId: number;
  onClose: () => void;
}

export function DeviceDetailPanel({ deviceId, onClose }: Props) {
  const { data: device, isLoading } = useQuery({
    queryKey: ['device', deviceId],
    queryFn: () => api.device(deviceId),
    refetchInterval: 15_000,
  });

  if (isLoading || !device) return null;

  return (
    <div className="detail-panel">
      <div className="detail-head">
        <div>
          <h3>{device.name}</h3>
          <span className="muted">
            {device.ip_address} · {device.device_type} ·{' '}
            <span className={`badge h-${device.health}`}>{device.health}</span>
          </span>
        </div>
        <button className="close-btn" onClick={onClose} aria-label="close">
          ✕
        </button>
      </div>

      {device.sys_description && <p className="sysdesc">{device.sys_description}</p>}

      <h4>Interfaces</h4>
      {device.interfaces.length === 0 ? (
        <p className="muted">no interfaces reported</p>
      ) : (
        <table className="iface-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Oper</th>
              <th>Speed</th>
              <th>MAC</th>
            </tr>
          </thead>
          <tbody>
            {device.interfaces.map((i) => (
              <tr key={i.id}>
                <td>{i.name}</td>
                <td>
                  <span className={`badge h-${i.oper_status === 'up' ? 'up' : 'down'}`}>
                    {i.oper_status}
                  </span>
                </td>
                <td>{i.speed_mbps ? `${i.speed_mbps >= 1000 ? `${i.speed_mbps / 1000}G` : `${i.speed_mbps}M`}` : '—'}</td>
                <td className="mac">{i.mac_address ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Latency history</h4>
      <MetricsChart deviceId={deviceId} metric="latency_ms" />
    </div>
  );
}
