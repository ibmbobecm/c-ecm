import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { apiGet, apiPost, setActiveConnectionId, setAuthToken, setUnauthorizedHandler } from "../api/client";
import type { User } from "../types";

type TokenResponse = { access_token: string; user: User };

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  can: (featureKey: string) => boolean;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const STORAGE_KEY = "filedrive_token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => logout());

    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      setLoading(false);
      return;
    }
    setAuthToken(stored);
    apiGet<User>("/auth/me")
      .then((u) => setUser(u))
      .catch(() => {
        localStorage.removeItem(STORAGE_KEY);
        setAuthToken(null);
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = async (username: string, password: string) => {
    const res = await apiPost<TokenResponse>("/auth/login", { username, password });
    localStorage.setItem(STORAGE_KEY, res.access_token);
    setAuthToken(res.access_token);
    setUser(res.user);
  };

  const logout = () => {
    // Best-effort — invalidates the server-side session and logs the audit
    // event; must fire before the token is cleared below since it needs a
    // still-valid Authorization header. Not awaited: a slow/dead backend
    // shouldn't block signing out locally.
    apiPost("/auth/logout").catch(() => {});
    localStorage.removeItem(STORAGE_KEY);
    setAuthToken(null);
    setActiveConnectionId(null);
    setUser(null);
  };

  const can = (featureKey: string) => Boolean(user?.is_superadmin) || (user?.features ?? []).includes(featureKey);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const value = useMemo(() => ({ user, loading, login, logout, can }), [user, loading]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
