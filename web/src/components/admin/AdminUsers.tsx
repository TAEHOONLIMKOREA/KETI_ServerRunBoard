import { type FormEvent, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { admin, useAdminAction, useAdminUsers, useServices } from "../../api";
import { MAX_SLOTS, userPaint } from "../../colors";
import type { AdminUser } from "../../types";
import { ErrorBox, Loading } from "../Status";
import { Swatch } from "../Swatch";
import { Field, FormError, blank, splitList } from "./common";

const EMPTY = { username: "", display_name: "", aliases: "", email: "", slack_id: "" };

function toForm(u: AdminUser): typeof EMPTY {
  return {
    username: u.username,
    display_name: u.display_name ?? "",
    aliases: u.aliases.join(", "),
    email: u.email ?? "",
    slack_id: u.slack_id ?? "",
  };
}

/** 등록 사용자. 목록 순서가 대시보드 색 순서다 (앞 8명이 고유색). */
export function AdminUsers() {
  const { data: users, error, isLoading } = useAdminUsers();
  const { data: services } = useServices();
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const save = useAdminAction(async () => {
    const fields = {
      display_name: blank(form.display_name),
      email: blank(form.email),
      slack_id: blank(form.slack_id),
      aliases: splitList(form.aliases),
    };
    if (editing) await admin.updateUser(editing, fields);
    else await admin.createUser({ username: form.username.trim(), ...fields });
  });
  const remove = useAdminAction((username: string) => admin.deleteUser(username));
  const reorder = useAdminAction((usernames: string[]) => admin.reorderUsers(usernames));

  function reset() {
    setForm(EMPTY);
    setEditing(null);
    save.reset();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(undefined, { onSuccess: reset });
  }

  function startEdit(u: AdminUser) {
    setEditing(u.username);
    setForm(toForm(u));
    save.reset();
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function move(index: number, delta: number) {
    if (!users) return;
    const order = users.map((u) => u.username);
    const [name] = order.splice(index, 1);
    order.splice(index + delta, 0, name);
    reorder.mutate(order);
  }

  function confirmDelete(u: AdminUser) {
    const s = services?.filter((x) => x.owner === u.username).length ?? 0;
    const effects = [
      s ? `담당 서비스 ${s}개는 담당자가 비워집니다.` : "",
      "지난 작업 이력의 소유자 이름은 남습니다.",
    ];
    const who = u.display_name ? `${u.display_name} (${u.username})` : u.username;
    if (window.confirm(`${who} 사용자를 지웁니다. ${effects.filter(Boolean).join(" ")}`)) {
      if (editing === u.username) reset();
      remove.mutate(u.username);
    }
  }

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!users) return null;

  return (
    <>
      <form className="form" onSubmit={submit} ref={formRef}>
        <div className="form-title">{editing ? `사용자 수정: ${editing}` : "사용자 추가"}</div>
        <div className="form-grid">
          <Field
            label="사용자 이름 *"
            hint={editing ? "작업 이력에 남아 있어 바꿀 수 없습니다" : "호스트 계정명과 같으면 자동으로 인식합니다"}
          >
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              disabled={editing !== null}
              required
              maxLength={64}
              placeholder="taehoon"
            />
          </Field>
          <Field label="표시 이름">
            <input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              maxLength={64}
              placeholder="임태훈"
            />
          </Field>
          <Field label="별칭" hint="쉼표로 구분. 다른 호스트 계정명 등">
            <input
              value={form.aliases}
              onChange={(e) => setForm({ ...form, aliases: e.target.value })}
              placeholder="th, taehoon2"
            />
          </Field>
          <Field label="이메일">
            <input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              maxLength={128}
            />
          </Field>
          <Field label="Slack ID">
            <input
              value={form.slack_id}
              onChange={(e) => setForm({ ...form, slack_id: e.target.value })}
              maxLength={64}
            />
          </Field>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn primary" disabled={save.isPending}>
            {editing ? "저장" : "추가"}
          </button>
          {editing && (
            <button type="button" className="btn" onClick={reset}>
              취소
            </button>
          )}
          <FormError error={save.error} />
        </div>
      </form>

      <FormError error={remove.error ?? reorder.error} />
      {users.length === 0 ? (
        <div className="empty">등록된 사용자가 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>색 순서</th>
                <th>사용자 이름</th>
                <th>표시 이름</th>
                <th>별칭</th>
                <th>이메일</th>
                <th>Slack ID</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u, i) => (
                <tr key={u.username} className={editing === u.username ? "editing" : ""}>
                  <td className="nowrap">
                    <Swatch paint={userPaint({ username: u.username, color_slot: i < MAX_SLOTS ? i : null })} />{" "}
                    {i + 1}{" "}
                    <button
                      type="button"
                      className="btn small"
                      aria-label="위로"
                      title="위로"
                      disabled={i === 0 || reorder.isPending}
                      onClick={() => move(i, -1)}
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="btn small"
                      aria-label="아래로"
                      title="아래로"
                      disabled={i === users.length - 1 || reorder.isPending}
                      onClick={() => move(i, 1)}
                    >
                      ↓
                    </button>
                  </td>
                  <td>
                    <Link to={`/users/${encodeURIComponent(u.username)}`}>{u.username}</Link>
                  </td>
                  <td>{u.display_name ?? <span className="muted">—</span>}</td>
                  <td>{u.aliases.join(", ") || <span className="muted">—</span>}</td>
                  <td>{u.email ?? <span className="muted">—</span>}</td>
                  <td>{u.slack_id ?? <span className="muted">—</span>}</td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="btn small" onClick={() => startEdit(u)}>
                        수정
                      </button>
                      <button
                        type="button"
                        className="btn small danger"
                        disabled={remove.isPending}
                        onClick={() => confirmDelete(u)}
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
        앞의 {MAX_SLOTS}명이 대시보드에서 고유색을 받고, 그 뒤 사용자는 중립색입니다. 사용자 이름은 지난 작업 이력에 그대로
        남아 있어 바꿀 수 없습니다. 바꾸려면 새 이름으로 추가하고 이전 사용자를 지웁니다.
      </p>
    </>
  );
}
