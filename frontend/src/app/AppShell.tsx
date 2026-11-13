import type { ReactNode } from "react";
import { Header } from "../components/layout/Header";
import { Sidebar } from "../components/layout/Sidebar";

interface AppShellProps {
  children: ReactNode;
  pageTitle: string;
  wide?: boolean;
}

export function AppShell({ children, pageTitle, wide = false }: AppShellProps) {
  return (
    <div className="min-h-screen bg-atlas-bg text-atlas-copy">
      <a
        className="sr-only z-50 rounded bg-atlas-accent px-4 py-2 text-sm font-semibold text-atlas-bg focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
        href="#main-content"
      >
        Skip to content
      </a>
      <div className="mx-auto flex min-h-screen max-w-[1680px] flex-col md:flex-row">
        <Sidebar />
        <div className="min-w-0 flex-1">
          <Header pageTitle={pageTitle} />
          <main
            className="min-w-0 px-4 py-7 sm:px-7 sm:py-9 lg:px-10"
            id="main-content"
            tabIndex={-1}
          >
            <div className={`mx-auto w-full ${wide ? "max-w-[1500px]" : "max-w-5xl"}`}>{children}</div>
          </main>
        </div>
      </div>
    </div>
  );
}
