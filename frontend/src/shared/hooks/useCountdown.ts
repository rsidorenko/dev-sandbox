"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Countdown timer hook — cleans up interval on unmount. */
export function useCountdown() {
  const [seconds, setSeconds] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const start = useCallback((duration = 60) => {
    setSeconds(duration);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setSeconds((t) => {
        if (t <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          timerRef.current = null;
          return 0;
        }
        return t - 1;
      });
    }, 1000);
  }, []);

  return { seconds, start, isRunning: seconds > 0 };
}
