/**
 * 앱 셸 nav 페이지 목록 — 레거시 index.html topbar nav(전 13개 페이지) 패리티.
 * perm 은 레거시 data-perm 매핑 그대로. 슬라이스 7(결제·회원·감사로그)로
 * 전 콘텐츠 페이지 이전이 끝나 legacy: true 항목은 더 이상 없다.
 * (legacy 필드는 /legacy 프록시가 완전히 내려갈 때까지 타입만 유지.)
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
  { key: "transactions", label: "매입/매출", href: "/transactions", perm: "stock:read" },
  { key: "valuation", label: "재고평가", href: "/valuation", perm: "stock:read" },
  { key: "payments", label: "결제", href: "/payments", perm: "payment:read" },
  { key: "aging", label: "채권/채무", href: "/aging", perm: "payment:read" },
  { key: "gl", label: "총계정원장", href: "/gl", perm: "payment:read" },
  { key: "users", label: "회원", href: "/users", perm: "user:read" },
  { key: "audit", label: "감사로그", href: "/audit", perm: "audit:read" },
];
