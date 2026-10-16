// WebSocket connection to /ws with automatic reconnect (exponential backoff + jitter) and a React hook API.
// One shared socket for the whole app; components subscribe to messages via useWsMessages().
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { MOCK } from './client';
import type { WsMessage, WsType } from './types';

export type WsState = 'connecting' | 'open' | 'closed';
type Listener = (msg: WsMessage) => void;

class WsManager {
  private listeners = new Set<Listener>();
  private stateListeners = new Set<() => void>();
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private started = false;
  state: WsState = 'closed';
  lastMessageAt = 0;

  start() {
    if (this.started) return;
    this.started = true;
    if (MOCK) {
      this.startMock();
      return;
    }
    this.connect();
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.state === 'closed') this.reconnectNow();
    });
    window.addEventListener('online', () => this.reconnectNow());
  }

  private setState(s: WsState) {
    this.state = s;
    this.stateListeners.forEach((l) => l());
  }

  private url() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/ws`;
  }

  private connect() {
    this.setState('connecting');
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url());
    } catch {
      this.scheduleReconnect();
      return;
    }
    ws.onopen = () => {
      this.attempt = 0;
      this.setState('open');
    };
    ws.onmessage = (ev) => {
      this.lastMessageAt = Date.now();
      let msg: WsMessage;
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
      } catch {
        return;
      }
      if (msg && typeof msg === 'object' && 'type' in msg) this.emit(msg);
    };
    ws.onclose = () => {
      this.setState('closed');
      this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect() {
    if (this.timer) return;
    const base = Math.min(30_000, 1000 * 2 ** this.attempt);
    const delay = base / 2 + Math.random() * (base / 2);
    this.attempt += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  /** Reconnect immediately if closed (e.g. right after logging in). */
  reconnectNow() {
    if (this.state !== 'closed') return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.attempt = 0;
    this.connect();
  }

  private emit(msg: WsMessage) {
    this.listeners.forEach((l) => {
      try {
        l(msg);
      } catch (e) {
        console.error('ws listener error', e);
      }
    });
  }

  subscribe(l: Listener) {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  }

  subscribeState = (l: () => void) => {
    this.stateListeners.add(l);
    return () => {
      this.stateListeners.delete(l);
    };
  };

  getState = () => this.state;

  private async startMock() {
    // literal env check so production builds drop the mock module entirely
    if (import.meta.env.VITE_MOCK !== '1') return;
    const { mockTick } = await import('./mock');
    this.setState('open');
    setInterval(() => {
      this.lastMessageAt = Date.now();
      mockTick().forEach((m) => this.emit(m));
    }, 1000);
  }
}

export const wsManager = new WsManager();

/** Connection state of the shared socket. */
export function useWsState(): WsState {
  return useSyncExternalStore(wsManager.subscribeState, wsManager.getState);
}

/** Subscribe to WS messages, optionally filtered by type. Handler may change between renders. */
export function useWsMessages(handler: (msg: WsMessage) => void, types?: WsType[]) {
  const ref = useRef(handler);
  ref.current = handler;
  const key = types?.join(',') ?? '';
  useEffect(() => {
    const filter = key ? new Set(key.split(',')) : null;
    return wsManager.subscribe((m) => {
      if (!filter || filter.has(m.type)) ref.current(m);
    });
  }, [key]);
}
