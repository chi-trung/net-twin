/**
 * Twin store — the frontend's mirror of the backend digital twin.
 *
 * A tiny observable store (no external state lib needed): REST provides the
 * initial snapshot, WebSocket events mutate it incrementally. Components
 * subscribe via useSyncExternalStore.
 */

import type { TwinEvent, WsEdge, WsNode } from '../types';

export interface TwinState {
  nodes: Map<number, WsNode>;
  edges: Map<number, WsEdge>;
  lastEventAt: number | null;
  topologyRevision: number; // bumped on structural changes → triggers refetch
}

function initialState(): TwinState {
  return { nodes: new Map(), edges: new Map(), lastEventAt: null, topologyRevision: 0 };
}

let state = initialState();
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export const twinStore = {
  getSnapshot(): TwinState {
    return state;
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  reset() {
    state = initialState();
    emit();
  },
  /** Apply one realtime event immutably so React sees a new reference. */
  applyEvent(event: TwinEvent) {
    const nodes = new Map(state.nodes);
    const edges = new Map(state.edges);
    let topologyRevision = state.topologyRevision;

    switch (event.type) {
      case 'twin.snapshot': {
        const snapshot = event as Extract<TwinEvent, { type: 'twin.snapshot' }>;
        nodes.clear();
        edges.clear();
        for (const n of snapshot.nodes) nodes.set(n.id, n);
        for (const e of snapshot.edges) edges.set(e.id, e);
        topologyRevision += 1;
        break;
      }
      case 'device.health_changed': {
        const ev = event as Extract<TwinEvent, { type: 'device.health_changed' }>;
        const node = nodes.get(ev.device_id);
        if (node) nodes.set(ev.device_id, { ...node, health: ev.health });
        break;
      }
      case 'topology.updated': {
        // structural change: mark revision so the graph refetches full topology
        topologyRevision += 1;
        break;
      }
      default:
        // alert/metric events don't change graph structure
        break;
    }

    state = { nodes, edges, lastEventAt: Date.now(), topologyRevision };
    emit();
  },
};
