import type { FeatureModule } from "../registry";
import { EvaluationsPage } from "./EvaluationsPage";
import { RunPage } from "./RunPage";

const module: FeatureModule = {
  order: 20,
  navItems: [{ to: "/app/evaluations", label: "Evaluations" }],
  routes: [
    { path: "evaluations", element: <EvaluationsPage /> },
    { path: "evaluations/:projectId/runs/:runId", element: <RunPage /> },
  ],
};

export default module;
