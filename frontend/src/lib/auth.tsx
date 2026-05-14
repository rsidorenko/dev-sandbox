"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type UserProfile } from "./api";

type AuthState = {
  loading: boolean;
  authenticated: boolean;
  profile: UserProfile | null;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  loading: true,
  authenticated: false,
  profile: null,
  refresh: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<UserProfile | null>(null);

  const refresh = async () => {
    setLoading(true);
    const result = await api.user.profile();
    if (result.ok) {
      setProfile(result.data);
    } else {
      setProfile(null);
    }
    setLoading(false);
  };

  useEffect(() => {
    refresh();
  }, []);

  return (
    <AuthContext.Provider
      value={{
        loading,
        authenticated: profile !== null,
        profile,
        refresh,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
