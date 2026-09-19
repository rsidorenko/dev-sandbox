/**
 * API проверки подключения (/check/<token>): серверный IP-детект «трафик идёт
 * через наш VPN», замер задержки/скорости и отправка результата/вердикта.
 * Bearer-токен живёт в пути (как /sub) — сессионные cookie/CSRF не нужны.
 */

import { API_BASE } from "@/shared/api/client";

export interface CheckStatus {
  enabled: boolean;
  connected: boolean | null;
  server_label?: string;
  server_country?: string;
}

export interface CheckResultPayload {
  latency_ms?: number | null;
  throughput_kbps?: number | null;
  platform?: string;
}

export interface CheckFeedbackPayload {
  verdict: "ok" | "problem";
  reason_code?: string;
  platform?: string;
}

type JsonResult<T> = { ok: true; data: T } | { ok: false; error: string };

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<JsonResult<T>> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers as Record<string, string>) },
    });
    const body = (await res.json()) as Record<string, unknown> & { ok?: boolean; error?: string };
    if (res.ok && body.ok) {
      return { ok: true, data: body as T };
    }
    return { ok: false, error: (body.error as string) || "unknown_error" };
  } catch {
    return { ok: false, error: "network_error" };
  }
}

export const checkApi = {
  /** Статус: идёт ли трафик через наши серверы + метка сервера. */
  status: (token: string) =>
    jsonFetch<CheckStatus>(`/api/v1/check/${encodeURIComponent(token)}`),

  /** Один ping-запрос: возвращает длительность в мс (null при ошибке). */
  ping: async (): Promise<number | null> => {
    try {
      const started = performance.now();
      const res = await fetch(`${API_BASE}/api/v1/check/ping?r=${Math.random()}`, {
        cache: "no-store",
      });
      await res.arrayBuffer();
      return res.ok ? Math.round(performance.now() - started) : null;
    } catch {
      return null;
    }
  },

  /** Загрузка N байт: возвращает скорость в кбит/с (null при ошибке). */
  speed: async (token: string, bytes = 262144): Promise<number | null> => {
    try {
      const started = performance.now();
      const res = await fetch(`${API_BASE}/api/v1/check/${encodeURIComponent(token)}/speed?bytes=${bytes}`, {
        cache: "no-store",
      });
      const body = await res.arrayBuffer();
      if (!res.ok || body.byteLength === 0) return null;
      const seconds = (performance.now() - started) / 1000;
      if (seconds <= 0) return null;
      return Math.round((body.byteLength * 8) / seconds / 1000);
    } catch {
      return null;
    }
  },

  /** Отправить результат прогона (connected определяет сервер сам). */
  result: (token: string, payload: CheckResultPayload) =>
    jsonFetch<{ stored: boolean }>(`/api/v1/check/${encodeURIComponent(token)}/result`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** Вердикт пользователя: ok | problem (+ причина для problem). */
  feedback: (token: string, payload: CheckFeedbackPayload) =>
    jsonFetch<{ feedback_stored: boolean }>(`/api/v1/check/${encodeURIComponent(token)}/feedback`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

/** Медиана нечётного числа замеров задержки. */
export function medianLatency(samples: Array<number | null>): number | null {
  const values = samples.filter((v): v is number => v !== null && v >= 0);
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/** Платформа по User-Agent — те же коды, что использует бот (пусто = неизвестно). */
export function detectPlatformFromUA(ua: string): string {
  if (/Android/i.test(ua)) return "android";
  if (/iPhone|iPad|iPod/i.test(ua)) return "ios";
  if (/Windows/i.test(ua)) return "win";
  if (/Macintosh|Mac OS X/i.test(ua)) return "mac";
  return "";
}
