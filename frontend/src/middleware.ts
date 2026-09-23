import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Пользовательские маршруты (/dashboard, /payment; /trial — публичная
// статичная страница) больше не гвардятся middleware: сессия — токен подписки
// в памяти клиента (link-auth), куки
// session больше нет. Защиту делает клиентский AuthProvider (редирект на
// /login), оплата валидируется на бэкенде по Bearer. Осталась только админка —
// отдельная httpOnly-кука admin_session.
const ADMIN_PREFIX = "/admin";
const ADMIN_LOGIN = "/admin/login";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

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
  loginUrl.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/admin/:path*"],
};
