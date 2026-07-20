"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Copy text to clipboard with temporary "copied" indicator. */
export function useCopyToClipboard(resetDelay = 2000) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // Track the pending reset timer so (a) a rapid second copy clears the first
  // indicator's timer (no early reset race) and (b) unmount clears it (no
  // setCopiedId on an unmounted component).
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  const copy = useCallback(
    async (text: string, id: string): Promise<boolean> => {
      try {
        await navigator.clipboard.writeText(text);
        if (timerRef.current !== null) clearTimeout(timerRef.current);
        setCopiedId(id);
        timerRef.current = setTimeout(() => {
          timerRef.current = null;
          setCopiedId(null);
        }, resetDelay);
        return true;
      } catch {
        // Clipboard API can reject (no permission, non-secure context, iframe block).
        // Fail silently in UI — the caller's "copied" indicator simply doesn't activate,
        // and we avoid an unhandled promise rejection.
        return false;
      }
    },
    [resetDelay],
  );

  const isCopied = useCallback(
    (id: string) => copiedId === id,
    [copiedId],
  );

  return { copy, isCopied };
}
