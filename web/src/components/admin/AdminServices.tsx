import { type FormEvent, useRef, useState } from "react";
import { admin, useAdminAction, useAdminUsers, useServices } from "../../api";
import type { ServiceEntry } from "../../types";
import { ErrorBox, Loading } from "../Status";
import { Field, FormError, blank } from "./common";

const EMPTY = {
  name: "",
  match_exec_account: "",
  match_container_pattern: "",
  match_image_pattern: "",
  owner: "",
  idle_exempt: true,
};

function toForm(s: ServiceEntry): typeof EMPTY {
  return {
    name: s.name,
    match_exec_account: s.match_exec_account ?? "",
    match_container_pattern: s.match_container_pattern ?? "",
    match_image_pattern: s.match_image_pattern ?? "",
    owner: s.owner ?? "",
    idle_exempt: s.idle_exempt,
  };
}

function Rule({ value }: { value: string | null }) {
  return value ? <span className="mono">{value}</span> : <span className="muted">—</span>;
}

/** 공용 서비스 레지스트리 (Ollama, vLLM 등). 맞는 프로세스는 유형이 공용 서비스, 소유자는 담당자다. */
export function AdminServices() {
  const { data: services, error, isLoading } = useServices();
  const { data: users } = useAdminUsers();
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const save = useAdminAction(async () => {
    const fields = {
      name: form.name.trim(),
      match_exec_account: blank(form.match_exec_account),
      match_container_pattern: blank(form.match_container_pattern),
      match_image_pattern: blank(form.match_image_pattern),
      owner: blank(form.owner),
      idle_exempt: form.idle_exempt,
    };
    if (editing !== null) await admin.updateService(editing, fields);
    else await admin.createService(fields);
  });
  const remove = useAdminAction((id: number) => admin.deleteService(id));

  function reset() {
    setForm(EMPTY);
    setEditing(null);
    save.reset();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(undefined, { onSuccess: reset });
  }

  function startEdit(s: ServiceEntry) {
    setEditing(s.id);
    setForm(toForm(s));
    save.reset();
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function ownerLabel(username: string | null): string {
    if (username === null) return "—";
    const u = users?.find((x) => x.username === username);
    return u?.display_name ? `${u.display_name} (${username})` : username;
  }

  function confirmDelete(s: ServiceEntry) {
    if (window.confirm(`공용 서비스를 지웁니다: ${s.name}. 맞던 작업은 다음 보고부터 개인·컨테이너 작업으로 다시 판정합니다.`)) {
      if (editing === s.id) reset();
      remove.mutate(s.id);
    }
  }

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!services) return null;

  return (
    <>
      <form className="form" onSubmit={submit} ref={formRef}>
        <div className="form-title">{editing !== null ? `공용 서비스 수정: ${form.name}` : "공용 서비스 추가"}</div>
        <div className="form-grid">
          <Field label="이름 *" hint="대시보드의 작업 이름">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
              maxLength={64}
              placeholder="ollama"
            />
          </Field>
          <Field label="실행 계정" hint="이 계정으로 도는 프로세스">
            <input
              value={form.match_exec_account}
              onChange={(e) => setForm({ ...form, match_exec_account: e.target.value })}
              maxLength={64}
              placeholder="ollama"
            />
          </Field>
          <Field label="컨테이너명 패턴" hint="정규식, 부분 일치">
            <input
              className="mono"
              value={form.match_container_pattern}
              onChange={(e) => setForm({ ...form, match_container_pattern: e.target.value })}
              maxLength={200}
              placeholder="^vllm"
            />
          </Field>
          <Field label="이미지 패턴" hint="정규식, 부분 일치">
            <input
              className="mono"
              value={form.match_image_pattern}
              onChange={(e) => setForm({ ...form, match_image_pattern: e.target.value })}
              maxLength={200}
              placeholder="vllm/vllm-openai"
            />
          </Field>
          <Field label="담당자">
            <select value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
              <option value="">없음 (소유자 미확인)</option>
              {(users ?? []).map((u) => (
                <option key={u.username} value={u.username}>
                  {ownerLabel(u.username)}
                </option>
              ))}
            </select>
          </Field>
          <label className="field check">
            <input
              type="checkbox"
              checked={form.idle_exempt}
              onChange={(e) => setForm({ ...form, idle_exempt: e.target.checked })}
            />
            <span>유휴 예외 (유휴 점유로 보지 않음)</span>
          </label>
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
      {services.length === 0 ? (
        <div className="empty">공용 서비스가 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>이름</th>
                <th>실행 계정</th>
                <th>컨테이너명 패턴</th>
                <th>이미지 패턴</th>
                <th>담당자</th>
                <th>유휴 예외</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={s.id} className={editing === s.id ? "editing" : ""}>
                  <td>{s.name}</td>
                  <td>
                    <Rule value={s.match_exec_account} />
                  </td>
                  <td>
                    <Rule value={s.match_container_pattern} />
                  </td>
                  <td>
                    <Rule value={s.match_image_pattern} />
                  </td>
                  <td>{ownerLabel(s.owner)}</td>
                  <td>{s.idle_exempt ? "예" : "아니오"}</td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="btn small" onClick={() => startEdit(s)}>
                        수정
                      </button>
                      <button
                        type="button"
                        className="btn small danger"
                        disabled={remove.isPending}
                        onClick={() => confirmDelete(s)}
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
        실행 계정·컨테이너명 패턴·이미지 패턴 중 하나라도 맞으면 공용 서비스로 봅니다. 여러 서비스가 맞으면 위에 있는 것이
        이깁니다. 담당자가 없으면 소유자는 미확인입니다.
      </p>
    </>
  );
}
