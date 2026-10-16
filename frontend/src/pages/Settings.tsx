import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { NewsSource, NotifySettings, RiskSettings, SettingsPatch } from '../api/types';
import { DisclaimerText } from '../components/Disclaimer';
import { Banner, Card, Chip, ErrorBox, Loading, Modal, PageHeader, Segmented, TableWrap, Toggle } from '../components/ui';
import { useApp } from '../lib/app-context';
import { cls } from '../lib/format';
import { useTheme } from '../lib/theme';
import { useApi } from '../lib/useApi';

const CONFIRM_TEXT = 'I UNDERSTAND THE RISK';

const RISK_FIELDS: Array<{ k: keyof RiskSettings; label: string; step?: number; suffix?: string }> = [
  { k: 'capital', label: 'Capital', step: 1000, suffix: '₹' },
  { k: 'risk_per_trade_pct', label: 'Risk per trade', step: 0.1, suffix: '%' },
  { k: 'max_daily_loss_pct', label: 'Max daily loss', step: 0.1, suffix: '%' },
  { k: 'max_trades_per_day', label: 'Max trades / day' },
  { k: 'max_consecutive_losses', label: 'Max consecutive losses' },
  { k: 'max_open_positions', label: 'Max open positions' },
  { k: 'no_trade_first_min', label: 'No trade first N min' },
  { k: 'no_trade_last_min', label: 'No trade last N min' },
  { k: 'cooldown_after_loss_min', label: 'Cooldown after loss (min)' },
  { k: 'revenge_size_multiplier', label: 'Max size after loss (×)', step: 0.1 },
  { k: 'stale_feed_seconds', label: 'Stale feed (s)' },
];

const SECRET_HELP: Record<string, string> = {
  ANGEL_API_KEY: 'SmartAPI app API key',
  ANGEL_CLIENT_CODE: 'Angel One client code',
  ANGEL_PIN: 'Trading PIN',
  ANGEL_TOTP_SECRET: 'TOTP secret (base32) from SmartAPI 2FA setup',
  TELEGRAM_BOT_TOKEN: 'Telegram bot token from @BotFather',
};

