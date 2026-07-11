import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCountdown } from "./useCountdown";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useCountdown", () => {
  it("starts at the given duration and is running", () => {
    const { result } = renderHook(() => useCountdown());
    act(() => result.current.start(60));
    expect(result.current.seconds).toBe(60);
    expect(result.current.isRunning).toBe(true);
  });

  it("decrements by one each second", () => {
    const { result } = renderHook(() => useCountdown());
    act(() => result.current.start(60));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.seconds).toBe(59);
  });

  it("stops at 0 and clears the interval", () => {
    const { result } = renderHook(() => useCountdown());
    act(() => result.current.start(2));
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.seconds).toBe(0);
    expect(result.current.isRunning).toBe(false);
    // Интервал очищен — значение не уходит в минус.
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current.seconds).toBe(0);
  });

  it("re-starting resets the previous interval", () => {
    const { result } = renderHook(() => useCountdown());
    act(() => result.current.start(60));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current.seconds).toBe(59);
    act(() => result.current.start(30));
    expect(result.current.seconds).toBe(30);
  });
});
