/**
 * useTwinEvents — resilient WebSocket connection to the twin event stream.
 *
 * Auto-reconnects with exponential backoff (1s → 30s cap). Every received
 * event is handed to the registered handler; the connection status is exposed
 * so the UI can show a live/reconnecting/offline badge.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { TwinEvent } from '../types';

export type ConnectionStatus = 'connecting' | 'open' | 'reconnecting' | 'closed';

const MAX_BACKOFF_MS = 30_000;

export function useTwinEvents(onEvent: (event: TwinEvent) => void) {
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const closedRef = useRef(false);

  const connect = useCallback(() => {
    if (closedRef.current) return;
    const url = import.meta.env.VITE_WS_URL || `${location.origin.replace(/^http/, 'ws')}/ws/events`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      attemptRef.current = 0;
      setStatus('open');
    };
    ws.onmessage = (msg) => {
      try {
        handlerRef.current(JSON.parse(msg.data) as TwinEvent);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onclose = () => {
      if (closedRef.current) return;
      setStatus('reconnecting');
      const delay = Math.min(1000 * 2 ** attemptRef.current, MAX_BACKOFF_MS);
      attemptRef.current += 1;
      timerRef.current = window.setTimeout(connect, delay);
    };
    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    closedRef.current = false;
    connect();
    return () => {
      closedRef.current = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { status };
}
