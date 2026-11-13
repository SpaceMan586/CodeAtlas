import { StatusBadge } from "../common/StatusBadge";

interface HeaderProps {
  pageTitle: string;
}

export function Header({ pageTitle }: HeaderProps) {
  return (
    <header className="flex min-w-0 flex-wrap items-end justify-between gap-4 border-b border-atlas-line/80 px-4 py-5 sm:px-7 sm:py-6 lg:px-10">
      <div className="min-w-0">
        <p className="text-[10px] font-semibold tracking-[0.18em] text-atlas-muted">
          CODEATLAS <span className="px-1.5 text-atlas-dim">/</span> WORKSPACE
        </p>
        <h1 className="mt-1.5 truncate text-xl font-semibold tracking-tight text-white sm:text-2xl">
          {pageTitle}
        </h1>
      </div>
      <StatusBadge>Foundation in progress</StatusBadge>
    </header>
  );
}
