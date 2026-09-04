/**
 * TopologyGraph — the digital twin rendered as a live Cytoscape graph.
 *
 * Full topology comes from REST (source of truth); the twin store supplies
 * incremental health changes so node colors update in realtime without a
 * refetch. Structural changes (topologyRevision bump) trigger a REST refetch.
 *
 * Node shape encodes device type; color encodes health; clicking a node
 * selects it (parent handles the detail panel).
 */

import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { api } from '../api/client';
import { twinStore } from '../state/twinStore';
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

interface Props {
  onSelectDevice: (id: number) => void;
}

export function TopologyGraph({ onSelectDevice }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const twin = useSyncExternalStore(twinStore.subscribe, twinStore.getSnapshot);

  const { data: topology } = useQuery({
    queryKey: ['topology', twin.topologyRevision],
    queryFn: api.topology,
  });

  // build/refresh elements when topology (re)loads
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
        ],
        layout: { name: 'preset' },
      });

      cyRef.current.on('tap', 'node', (evt) => {
        const id = Number(evt.target.id());
        if (!Number.isNaN(id)) onSelectDevice(id);
      });
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

  useEffect(() => () => cyRef.current?.destroy(), []);

  return <div ref={containerRef} className="topology-canvas" />;
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
