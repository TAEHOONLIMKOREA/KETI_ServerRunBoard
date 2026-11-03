import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { AdminNodes } from "../components/admin/AdminNodes";
import { AdminNotices } from "../components/admin/AdminNotices";
import { AdminServices } from "../components/admin/AdminServices";
import { AdminUsers } from "../components/admin/AdminUsers";

const TABS: { key: string; label: string; render: () => ReactNode }[] = [
  { key: "users", label: "사용자", render: () => <AdminUsers /> },
  { key: "services", label: "공용 서비스", render: () => <AdminServices /> },
  { key: "nodes", label: "서버(노드)", render: () => <AdminNodes /> },
  { key: "notices", label: "공지", render: () => <AdminNotices /> },
];

/** 관리: 사용자·공용 서비스(DB 의 등록 정보), 서버(노드), 공지. 탭은 URL ?tab= 에 둔다. */
export default function Admin() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get("tab")) ?? TABS[0];

  return (
    <>
      <div className="controls">
        <h1 style={{ margin: 0 }}>관리</h1>
      </div>
      <p className="note">
        고친 내용은 PostgreSQL 에 저장되고, 돌고 있는 작업도 다음 보고(1초 안팎) 때 유형·소유자를 다시 판정합니다. 로그인이
        없어 주소를 아는 누구나 고칠 수 있습니다.
      </p>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={t.key === tab.key}
            onClick={() => setParams({ tab: t.key }, { replace: true })}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab.render()}
    </>
  );
}
