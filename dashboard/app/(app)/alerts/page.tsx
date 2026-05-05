import { AlertFeed } from "@/components/alert-feed";

export default function AlertsPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
        <p className="text-sm text-muted">
          Cost spikes and prompt regressions, detected in real time. Refreshes every 10 seconds.
        </p>
      </header>
      <AlertFeed />
    </div>
  );
}
