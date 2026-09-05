/**
 * TopologyGraph — the digital twin rendered as a live Cytoscape graph.
 *
 * Full topology comes from REST (source of truth); the twin store supplies
 * incremental health changes so node colors update in realtime without a
 * refetch. Structural changes (topologyRevision bump) trigger a REST refetch.
 *
 * Node shape encodes device type; color encodes health; clicking a node
 * selects it (parent handles the detail panel).
 *
 * Toolbar modes:
 * - explore   — click selects the device (default)
 * - what-if   — click runs blast-radius analysis and highlights the impact;
 *               "apply" pushes the failure into the real outage registry
 * - path      — click two devices to trace and highlight the shortest path
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { api } from '../api/client';
import { twinStore } from '../state/twinStore';
import type { PathResult, WhatIfResult } from '../types';
import type { DeviceType, HealthState } from '../types';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(cytoscape as any).use?.(fcose);

const HEALTH_COLOR: Record<HealthState, string> = {
  up: '#2ecc71',
  down: '#e74c3c',
  degraded: '#f1c40f',
  unknown: '#7f8c8d',
};

const NODE_SHAPE: Record<DeviceType, string> = {
  router: 'hexagon',
  switch: 'round-rectangle',
  host: 'ellipse',
  firewall: 'diamond',
  access_point: 'triangle',
  unknown: 'ellipse',
};

type Mode = 'explore' | 'whatif' | 'path';

interface Props {
  onSelectDevice: (id: number) => void;
  onSelectLink: (id: number) => void;
}

export function TopologyGraph({ onSelectDevice, onSelectLink }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const twin = useSyncExternalStore(twinStore.subscribe, twinStore.getSnapshot);

  const [mode, setMode] = useState<Mode>('explore');
  const modeRef = useRef<Mode>('explore'); // live mode for handlers registered once
  modeRef.current = mode;
  const [whatIf, setWhatIf] = useState<WhatIfResult | null>(null);
  const [pathResult, setPathResult] = useState<PathResult | null>(null);
  const pathPicksRef = useRef<number[]>([]); // first/second click in path mode
  const [pathPickCount, setPathPickCount] = useState(0);

  const { data: topology } = useQuery({
    queryKey: ['topology', twin.topologyRevision],
    queryFn: api.topology,
  });

  const whatIfMutation = useMutation({ mutationFn: api.whatIf });
  const pathMutation = useMutation({ mutationFn: ([a, b]: [number, number]) => api.tracePath(a, b) });
  const applyOutage = useMutation({
    mutationFn: (ip: string) => api.simulateOutage(ip),
    onSuccess: () => api.runDiscovery().then(() => api.runMonitor()),
  });

  const clearHighlight = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('isolated degraded path fail');
    cy.nodes().removeStyle('overlay-color overlay-opacity');
    cy.edges().removeStyle('overlay-color overlay-opacity line-color');
  }, []);

  // ── node click behavior per mode ────────────────────────────────
  const handleNodeTap = useCallback(
    (id: number) => {
      const cy = cyRef.current;
      if (!cy) return;
      clearHighlight();

      // The cy tap listener is registered once (see the topology effect
      // below), so a closure over `mode` would be stale — read the live
      // mode from the ref instead.
      if (modeRef.current === 'explore') {
        onSelectDevice(id);
        return;
      }

      if (modeRef.current === 'whatif') {
        whatIfMutation.mutate(id, {
          onSuccess: (result) => {
            setWhatIf(result);
            const failed = cy.getElementById(String(id));
            failed.addClass('fail');
            result.isolated.forEach((d) => cy.getElementById(String(d.id)).addClass('isolated'));
            result.degraded.forEach((d) => cy.getElementById(String(d.id)).addClass('degraded'));
          },
        });
        return;
      }

      // path mode: collect two picks then trace
      const picks = pathPicksRef.current;
      picks.push(id);
      cy.getElementById(String(id)).addClass('path');
      if (picks.length < 2) {
        setPathPickCount(1);
        return;
      }
      const [a, b] = picks.splice(0, 2);
      setPathPickCount(0);
      pathMutation.mutate([a, b], {
        onSuccess: (result) => {
          setPathResult(result);
          clearHighlight();
          if (result.found) {
            result.device_ids.forEach((nid) => cy.getElementById(String(nid)).addClass('path'));
            result.link_ids.forEach((lid) => cy.getElementById(String(lid)).addClass('path'));
          }
        },
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [onSelectDevice, clearHighlight],
  );

  // ── build/refresh elements when topology (re)loads ──────────────
  useEffect(() => {
    if (!containerRef.current || !topology) return;

    const elements: cytoscape.ElementDefinition[] = [
      ...topology.nodes.map((n) => ({
        group: 'nodes' as const,
        data: {
          id: String(n.id),
          label: n.name,
          health: n.health,
          deviceType: n.device_type,
          ip: n.ip_address,
        },
      })),
      ...topology.edges.map((e) => ({
        group: 'edges' as const,
        data: {
          id: String(e.id),
          source: String(e.source_device_id),
          target: String(e.target_device_id),
          protocol: e.protocol,
        },
      })),
    ];

    if (!cyRef.current) {
      cyRef.current = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: 'node',
            style: {
              label: 'data(label)',
              shape: 'ellipse',
              width: 46,
              height: 46,
              color: '#dbe4f5',
              'font-size': '10px',
              'text-valign': 'bottom',
              'text-margin-y': 5,
              'background-opacity': 0.9,
              'border-width': 2,
              'border-color': '#223052',
            },
          },
          {
            selector: 'edge',
            style: {
              width: 2,
              'line-color': '#3a4d78',
              'curve-style': 'bezier',
              'target-arrow-shape': 'none',
            },
          },
          {
            selector: 'edge[protocol = "lldp"]',
            style: { 'line-style': 'solid', width: 3 },
          },
          {
            selector: 'node:selected',
            style: { 'border-color': '#4f8ff7', 'border-width': 4 },
          },
          // ── analysis highlights ────────────────────────────────
          {
            selector: 'node.fail',
            style: { 'border-color': '#e74c3c', 'border-width': 5, 'overlay-opacity': 0.25, 'overlay-color': '#e74c3c' },
          },
          {
            selector: 'node.isolated',
            style: { 'border-color': '#e74c3c', 'border-width': 4, 'overlay-opacity': 0.35, 'overlay-color': '#e74c3c' },
          },
          {
            selector: 'node.degraded',
            style: { 'border-color': '#f1c40f', 'border-width': 4, 'overlay-opacity': 0.3, 'overlay-color': '#f1c40f' },
          },
          {
            selector: 'node.path',
            style: { 'border-color': '#4f8ff7', 'border-width': 4, 'overlay-opacity': 0.3, 'overlay-color': '#4f8ff7' },
          },
          {
            selector: 'edge.path',
            style: { 'line-color': '#4f8ff7', width: 5, 'overlay-opacity': 0.25, 'overlay-color': '#4f8ff7' },
          },
        ],
        layout: { name: 'preset' },
      });

      cyRef.current.on('tap', 'node', (evt) => {
        const id = Number(evt.target.id());
        if (!Number.isNaN(id)) handleNodeTap(id);
      });
      cyRef.current.on('tap', 'edge', (evt) => {
        if (modeRef.current !== 'explore') return;
        const id = Number(evt.target.id());
        if (!Number.isNaN(id)) onSelectLink(id);
      });
      // expose for console debugging and the e2e harness
      (window as unknown as Record<string, unknown>).cy = cyRef.current;
    } else {
      cyRef.current.elements().remove();
      cyRef.current.add(elements);
    }

    cyRef.current.layout({ name: 'fcose', animate: false } as cytoscape.LayoutOptions).run();
    applyHealth(cyRef.current, twin);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topology]);

  // live health recoloring on every twin event
  useEffect(() => {
    if (cyRef.current) applyHealth(cyRef.current, twin);
  }, [twin]);

  // switching mode resets any analysis overlay
  useEffect(() => {
    clearHighlight();
    setWhatIf(null);
    setPathResult(null);
    pathPicksRef.current = [];
    setPathPickCount(0);
  }, [mode, clearHighlight]);

  useEffect(() => () => cyRef.current?.destroy(), []);

  return (
    <div className="topology-wrap">
      <div className="graph-toolbar">
        {(
          [
            ['explore', '🔍 Explore'],
            ['whatif', '💥 What-if'],
            ['path', '🛣 Path'],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            className={`tool-btn ${mode === m ? 'active' : ''}`}
            onClick={() => setMode(m)}
            type="button"
          >
            {label}
          </button>
        ))}
        {mode === 'path' && (
          <span className="tool-hint">
            {pathPickCount === 0 ? 'click source device' : 'click destination device'}
          </span>
        )}
        {mode === 'whatif' && <span className="tool-hint">click a device to simulate failure</span>}
      </div>

      <div ref={containerRef} className="topology-canvas" />

      {mode === 'whatif' && whatIf && (
        <div className="whatif-report">
          <div className="whatif-title">
            💥 Failure: <b>{whatIf.failed_device.name}</b>
            <span className={`chip chip-${whatIf.failed_device.health}`}>
              {whatIf.failed_device.health}
            </span>
          </div>
          <div className="whatif-stats">
            <span className="wi-stat wi-iso">⛔ {whatIf.isolated.length} isolated</span>
            <span className="wi-stat wi-deg">⚠ {whatIf.degraded.length} degraded</span>
            <span className="wi-stat">🔗 {whatIf.affected_links.length} links lost</span>
          </div>
          {whatIf.isolated.length > 0 && (
            <div className="whatif-list">
              {whatIf.isolated.map((d) => (
                <span key={d.id} className="wi-dev wi-iso">⛔ {d.name}</span>
              ))}
              {whatIf.degraded.map((d) => (
                <span key={d.id} className="wi-dev wi-deg">⚠ {d.name}</span>
              ))}
            </div>
          )}
          <div className="whatif-actions">
            <button
              type="button"
              className="tool-btn danger"
              disabled={applyOutage.isPending}
              onClick={() => applyOutage.mutate(whatIf.failed_device.ip_address)}
            >
              {applyOutage.isPending ? 'applying…' : '⚡ Apply outage'}
            </button>
            <button type="button" className="tool-btn" onClick={() => { clearHighlight(); setWhatIf(null); }}>
              Dismiss
            </button>
          </div>
        </div>
      )}

      {mode === 'path' && pathResult && (
        <div className="whatif-report">
          {pathResult.found ? (
            <>
              <div className="whatif-title">
                🛣 Path: <b>{pathResult.devices[0].name}</b> → <b>{pathResult.devices.at(-1)?.name}</b>
              </div>
              <div className="whatif-list">
                {pathResult.devices.map((d, i) => (
                  <span key={d.id} className="wi-dev wi-path">
                    {i > 0 && ' → '}
                    {d.name}
                  </span>
                ))}
              </div>
              <div className="whatif-stats">
                <span className="wi-stat">{pathResult.hops} hops · {pathResult.link_ids.length} links</span>
              </div>
            </>
          ) : (
            <div className="whatif-title">❌ No path between the selected devices</div>
          )}
          <div className="whatif-actions">
            <button type="button" className="tool-btn" onClick={() => { clearHighlight(); setPathResult(null); }}>
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function applyHealth(cy: cytoscape.Core, twin: ReturnType<typeof twinStore.getSnapshot>) {
  for (const [id, node] of twin.nodes) {
    const el = cy.getElementById(String(id));
    if (el.nonempty()) {
      el.style('background-color', HEALTH_COLOR[node.health] ?? HEALTH_COLOR.unknown);
      el.style('shape', NODE_SHAPE[node.device_type] ?? 'ellipse');
    }
  }
  // also color from REST-loaded health for nodes not yet in the twin map
  cy.nodes().forEach((el) => {
    const health = el.data('health') as HealthState;
    if (!twin.nodes.has(Number(el.id()))) {
      el.style('background-color', HEALTH_COLOR[health] ?? HEALTH_COLOR.unknown);
      el.style('shape', NODE_SHAPE[(el.data('deviceType') ?? 'unknown') as DeviceType]);
    }
  });
}
