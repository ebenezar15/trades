// App shell: header (mode badge, market status, connection dot, theme), desktop sidebar, mobile bottom nav,
// demo banner, first-launch disclaimer, toasts.
import { useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { MOCK } from '../api/client';
import { useWsState } from '../api/ws';
import { useApp } from '../lib/app-context';
import { cls, timeIST } from '../lib/format';
import { useTheme } from '../lib/theme';
import { DisclaimerModal } from './Disclaimer';
import { Icon } from './Icon';
import { DataSourceBadge } from './ui';

export const NAV = [
  { to: '/', label: 'Dashboard', icon: 'dashboard' },
  { to: '/signals', label: 'Signals', icon: 'signals' },
  { to: '/agent', label: 'Agent', icon: 'agent' },
  { to: '/option-chain', label: 'Option Chain', icon: 'chain' },
  { to: '/context', label: 'Market Context', icon: 'context' },
  { to: '/news', label: 'News & Events', icon: 'news' },
  { to: '/trades', label: 'Trades & Journal', icon: 'trades' },
  { to: '/analytics', label: 'Analytics', icon: 'analytics' },
  { to: '/strategies', label: 'Strategy Lab', icon: 'lab' },
  { to: '/replay', label: 'Replay', icon: 'replay' },
  { to: '/learn', label: 'Learn', icon: 'learn' },
  { to: '/premarket', label: 'Pre-market', icon: 'premarket' },
  { to: '/eod', label: 'EOD Report', icon: 'eod' },
  { to: '/health', label: 'Health', icon: 'health' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
];
const BOTTOM = ['/', '/signals', '/option-chain', '/trades'];

function Header() {
  const { status, statusError } = useApp();
  const ws = useWsState();
  const [theme, setTheme] = useTheme();
  const live = status?.mode === 'live';
  const backendDown = !!statusError && !status;
  const dot = backendDown ? 'bg-down' : ws === 'open' ? 'bg-up' : ws === 'connecting' ? 'bg-warn animate-pulse' : 'bg-down';
  const dotTitle = backendDown ? `Backend unreachable: ${statusError}` : `Live feed: ${ws}`;
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-panel/95 backdrop-blur" style={{ paddingTop: 'env(safe-area-inset-top)' }}>
      <div className="flex h-12 items-center gap-2 px-3 sm:px-4">
        <NavLink to="/" className="flex shrink-0 items-center gap-2 font-bold">
          <img src="/icons/icon.svg" alt="" className="h-7 w-7" />
          <span className="hidden sm:inline">IndexEdge</span>
        </NavLink>
        {status && (
          <span
            className={cls(
              'rounded-md px-2 py-0.5 text-xs font-extrabold tracking-wider',
              live ? 'bg-down text-white' : 'bg-up/90 text-white dark:text-slate-900',
            )}
            title={live ? 'LIVE trading — real orders' : 'Paper trading — no real orders'}
          >
            {live ? 'LIVE' : 'PAPER'}
          </span>
        )}
        {status && (
          <span className={cls('chip', status.market_open ? 'border-up/40 text-up' : 'text-muted')}>
            <span className={cls('h-1.5 w-1.5 rounded-full', status.market_open ? 'bg-up' : 'bg-muted')} />
            <span className="sm:hidden">{status.market_open ? 'Open' : 'Closed'}</span>
            <span className="hidden sm:inline">{status.market_open ? 'Market open' : 'Closed'}</span>
          </span>
        )}
        {status?.data_source && (
          <Link to={status.data_source === 'angel_one' ? '/agent' : '/connect'} className="min-w-0">
            <DataSourceBadge source={status.data_source} short />
          </Link>
        )}
        {status?.risk.locked && (
          <span className="chip hidden border-down/50 text-down sm:inline-flex" title={status.risk.lock_reason}>
            🔒 Day locked
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {status && <span className="hidden font-mono text-xs text-muted md:inline">{timeIST(status.server_time)} IST</span>}
          <span className="flex items-center gap-1.5 text-xs text-muted" title={dotTitle}>
            <span className={cls('h-2.5 w-2.5 rounded-full', dot)} />
            <span className="hidden sm:inline">{backendDown ? 'Offline' : ws === 'open' ? 'Live' : ws === 'connecting' ? 'Connecting' : 'Disconnected'}</span>
          </span>
          <button
            className="btn btn-ghost px-2"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label="Toggle theme"
            title="Toggle light/dark"
          >
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} className="h-4 w-4" />
          </button>
        </div>
      </div>
      {(status?.demo_mode || MOCK) && (
        <div className="border-t border-warn/30 bg-warn/15 px-3 py-1 text-center text-xs font-medium text-warn">
          {MOCK
            ? 'MOCK MODE — UI running on built-in fake data'
            : status?.data_source === 'yahoo'
              ? 'REAL PRICES via Yahoo Finance (may lag 1–2 min) — broker not connected'
              : 'DEMO MODE — synthetic data, broker not configured'}
          {status?.data_source !== 'angel_one' && (
            <Link to="/connect" className="ml-2 whitespace-nowrap font-bold underline underline-offset-2">
              Connect Angel One →
            </Link>
          )}
        </div>
      )}
      {!status?.demo_mode && !MOCK && status?.data_source === 'synthetic' && (
        <div className="border-t border-warn/30 bg-warn/15 px-3 py-1 text-center text-xs font-medium text-warn">
          Not enough real history downloaded yet — signals are withheld for underlyings without real data
        </div>
      )}
      {status && status.configured_mode === 'live' && status.mode !== 'live' && (
        <div className="border-t border-down/30 bg-down/15 px-3 py-1 text-center text-xs font-medium text-down">
          LIVE mode is configured but Angel One is not connected — orders are going to PAPER
        </div>
      )}
      {status?.safe_mode?.active && (
        <div className="border-t border-down/30 bg-down/15 px-3 py-1 text-center text-xs font-medium text-down">
          SAFE MODE — new entries paused: {status.safe_mode.reason}
        </div>
      )}
      {backendDown && (
        <div className="border-t border-down/30 bg-down/15 px-3 py-1 text-center text-xs font-medium text-down">
          Cannot reach the IndexEdge backend — retrying…
        </div>
      )}
    </header>
  );
}

function Sidebar() {
  return (
    <nav className="sticky top-12 hidden h-[calc(100vh-3rem)] w-52 shrink-0 overflow-y-auto border-r border-line bg-panel/60 p-2 lg:block">
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.to === '/'}
          className={({ isActive }) =>
            cls('flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition', isActive ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-panel2 hover:text-fg')
          }
        >
          <Icon name={n.icon} className="h-4 w-4" />
          {n.label}
        </NavLink>
      ))}
    </nav>
  );
}

