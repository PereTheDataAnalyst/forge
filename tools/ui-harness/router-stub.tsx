// Harness-only stand-in for react-router-dom. It models only what the pages use.
import { createElement } from "react";

declare global {
  interface Window {
    __params?: Record<string, string>;
    __page?: unknown;
    __navigations?: string[];
    __search?: string;
  }
}

export function Link(props: any) {
  const { to, children, className, ...rest } = props;
  return createElement(
    "a",
    {
      href: to,
      className,
      ...rest,
      onClick: (event: any) => {
        event.preventDefault();
        (window.__navigations ??= []).push(to);
      },
    },
    children,
  );
}

export function NavLink(props: any) {
  const { to, children, className, end: _end, ...rest } = props;
  const active = window.location.pathname === to;
  const resolved = typeof className === "function" ? className({ isActive: active }) : className;
  return createElement(Link, { to, className: resolved, ...rest }, children);
}

export function Outlet() {
  return (window.__page ?? null) as any;
}
export function Navigate() {
  return null;
}
export function useParams() {
  return window.__params ?? {};
}
export function useNavigate() {
  return (to: string) => {
    (window.__navigations ??= []).push(to);
  };
}
export function useSearchParams() {
  return [new URLSearchParams(window.__search ?? ""), () => undefined] as const;
}
export function useLocation() {
  return { pathname: window.location.pathname, search: "", hash: "" };
}
export function createBrowserRouter() {
  return {};
}
export function RouterProvider() {
  return null;
}
