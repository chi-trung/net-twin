/**
 * LinkTrafficChart — ECharts dual-line chart for one link's throughput.
 *
 * Two series (in/out bps) = two series → legend present, direct identity via
 * distinct hues (blue/orange from the categorical order). Y axis auto-formats
 * to human-readable bps (Kbps/Mbps/Gbps).
 *
 * A forecast overlay projects the busier direction over the horizon: dashed
 * trend line + shaded confidence band, with the capacity line marked when the
 * verdict flags a risk.
 */

import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { api } from '../api/client';
import type { ForecastVerdict } from '../types';

echarts.use([LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

export function formatBps(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)} Gbps`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} Mbps`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)} Kbps`;
  return `${Math.round(v)} bps`;
}

function forecastSummary(v: ForecastVerdict): string {
  const trend = v.slope_per_hour >= 0 ? '+' : '−';
  const slope = formatBps(Math.abs(v.slope_per_hour));
  const eta = v.eta_text ? ` · ETA ${v.eta_text}` : '';
  const risk = v.risk ? ' · ⚠ capacity risk' : '';
  return `forecast: ${trend}${slope}/h → ${formatBps(v.projected)}${eta}${risk}`;
}

export function LinkTrafficChart({ linkId }: { linkId: number }) {
  const { data } = useQuery({
    queryKey: ['linkTraffic', linkId],
    queryFn: () => api.linkTraffic(linkId),
    refetchInterval: 10_000,
  });
  const { data: fc } = useQuery({
    queryKey: ['linkForecast', linkId],
    queryFn: () => api.linkForecast(linkId),
    refetchInterval: 60_000,
  });

  const points = data?.points ?? [];
  if (points.length === 0) return <p className="muted">no traffic samples yet — wait one monitor cycle</p>;

  const verdict = fc?.verdict ?? null;
  const fcPoints = fc?.points ?? [];

  const series: object[] = [
    {
      name: 'in',
      type: 'line' as const,
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#4f8ff7', width: 2 },
      areaStyle: { color: 'rgba(79,143,247,0.10)' },
      data: points
        .filter((p) => p.in_bps !== null)
        .map((p) => [new Date(p.timestamp).getTime(), p.in_bps]),
    },
    {
      name: 'out',
      type: 'line' as const,
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#eb6834', width: 2 },
      areaStyle: { color: 'rgba(235,104,52,0.10)' },
      data: points
        .filter((p) => p.out_bps !== null)
        .map((p) => [new Date(p.timestamp).getTime(), p.out_bps]),
    },
  ];

  if (verdict && fcPoints.length > 0) {
    // band as two stacked, mostly-transparent areas around the projection
    series.push({
      name: 'forecast band',
      type: 'line' as const,
      symbol: 'none',
      stack: 'forecast-band',
      lineStyle: { opacity: 0 },
      areaStyle: { color: 'rgba(148,116,235,0.14)' },
      data: fcPoints.map((p) => [new Date(p.timestamp).getTime(), p.upper - p.lower]),
    });
    series.push({
      name: 'forecast band base',
      type: 'line' as const,
      symbol: 'none',
      stack: 'forecast-band',
      lineStyle: { opacity: 0 },
      tooltip: { show: false },
      data: fcPoints.map((p) => [new Date(p.timestamp).getTime(), p.lower]),
    });
    // dashed projection of the mean
    series.push({
      name: 'forecast',
      type: 'line' as const,
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#9474eb', width: 2, type: 'dashed' },
      data: fcPoints.map((p) => [new Date(p.timestamp).getTime(), p.value]),
    });
  }

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 60, right: 12, top: 34, bottom: 26 },
    legend: {
      top: 4,
      textStyle: { color: '#7d8db1', fontSize: 10 },
      itemWidth: 14,
    },
    xAxis: {
      type: 'time' as const,
      axisLabel: { color: '#7d8db1', fontSize: 10 },
      axisLine: { lineStyle: { color: '#223052' } },
    },
    yAxis: {
      type: 'value' as const,
      name: 'throughput',
      nameTextStyle: { color: '#7d8db1' },
      axisLabel: {
        color: '#7d8db1',
        fontSize: 10,
        formatter: (v: number) => formatBps(v),
      },
      splitLine: { lineStyle: { color: 'rgba(34,48,82,0.5)' } },
      max: (v: { max: number }) =>
        verdict && verdict.risk ? Math.max(v.max, verdict.capacity * 1.05) : null,
    },
    tooltip: {
      trigger: 'axis' as const,
      valueFormatter: (v: number) => (v !== null && v !== undefined ? formatBps(v) : '—'),
    },
    series,
  };

  return (
    <div>
      <ReactECharts echarts={echarts} option={option} style={{ height: 200 }} notMerge lazyUpdate />
      {verdict && verdict.risk && (
        <p style={{ color: '#f5b04d', fontSize: 12, margin: '2px 0 0' }}>
          ⚠ {forecastSummary(verdict)}
        </p>
      )}
      {verdict && !verdict.risk && (
        <p className="muted" style={{ fontSize: 12, margin: '2px 0 0' }}>
          ↗ {forecastSummary(verdict)}
        </p>
      )}
    </div>
  );
}
