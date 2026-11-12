import { NextResponse, type NextRequest } from "next/server";

const PROTECTED = [
  "/dashboard", "/markets", "/stocks", "/options", "/crypto", "/forex", "/portfolio", "/orders",
  "/positions", "/watchlist", "/wallet", "/transactions", "/profile", "/settings", "/admin",
];

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const needsAuth = PROTECTED.some((p) => pathname === p || pathname.startsWith(p + "/"));
  const hasSession = Boolean(req.cookies.get("scriza_session")?.value);
  if (needsAuth && !hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  if ((pathname === "/login" || pathname === "/register") && hasSession && !req.nextUrl.searchParams.get("switch")) {
    // Server still validates the session; a stale cookie simply bounces back to /login via the API 401 handler.
    const url = req.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api|_next|favicon|.*\\..*).*)"] };
