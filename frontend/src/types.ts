/** Shared API types mirroring backend/app/api/schemas.py. */

export type DeviceType = 'router' | 'switch' | 'host' | 'firewall' | 'access_point' | 'unknown';
export type HealthState = 'up' | 'down' | 'degraded' | 'unknown';
export type AlertSeverity = 'info' | 'warning' | 'critical';
export type AlertStatus = 'active' | 'cleared';

export interface Device {
  id: number;
  name: string;
  ip_address: string;
  mac_address: string | null;
  device_type: DeviceType;
  health: HealthState;
  sys_description: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface InterfaceInfo {
  id: number;
  if_index: number;
  name: string;
  mac_address: string | null;
  ip_address: string | null;
  admin_status: string;
  oper_status: string;
  speed_mbps: number | null;
}

export interface DeviceDetail extends Device {
  interfaces: InterfaceInfo[];
}

export interface Link {
  id: number;
  source_device_id: number;
  target_device_id: number;
  source_interface_id: number | null;
  target_interface_id: number | null;
  protocol: string;
  health: HealthState;
}

export interface Topology {
  nodes: Device[];
  edges: Link[];
}

export interface MetricPoint {
  timestamp: string;
  value: number;
}

export interface MetricSeries {
  device_id: number;
  metric_name: string;
  points: MetricPoint[];
}

export interface Alert {
  id: number;
  device_id: number | null;
  rule: string;
  severity: AlertSeverity;
  status: AlertStatus;
  message: string;
  value: number | null;
  threshold: number | null;
  created_at: string;
  cleared_at: string | null;
}

/** Twin graph node shape sent by the WebSocket `twin.snapshot` event. */
export interface WsNode {
  id: number;
  name: string;
  ip: string;
  device_type: DeviceType;
  health: HealthState;
}

export interface WsEdge {
  id: number;
  source: number;
  target: number;
  protocol: string;
  health: HealthState;
}

/** Discriminated union of realtime events from /ws/events. */
export type TwinEvent =
  | { type: 'twin.snapshot'; nodes: WsNode[]; edges: WsEdge[] }
  | { type: 'device.health_changed'; device_id: number; name: string; health: HealthState }
  | { type: 'topology.updated'; devices_created: number; devices_staled: number; links_created: number }
  | { type: 'alert.raised'; alert_id: number; device_id: number; rule: string; severity: AlertSeverity; message: string }
  | { type: 'alert.cleared'; alert_id: number; device_id: number; rule: string }
  | { type: 'metrics.flushed'; devices: number; timestamp: string }
  | { type: string; [key: string]: unknown };

// ── twin intelligence ──────────────────────────────────────────────

export interface RcaDevice {
  id: number;
  name: string;
  health: HealthState;
  device_type: DeviceType;
}

export interface RcaEvidence {
  alert_id: number;
  rule: string;
  severity: AlertSeverity;
  message: string;
}

export interface RcaHypothesis {
  device: RcaDevice;
  score: number;
  headline: string;
  reasons: string[];
  evidence: RcaEvidence[];
}

export interface RcaResult {
  symptom: RcaDevice;
  hypotheses: RcaHypothesis[];
}

export interface WhatIfResult {
  failed_device: Device;
  isolated: Device[];
  degraded: Device[];
  affected_links: Link[];
  impacted_count: number;
}

export interface PathResult {
  found: boolean;
  hops: number;
  device_ids: number[];
  devices: Device[];
  link_ids: number[];
}

export interface Overview {
  total_devices: number;
  up: number;
  down: number;
  degraded: number;
  unknown: number;
  total_links: number;
  active_alerts: number;
  critical_alerts: number;
  avg_latency_ms: number | null;
  healthiest_updated_at: string | null;
}

export interface TrafficPoint {
  timestamp: string;
  in_bps: number | null;
  out_bps: number | null;
}

export interface LinkTraffic {
  link_id: number;
  points: TrafficPoint[];
}
