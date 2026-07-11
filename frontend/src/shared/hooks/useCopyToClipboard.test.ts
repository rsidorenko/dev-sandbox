import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCopyToClipboard } from "./useCopyToClipboard";

const writeText = vi.fn(() => Promise.resolve());

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  writeText.mockClear();
});

afterEach(() => vi.useRealTimers());

describe("useCopyToClipboard", () => {
  it("copies text and marks the given id as copied", async () => {
    const { result } = renderHook(() => useCopyToClipboard(1000));
    await act(async () => {
      await result.current.copy("hello", "k1");
    });
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(result.current.isCopied("k1")).toBe(true);
    expect(result.current.isCopied("other")).toBe(false);
  });

  it("resets the copied state after resetDelay", async () => {
    const { result } = renderHook(() => useCopyToClipboard(1000));
    await act(async () => {
      await result.current.copy("hello", "k1");
    });
    expect(result.current.isCopied("k1")).toBe(true);
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.isCopied("k1")).toBe(false);
  });
});
