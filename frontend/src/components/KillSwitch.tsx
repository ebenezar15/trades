// Big red kill switch with confirm dialog: cancel all orders → square off → lock the day.
import { useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../lib/app-context';
import { cls } from '../lib/format';
import { Modal } from './ui';

export function KillSwitch({ className }: { className?: string }) {
  const { toast, reloadStatus, status } = useApp();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const fire = async () => {
    setBusy(true);
    try {
      const r = await api.killSwitch();
      toast(`Kill switch: ${r.cancelled} orders cancelled, ${r.squared_off} positions squared off. Day locked.`, r.ok ? 'warn' : 'down');
      setOpen(false);
      await reloadStatus();
    } catch (e) {
      toast(`Kill switch FAILED: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        className={cls(
          'w-full rounded-xl border-2 border-down bg-down px-4 py-3 text-base font-extrabold uppercase tracking-wider text-white shadow-lg shadow-down/30 transition hover:brightness-110 active:scale-[0.99]',
          className,
        )}
        onClick={() => setOpen(true)}
      >
        ⏻ Kill Switch
      </button>
      {status?.risk.locked && <p className="mt-1 text-center text-xs text-down">Day locked: {status.risk.lock_reason || '—'}</p>}
      <Modal
        open={open}
        onClose={() => !busy && setOpen(false)}
        title="Activate kill switch?"
        footer={
          <>
            <button className="btn" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button className="btn btn-danger" onClick={fire} disabled={busy}>
              {busy ? 'Executing…' : 'Yes — stop everything'}
            </button>
          </>
        }
      >
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li>Cancel all open orders</li>
          <li>Square off all positions with marketable LIMIT orders</li>
          <li>Lock trading for the rest of the day</li>
        </ol>
        <p className="mt-3 text-sm text-muted">This cannot be undone today.</p>
      </Modal>
    </>
  );
}
