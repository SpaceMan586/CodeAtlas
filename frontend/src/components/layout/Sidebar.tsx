type NavigationItem = {
  label: string;
  active?: boolean;
};

const navigationItems: NavigationItem[] = [
  { label: "Overview", active: true },
  { label: "Project Explorer" },
  { label: "Graph Explorer" },
  { label: "Function Search" },
  { label: "Function Trace" },
  { label: "Settings" },
];

export function Sidebar() {
  return (
    <aside className="w-full shrink-0 border-b border-atlas-line bg-atlas-sidebar px-4 py-4 sm:px-6 md:sticky md:top-0 md:h-screen md:w-64 md:border-b-0 md:border-r md:px-5 md:py-6">
      <div className="flex items-center gap-3">
        <div
          aria-hidden="true"
          className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-atlas-accent/25 bg-atlas-accent/10 text-sm font-bold tracking-tight text-atlas-accent"
        >
          CA
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-wide text-white">
            CodeAtlas
          </p>
          <p className="truncate text-xs text-atlas-muted">
            Software architecture explorer
          </p>
        </div>
      </div>

      <div className="mt-6 md:mt-10">
        <p className="mb-3 hidden px-3 text-[10px] font-semibold tracking-[0.18em] text-atlas-dim md:block">
          WORKSPACE
        </p>
        <nav aria-label="Primary navigation">
          <ul className="grid grid-cols-2 gap-1.5 md:flex md:flex-col">
            {navigationItems.map((item) => (
              <li key={item.label} className="min-w-0">
                {item.active ? (
                  <a
                    aria-current="page"
                    className="flex min-h-10 min-w-0 items-center gap-2.5 rounded-md border border-atlas-accent/15 bg-atlas-accent/10 px-3 py-2 text-sm font-medium text-atlas-accent"
                    href="#overview"
                  >
                    <span
                      aria-hidden="true"
                      className="size-1.5 shrink-0 rounded-full bg-atlas-accent"
                    />
                    <span className="truncate">{item.label}</span>
                  </a>
                ) : (
                  <span
                    aria-disabled="true"
                    className="flex min-h-10 min-w-0 items-center justify-between gap-2 rounded-md px-3 py-2 text-sm text-atlas-dim"
                  >
                    <span className="truncate">{item.label}</span>
                    <span className="hidden shrink-0 text-[10px] text-atlas-dim/80 sm:inline">
                      Planned
                    </span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </aside>
  );
}
