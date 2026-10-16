// Auth gate (Iteration 3): on start, GET /api/auth/status. Logged out → full-screen Login (or Create password when
// no password exists yet). Any 401 from the API client re-checks status, so an expired session lands on Login.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, setUnauthorizedHandler } from '../api/client';
import type { AuthStatus } from '../api/types';
import { wsManager } from '../api/ws';
import { AuthScreen } from '../pages/Login';

interface AuthCtx {
  auth: AuthStatus;
  recheck: () => Promise<AuthStatus | null>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

/** Backends from before Iteration 3 have no /api/auth/status — treat them as open (local) installs. */
const LEGACY: AuthStatus = { logged_in: true, local: true, needs_setup: false, username: null, remote_url: null };

export function AuthGate({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<Promise<AuthStatus | null> | null>(null);

  const recheck = useCallback(() => {
    inflight.current ??= api
      .authStatus()
      .then((a) => {
        setAuth(a);
        setError(null);
        return a;
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) {
          setAuth(LEGACY);
          setError(null);
          return LEGACY;
        }
        setError(e instanceof Error ? e.message : String(e));
        return null;
      })
      .finally(() => {
        inflight.current = null;
      });
    return inflight.current;
  }, []);

  useEffect(() => {
    void recheck();
    setUnauthorizedHandler(() => void recheck());
    return () => setUnauthorizedHandler(null);
  }, [recheck]);

  // backend unreachable on first load: keep retrying quietly
  useEffect(() => {
    if (!error || auth) return;
    const id = setInterval(() => void recheck(), 5000);
    return () => clearInterval(id);
  }, [error, auth, recheck]);

  const loggedIn = !!auth?.logged_in;
  useEffect(() => {
    if (!loggedIn) return;
    wsManager.start();
    wsManager.reconnectNow();
  }, [loggedIn]);

  const logout = useCallback(async () => {
    try {
      await api.authLogout();
    } finally {
      await recheck();
    }
  }, [recheck]);

  const value = useMemo(() => (auth ? { auth, recheck, logout } : null), [auth, recheck, logout]);

  if (!auth) return <Splash error={error} onRetry={() => void recheck()} />;
  if (!auth.logged_in) return <AuthScreen auth={auth} onDone={() => void recheck()} />;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function Splash({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <div className="safe-screen flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
      <img src="/icons/icon.svg" alt="" className="h-16 w-16" />
      {error ? (
        <>
          <h1 className="text-xl font-bold">Can’t reach IndexEdge</h1>
          <p className="max-w-sm text-sm text-muted">
            Make sure the IndexEdge engine is running on your PC and this device is online. If you use the remote link, it
            changes every time the PC restarts — check Telegram for the new one.
          </p>
          <p className="font-mono text-xs text-down">{error}</p>
          <button className="btn btn-primary h-11 px-6" onClick={onRetry}>
            Retry
          </button>
        </>
      ) : (
        <span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-transparent" aria-label="Loading" />
      )}
    </div>
  );
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth outside AuthGate');
  return c;
}
