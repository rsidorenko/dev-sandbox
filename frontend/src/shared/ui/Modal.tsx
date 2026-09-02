"use client";

import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  onClose: () => void;
};

export function Modal({ children, onClose }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div className="max-h-[calc(100dvh-2rem)] w-full overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}
