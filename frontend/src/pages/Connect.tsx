// Stand-alone page for the "Connect Angel One" wizard (linked from the demo-mode banner).
import { ConnectWizard } from '../components/ConnectWizard';
import { PageHeader } from '../components/ui';

export default function Connect() {
  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <PageHeader title="Connect Angel One" subtitle="Switch IndexEdge from demo / delayed data to Angel One real-time data" />
      <ConnectWizard />
    </div>
  );
}
