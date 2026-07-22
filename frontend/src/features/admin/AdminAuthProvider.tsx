"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { adminApi } from "./api";

type AdminAuthState = {
  loading: boolean;
  authenticated: boolean;
  logout: () => Promise<void>;
  refresh: () => void;
};

const AdminAuthContext = createContext<AdminAuthState>({
  loading: true,
  authenticated: false,
  logout: async () => {},
  refresh: () => {},
});

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  const probe = useCallback(async () => {
    // Лёгкая проверка сессии: список промокодов доступен только админу.
    const r = await adminApi.listPromocodes();
    setAuthenticated(r.ok);
    setLoading(false);
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  const logout = useCallback(async () => {
    await adminApi.logout();
    setAuthenticated(false);
    window.location.href = "/admin/login";
  }, []);

  return (
    <AdminAuthContext.Provider value={{ loading, authenticated, logout, refresh: probe }}>
      {children}
    </AdminAuthContext.Provider>
  );
}

export function useAdminAuth() {
  return useContext(AdminAuthContext);
}
