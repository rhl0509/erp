/**
 * 앱 셸 nav 페이지 목록 — 레거시 index.html topbar nav(전 13개 페이지) 패리티.
 * perm 은 레거시 data-perm 매핑 그대로. 이전 완료된 페이지만 Next 경로를 쓰고,
 * 미이전 페이지는 /legacy#<page> 로 프록시(스트랭글러 공존 — 같은 origin 이라
 * localStorage 토큰 공유 = 재로그인 불필요). 슬라이스가 끝날 때마다 여기의
 * legacy: true 를 지우고 href 를 Next 경로로 바꾼다.
 */
export type NavItem = {
  /** 레거시 data-page 키 */
  key: string;
  label: string;
  href: string;
  /** 없으면 모두에게 노출. 있으면 can(perm) 통과 시에만 노출 */
  perm?: string;
  /** 미이전 페이지(레거시 SPA 로 가는 링크) 여부 */
  legacy?: boolean;
};

export const NAV_ITEMS: NavItem[] = [
  { key: "dashboard", label: "대시보드", href: "/dashboard" },
  { key: "partners", label: "거래처", href: "/partners" },
  { key: "items", label: "품목", href: "/items" },
  { key: "stock", label: "재고", href: "/stock", perm: "stock:read" },
  { key: "warehouses", label: "창고", href: "/warehouses", perm: "stock:read" },
  { key: "purchase", label: "발주", href: "/purchase", perm: "purchase:read" },
  { key: "sales", label: "수주", href: "/sales", perm: "sales:read" },
  { key: "transactions", label: "매입/매출", href: "/legacy#transactions", perm: "stock:read", legacy: true },
  { key: "valuation", label: "재고평가", href: "/legacy#valuation", perm: "stock:read", legacy: true },
  { key: "payments", label: "결제", href: "/legacy#payments", perm: "payment:read", legacy: true },
  { key: "aging", label: "채권/채무", href: "/legacy#aging", perm: "payment:read", legacy: true },
  { key: "users", label: "회원", href: "/legacy#users", perm: "user:read", legacy: true },
  { key: "audit", label: "감사로그", href: "/legacy#audit", perm: "audit:read", legacy: true },
];
