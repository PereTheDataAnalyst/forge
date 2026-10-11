import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, setUnauthorizedHandler } from "../lib/api";
import type { User } from "../lib/types";

type Status = "loading" | "authenticated" | "anonymous";

interface AuthValue {
  status: Status;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

interface AuthState {
  status: Status;
  user: User | null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });

  useEffect(() => {
    let active = true;
    api.auth
      .me()
      .then((user) => {
        if (active) setState({ status: "authenticated", user });
      })
      .catch(() => {
        if (active) setState({ status: "anonymous", user: null });
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setState({ status: "anonymous", user: null }));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const user = await api.auth.login(email, password);
    setState({ status: "authenticated", user });
  }, []);

  const register = useCallback(async (email: string, password: string, displayName?: string) => {
    const user = await api.auth.register(email, password, displayName);
    setState({ status: "authenticated", user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setState({ status: "anonymous", user: null });
    }
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ ...state, login, register, logout }),
    [state, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
