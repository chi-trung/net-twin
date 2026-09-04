/**
 * App shell: header with connection status + twin event wiring.
 * The topology graph and dashboard panels land in Phase 6.
 */

import { useCallback, useSyncExternalStore } from 'react';
import { useTwinEvents } from './hooks/useTwinEvents';
import { twinStore } from './state/twinStore';
import type { TwinEvent } from './types';
import { DeviceTable } from './components/DeviceTable';

const STATUS_LABEL: Record<string, string> = {
  connecting: 'connecting…',
  open: 'live',
  reconnecting: 'reconnecting…',
  closed: 'offline',
};

export default function App() {
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
            {twin.nodes.size} nodes · {upCount} up
          </span>
          <span className={`conn-badge conn-${status}`}>{STATUS_LABEL[status]}</span>
        </div>
      </header>
      <main className="layout">
        <section className="panel panel-graph">
          <h2>Topology</h2>
          <p className="placeholder">graph view lands in Phase 6 — twin currently mirrors {twin.nodes.size} nodes / {twin.edges.size} links</p>
        </section>
        <aside className="panel panel-side">
          <DeviceTable />
        </aside>
      </main>
    </div>
  );
}
