/**
 * App shell: header with connection status, live topology graph, device
 * detail drawer and alert feed — the digital twin control console.
 */

import { useCallback, useState, useSyncExternalStore } from 'react';
import { useTwinEvents } from './hooks/useTwinEvents';
import { twinStore } from './state/twinStore';
import type { TwinEvent } from './types';
import { DeviceTable } from './components/DeviceTable';
import { TopologyGraph } from './components/TopologyGraph';
import { DeviceDetailPanel } from './components/DeviceDetailPanel';
import { AlertFeed } from './components/AlertFeed';

const STATUS_LABEL: Record<string, string> = {
  connecting: 'connecting…',
  open: 'live',
  reconnecting: 'reconnecting…',
  closed: 'offline',
};

export default function App() {
  const [selectedDevice, setSelectedDevice] = useState<number | null>(null);
  const handleEvent = useCallback((event: TwinEvent) => twinStore.applyEvent(event), []);
  const { status } = useTwinEvents(handleEvent);
  const twin = useSyncExternalStore(twinStore.subscribe, twinStore.getSnapshot);

  const upCount = [...twin.nodes.values()].filter((n) => n.health === 'up').length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◉</span>
          <h1>net-twin</h1>
          <span className="brand-sub">network digital twin</span>
        </div>
        <div className="topbar-stats">
          <span className="stat">
            {twin.nodes.size} nodes · {upCount} up · {twin.edges.size} links
          </span>
          <span className={`conn-badge conn-${status}`}>{STATUS_LABEL[status]}</span>
        </div>
      </header>
      <main className="layout">
        <section className="panel panel-graph">
          <h2>Topology</h2>
          <TopologyGraph onSelectDevice={setSelectedDevice} />
        </section>
        <aside className="panel panel-side">
          {selectedDevice !== null ? (
            <DeviceDetailPanel deviceId={selectedDevice} onClose={() => setSelectedDevice(null)} />
          ) : (
            <DeviceTable />
          )}
          <AlertFeed />
        </aside>
      </main>
    </div>
  );
}
