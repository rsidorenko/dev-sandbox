import { describe, expect, it } from "vitest";

import { safeNextPath } from "./safeRedirect";

describe("safeNextPath", () => {
  it("returns the fallback for null/empty", () => {
    expect(safeNextPath(null)).toBe("/dashboard");
    expect(safeNextPath(undefined)).toBe("/dashboard");
    expect(safeNextPath("")).toBe("/dashboard");
    expect(safeNextPath("   ")).toBe("/dashboard");
  });

  it("accepts same-origin relative paths", () => {
    expect(safeNextPath("/dashboard")).toBe("/dashboard");
    expect(safeNextPath("/payment/1m")).toBe("/payment/1m");
    expect(safeNextPath("/trial?ref=ABC")).toBe("/trial?ref=ABC");
    expect(safeNextPath("/")).toBe("/");
  });

  it("rejects absolute URLs (open redirect)", () => {
    expect(safeNextPath("https://evil.com")).toBe("/dashboard");
    expect(safeNextPath("http://evil.com/x")).toBe("/dashboard");
    expect(safeNextPath("https://bravada-connect.ru/dashboard")).toBe("/dashboard");
  });

  it("rejects protocol-relative URLs", () => {
    expect(safeNextPath("//evil.com")).toBe("/dashboard");
    expect(safeNextPath("/\\evil.com")).toBe("/dashboard");
    expect(safeNextPath("//bravada-connect.ru/")).toBe("/dashboard");
  });

  it("rejects dangerous schemes", () => {
    expect(safeNextPath("javascript:alert(1)")).toBe("/dashboard");
    expect(safeNextPath("data:text/html,<script>1</script>")).toBe("/dashboard");
  });

  it("honors a custom fallback", () => {
    expect(safeNextPath("https://x", "/home")).toBe("/home");
  });
});
