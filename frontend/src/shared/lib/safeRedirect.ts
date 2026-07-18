/**
 * Sanitize a user-controlled redirect target (`?next=…`) to a same-origin
 * relative path. Prevents open-redirect / phishing — e.g. an attacker crafting
 * `https://bravada-connect.ru/login?next=https://evil.com` to bounce a logged-in
 * user to a lookalike site after a successful login.
 *
 * Only same-origin relative paths are allowed: must start with "/", but not
 * with "//" (protocol-relative → other origin) or "/\" (some browsers treat
 * `/\` as a scheme separator). Everything else (absolute URLs, `javascript:`,
 * `data:`, etc.) falls back to a safe default.
 */
export function safeNextPath(next: string | null | undefined, fallback = "/dashboard"): string {
  if (!next) return fallback;
  if (next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")) {
    return next;
  }
  return fallback;
}
