import { createRoot } from "react-dom/client";
import { AuthProvider } from "../../frontend/src/auth/AuthContext";
import { AppLayout } from "../../frontend/src/layouts/AppLayout";
import { EvaluationsPage } from "../../frontend/src/extensions/evaluation/EvaluationsPage";
import { RunPage } from "../../frontend/src/extensions/evaluation/RunPage";
import { PlaygroundPage } from "../../frontend/src/extensions/playground/PlaygroundPage";

// Add a page here to test it: the query string picks it, for example /?page=run&runId=...
const pages: Record<string, () => unknown> = {
  playground: () => <PlaygroundPage />,
  evaluations: () => <EvaluationsPage />,
  run: () => <RunPage />,
};

const params = new URLSearchParams(window.location.search);
const choose = pages[params.get("page") ?? "playground"] ?? pages.playground;
window.__page = choose();
window.__params = Object.fromEntries(params.entries());

createRoot(document.getElementById("root")!).render(
  <AuthProvider>
    <AppLayout />
  </AuthProvider>,
);
