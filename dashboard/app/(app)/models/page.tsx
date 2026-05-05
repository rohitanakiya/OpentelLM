import { ModelComparison } from "@/components/model-comparison";

export default function ModelsPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Models</h1>
        <p className="text-sm text-muted">Compare cost, latency, and error rates across models.</p>
      </header>
      <ModelComparison />
    </div>
  );
}
