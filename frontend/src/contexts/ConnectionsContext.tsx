import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { apiDelete, apiGet, apiPost, ApiError, setActiveConnectionId } from "../api/client";
import type { Connection, Provider } from "../types";
import { useAuth } from "./AuthContext";

type ConnectionsContextValue = {
  providers: Provider[];
  connections: Connection[];
  activeConnectionId: string | null;
  loading: boolean;
  selectConnection: (id: string | null) => void;
  refresh: () => void;
  createConnection: (
    providerKey: string,
    displayName: string,
    username: string,
    password: string,
    config?: Record<string, string>
  ) => Promise<void>;
  connectOAuth: (providerKey: string, displayName: string) => Promise<void>;
  removeConnection: (id: string) => Promise<void>;
};

const ConnectionsContext = createContext<ConnectionsContextValue | null>(null);

const ACTIVE_KEY = "filedrive_active_connection";

export function ConnectionsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [activeConnectionId, setActive] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    if (!user) return;
    setLoading(true);
    Promise.all([apiGet<Provider[]>("/connections/providers"), apiGet<Connection[]>("/connections")])
      .then(([p, c]) => {
        setProviders(p);
        setConnections(c);
        setActive((current) => {
          const stored = current ?? localStorage.getItem(ACTIVE_KEY);
          const valid = stored && c.some((x) => x.id === stored) ? stored : c[0]?.id ?? null;
          setActiveConnectionId(valid);
          return valid;
        });
      })
      .catch(() => {
        setProviders([]);
        setConnections([]);
      })
      .finally(() => setLoading(false));
  }, [user]);

  useEffect(() => {
    if (user) refresh();
    else {
      setConnections([]);
      setProviders([]);
      setActive(null);
      setActiveConnectionId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const selectConnection = (id: string | null) => {
    setActive(id);
    setActiveConnectionId(id);
    if (id) localStorage.setItem(ACTIVE_KEY, id);
    else localStorage.removeItem(ACTIVE_KEY);
  };

  const createConnection = async (
    providerKey: string,
    displayName: string,
    username: string,
    password: string,
    config?: Record<string, string>
  ) => {
    const created = await apiPost<Connection>("/connections", {
      provider_key: providerKey,
      display_name: displayName,
      username,
      password,
      config: config && Object.keys(config).length > 0 ? config : undefined,
    });
    setConnections((prev) => [...prev, created]);
    selectConnection(created.id);
  };

  const connectOAuth = (providerKey: string, displayName: string): Promise<void> => {
    return new Promise((resolve, reject) => {
      apiGet<{ authorize_url: string }>(`/connections/oauth/${providerKey}/start`, { display_name: displayName })
        .then(({ authorize_url }) => {
          const popup = window.open(authorize_url, "filedrive-oauth", "width=520,height=650");
          if (!popup) {
            reject(new Error("Pop-up was blocked — allow pop-ups for this site and try again."));
            return;
          }
          const onMessage = (event: MessageEvent) => {
            if (!event.data || event.data.type !== "filedrive-oauth") return;
            window.removeEventListener("message", onMessage);
            clearInterval(closedCheck);
            if (event.data.error) {
              reject(new Error(event.data.error));
              return;
            }
            refresh();
            resolve();
          };
          window.addEventListener("message", onMessage);
          const closedCheck = setInterval(() => {
            if (popup.closed) {
              clearInterval(closedCheck);
              window.removeEventListener("message", onMessage);
              reject(new Error("Sign-in window was closed."));
            }
          }, 500);
        })
        .catch((err) => reject(err instanceof ApiError ? err : new Error("Couldn't start sign-in.")));
    });
  };

  const removeConnection = async (id: string) => {
    await apiDelete(`/connections/${id}`);
    setConnections((prev) => prev.filter((c) => c.id !== id));
    if (activeConnectionId === id) {
      const remaining = connections.filter((c) => c.id !== id);
      selectConnection(remaining[0]?.id ?? null);
    }
  };

  const value = useMemo(
    () => ({
      providers,
      connections,
      activeConnectionId,
      loading,
      selectConnection,
      refresh,
      createConnection,
      connectOAuth,
      removeConnection,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [providers, connections, activeConnectionId, loading]
  );

  return <ConnectionsContext.Provider value={value}>{children}</ConnectionsContext.Provider>;
}

export function useConnections(): ConnectionsContextValue {
  const ctx = useContext(ConnectionsContext);
  if (!ctx) throw new Error("useConnections must be used within ConnectionsProvider");
  return ctx;
}
