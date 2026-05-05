// Number, currency, and date formatters. Centralised so every page
// renders values identically — no "$0.00" in one place and "$0.0000"
// in another.

const _cost = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

const _smallCost = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 6,
  maximumFractionDigits: 6,
});

const _int = new Intl.NumberFormat("en-US");
const _pct = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 2 });

export function fmtCost(usd: number): string {
  if (!Number.isFinite(usd)) return "—";
  if (usd > 0 && usd < 0.0001) return _smallCost.format(usd);
  return _cost.format(usd);
}

export function fmtTokens(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return _int.format(Math.round(n));
}

export function fmtMs(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  return `${Math.round(ms)}ms`;
}

export function fmtPct(ratio: number): string {
  if (!Number.isFinite(ratio)) return "—";
  return _pct.format(ratio);
}

/** Local-time date in the user's locale. */
export function fmtDate(iso: string | number | Date): string {
  const d = typeof iso === "object" ? iso : new Date(iso);
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(d);
}

/** "5m ago" style relative time, used in alert cards. */
export function fmtRelative(iso: string | number | Date): string {
  const d = typeof iso === "object" ? iso : new Date(iso);
  const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diffSec < 60)   return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86_400)}d ago`;
}
