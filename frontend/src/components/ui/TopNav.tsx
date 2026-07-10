"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import Button from "@/components/ui/Button";
import { client, unwrap } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/AuthProvider";
import { NAV_ITEMS } from "@/lib/nav";

import styles from "./TopNav.module.css";

/**
 * 앱 셸 topbar — 브랜드 + 권한 기반 nav + 사용자명/로그아웃.
 * 레거시 패리티: data-perm 게이팅은 can(perm) 필터로. 재고 알림 배지
 * (#stockAlertCnt)는 /api/stock/alerts/count 쿼리로 — 재고 화면의
 * mutation 이 ["stock"] 을 invalidate 하면 함께 갱신된다.
 * 승인 대기 회원 배지(#pendingUserCnt)는 /api/users/pending/count 쿼리로 —
 * 회원 화면의 승인/등록/비활성이 ["users"] 를 invalidate 하면 함께 갱신된다.
 */
export default function TopNav() {
  const pathname = usePathname();
  const { me, can, logout } = useAuth();

  const visibleItems = NAV_ITEMS.filter((item) => !item.perm || can(item.perm));

  // 레거시 refreshStockAlertBadge 패리티 — 실패해도 조용히 무시(배지만 숨김)
  const alerts = useQuery({
    queryKey: ["stock", "alerts", "count"],
    queryFn: async () =>
      unwrap(await client.GET("/api/stock/alerts/count")) as { count: number },
    enabled: can("stock:read"),
  });
  const alertCount = alerts.data?.count ?? 0;

  // 레거시 refreshPendingBadge 패리티 — 승인 대기 회원 수(회원 nav 배지)
  const pendingUsers = useQuery({
    queryKey: ["users", "pending", "count"],
    queryFn: async () =>
      unwrap(await client.GET("/api/users/pending/count")) as {
        count: number;
      },
    enabled: can("user:read"),
  });
  const pendingUserCount = pendingUsers.data?.count ?? 0;

  return (
    <div className={styles.topbar}>
      <div className={styles.brand}>ERP&nbsp;Console</div>
      <nav className={styles.nav}>
        {visibleItems.map((item) =>
          item.legacy ? (
            // 미이전 페이지 — 레거시 SPA(hash 라우팅)로 풀 페이지 이동
            <a key={item.key} className={styles.navLink} href={item.href}>
              {item.label}
            </a>
          ) : (
            <Link
              key={item.key}
              className={
                pathname.startsWith(item.href)
                  ? `${styles.navLink} ${styles.active}`
                  : styles.navLink
              }
              href={item.href}
            >
              {item.label}
              {item.key === "stock" && alertCount > 0 && (
                <span className={styles.cnt}>{alertCount}</span>
              )}
              {item.key === "users" && pendingUserCount > 0 && (
                <span className={styles.cnt}>{pendingUserCount}</span>
              )}
            </Link>
          ),
        )}
      </nav>
      <div className={styles.who}>
        <a className={styles.whoBtn} href="/legacy#me" title="내 정보 보기">
          <b>{me ? me.full_name || me.username : "-"}</b> 님
        </a>
        <Button
          variant="ghost"
          size="sm"
          className={styles.logoutBtn}
          onClick={logout}
        >
          로그아웃
        </Button>
      </div>
    </div>
  );
}
