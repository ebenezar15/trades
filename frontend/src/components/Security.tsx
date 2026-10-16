// Settings cards for Iteration 3: Security (logout / password) and free Remote access (Cloudflare quick tunnel).
import QRCode from 'qrcode';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { useAuth } from '../lib/auth';
import { cls } from '../lib/format';
import { useApi } from '../lib/useApi';
import { CopyButton, useSaver } from './ConnectWizard';
import { Card, Chip, ErrorBox, Loading, Toggle } from './ui';

function PwField({ label, value, onChange, autoComplete, show }: { label: string; value: string; onChange: (v: string) => void; autoComplete: string; show: boolean }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input
        type={show ? 'text' : 'password'}
        className="input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
      />
    </label>
  );
}

export function SecurityCard({ setupCode, onChanged }: { setupCode?: string | null; onChanged?: () => void }) {
  const { auth, recheck, logout } = useAuth();
  const saver = useSaver();
  const [username, setUsername] = useState('trader');
  const [cur, setCur] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [show, setShow] = useState(false);
  const setup = auth.needs_setup;
  const valid = pw.length >= 8 && pw === pw2 && (setup ? !!username.trim() : !!cur || auth.local);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    const ok = await saver.run(
      () => (setup ? api.authSetup({ username: username.trim(), password: pw }) : api.changePassword({ current: cur, new: pw })),
      setup ? 'Password created — you can now log in from other devices' : 'Password changed',
    );
    if (ok) {
      setCur('');
      setPw('');
      setPw2('');
      await recheck();
      onChanged?.();
    }
  };
  return (
    <Card title="Security">
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          {auth.local ? <Chip tone="accent">🖥 This PC — no login needed here</Chip> : <Chip tone="up">✓ Logged in{auth.username ? ` as ${auth.username}` : ''}</Chip>}
          {setup ? <Chip tone="warn">No password yet</Chip> : <Chip>Password set{auth.local && auth.username ? ` · user ${auth.username}` : ''}</Chip>}
          {!(auth.local && setup) && (
            <button type="button" className="btn ml-auto" disabled={saver.busy} onClick={() => void logout()}>
              ⎋ Log out
            </button>
          )}
        </div>
        {setup && (
          <div className="rounded-lg border border-warn/40 bg-warn/10 p-2.5">
            <p>Create a password to use IndexEdge from your iPad or phone, and to turn on remote access.</p>
            {setupCode && (
              <p className="mt-1">
                Setting it up from the iPad instead? Enter this setup code there:{' '}
                <b className="select-all font-mono text-lg tracking-[0.3em] text-fg">{setupCode}</b>
              </p>
            )}
          </div>
        )}
        <form onSubmit={submit} className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{setup ? 'Create password' : 'Change password'}</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {setup ? (
              <label className="block sm:col-span-2">
                <span className="label">Username</span>
                <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" />
              </label>
            ) : (
              <div className="sm:col-span-2">
                <PwField label={auth.local ? "Current password (not needed on this PC — use it to reset a forgotten one)" : "Current password"} value={cur} onChange={setCur} autoComplete="current-password" show={show} />
              </div>
            )}
            <PwField label="New password (8+ characters)" value={pw} onChange={setPw} autoComplete="new-password" show={show} />
            <PwField label="Repeat new password" value={pw2} onChange={setPw2} autoComplete="new-password" show={show} />
          </div>
          {pw2 && pw !== pw2 && <p className="text-xs text-down">Passwords don’t match</p>}
          {pw && pw.length < 8 && <p className="text-xs text-down">At least 8 characters</p>}
          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" className="btn btn-primary" disabled={!valid || saver.busy}>
              {setup ? 'Create password' : 'Change password'}
            </button>
            <label className="flex min-h-[44px] items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> Show passwords
            </label>
          </div>
        </form>
      </div>
    </Card>
  );
}

function Qr({ url }: { url: string }) {
  const [qr, setQr] = useState('');
  useEffect(() => {
    QRCode.toDataURL(url, { margin: 1, width: 240 }).then(setQr).catch(() => setQr(''));
  }, [url]);
  if (!qr) return null;
  return <img src={qr} alt="QR code for the remote link" className="h-40 w-40 shrink-0 rounded-lg bg-white p-1.5" />;
}

const STATUS_CHIP = {
  off: { tone: 'neutral', label: 'Off' },
  starting: { tone: 'warn', label: 'Starting…' },
  on: { tone: 'up', label: '● Online' },
  error: { tone: 'down', label: 'Error' },
} as const;

export function RemoteAccessCard({ reloadKey = 0 }: { reloadKey?: number }) {
  const r = useApi(api.remote, [reloadKey], 4000);
  const saver = useSaver();
  const d = r.data;
  // poll faster while the tunnel is coming up
  const reload = r.reload;
  useEffect(() => {
    if (d?.status !== 'starting') return;
    const id = setInterval(() => void reload(), 1200);
    return () => clearInterval(id);
  }, [d?.status, reload]);
  const toggle = async (on: boolean) => {
    if (await saver.run(() => api.setRemote(on), on ? 'Starting remote access…' : 'Remote access turned off')) await r.reload();
  };
  const chip = d ? STATUS_CHIP[d.status] ?? STATUS_CHIP.off : null;
  return (
    <Card title="Use IndexEdge from anywhere — free" right={chip && <Chip tone={chip.tone}>{chip.label}</Chip>}>
      {!d && r.loading && <Loading />}
      {r.error && !d && <ErrorBox error={r.error} onRetry={r.reload} />}
      {d && (
        <div className="space-y-3 text-sm">
          <p className="text-muted">
            Opens a secure <b className="text-fg">https</b> link to the engine on your PC (Cloudflare quick tunnel — no account, no static IP, no
            router setup). Anyone with the link still needs your password.
          </p>
          <label className={cls('flex min-h-[44px] items-center justify-between gap-3 rounded-lg border p-3', d.enabled ? 'border-up/40 bg-up/5' : 'border-line')}>
            <span>
              <span className="block font-semibold">Remote access</span>
              <span className="block text-xs text-muted">{d.message}</span>
            </span>
            <Toggle checked={d.enabled} disabled={!d.password_set || saver.busy} onChange={toggle} label="Remote access" />
          </label>
          {!d.password_set && (
            <div className="rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-warn">
              🔒 Create a password first (Security card) — the remote link is reachable from the internet, so a login is required.
            </div>
          )}
          {d.status === 'error' && <div className="rounded-lg border border-down/40 bg-down/10 p-2.5 text-down">⚠ {d.message || 'The tunnel failed to start.'}</div>}
          {d.status === 'starting' && (
            <p className="text-xs text-muted">First start downloads <code className="font-mono">cloudflared</code> (~30 MB) — this can take a minute.</p>
          )}
          {d.url && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-panel2/40 p-3">
              <Qr url={d.url} />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted">Your link</div>
                <a href={d.url} target="_blank" rel="noreferrer" className="block break-all font-mono text-base font-semibold text-accent underline underline-offset-2">
                  {d.url}
                </a>
                <div className="flex flex-wrap gap-2">
                  <CopyButton text={d.url} className="h-11 px-4 text-sm" />
                </div>
                <p className="text-xs text-muted">
                  Scan with the iPad camera, log in, then Share → <b>Add to Home Screen</b>.
                </p>
              </div>
            </div>
          )}
          <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
            <li>
              The link <b className="text-fg">changes every time the PC (or IndexEdge) restarts</b>. The new link is sent to Telegram automatically.
            </li>
            <li>The PC must stay on and awake for the iPad to reach it.</li>
          </ul>
        </div>
      )}
    </Card>
  );
}
