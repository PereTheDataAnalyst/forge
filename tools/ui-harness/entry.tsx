import { createRoot } from "react-dom/client";
import { AuthProvider } from "../../frontend/src/auth/AuthContext";
import { AppLayout } from "../../frontend/src/layouts/AppLayout";
import { PublicLayout } from "../../frontend/src/layouts/PublicLayout";
import { EvaluationsPage } from "../../frontend/src/extensions/evaluation/EvaluationsPage";
import { RunPage } from "../../frontend/src/extensions/evaluation/RunPage";
import { PlaygroundPage } from "../../frontend/src/extensions/playground/PlaygroundPage";
import { DashboardPage } from "../../frontend/src/pages/Dashboard";
import { DocsPage } from "../../frontend/src/pages/Docs";
import { LandingPage } from "../../frontend/src/pages/Landing";

// Add a page here to test it: the query string picks it, for example /?page=run&runId=...
// Public pages render inside the public shell: add &layout=public (the landing page does).
const pages: Record<string, () => unknown> = {
  playground: () => <PlaygroundPage />,
  evaluations: () => <EvaluationsPage />,
  run: () => <RunPage />,
  dashboard: () => <DashboardPage />,
  docs: () => <DocsPage />,
  landing: () => <LandingPage />,
};

const params = new URLSearchParams(window.location.search);
const choose = pages[params.get("page") ?? "playground"] ?? pages.playground;
window.__page = choose();
window.__params = Object.fromEntries(params.entries());
const Layout = params.get("layout") === "public" ? PublicLayout : AppLayout;

createRoot(document.getElementById("root")!).render(
  <AuthProvider>
    <Layout />
  </AuthProvider>,
);
