import type { FeatureModule } from "../registry";
import { PlaygroundPage } from "./PlaygroundPage";

const module: FeatureModule = {
  order: 10,
  navItems: [{ to: "/app/playground", label: "Playground" }],
  routes: [{ path: "playground", element: <PlaygroundPage /> }],
};

export default module;
