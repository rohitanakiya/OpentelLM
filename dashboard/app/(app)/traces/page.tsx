import { TracesTable } from "@/components/traces-table";

export default function TracesPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Traces</h1>
        <p className="text-sm text-muted">Every LLM call your app has made. Click a row to inspect.</p>
      </header>
      <TracesTable />
    </div>
  );
}
