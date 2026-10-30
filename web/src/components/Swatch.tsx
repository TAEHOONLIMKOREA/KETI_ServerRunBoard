import type { Paint } from "../colors";

/** 색 견본. GPU 행, 범례, 사용자 목록에서 같은 모양으로 쓴다. 미확인은 CSS 빗금이라 인라인 색을 주지 않는다. */
export function Swatch({ paint }: { paint: Paint }) {
  const style = paint.kind === "unknown" ? undefined : { background: paint.color };
  return <i className={`swatch ${paint.kind}`} style={style} />;
}
