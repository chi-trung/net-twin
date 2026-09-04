/** Typed HTTP client for the net-twin REST API. */

import axios from 'axios';
import type { Alert, DeviceDetail, MetricSeries, Topology } from '../types';

const baseURL = import.meta.env.VITE_API_BASE_URL || '';

export const http = axios.create({
  baseURL: `${baseURL}/api/v1`,
  timeout: 10_000,
});

export const api = {
  topology: () => http.get<Topology>('/topology').then((r) => r.data),
  devices: () => http.get<DeviceDetail[]>('/devices').then((r) => r.data),
  device: (id: number) => http.get<DeviceDetail>(`/devices/${id}`).then((r) => r.data),
  metrics: (id: number, metric = 'latency_ms', limit = 300) =>
    http
      .get<MetricSeries>(`/devices/${id}/metrics`, { params: { metric, limit } })
      .then((r) => r.data),
  alerts: (status?: string) =>
    http.get<Alert[]>('/alerts', { params: status ? { status } : {} }).then((r) => r.data),
  runDiscovery: () => http.post('/discovery/run').then((r) => r.data),
  runMonitor: () => http.post('/monitor/run').then((r) => r.data),
};
