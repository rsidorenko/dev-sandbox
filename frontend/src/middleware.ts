import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// /payment больше не защищён на уровне middleware: страница показывает тариф
// и встроенный gate (вход по email или оплата через Telegram-бота), чтобы не
// терять пользователя на редиректе. Реальная защита оплаты — на бэкенде.
const PROTECTED_DASHBOARD = "/dashboard";
const ADMIN_PREFIX = "/admin";
const ADMIN_LOGIN = "/admin/login";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Dashboard — пользовательская сессия (кука session)
  if (pathname.startsWith(PROTECTED_DASHBOARD)) {
    const session = request.cookies.get("session")?.value;
    if (!session) {
      return redirectToLogin(request, "/login");
    }
    return NextResponse.next();
  }

  // Админ-панель — отдельная сессия (кука admin_session), кроме страницы входа.
  if (pathname.startsWith(ADMIN_PREFIX) && pathname !== ADMIN_LOGIN) {
    const adminSession = request.cookies.get("admin_session")?.value;
    if (!adminSession) {
      return redirectToLogin(request, ADMIN_LOGIN);
    }
    return NextResponse.next();
  }

  return NextResponse.next();
}

function redirectToLogin(request: NextRequest, loginPath: string) {
  const loginUrl = new URL(loginPath, request.url);
  loginUrl.searchParams.set("next", request.nextUrl.pathname);
  // Preserve ?ref= across the redirect so a referral link to a protected page
  // (e.g. /dashboard?ref=ABC) still attributes on /login.
  const ref = request.nextUrl.searchParams.get("ref");
  if (ref) loginUrl.searchParams.set("ref", ref);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/dashboard/:path*", "/admin/:path*"],
};
