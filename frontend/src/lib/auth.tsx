import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, setUnauthorizedHandler, tokenStore } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [token, setToken] = useState(() => tokenStore.get());

  const logout = useCallback(() => {
    tokenStore.clear();
    setToken(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(logout);
    return () => setUnauthorizedHandler(null);
  }, [logout]);

  const meQuery = useQuery({
    queryKey: ["me", token],
    queryFn: () => api.get<Me>("/auth/me"),
    enabled: !!token,
    staleTime: 60_000,
    retry: false,
  });

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.post<{ access_token: string }>("/auth/login", { email, password });
    tokenStore.set(res.access_token);
    setToken(res.access_token);
  }, []);

  const value = useMemo<AuthState>(() => {
    const me = token ? (meQuery.data ?? null) : null;
    const perms = new Set(me?.permissions ?? []);
    return {
      me,
      loading: !!token && meQuery.isLoading,
      login,
      logout,
      can: (p: string) => perms.has(p),
    };
  }, [token, meQuery.data, meQuery.isLoading, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
