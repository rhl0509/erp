import Tag, { type TagVariant } from "@/components/ui/Tag";

/**
 * 주문문서(발주/수주) 상태 → [태그 변형, 라벨] 맵.
 * 발주는 PO_STATUS, 수주는 SO_STATUS 를 각 페이지에서 정의해 넘긴다
 * (레거시 PO_STATUS/SO_STATUS 상수 패리티).
 */
export type OrderStatusMap = Record<string, readonly [TagVariant, string]>;

/** 상태 태그 — 맵에 없는 상태는 회색 태그로 원문 표시(레거시 `||o.status` 패리티) */
export default function OrderStatusTag({
  map,
  status,
}: {
  map: OrderStatusMap;
  status: string;
}) {
  const entry = map[status];
  return entry ? (
    <Tag variant={entry[0]}>{entry[1]}</Tag>
  ) : (
    <Tag variant="gray">{status}</Tag>
  );
}
