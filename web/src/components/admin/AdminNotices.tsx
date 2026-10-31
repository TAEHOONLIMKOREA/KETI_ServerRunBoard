import { type FormEvent, useId, useRef, useState } from "react";
import { admin, useAdminAction, useNotices, useOverview } from "../../api";
import type { NoticeEntry, NoticeLevel } from "../../types";
import { LevelChip, Until } from "../Notices";
import { ErrorBox, Loading } from "../Status";
import { Field, FormError } from "./common";

const EMPTY: { text: string; level: NoticeLevel; until: string } = { text: "", level: "info", until: "" };

/** epoch 초 → datetime-local 값 "2026-11-07T18:00" (이 브라우저의 시간대, 분 단위). */
function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** datetime-local 값 → epoch 초. 빈 칸은 기한 없음 (null). */
function fromLocalInput(value: string): number | null {
  return value ? new Date(value).getTime() / 1000 : null;
}

function toForm(n: NoticeEntry): typeof EMPTY {
  return { text: n.text, level: n.level, until: n.until === null ? "" : toLocalInput(n.until) };
}

/** 공지 배너. 모든 화면의 헤더 아래에 보이고, 표시 기한이 지나면 내려가 여기에 '만료'로 남는다. */
export function AdminNotices() {
  const { data: notices, error, isLoading } = useNotices();
  const { data: overview } = useOverview();
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const untilId = useId();

  const save = useAdminAction(async () => {
    const fields = { text: form.text.trim(), level: form.level, until: fromLocalInput(form.until) };
    if (editing !== null) await admin.updateNotice(editing, fields);
    else await admin.createNotice(fields);
  });
  const remove = useAdminAction((id: number) => admin.deleteNotice(id));

  function reset() {
    setForm(EMPTY);
    setEditing(null);
    save.reset();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(undefined, { onSuccess: reset });
  }

  function startEdit(n: NoticeEntry) {
    setEditing(n.id);
    setForm(toForm(n));
    save.reset();
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function confirmDelete(n: NoticeEntry) {
    if (window.confirm(`공지를 지웁니다: ${n.text}`)) {
      if (editing === n.id) reset();
      remove.mutate(n.id);
    }
  }

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!notices) return null;

  // 표시 중·만료는 배너와 같게 서버 시계로 가른다. overview 는 헤더가 이미 1초마다 읽는다
  const now = overview?.generated_ts ?? Date.now() / 1000;

  return (
    <>
      <form className="form" onSubmit={submit} ref={formRef}>
        <div className="form-title">{editing !== null ? "공지 수정" : "공지 추가"}</div>
        <div className="form-grid">
          <Field label="내용 *" hint="한 줄, 200자 이하" wide>
            <input
              value={form.text}
              onChange={(e) => setForm({ ...form, text: e.target.value })}
              required
              maxLength={200}
              placeholder="11/7(금) 18시까지 Server_3 점검으로 사용할 수 없습니다"
            />
          </Field>
          <Field label="수준">
            <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value as NoticeLevel })}>
              <option value="info">안내</option>
              <option value="warning">주의 (안내보다 위)</option>
            </select>
          </Field>
          <div className="field span-2">
            <label htmlFor={untilId}>표시 기한</label>
            <div className="field-row">
              <input
                id={untilId}
                type="datetime-local"
                max="2099-12-31T23:59"
                aria-describedby={`${untilId}-hint`}
                value={form.until}
                onChange={(e) => setForm({ ...form, until: e.target.value })}
              />
              <button
                type="button"
                className="btn"
                disabled={!form.until}
                onClick={() => setForm({ ...form, until: "" })}
              >
                지우기
              </button>
            </div>
            <small id={`${untilId}-hint`}>비우면 지울 때까지 보입니다</small>
          </div>
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
      {notices.length === 0 ? (
        <div className="empty">공지가 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>내용</th>
                <th>수준</th>
                <th>표시 기한</th>
                <th>상태</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {notices.map((n) => {
                const shown = n.until === null || n.until > now;
                return (
                  <tr key={n.id} className={editing === n.id ? "editing" : ""}>
                    <td className="notice-cell">{n.text}</td>
                    <td>
                      <LevelChip level={n.level} />
                    </td>
                    <td className="nowrap">
                      {n.until === null ? <span className="muted">없음</span> : <Until ts={n.until} />}
                    </td>
                    <td className="nowrap">
                      <span className={`dot ${shown ? "online" : "offline"}`} />
                      {shown ? "표시 중" : "만료"}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button type="button" className="btn small" onClick={() => startEdit(n)}>
                          수정
                        </button>
                        <button
                          type="button"
                          className="btn small danger"
                          disabled={remove.isPending}
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
        공지는 모든 화면의 헤더 아래에 한 줄씩 보입니다. 주의가 안내보다 먼저, 같은 수준은 먼저 등록한 것부터 나옵니다.
        표시 기한이 지나면 재시작 없이 배너에서 내려가고 이 목록에는 만료로 남습니다. 일찍 내리려면 기한을 지난 시각으로
        저장합니다. 기한은 이 브라우저의 시간대로 적습니다.
      </p>
    </>
  );
}
