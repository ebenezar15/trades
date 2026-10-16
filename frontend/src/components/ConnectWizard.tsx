// "Connect Angel One" wizard: walks the user through creating a SmartAPI app, saving the four secrets,
// testing the connection and hot-switching the data feed to Angel One real-time data.
import { useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { BrokerConnectResponse, BrokerTestResponse, BrokerTestStep } from '../api/types';
import { useApp } from '../lib/app-context';
import { cls } from '../lib/format';
import { useApi } from '../lib/useApi';
import { Card, Chip, DataSourceBadge, ErrorBox, Loading, Spinner } from './ui';

export const SECRET_HELP: Record<string, string> = {
  ANGEL_API_KEY: 'SmartAPI app API key',
  ANGEL_CLIENT_CODE: 'Angel One client code',
  ANGEL_PIN: 'Trading PIN',
  ANGEL_TOTP_SECRET: 'TOTP secret (base32) from SmartAPI 2FA setup',
  TELEGRAM_BOT_TOKEN: 'Telegram bot token from @BotFather',
};
const ANGEL_SECRETS = ['ANGEL_API_KEY', 'ANGEL_CLIENT_CODE', 'ANGEL_PIN', 'ANGEL_TOTP_SECRET'];
const SMARTAPI_URL = 'https://smartapi.angelone.in/';

export function useSaver() {
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

/** Copy text to the clipboard; falls back to a hidden textarea + execCommand on plain-http origins. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle');
  return (
    <button
      type="button"
      className={cls('btn shrink-0 px-2 py-1 text-xs', state === 'ok' && 'border-up/50 text-up', state === 'fail' && 'border-down/50 text-down', className)}
      onClick={async () => {
        const ok = await copyText(text);
        setState(ok ? 'ok' : 'fail');
        setTimeout(() => setState('idle'), 1500);
      }}
      aria-label={`Copy ${text}`}
    >
      {state === 'ok' ? '✓ Copied' : state === 'fail' ? 'Select & copy' : 'Copy'}
    </button>
  );
}

export function SecretRow({ name, configured, onSaved }: { name: string; configured: boolean; onSaved: () => void }) {
  const [v, setV] = useState('');
  const { busy, run } = useSaver();
  return (
    <div className="grid gap-1 border-b border-line/60 py-2 last:border-0 sm:grid-cols-[14rem_minmax(0,1fr)_auto] sm:items-center sm:gap-2">
      <div className="min-w-0">
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
        aria-label={name}
      />
      <div className="flex items-center gap-2">
        {configured ? <Chip tone="up">✓ configured</Chip> : <Chip tone="warn">missing</Chip>}
        <button
          className="btn"
          disabled={!v || busy}
          onClick={async () => {
            if (await run(() => api.setSecret(name, v.trim()), `${name} saved to OS keyring`)) {
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

function Step({ n, title, done, children }: { n: number; title: ReactNode; done?: boolean; children: ReactNode }) {
  return (
    <li className="relative flex gap-3 pb-5 last:pb-0">
      <span
        className={cls(
          'z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm font-bold',
          done ? 'border-up bg-up text-white dark:text-slate-900' : 'border-accent/60 bg-accent/10 text-accent',
        )}
      >
        {done ? '✓' : n}
      </span>
      <span className="absolute bottom-0 left-[13px] top-7 w-px bg-line" aria-hidden />
      <div className="min-w-0 flex-1 pt-0.5">
        <h3 className="mb-1.5 font-semibold">{title}</h3>
        <div className="space-y-2 text-sm">{children}</div>
      </div>
    </li>
  );
}

function StepResults({ steps }: { steps: BrokerTestStep[] }) {
  return (
    <ul className="divide-y divide-line/60 rounded-lg border border-line">
      {steps.map((s, i) => (
        <li key={`${s.name}-${i}`} className="flex items-start gap-2 px-2.5 py-1.5">
          <span className={cls('mt-0.5 w-4 shrink-0 text-center font-bold', s.ok ? 'text-up' : 'text-down')}>{s.ok ? '✓' : '✕'}</span>
          <div className="min-w-0">
            <div className="font-medium">{s.name}</div>
            {s.detail && <div className="break-words text-xs text-muted">{s.detail}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function ConnectWizard({ compact }: { compact?: boolean }) {
  const { reloadStatus, toast } = useApp();
  const setup = useApi(api.brokerSetup);
  const [test, setTest] = useState<BrokerTestResponse | null>(null);
  const [testErr, setTestErr] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [conn, setConn] = useState<BrokerConnectResponse | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [open, setOpen] = useState(!compact);

  const d = setup.data;
  const stepDone = (id: string) => d?.steps.find((s) => s.id === id)?.done;
  const secretsDone = ANGEL_SECRETS.every((k) => d?.secrets[k]);
  const secretNames = d ? [...ANGEL_SECRETS.filter((k) => k in d.secrets), ...Object.keys(d.secrets).filter((k) => !ANGEL_SECRETS.includes(k))] : [];

  const runTest = async () => {
    setTesting(true);
    setTestErr(null);
    try {
      setTest(await api.brokerTest());
    } catch (e) {
      setTestErr(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };
  const connect = async () => {
    setConnecting(true);
    try {
      const r = await api.brokerConnect();
      setConn(r);
      toast(r.message || (r.ok ? 'Connected to Angel One' : 'Connection failed'), r.ok ? 'up' : 'down');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setConn({ ok: false, message: msg });
      toast(`Connect failed: ${msg}`, 'down');
    } finally {
      setConnecting(false);
      await Promise.all([setup.reload(), reloadStatus()]);
    }
  };

  return (
    <Card
      title="Connect Angel One"
      right={
        <>
          {d && <DataSourceBadge source={d.data_source} />}
          {compact && (
            <button className="text-xs text-accent" onClick={() => setOpen((o) => !o)}>
              {open ? 'Hide steps' : 'Show steps'}
            </button>
          )}
        </>
      }
    >
      {setup.error && !d && <ErrorBox error={setup.error} onRetry={setup.reload} />}
      {!d && setup.loading && <Loading />}
      {d && (
        <div className="space-y-3">
          {d.connected ? (
            <div className="rounded-lg border border-up/40 bg-up/10 p-2.5 text-sm text-up">✓ Connected — IndexEdge is using Angel One real-time data.</div>
          ) : (
            <p className="text-sm text-muted">
              Link your Angel One account to get real-time NIFTY / BANKNIFTY / SENSEX data (instead of delayed Yahoo or synthetic demo data). Takes about 5 minutes.
            </p>
          )}
          {d.steps.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {d.steps.map((s) => (
                <Chip key={s.id} tone={s.done ? 'up' : 'neutral'}>
                  {s.done ? '✓' : '○'} {s.title}
                </Chip>
              ))}
            </div>
          )}

          {open && (
            <ol className="pt-1">
              <Step n={1} title="Create a SmartAPI app" done={stepDone('create_app')}>
                <p>
                  Open{' '}
                  <a href={SMARTAPI_URL} target="_blank" rel="noreferrer" className="font-medium text-accent underline">
                    smartapi.angelone.in
                  </a>{' '}
                  and sign in with your Angel One client ID. Go to <b>My Apps → Create an App</b> and choose the type <b>“Trading APIs”</b>.
                </p>
              </Step>

              <Step n={2} title="Fill the Create App form exactly like this">
                <div className="divide-y divide-line/60 rounded-lg border border-line">
                  {d.smartapi_fields.map((f) => (
                    <div key={f.field} className="grid gap-1 px-2.5 py-2 sm:grid-cols-[10rem_minmax(0,1fr)_auto] sm:items-center sm:gap-3">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">{f.field}</div>
                      <div className="min-w-0">
                        {f.value ? (
                          <code className="block break-all rounded bg-panel2 px-1.5 py-0.5 font-mono text-[13px]">{f.value}</code>
                        ) : (
                          <span className="italic text-muted">leave blank</span>
                        )}
                        {f.note && <div className="mt-0.5 text-[11px] text-muted">{f.note}</div>}
                      </div>
                      <div className="sm:justify-self-end">{f.value && <CopyButton text={f.value} />}</div>
                    </div>
                  ))}
                </div>
                <div className="rounded-lg border border-accent/40 bg-accent/10 p-2.5 text-[13px]">
                  <b>Why that Redirect URL?</b> Angel One requires an https Redirect URL — localhost and IP addresses are rejected. IndexEdge logs in with
                  PIN+TOTP and never uses this URL, so <code className="font-mono">https://smartapi.angelone.in/</code> is fine.
                </div>
                <p className="text-xs text-muted">After you create the app, Angel One shows an <b>API Key</b> — you will paste it in step 4.</p>
              </Step>

              <Step n={3} title="Enable TOTP and copy the text key" done={stepDone('totp')}>
                <p>
                  On SmartAPI open <b>Enable TOTP</b>, sign in with your client ID, PIN and the OTP sent to you. Angel One shows a QR code and a{' '}
                  <b>text key</b> (letters and digits, e.g. <code className="font-mono">JBSWY3DPEHPK3PXP</code>). Copy that text key — it is your{' '}
                  <code className="font-mono">ANGEL_TOTP_SECRET</code>. You can also scan the QR with an authenticator app for manual logins.
                </p>
              </Step>

              <Step n={4} title="Save your four secrets" done={secretsDone}>
                <p className="text-xs text-muted">Write-only — stored in the OS keyring on this PC; values are never sent back to the browser.</p>
                <div>
                  {secretNames.map((name) => (
                    <SecretRow key={name} name={name} configured={!!d.secrets[name]} onSaved={setup.reload} />
                  ))}
                </div>
              </Step>

              <Step n={5} title="Test the connection" done={test?.ok}>
                <p className="text-xs text-muted">Checks secrets, TOTP clock, login, live prices, candles, funds and static IP. Never places orders.</p>
                <button className="btn btn-primary" disabled={testing} onClick={runTest}>
                  {testing && <Spinner className="border-white border-t-transparent" />} Test connection
                </button>
                {testErr && <ErrorBox error={testErr} />}
                {test && (
                  <>
                    <div className={cls('text-sm font-semibold', test.ok ? 'text-up' : 'text-down')}>
                      {test.ok ? '✓ All checks passed' : '✕ Some checks failed — fix them and test again'}
                    </div>
                    <StepResults steps={test.steps} />
                  </>
                )}
              </Step>

              <Step n={6} title="Connect now" done={d.connected}>
                <p className="text-xs text-muted">Switches the data feed from demo / Yahoo to Angel One real-time data — no restart needed.</p>
                <button className="btn btn-primary" disabled={connecting || d.connected} onClick={connect}>
                  {connecting && <Spinner className="border-white border-t-transparent" />} {d.connected ? 'Connected' : 'Connect now'}
                </button>
                {conn && (
                  <>
                    <div className={cls('text-sm font-medium', conn.ok ? 'text-up' : 'text-down')}>{conn.message}</div>
                    {conn.steps && conn.steps.length > 0 && !conn.ok && <StepResults steps={conn.steps} />}
                  </>
                )}
              </Step>
            </ol>
          )}

          <div className="rounded-lg border border-line bg-panel2/50 p-2.5 text-xs text-muted">
            <b className="text-fg">Static IP:</b> real-time market data works without a static IP; only <b>live orders</b> need the registered static IP.
            {d.public_ip && (
              <>
                {' '}
                Your public IP now: <code className="font-mono text-fg">{d.public_ip}</code>
                {d.static_ip ? (
                  <>
                    {' '}
                    · registered: <code className="font-mono text-fg">{d.static_ip}</code>
                  </>
                ) : null}
                .
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
