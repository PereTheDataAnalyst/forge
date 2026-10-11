import type { RouteObject } from "react-router-dom";

/**
 * Extension point for feature modules built by the second build chat (Evaluation, Voice, MCP).
 *
 * A module is a file `src/extensions/<name>/module.tsx` with a default export of type
 * FeatureModule. Routes are mounted under /app, so use relative paths such as "evaluations".
 * Navigation links use absolute paths such as "/app/evaluations". No core file needs editing.
 */
export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

export interface FeatureModule {
  routes?: RouteObject[];
  navItems?: NavItem[];
}

const modules = import.meta.glob<{ default: FeatureModule }>("./*/module.tsx", { eager: true });

export const extensionRoutes: RouteObject[] = Object.values(modules).flatMap(
  (module) => module.default.routes ?? [],
);

export const extensionNavItems: NavItem[] = Object.values(modules).flatMap(
  (module) => module.default.navItems ?? [],
);
