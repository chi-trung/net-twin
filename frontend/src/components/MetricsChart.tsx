/**
 * MetricsChart — ECharts time-series line for one device metric.
 *
 * Uses echarts/core with tree-shaken module registration so the bundle only
 * carries the line chart + time axis, not the whole ECharts library.
 */

import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { api } from '../api/client';

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

interface Props {
  deviceId: number;
  metric: string;
}

export function MetricsChart({ deviceId, metric }: Props) {
  const { data } = useQuery({
    queryKey: ['metrics', deviceId, metric],
    queryFn: () => api.metrics(deviceId, metric),
    refetchInterval: 10_000,
  });

  const points = data?.points ?? [];
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 44, right: 12, top: 18, bottom: 26 },
    xAxis: {
      type: 'time' as const,
      axisLabel: { color: '#7d8db1', fontSize: 10 },
      axisLine: { lineStyle: { color: '#223052' } },
    },
    yAxis: {
      type: 'value' as const,
      name: metric === 'latency_ms' ? 'ms' : '%',
      nameTextStyle: { color: '#7d8db1' },
      axisLabel: { color: '#7d8db1', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(34,48,82,0.5)' } },
    },
    tooltip: { trigger: 'axis' as const },
    series: [
      {
        name: metric,
        type: 'line' as const,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#4f8ff7', width: 2 },
        areaStyle: { color: 'rgba(79,143,247,0.12)' },
        data: points.map((p) => [new Date(p.timestamp).getTime(), p.value]),
      },
    ],
  };

  if (points.length === 0) return <p className="muted">no metric samples yet</p>;

  return (
    <ReactECharts
      echarts={echarts}
      option={option}
      style={{ height: 180 }}
      notMerge
      lazyUpdate
    />
  );
}
