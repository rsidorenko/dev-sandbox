import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkApi,
  detectPlatformFromUA,
  medianLatency,
} from "./api";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("performance", { now: () => 0 });

afterEach(() => {
  fetchMock.mockReset();
});

describe("checkApi.status", () => {
  it("parses a connected payload", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          enabled: true,
          connected: true,
          server_label: "NL-1",
          server_country: "NL",
        }),
        { status: 200 },
      ),
    );
    const res = await checkApi.status("tok123");
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(res.data.connected).toBe(true);
      expect(res.data.server_label).toBe("NL-1");
    }
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/check/tok123");
  });

  it("maps 404 to not_found", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: false, error: "not_found" }), { status: 404 }),
    );
    const res = await checkApi.status("bad");
    expect(res).toEqual({ ok: false, error: "not_found" });
  });

  it("maps network failure to network_error", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    const res = await checkApi.status("tok");
    expect(res).toEqual({ ok: false, error: "network_error" });
  });
});

describe("checkApi.feedback", () => {
  it("sends verdict and reason as JSON POST", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, feedback_stored: true }), { status: 200 }),
    );
    const res = await checkApi.feedback("tok123", {
      verdict: "problem",
      reason_code: "connect_failed",
      platform: "win",
    });
    expect(res.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/check/tok123/feedback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      verdict: "problem",
      reason_code: "connect_failed",
      platform: "win",
    });
  });
});

describe("checkApi.speed", () => {
  it("computes kbps from payload size and elapsed time", async () => {
    let calls = 0;
    vi.stubGlobal("performance", {
      now: () => {
        calls += 1;
        // t0=0 → t1=1000 (start), t2=2000 (after body read): elapsed = 1000 мс
        return calls === 1 ? 0 : calls === 2 ? 1000 : 2000;
      },
    });
    const payload = new Uint8Array(262144);
    fetchMock.mockResolvedValueOnce(new Response(payload.buffer, { status: 200 }));
    const kbps = await checkApi.speed("tok123", 262144);
    expect(kbps).toBe(Math.round((262144 * 8) / 1 / 1000)); // 2097 кбит/с
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/check/tok123/speed?bytes=262144");
    vi.stubGlobal("performance", { now: () => 0 });
  });

  it("returns null on HTTP error", async () => {
    fetchMock.mockResolvedValueOnce(new Response("rate limited", { status: 429 }));
    expect(await checkApi.speed("tok123")).toBeNull();
  });
});

describe("medianLatency", () => {
  it("takes the median of successful samples ignoring nulls", () => {
    expect(medianLatency([120, null, 80, 200])).toBe(120);
    expect(medianLatency([null, null])).toBeNull();
    expect(medianLatency([])).toBeNull();
  });
});

describe("detectPlatformFromUA", () => {
  it("maps UA to bot platform codes", () => {
    expect(detectPlatformFromUA("Mozilla/5.0 (Linux; Android 14)")).toBe("android");
    expect(detectPlatformFromUA("Mozilla/5.0 (iPhone; CPU iPhone OS 17)")).toBe("ios");
    expect(detectPlatformFromUA("Mozilla/5.0 (Windows NT 10.0)")).toBe("win");
    expect(detectPlatformFromUA("Mozilla/5.0 (Macintosh)")).toBe("mac");
    expect(detectPlatformFromUA("curl/8.0")).toBe("");
  });
});
