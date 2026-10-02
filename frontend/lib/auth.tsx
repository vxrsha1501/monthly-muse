"use client";

/* Auth context: login / register / logout with silent refresh. */
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import type { TokenResponse, User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (input: { email: string; password: string; full_name?: string; region?: string }) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => {
    if (!getToken()) {
      // try the refresh cookie once (e.g. after a page reload)
      try {
        const data = await api<TokenResponse>("/auth/refresh", { method: "POST", auth: false });
        setToken(data.access_token);
        setUser(data.user);
      } catch {
        setUser(null);
      }
    } else {
      try {
        setUser(await api<User>("/me"));
      } catch {
        setUser(null);
        setToken(null);
      }
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadMe();
  }, [loadMe]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await api<TokenResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    });
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const register = useCallback(
    async (input: { email: string; password: string; full_name?: string; region?: string }) => {
      const data = await api<TokenResponse>("/auth/register", {
        method: "POST",
        body: { ...input, timezone: "Asia/Kolkata", region: input.region ?? "India" },
        auth: false,
      });
      setToken(data.access_token);
      setUser(data.user);
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", auth: false });
    } finally {
      setToken(null);
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refresh: loadMe }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
