"use client";

import { AuthProvider } from "@/lib/auth";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}
