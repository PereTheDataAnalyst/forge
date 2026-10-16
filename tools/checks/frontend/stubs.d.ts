// Sandbox-only stand-ins for packages that cannot be installed here. They model the subset of the
// real APIs that the project uses, so type errors in project code still surface.

declare module "react" {
  export interface ReactElement { type: unknown; props: unknown; key: string | null }
  export type ReactNode = ReactElement | string | number | boolean | null | undefined | Iterable<ReactNode>;
  export type Dispatch<A> = (value: A) => void;
  export type SetStateAction<S> = S | ((previous: S) => S);
  export function useState<S>(initial: S | (() => S)): [S, Dispatch<SetStateAction<S>>];
  export function useEffect(effect: () => void | (() => void), deps?: readonly unknown[]): void;
  export function useMemo<T>(factory: () => T, deps: readonly unknown[]): T;
  export function useCallback<T extends (...args: never[]) => unknown>(callback: T, deps: readonly unknown[]): T;
  export interface RefObject<T> { current: T }
  export function useRef<T>(initial: T | null): RefObject<T | null>;
  export function useId(): string;
  export interface Context<T> { Provider: (props: { value: T; children?: ReactNode }) => ReactElement | null }
  export function createContext<T>(defaultValue: T): Context<T>;
  export function useContext<T>(context: Context<T>): T;
  export interface FormEvent<T = Element> { preventDefault(): void; currentTarget: T }
  export const StrictMode: (props: { children?: ReactNode }) => ReactElement;
}

declare module "react/jsx-runtime" {
  import type { FormEvent, ReactElement, ReactNode } from "react";
  export namespace JSX {
    type Element = ReactElement;
    type ElementType = string | ((props: never) => ReactNode);
    interface ChangeLike { target: EventTarget & { value: string; checked: boolean } }
    interface MouseLike { preventDefault(): void; target: EventTarget }
    interface KeyLike { key: string; ctrlKey: boolean; metaKey: boolean; preventDefault(): void }
    interface DomProps {
      onChange?: (event: ChangeLike) => void;
      onClick?: (event: MouseLike) => void;
      onKeyDown?: (event: KeyLike) => void;
      onSubmit?: (event: FormEvent<HTMLFormElement>) => void;
      onClose?: (event: MouseLike) => void;
      onCancel?: (event: MouseLike) => void;
      ref?: { current: any };
      [attr: string]: any;
    }
    interface IntrinsicElements { [name: string]: DomProps }
    interface ElementChildrenAttribute { children: unknown }
    interface IntrinsicAttributes { key?: string | number }
  }
  export function jsx(type: unknown, props: unknown, key?: string): ReactElement;
  export function jsxs(type: unknown, props: unknown, key?: string): ReactElement;
  export const Fragment: unique symbol;
}

declare module "react-dom/client" {
  import type { ReactNode } from "react";
  export function createRoot(container: Element | DocumentFragment): { render(node: ReactNode): void };
}

declare module "react-router-dom" {
  import type { ReactElement, ReactNode } from "react";
  export interface RouteObject {
    path?: string;
    index?: boolean;
    element?: ReactNode;
    children?: RouteObject[];
  }
  export function createBrowserRouter(routes: RouteObject[]): unknown;
  export function RouterProvider(props: { router: unknown }): ReactElement;
  export function Outlet(): ReactElement | null;
  export function Navigate(props: { to: string; replace?: boolean }): null;
  export function Link(props: {
    to: string; className?: string; children?: ReactNode; "aria-label"?: string;
  }): ReactElement;
  export function NavLink(props: {
    to: string; end?: boolean; children?: ReactNode;
    className?: string | ((state: { isActive: boolean }) => string);
  }): ReactElement;
  export function useNavigate(): (to: string, options?: { replace?: boolean }) => void;
  export function useParams<T extends Record<string, string | undefined>>(): Partial<T>;
  export function useSearchParams(): [URLSearchParams, (next: URLSearchParams) => void];
  export function useLocation(): { pathname: string; search: string; hash: string };
}

interface ImportMetaEnv { [key: string]: any; readonly MODE: string; readonly DEV: boolean }
interface ImportMeta {
  readonly env: ImportMetaEnv;
  glob<M = unknown>(pattern: string | string[], options?: { eager?: boolean }): Record<string, M>;
}

declare module "*.css";
declare module "vite" { export function defineConfig(config: unknown): unknown }
declare module "@vitejs/plugin-react" { const plugin: () => unknown; export default plugin }
