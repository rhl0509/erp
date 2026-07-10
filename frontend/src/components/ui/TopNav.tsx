"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import Button from "@/components/ui/Button";
import { useAuth } from "@/lib/auth/AuthProvider";
import { NAV_ITEMS } from "@/lib/nav";

import styles from "./TopNav.module.css";

/**
 * 앱 셸 topbar — 브랜드 + 권한 기반 nav + 사용자명/로그아웃.
 * 레거시 패리티: data-perm 게이팅은 can(perm) 필터로, nav 배지 2종
 * (재고 알림/승인 대기)은 슬라이스 2에서 폴링 쿼리로 붙인다.
 */
export default function TopNav() {
  const pathname = usePathname();
  const { me, can, logout } = useAuth();

  const visibleItems = NAV_ITEMS.filter((item) => !item.perm || can(item.perm));

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
