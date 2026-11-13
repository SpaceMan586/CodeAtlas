import type { ReactNode } from "react";

interface StatusBadgeProps {
  children: ReactNode;
}

export function StatusBadge({ children }: StatusBadgeProps) {
  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-md border border-atlas-line bg-atlas-surface px-2.5 py-1.5 text-xs font-medium text-atlas-muted">
      <span
        aria-hidden="true"
        className="size-1.5 shrink-0 rounded-full bg-atlas-accent"
      />
      <span className="truncate">{children}</span>
    </span>
  );
}
