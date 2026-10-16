import type { RouteObject } from "react-router-dom";

/**
 * Extension point for feature modules (Playground, Evaluation, and later Voice and MCP).
 *
 * A module is a file `src/extensions/<n>/module.tsx` with a default export of type
 * FeatureModule. Routes are mounted under /app, so use relative paths such as "evaluations".
 * Navigation links use absolute paths such as "/app/evaluations". No core file needs editing.
 */
export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

export interface FeatureModule {
  /** Position in the navigation. Lower numbers come first. Defaults to 100. */
  order?: number;
  routes?: RouteObject[];
  navItems?: NavItem[];
}

const modules = import.meta.glob<{ default: FeatureModule }>("./*/module.tsx", { eager: true });

const loaded = Object.entries(modules)
  .map(([path, module]) => ({ path, feature: module.default }))
  .sort(
    (a, b) =>
      (a.feature.order ?? 100) - (b.feature.order ?? 100) || a.path.localeCompare(b.path),
  );

export const extensionRoutes: RouteObject[] = loaded.flatMap(({ feature }) => feature.routes ?? []);

export const extensionNavItems: NavItem[] = loaded.flatMap(({ feature }) => feature.navItems ?? []);
