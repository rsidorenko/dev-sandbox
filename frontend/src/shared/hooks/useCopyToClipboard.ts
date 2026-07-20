"use client";

import { useCallback, useState } from "react";

/** Copy text to clipboard with temporary "copied" indicator. */
export function useCopyToClipboard(resetDelay = 2000) {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copy = useCallback(
    async (text: string, id: string): Promise<boolean> => {
      try {
        await navigator.clipboard.writeText(text);
        setCopiedId(id);
        setTimeout(() => setCopiedId(null), resetDelay);
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
