type Trend = "up" | "down" | "neutral";

export function StatCard({
  label, value, sublabel, trend,
}: {
  label: string;
  value: string;
  sublabel?: string;
  trend?: Trend;
}) {
  const trendColor =
    trend === "up"   ? "text-success" :
    trend === "down" ? "text-danger" : "text-muted";

  return (
    <div className="gradient-border p-5">
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
      {sublabel ? (
        <div className={`mt-1 text-xs tabular-nums ${trendColor}`}>{sublabel}</div>
      ) : null}
    </div>
  );
}