function useSaver() {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      toast(ok, 'up');
      return true;
    } catch (e) {
      toast(`Failed: ${e instanceof Error ? e.message : e}`, 'down');
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

function SecretRow({ name, configured, onSaved }: { name: string; configured: boolean; onSaved: () => void }) {
  const [v, setV] = useState('');
  const { busy, run } = useSaver();
  return (
    <div className="grid gap-1 border-b border-line/60 py-2 last:border-0 sm:grid-cols-[14rem_minmax(0,1fr)_auto] sm:items-center sm:gap-2">
      <div>
        <div className="font-mono text-xs">{name}</div>
        <div className="text-[11px] text-muted">{SECRET_HELP[name] ?? ''}</div>
      </div>
      <input
        type="password"
        autoComplete="new-password"
        className="input"
        placeholder={configured ? '•••••••• (configured — enter to replace)' : 'not set'}
        value={v}
        onChange={(e) => setV(e.target.value)}
      />
      <div className="flex items-center gap-2">
        {configured ? <Chip tone="up">✓ configured</Chip> : <Chip tone="warn">missing</Chip>}
        <button
          className="btn"
          disabled={!v || busy}
          onClick={async () => {
            if (await run(() => api.setSecret(name, v), `${name} saved to OS keyring`)) {
              setV('');
              onSaved();
            }
          }}
        >
          Save
        </button>
      </div>
    </div>
  );
}

function PhoneAccess({ url }: { url: string }) {
  const [qr, setQr] = useState('');
  useEffect(() => {
    QRCode.toDataURL(url, { margin: 1, width: 220 }).then(setQr).catch(() => setQr(''));
  }, [url]);
  return (
    <Card title="Phone access (home Wi-Fi)">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {qr && <img src={qr} alt="Pairing QR code" className="h-36 w-36 rounded bg-white p-1" />}
        <div className="min-w-0 flex-1 space-y-1">
          <p>Scan with your phone (same Wi-Fi) or open the link once. Only paired devices can open IndexEdge.</p>
          <code className="block break-all rounded bg-panel2 p-1 text-xs">{url}</code>
          <p className="text-xs text-muted">Keep this link private — it grants full control. Then use “Add to Home screen”.</p>
        </div>
      </div>
    </Card>
  );
}

export default function Settings() {
  const { status, reloadStatus } = useApp();
  const s = useApi(api.settings);
  const [theme, setTheme] = useTheme();
  const saver = useSaver();
  const [risk, setRisk] = useState<RiskSettings | null>(null);
  const [notify, setNotify] = useState<NotifySettings | null>(null);
  const [sources, setSources] = useState<NewsSource[] | null>(null);
  const [liveOpen, setLiveOpen] = useState(false);
  const [confirm, setConfirm] = useState('');

  useEffect(() => {
    if (!s.data) return;
    setRisk(s.data.risk);
    setNotify(s.data.notify);
    setSources(s.data.news.sources);
  }, [s.data]);

  const d = s.data;
  const canEdit = d?.can_edit_limits ?? false;
  const mode = status?.mode ?? d?.execution.mode ?? 'paper';
  const save = (patch: SettingsPatch, msg: string) => saver.run(() => api.updateSettings(patch).then(() => s.reload()), msg);

  const switchMode = async (m: 'paper' | 'live') => {
    if (m === mode) return;
    if (m === 'live') {
      setConfirm('');
      setLiveOpen(true);
      return;
    }
    if (await saver.run(() => api.setMode('paper'), 'Switched to PAPER')) await reloadStatus();
  };

  return (
    <div className="space-y-3">
      <PageHeader title="Settings" />
      {s.error && <ErrorBox error={s.error} onRetry={s.reload} />}
      {!d && s.loading && <Loading />}

      <div className="grid gap-3 lg:grid-cols-2">
        <Card title="Broker (Angel One)">
          {status ? (
            <div className="space-y-2 text-sm">
              <div className="flex flex-wrap gap-1.5">
                <Chip tone={status.broker.connected ? 'up' : 'down'}>{status.broker.connected ? 'Connected' : 'Disconnected'} · {status.broker.name}</Chip>
                <Chip tone={status.broker.logged_in ? 'up' : 'warn'}>{status.broker.logged_in ? 'Logged in today' : 'Not logged in'}</Chip>
                <Chip tone={status.feed.ws === 'connected' ? 'up' : 'warn'}>Feed: {status.feed.ws}</Chip>
              </div>
              <div className={cls('rounded-lg border p-2', status.static_ip.ok ? 'border-up/40 bg-up/5' : status.static_ip.ok === null ? 'border-line' : 'border-down/40 bg-down/10')}>
                <div className="font-medium">{status.static_ip.ok ? '✓ Static IP OK' : status.static_ip.ok === null ? 'Static IP not configured (needed only for live orders)' : '✕ Static IP mismatch — live orders blocked'}</div>
                <div className="font-mono text-xs text-muted">
                  public {status.static_ip.public_ip ?? '?'} · registered {status.static_ip.expected || 'not set'}
                </div>
                <div className="text-xs text-muted">{status.static_ip.message}</div>
              </div>
              <button className="btn btn-primary" disabled={saver.busy} onClick={() => saver.run(() => api.login().then(reloadStatus), 'Broker login triggered')}>
                Login for today
              </button>
              {d && <p className="text-xs text-muted">Auto-login at {d.broker.login_time} IST · intraday cutoff {d.broker.intraday_cutoff}</p>}
            </div>
          ) : (
            <Loading />
          )}
        </Card>

        {d?.phone_url && <PhoneAccess url={d.phone_url} />}

        <Card title="Trading mode">
          <div className="flex flex-wrap items-center gap-3">
            <Segmented
              value={mode}
              onChange={switchMode}
              options={[
                { value: 'paper', label: <span className={mode === 'paper' ? 'text-up' : ''}>PAPER</span> },
                { value: 'live', label: <span className={mode === 'live' ? 'text-down' : ''}>LIVE</span> },
              ]}
            />
            <span className="text-sm text-muted">{mode === 'live' ? 'Real orders are sent to Angel One.' : 'Simulated fills only — no real orders.'}</span>
          </div>
          {mode === 'live' && <div className="mt-2"><Banner tone="down">LIVE mode active. Risk limits and the kill switch are your safety net.</Banner></div>}
          <div className="mt-4">
            <span className="label">Appearance</span>
            <Segmented value={theme} onChange={setTheme} options={[{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }]} />
          </div>
        </Card>
      </div>

      {risk && (
        <Card title="Risk limits" right={!canEdit && <Chip tone="warn">🔒 locked during market hours</Chip>}>
          {!canEdit && <p className="mb-2 text-sm text-muted">Limits can only be changed outside market hours, to prevent in-the-moment loosening.</p>}
          <fieldset disabled={!canEdit} className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {RISK_FIELDS.map((f) => (
              <label key={f.k}>
                <span className="label">
                  {f.label} {f.suffix && <span className="text-muted">({f.suffix})</span>}
                </span>
                <input
                  type="number"
                  step={f.step ?? 1}
                  className="input"
                  value={risk[f.k] as number}
                  onChange={(e) => setRisk({ ...risk, [f.k]: Number(e.target.value) })}
                />
              </label>
            ))}
            {Object.entries(risk.max_lots_per_index).map(([u, v]) => (
              <label key={u}>
                <span className="label">Max lots {u}</span>
                <input
                  type="number"
                  className="input"
                  value={v}
                  onChange={(e) => setRisk({ ...risk, max_lots_per_index: { ...risk.max_lots_per_index, [u]: Number(e.target.value) } })}
                />
              </label>
            ))}
          </fieldset>
          <div className="mt-3">
            <button className="btn btn-primary" disabled={!canEdit || saver.busy} onClick={() => save({ risk }, 'Risk limits saved')}>
              Save risk limits
            </button>
          </div>
        </Card>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {notify && (
          <Card title="Notifications (Telegram)">
            <div className="space-y-2 text-sm">
              {(
                [
                  ['telegram_enabled', 'Enable Telegram'],
                  ['notify_signals', 'Signals (with one-tap confirm)'],
                  ['notify_high_impact_news', 'High-impact news'],
                  ['notify_eod', 'End-of-day report'],
                ] as const
              ).map(([k, label]) => (
                <label key={k} className="flex items-center justify-between gap-2">
                  {label}
                  <Toggle checked={notify[k]} onChange={(v) => setNotify({ ...notify, [k]: v })} label={label} />
                </label>
              ))}
              <label>
                <span className="label">Chat ID</span>
                <input className="input" value={notify.telegram_chat_id} onChange={(e) => setNotify({ ...notify, telegram_chat_id: e.target.value })} />
              </label>
              <button className="btn btn-primary" disabled={saver.busy} onClick={() => save({ notify }, 'Notification settings saved')}>
                Save notifications
              </button>
            </div>
          </Card>
        )}

        {d && (
          <Card title="Secrets">
            <p className="mb-2 text-xs text-muted">Write-only. Stored in the OS keyring; values are never sent back to the browser.</p>
            {Object.entries(d.secrets).map(([name, ok]) => (
              <SecretRow key={name} name={name} configured={ok} onSaved={s.reload} />
            ))}
          </Card>
        )}
      </div>

      {d && sources && (
        <Card title="Data sources">
          <h3 className="mb-1 text-xs font-semibold uppercase text-muted">Context inputs</h3>
          <TableWrap>
            <table className="tbl">
              <thead>
                <tr><th>Input</th><th>Symbol</th><th className="num">Weight</th><th className="num">Max age</th><th>Required</th></tr>
              </thead>
              <tbody>
                {Object.entries(d.context.inputs).map(([k, v]) => (
                  <tr key={k}>
                    <td>{v.label}</td>
                    <td className="font-mono text-xs">{v.symbol}</td>
                    <td className="num">{v.weight}</td>
                    <td className="num">{v.max_age_min}m</td>
                    <td>{v.required ? <Chip tone="accent">required</Chip> : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <h3 className="mb-1 mt-4 text-xs font-semibold uppercase text-muted">News feeds</h3>
          <ul className="space-y-1.5">
            {sources.map((src, i) => (
              <li key={src.url} className="flex items-center justify-between gap-2 text-sm">
                <div className="min-w-0">
                  <div>{src.name}</div>
                  <div className="truncate font-mono text-[11px] text-muted">{src.url}</div>
                </div>
                <Toggle checked={src.enabled} onChange={(v) => setSources(sources.map((x, j) => (j === i ? { ...x, enabled: v } : x)))} label={src.name} />
              </li>
            ))}
          </ul>
          <button className="btn btn-primary mt-3" disabled={saver.busy} onClick={() => save({ news: { sources } }, 'Data sources saved')}>
            Save data sources
          </button>
        </Card>
      )}

      <Card title="Disclaimer">
        <DisclaimerText />
      </Card>

      <Modal
        open={liveOpen}
        onClose={() => setLiveOpen(false)}
        title="Switch to LIVE trading?"
        footer={
          <>
            <button className="btn" onClick={() => setLiveOpen(false)}>Cancel</button>
            <button
              className="btn btn-danger"
              disabled={confirm !== CONFIRM_TEXT || saver.busy}
              onClick={async () => {
                if (await saver.run(() => api.setMode('live', confirm), 'Switched to LIVE')) {
                  setLiveOpen(false);
                  await reloadStatus();
                }
              }}
            >
              Go LIVE
            </button>
          </>
        }
      >
        <p className="text-sm">Real orders will be placed with real money on Angel One. Static IP must match and broker login must be complete.</p>
        <label className="mt-3 block">
          <span className="label">
            Type <b className="font-mono text-down">{CONFIRM_TEXT}</b> to continue
          </span>
          <input className="input font-mono" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
        </label>
      </Modal>
    </div>
  );
}
