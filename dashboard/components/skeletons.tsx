// Skeleton loaders. Every data-fetching component uses one of these
// instead of a spinner — perceived performance is dramatically better
// when the layout doesn't shift on first paint.

export function StatCardSkeleton() {
  return (
    <div className="gradient-border p-5">
      <div className="skeleton h-3 w-24 mb-3" />
      <div className="skeleton h-8 w-32" />
    </div>
  );
}

export function ChartSkeleton({ height = 240 }: { height?: number }) {
  return (
    <div className="gradient-border p-5">
      <div className="skeleton h-4 w-40 mb-4" />
      <div className="skeleton w-full" style={{ height }} />
    </div>
  );
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="gradient-border p-1">
      <div className="space-y-2 p-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="skeleton h-9 w-full" />
        ))}
      </div>
    </div>
  );
}

export function CardListSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="gradient-border p-4">
          <div className="skeleton h-3 w-32 mb-2" />
          <div className="skeleton h-5 w-3/4 mb-2" />
          <div className="skeleton h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}