function BottomNav() {
  const [more, setMore] = useState(false);
  const loc = useLocation();
  const inMore = !BOTTOM.includes(loc.pathname);
  return (
    <>
      {more && (
        <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setMore(false)}>
          <div className="pb-safe absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-line bg-panel p-3 pb-20" onClick={(e) => e.stopPropagation()}>
            <div className="grid grid-cols-3 gap-2">
              {NAV.filter((n) => !BOTTOM.includes(n.to)).map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  onClick={() => setMore(false)}
                  className={({ isActive }) =>
                    cls('flex flex-col items-center gap-1 rounded-xl border border-line p-2.5 text-center text-xs', isActive ? 'bg-accent/15 text-accent' : 'bg-panel2/60')
                  }
                >
                  <Icon name={n.icon} />
                  {n.label}
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}
      <nav className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/95 backdrop-blur lg:hidden">
        <div className="grid grid-cols-5">
          {NAV.filter((n) => BOTTOM.includes(n.to)).map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === '/'}
              onClick={() => setMore(false)}
              className={({ isActive }) => cls('flex flex-col items-center gap-0.5 py-2 text-[10px] font-medium', isActive && !more ? 'text-accent' : 'text-muted')}
            >
              <Icon name={n.icon} />
              {n.label.split(' ')[0]}
            </NavLink>
          ))}
          <button
            className={cls('flex flex-col items-center gap-0.5 py-2 text-[10px] font-medium', more || inMore ? 'text-accent' : 'text-muted')}
            onClick={() => setMore((m) => !m)}
          >
            <Icon name="more" />
            More
          </button>
        </div>
      </nav>
    </>
  );
}

function Toasts() {
  const { toasts, dismiss } = useApp();
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-3 lg:bottom-4 lg:items-end">
      {toasts.map((t) => (
        <button
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={cls(
            'pointer-events-auto max-w-sm rounded-lg border px-3 py-2 text-left text-sm shadow-lg',
            t.tone === 'down' ? 'border-down/50 bg-panel text-down' : t.tone === 'up' ? 'border-up/50 bg-panel text-up' : t.tone === 'warn' ? 'border-warn/50 bg-panel text-warn' : 'border-line bg-panel',
          )}
        >
          {t.text}
        </button>
      ))}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <Header />
      <div className="flex">
        <Sidebar />
        <main className="min-w-0 flex-1 px-3 pb-24 pt-3 sm:px-4 lg:pb-8">{children}</main>
      </div>
      <BottomNav />
      <Toasts />
      <DisclaimerModal />
    </div>
  );
}
