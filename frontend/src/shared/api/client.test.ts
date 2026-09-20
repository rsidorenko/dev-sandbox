import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { apiFetch } from "./client";
import { clearToken, setToken } from "@/shared/lib/tokenStore";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  clearToken();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiFetch", () => {
  it("returns data on a successful response", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, id: 7 }),
    });
    const r = await apiFetch<{ id: number }>("/x");
    expect(r).toEqual({ ok: true, data: { ok: true, id: 7 } });
  });

  it("returns an error when body.ok is false", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: false, error: "bad", detail: "d" }),
    });
    const r = await apiFetch("/x");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toBe("bad");
      expect(r.detail).toBe("d");
    }
  });

  it("returns network_error when fetch throws", async () => {
    fetchMock.mockRejectedValue(new Error("net"));
    const r = await apiFetch("/x");
    expect(r).toEqual({ ok: false, error: "network_error" });
  });

  it("attaches the Bearer header from the in-memory token store", async () => {
    setToken("unit-test-token");
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });
    await apiFetch("/x");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer unit-test-token");
    // Куки не отправляем (нет credentials: "include") — сессия без cookies.
    expect(init.credentials).toBeUndefined();
  });

  it("sends no Authorization without a token, and no CSRF header for POST", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    });
    await apiFetch("/x", { method: "POST", body: "{}" });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBeUndefined();
    expect(headers["X-CSRF-Token"]).toBeUndefined();
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.credentials).toBeUndefined();
  });
});
