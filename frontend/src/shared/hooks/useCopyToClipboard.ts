"use client";

import { useCallback, useState } from "react";

/** Copy text to clipboard with temporary "copied" indicator. */
export function useCopyToClipboard(resetDelay = 2000) {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copy = useCallback(
    async (text: string, id: string) => {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), resetDelay);
    },
    [resetDelay],
  );

  const isCopied = useCallback(
    (id: string) => copiedId === id,
    [copiedId],
  );

  return { copy, isCopied };
}
