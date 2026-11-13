import { AppShell } from "./AppShell";
import { OverviewPage } from "../features/dashboard/OverviewPage";

export default function App() {
  return (
    <AppShell pageTitle="Project Analysis" wide>
      <OverviewPage />
    </AppShell>
  );
}
