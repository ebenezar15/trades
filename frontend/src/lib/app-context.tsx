// App-wide state: backend status (polled + refreshed on demand) and toast notifications (incl. WS alerts).
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { StatusResponse } from '../api/types';
import { useWsMessages } from '../api/ws';
import { alertKind, beep } from './sound';
import { useApi } from './useApi';

export interface Toast {
  id: number;
  tone: 'info' | 'up' | 'down' | 'warn';
  text: string;
}

interface AppCtx {
  status: StatusResponse | null;
  statusError: string | null;
  reloadStatus: () => Promise<void>;
  toasts: Toast[];
  toast: (text: string, tone?: Toast['tone']) => void;
  dismiss: (id: number) => void;
}

const Ctx = createContext<AppCtx | null>(null);
let toastId = 0;

export function AppProvider({ children }: { children: ReactNode }) {
  const st = useApi(api.status, [], 5000);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const toast = useCallback(
    (text: string, tone: Toast['tone'] = 'info') => {
      const id = ++toastId;
      setToasts((ts) => [...ts.slice(-3), { id, tone, text }]);
      setTimeout(() => dismiss(id), 5000);
    },
    [dismiss],
  );

  useWsMessages(
    (m) => {
      if (m.type === 'alert') {
        const lvl = m.data.level;
        const kind = alertKind(`${m.data.title ?? ''} ${m.data.message}`);
        if (kind !== 'info') beep(kind);
        else if (lvl === 'error' || lvl === 'critical') beep('stop');
        if (kind === 'stop') {
          toast(m.data.title ? `⛔ ${m.data.title}: ${m.data.message}` : `⛔ ${m.data.message}`, 'down');
          return;
        }
        if (kind === 'target') {
          toast(m.data.title ? `🎯 ${m.data.title}: ${m.data.message}` : `🎯 ${m.data.message}`, 'up');
          return;
        }
        toast(m.data.title ? `${m.data.title}: ${m.data.message}` : m.data.message, lvl === 'error' || lvl === 'critical' ? 'down' : lvl === 'warning' ? 'warn' : 'info');
      } else if (m.type === 'signal' && m.data.status !== 'blocked') {
        toast(`New signal: ${m.data.strategy_name} ${m.data.underlying} ${m.data.side}`, 'up');
      }
    },
    ['alert', 'signal'],
  );

  const value = useMemo(
    () => ({ status: st.data, statusError: st.error, reloadStatus: st.reload, toasts, toast, dismiss }),
    [st.data, st.error, st.reload, toasts, toast, dismiss],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useApp outside AppProvider');
  return c;
}
