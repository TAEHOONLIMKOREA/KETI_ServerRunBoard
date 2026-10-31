import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { admin, useAdminAction, useOverview } from "../../api";
import { fmtAgo } from "../../format";
import type { Node } from "../../types";
import { ConnDot } from "../NodeCard";
import { ErrorBox, Loading } from "../Status";
import { FormError } from "./common";

/** 노드는 처음 보고할 때 자동 등록된다. 여기서는 화면 이름을 바꾸거나 끊긴 노드를 지운다. */
export function AdminNodes() {
  const { data, error, isLoading } = useOverview();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");

  const rename = useAdminAction(({ key, displayName }: { key: string; displayName: string }) =>
    admin.renameNode(key, displayName),
  );
  const remove = useAdminAction((key: string) => admin.deleteNode(key));

  function startEdit(n: Node) {
    setEditing(n.key);
    setName(n.display_name);
    rename.reset();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (editing === null) return;
    rename.mutate({ key: editing, displayName: name.trim() }, { onSuccess: () => setEditing(null) });
  }

  function confirmDelete(n: Node) {
    const text =
      `${n.display_name} (${n.hostname}) 노드를 지웁니다. 열린 작업은 '노드 삭제'로 끝나고 지난 작업·세션 이력은 남습니다. ` +
      "이 노드의 에이전트가 다시 보고하면 새 이름으로 다시 등록됩니다.";
    if (window.confirm(text)) remove.mutate(n.key);
  }

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <>
      <FormError error={rename.error ?? remove.error} />
      {data.nodes.length === 0 ? (
        <div className="empty">아직 보고한 노드가 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>화면 이름</th>
                <th>호스트명</th>
                <th>식별자</th>
                <th>IP</th>
                <th className="num">GPU</th>
                <th>상태</th>
                <th>마지막 보고</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.nodes.map((n) => {
                const offline = n.conn_status === "offline";
                return (
                  <tr key={n.key} className={editing === n.key ? "editing" : ""}>
                    <td>
                      {editing === n.key ? (
                        <form className="inline-form" onSubmit={submit}>
                          <input
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            required
                            maxLength={64}
                            autoFocus
                            aria-label="화면 이름"
                          />
                          <button type="submit" className="btn small primary" disabled={rename.isPending}>
                            저장
                          </button>
                          <button type="button" className="btn small" onClick={() => setEditing(null)}>
                            취소
                          </button>
                        </form>
                      ) : (
                        <Link to={`/nodes/${encodeURIComponent(n.key)}`}>{n.display_name}</Link>
                      )}
                    </td>
                    <td>{n.hostname}</td>
                    <td className="mono">{n.node_id ?? <span className="muted">(호스트명)</span>}</td>
                    <td>{n.ip ?? "—"}</td>
                    <td className="num">{n.gpu_count}</td>
                    <td className="nowrap">
                      <ConnDot status={n.conn_status} />
                    </td>
                    <td className="nowrap">{fmtAgo(n.age_s)}</td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="btn small" onClick={() => startEdit(n)}>
                          이름 변경
                        </button>
                        <button
                          type="button"
                          className="btn small danger"
                          disabled={!offline || remove.isPending}
                          title={offline ? undefined : "보고 중인 노드는 지울 수 없습니다. 에이전트를 멈추고 비활성화가 된 뒤에 지웁니다"}
                          onClick={() => confirmDelete(n)}
                        >
                          삭제
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="note">
        노드는 에이전트가 처음 보고할 때 Server_n 이름으로 자동 등록됩니다. 화면 이름은 다른 노드의 화면 이름·식별자·호스트명과
        겹치지 않아야 합니다.
        계정 매핑을 화면 이름으로 적어 두었다면 이름을 바꾼 뒤 매핑도 고칩니다. 삭제는 끊긴(비활성화) 노드만 할 수 있습니다.
      </p>
    </>
  );
}
