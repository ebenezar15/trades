// First-launch disclaimer (spec §14 / brief §7). Shown until the backend records acceptance.
import { useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../lib/app-context';
import { Modal } from './ui';

export function DisclaimerText() {
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed">
      <li>
        Signals are <b>probabilistic</b>. A displayed probability is an estimate measured from past data, with a confidence
        interval — never a guarantee.
      </li>
      <li>
        <b>Past performance does not guarantee future results.</b>
      </li>
      <li>
        Trading futures &amp; options (F&amp;O) carries a <b>high risk of loss</b>; most retail F&amp;O traders lose money.
      </li>
      <li>
        <b>You are solely responsible for all trades</b> placed with or without this software. IndexEdge is not investment
        advice.
      </li>
    </ul>
  );
}

export function DisclaimerModal() {
  const { status, reloadStatus, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const open = !!status && !status.disclaimer_accepted;
  const accept = async () => {
    setBusy(true);
    try {
      await api.acceptDisclaimer();
      await reloadStatus();
    } catch (e) {
      toast(`Could not record acceptance: ${e instanceof Error ? e.message : e}`, 'down');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={() => undefined}
      dismissable={false}
      title="⚠ Before you continue"
      footer={
        <button className="btn btn-primary" disabled={!checked || busy} onClick={accept}>
          {busy ? 'Saving…' : 'I understand and accept'}
        </button>
      }
    >
      <DisclaimerText />
      <label className="mt-4 flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        <span>I have read and accept these terms.</span>
      </label>
    </Modal>
  );
}
