// Full-screen Login / Create-password screens shown by the auth gate (Iteration 3).
import { useState, type FormEvent, type ReactNode } from 'react';
import { api, ApiError, MOCK } from '../api/client';
import type { AuthStatus } from '../api/types';
import { cls } from '../lib/format';

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return e.message || 'Too many failed attempts — try again in 15 minutes.';
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

function Shell({ title, subtitle, children }: { title: string; subtitle: ReactNode; children: ReactNode }) {
  return (
    <div className="safe-screen flex min-h-screen items-center justify-center bg-bg p-4">
      <div className="w-full max-w-md">
        <div className="mb-6 flex flex-col items-center text-center">
          <img src="/icons/icon.svg" alt="" className="mb-3 h-16 w-16" />
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="mt-1 text-sm text-muted">{subtitle}</p>
        </div>
        <div className="rounded-2xl border border-line bg-panel p-5 shadow-xl sm:p-6">{children}</div>
        {MOCK && <p className="mt-3 text-center text-xs text-warn">MOCK MODE — user “trader”, password “demo1234”, setup code 123456</p>}
      </div>
    </div>
  );
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="label text-sm">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

function PasswordInput({ value, onChange, autoComplete, show, onToggle, id }: {
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
  show: boolean;
  onToggle?: () => void;
  id?: string;
}) {
  return (
    <div className="relative">
      <input
        id={id}
        type={show ? 'text' : 'password'}
        className="input input-lg pr-20"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        required
      />
      {onToggle && (
        <button
          type="button"
          className="absolute inset-y-0 right-0 flex min-w-[44px] items-center px-3 text-sm font-semibold text-accent"
          onClick={onToggle}
          aria-label={show ? 'Hide password' : 'Show password'}
        >
          {show ? 'Hide' : 'Show'}
        </button>
      )}
    </div>
  );
}

function ErrorLine({ error, locked }: { error: string; locked?: boolean }) {
  if (!error) return null;
  return (
    <div role="alert" className={cls('rounded-lg border p-3 text-sm', locked ? 'border-warn/50 bg-warn/10 text-warn' : 'border-down/40 bg-down/10 text-down')}>
      {locked ? '🔒 ' : '⚠ '}
      {error}
      {locked && <div className="mt-1 text-xs text-muted">This protects your account from guessing. Wait, then try again — or log in on the PC.</div>}
    </div>
  );
}

export function LoginPage({ auth, onDone }: { auth: AuthStatus; onDone: () => void }) {
  const [username, setUsername] = useState(auth.username ?? '');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [locked, setLocked] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.authLogin({ username: username.trim(), password });
      onDone();
    } catch (err) {
      setLocked(err instanceof ApiError && err.status === 429);
      setError(err instanceof ApiError && err.status === 401 ? 'Wrong username or password.' : errText(err));
      setPassword('');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Log in to IndexEdge" subtitle="Your trading engine is running on your PC. Log in to control it from this device.">
      <form className="space-y-4" onSubmit={submit}>
        <Field label="Username">
          <input
            className="input input-lg"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            required
            autoFocus={!username}
          />
        </Field>
        <Field label="Password">
          <PasswordInput value={password} onChange={setPassword} autoComplete="current-password" show={show} onToggle={() => setShow((s) => !s)} />
        </Field>
        <ErrorLine error={error} locked={locked} />
        <button type="submit" className="btn btn-primary h-12 w-full text-base" disabled={busy || !username.trim() || !password}>
          {busy ? 'Logging in…' : 'Log in'}
        </button>
        <p className="text-center text-xs text-muted">Stays logged in for 30 days on this device. Forgot it? Change it in Settings on the PC.</p>
      </form>
    </Shell>
  );
}

export function SetupPage({ auth, onDone }: { auth: AuthStatus; onDone: () => void }) {
  const [username, setUsername] = useState('trader');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [code, setCode] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const tooShort = pw.length > 0 && pw.length < 8;
  const mismatch = pw2.length > 0 && pw !== pw2;
  const needCode = !auth.local;
  const valid = username.trim() && pw.length >= 8 && pw === pw2 && (!needCode || /^\d{6}$/.test(code.trim()));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError('');
    try {
      await api.authSetup({ username: username.trim(), password: pw, ...(needCode ? { setup_code: code.trim() } : {}) });
      onDone();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell
      title="Create your password"
      subtitle="Protect IndexEdge before using it from your iPad or over the internet. You’ll use this to log in on every device except the PC."
    >
      <form className="space-y-4" onSubmit={submit}>
        <Field label="Username">
          <input className="input input-lg" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} required />
        </Field>
        <Field label="Password" hint={tooShort ? <span className="text-down">At least 8 characters</span> : 'At least 8 characters'}>
          <PasswordInput value={pw} onChange={setPw} autoComplete="new-password" show={show} onToggle={() => setShow((s) => !s)} />
        </Field>
        <Field label="Repeat password" hint={mismatch ? <span className="text-down">Passwords don’t match</span> : undefined}>
          <PasswordInput value={pw2} onChange={setPw2} autoComplete="new-password" show={show} />
        </Field>
        {needCode && (
          <Field label="Setup code (shown on your PC)" hint="Open IndexEdge on the PC → Settings → Security, or look at the console window. 6 digits.">
            <input
              className="input input-lg text-center font-mono tracking-[0.4em]"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="••••••"
              required
            />
          </Field>
        )}
        <ErrorLine error={error} />
        <button type="submit" className="btn btn-primary h-12 w-full text-base" disabled={busy || !valid}>
          {busy ? 'Saving…' : 'Create password & continue'}
        </button>
      </form>
    </Shell>
  );
}

export function AuthScreen({ auth, onDone }: { auth: AuthStatus; onDone: () => void }) {
  return auth.needs_setup ? <SetupPage auth={auth} onDone={onDone} /> : <LoginPage auth={auth} onDone={onDone} />;
}
