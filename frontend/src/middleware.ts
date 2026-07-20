import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// /payment больше не защищён на уровне middleware: страница показывает тариф
// и встроенный gate (вход по email или оплата через Telegram-бота), чтобы не
// терять пользователя на редиректе. Реальная защита оплаты — на бэкенде.
const PROTECTED_PATHS = ["/dashboard"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isProtected = PROTECTED_PATHS.some((p) => pathname.startsWith(p));

  if (!isProtected) {
    return NextResponse.next();
  }

  // Check for session cookie — actual JWT validation happens on backend
  const session = request.cookies.get("session")?.value;
  if (!session) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    // Preserve ?ref= across the redirect so a referral link to a protected page
    // (e.g. /dashboard?ref=ABC) still attributes on /login.
    const ref = request.nextUrl.searchParams.get("ref");
    if (ref) loginUrl.searchParams.set("ref", ref);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*"],
};
