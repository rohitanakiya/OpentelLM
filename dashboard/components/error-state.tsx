"use client";
import { AlertCircle, RotateCcw } from "lucide-react";

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="gradient-border p-6 flex items-start gap-4">
      <AlertCircle className="text-danger shrink-0 mt-0.5" size={20} />
      <div className="flex-1">
        <div className="font-medium">Something went wrong</div>
        <div className="text-sm text-muted mt-1">{message}</div>
      </div>
      {onRetry ? (
        <button
          onClick={onRetry}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border border-border hover:bg-surface2 transition-colors"
        >
          <RotateCcw size={14} /> Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  icon: Icon, title, description,
}: {
  icon: React.ElementType;
  title: string;
  description: string;
}) {
  return (
    <div className="gradient-border p-10 text-center">
      <Icon size={32} className="text-muted mx-auto mb-3" />
      <div className="font-medium">{title}</div>
      <div className="text-sm text-muted mt-1">{description}</div>
    </div>
  );
}
