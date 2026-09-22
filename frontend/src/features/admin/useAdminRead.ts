"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { adminFetch } from "./api";

/** Ограничивает гонки ответов и сохраняет адрес при истечении админ-сессии. */
export function useAdminRead<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const id = ++sequence.current;
    setLoading(true); setError("");
    const result = await adminFetch<T>(path);
    if (id !== sequence.current) return;
    if (result.ok) setData(result.data);
    else if (result.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    } else { setData(null); setError("Не удалось получить данные. Повторите запрос."); }
    setLoading(false);
  }, [path]);
  useEffect(() => { const counter = sequence; void refresh(); return () => { counter.current++; }; }, [refresh]);
  return { data, loading, error, refresh };
}
