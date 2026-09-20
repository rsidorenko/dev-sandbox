"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { userApi } from "@/entities/user/api";
import type { UserProfile } from "@/entities/user/types";
import { getToken, subscribeToken } from "@/shared/lib/tokenStore";

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

  const refresh = useCallback(async () => {
    // Токен живёт в памяти (tokenStore): без него запрос бессмыслен — Bearer
    // не прикрепится, сразу считаем пользователя неавторизованным.
    if (!getToken()) {
      setProfile(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    const result = await userApi.profile();
    if (result.ok) {
      setProfile(result.data);
    } else {
      setProfile(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
    // Вход/выход (setToken/clearToken из /auth/[token], LoginForm, ЛК) должны
    // перевалидировать профиль без перезагрузки страницы.
    return subscribeToken(() => {
      void refresh();
    });
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({
      loading,
      authenticated: profile !== null,
      profile,
      refresh,
    }),
    [loading, profile, refresh],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
