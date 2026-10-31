import { type FormEvent, useRef, useState } from "react";
import { admin, useAccountMappings, useAdminAction, useAdminUsers, useOverview } from "../../api";
import type { AccountMapping, Node } from "../../types";
import { ErrorBox, Loading } from "../Status";
import { Field, FormError, blank } from "./common";

const EMPTY = { node: "", uid: "", username: "" };

/** 매핑의 node 는 식별자·호스트명·화면 이름 중 하나다. 아는 노드면 그 노드를 찾는다. */
function findNode(nodes: Node[], value: string): Node | undefined {
  return nodes.find((n) => n.key === value || n.hostname === value || n.display_name === value);
}

/** (노드, uid) → 사용자. 호스트 계정명이 사용자 이름·별칭과 다를 때 쓴다. */
export function AdminMappings() {
  const { data: mappings, error, isLoading } = useAccountMappings();
  const { data: users } = useAdminUsers();
  const { data: overview } = useOverview();
  const nodes = overview?.nodes ?? [];
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const save = useAdminAction(async () => {
    const fields = { node: blank(form.node), uid: Number(form.uid), username: form.username };
    if (editing !== null) await admin.updateMapping(editing, fields);
    else await admin.createMapping(fields);
  });
  const remove = useAdminAction((id: number) => admin.deleteMapping(id));

  function reset() {
    setForm(EMPTY);
    setEditing(null);
    save.reset();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(undefined, { onSuccess: reset });
  }

  function startEdit(m: AccountMapping) {
    // 호스트명·화면 이름으로 적힌 매핑도 아는 노드면 식별자로 고쳐 저장한다 (이름이 바뀌어도 맞게)
    const node = m.node === null ? "" : (findNode(nodes, m.node)?.key ?? m.node);
    setEditing(m.id);
    setForm({ node, uid: String(m.uid), username: m.username });
    save.reset();
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function nodeLabel(value: string | null): string {
    if (value === null) return "모든 노드";
    const n = findNode(nodes, value);
    return n ? `${n.display_name} (${n.hostname})` : value;
  }

  function userLabel(username: string): string {
    const u = users?.find((x) => x.username === username);
    return u?.display_name ? `${u.display_name} (${username})` : username;
  }

  function confirmDelete(m: AccountMapping) {
    if (window.confirm(`${nodeLabel(m.node)}, uid ${m.uid} → ${userLabel(m.username)} 매핑을 지웁니다.`)) {
      if (editing === m.id) reset();
      remove.mutate(m.id);
    }
  }

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!mappings) return null;

  const nodeOptions = nodes.map((n) => ({ value: n.key, label: `${n.display_name} (${n.hostname})` }));
  if (form.node && !nodeOptions.some((o) => o.value === form.node)) {
    nodeOptions.push({ value: form.node, label: form.node });
  }

  return (
    <>
      <form className="form" onSubmit={submit} ref={formRef}>
        <div className="form-title">{editing !== null ? "계정 매핑 수정" : "계정 매핑 추가"}</div>
        <div className="form-grid">
          <Field label="노드" hint="노드별 매핑이 모든 노드 매핑보다 먼저입니다">
            <select value={form.node} onChange={(e) => setForm({ ...form, node: e.target.value })}>
              <option value="">모든 노드</option>
              {nodeOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="uid *" hint="호스트 계정의 숫자 ID (id -u)">
            <input
              type="number"
              min={0}
              max={2147483647}
              value={form.uid}
              onChange={(e) => setForm({ ...form, uid: e.target.value })}
              required
              placeholder="1001"
            />
          </Field>
          <Field label="사용자 *" hint={users?.length ? undefined : "먼저 사용자 탭에서 사용자를 추가합니다"}>
            <select value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required>
              <option value="" disabled>
                선택
              </option>
              {(users ?? []).map((u) => (
                <option key={u.username} value={u.username}>
                  {userLabel(u.username)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn primary" disabled={save.isPending}>
            {editing !== null ? "저장" : "추가"}
          </button>
          {editing !== null && (
            <button type="button" className="btn" onClick={reset}>
              취소
            </button>
          )}
          <FormError error={save.error} />
        </div>
      </form>

      <FormError error={remove.error} />
      {mappings.length === 0 ? (
        <div className="empty">계정 매핑이 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>노드</th>
                <th className="num">uid</th>
                <th>사용자</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {mappings.map((m) => (
                <tr key={m.id} className={editing === m.id ? "editing" : ""}>
                  <td>{nodeLabel(m.node)}</td>
                  <td className="num mono">{m.uid}</td>
                  <td>{userLabel(m.username)}</td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="btn small" onClick={() => startEdit(m)}>
                        수정
                      </button>
                      <button
                        type="button"
                        className="btn small danger"
                        disabled={remove.isPending}
                        onClick={() => confirmDelete(m)}
                      >
                        삭제
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="note">
        실행 계정명이 사용자 이름·별칭과 같으면 매핑 없이도 인식합니다. uid 0(root)은 매핑이 있을 때만 사용자로 봅니다.
        컨테이너 라벨(runboard.owner)과 컨테이너명 규칙이 매핑보다 먼저입니다.
      </p>
    </>
  );
}
