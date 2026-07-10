/** 레거시 index.html won() 패리티 — 천단위 콤마(ko-KR). 금액/수량/건수 공용. */
export function won(n: number | null | undefined): string {
  return (Number(n) || 0).toLocaleString("ko-KR");
}

/** ISO datetime → YYYY-MM-DD (레거시 created_at.slice(0,10) 패리티) */
export function dateOnly(iso: string | null | undefined): string {
  return (iso ?? "").slice(0, 10);
}
