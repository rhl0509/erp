import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * 인증 가드(Next 16 proxy — 구 middleware). 세션 쿠키(erp_session) 없이 보호 경로
 * 접근 시 /login 으로 돌린다. 이는 보호 셸의 깜빡임을 막는 서버측 1차 가드일 뿐,
 * 토큰의 서명·만료 검증은 FastAPI(/api)와 (app) 셸 가드(/me)가 담당한다
 * (쿠키가 httpOnly라 여기선 존재 여부만 본다).
 *
 * 단방향(쿠키 없음 → /login)만 수행한다. "쿠키 있음 → /dashboard" 역방향을 넣으면,
 * 만료된 쿠키가 남아 있을 때 /login↔/dashboard 무한 리다이렉트가 생길 수 있다.
 */
const SESSION_COOKIE = "erp_session";
const PUBLIC_PREFIXES = ["/login"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isPublic = PUBLIC_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/"),
  );
  if (isPublic) return NextResponse.next();

  if (!request.cookies.has(SESSION_COOKIE)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // api·legacy·static 프록시(rewrites 대상)와 Next 내부 자산은 가드에서 제외한다.
  matcher: [
    "/((?!api|legacy|static|_next/static|_next/image|favicon.ico).*)",
  ],
};
