/**
 * AlertToasts — ephemeral popup stack for realtime alert events.
 *
 * Listens to raw twin events (see twinStore.onRawEvent); every alert.raised
 * slides a toast in from the right that auto-dismisses after a few seconds,
 * alert.cleared removes its counterpart early. Deliberately dependency-free.
 */

import { useEffect, useState } from 'react';
import { twinStore } from '../state/twinStore';
import type { TwinEvent } from '../types';

interface Toast {
  key: number; // alert_id
  severity: 'info' | 'warning' | 'critical';
  rule: string;
  message: string;
}

const LIFETIME_MS = 6_000;
const MAX_STACK = 4;

export function AlertToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const onEvent = (event: TwinEvent) => {
      if (event.type === 'alert.raised') {
        const raised = event as Extract<TwinEvent, { type: 'alert.raised' }>;
        setToasts((prev) => {
          if (prev.some((t) => t.key === raised.alert_id)) return prev;
          return [
            ...prev,
            {
              key: raised.alert_id,
              severity: raised.severity,
              rule: raised.rule,
              message: raised.message,
            },
          ].slice(-MAX_STACK);
        });
      } else if (event.type === 'alert.cleared') {
        const cleared = event as Extract<TwinEvent, { type: 'alert.cleared' }>;
        setToasts((prev) => prev.filter((t) => t.key !== cleared.alert_id));
      }
    };
    return twinStore.onRawEvent(onEvent);
  }, []);

  // auto-dismiss the oldest toast on a timer that restarts with the stack
  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = window.setTimeout(() => {
      setToasts((prev) => prev.slice(1));
    }, LIFETIME_MS);
    return () => window.clearTimeout(timer);
  }, [toasts]);

  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack" role="status">
      {toasts.map((t) => (
        <div key={t.key} className={`toast toast-${t.severity}`}>
          <span className="toast-rule">{t.rule.replace(/_/g, ' ')}</span>
          <span className="toast-msg">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
