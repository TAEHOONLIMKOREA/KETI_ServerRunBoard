import { fmtStamp, noticeLevelLabel } from "../format";
import type { Notice, NoticeLevel } from "../types";

/** 공지 수준 칩: 안내(accent)·주의(호박색). 배너와 관리 표가 같이 쓴다. */
export function LevelChip({ level }: { level: NoticeLevel }) {
  return <span className={`notice-level ${level}`}>{noticeLevelLabel[level]}</span>;
}

/** 표시 기한 "11. 7. (금) 18:00". 서버가 2100년까지만 받으므로 날짜로 적을 수 있다. */
export function Until({ ts }: { ts: number }) {
  return <time dateTime={new Date(ts * 1000).toISOString()}>{fmtStamp(ts)}</time>;
}

/** 헤더 아래 공지 배너. overview 가 기한 전인 것만 주의 먼저 준다. 공지마다 한 줄이고 긴 글은 줄을 바꾼다. */
export function NoticeBanner({ notices }: { notices: Notice[] }) {
  if (notices.length === 0) return null;
  return (
    <section className="notices" aria-label="공지">
      {notices.map((n) => (
        <p key={n.id} className={`notice ${n.level}`}>
          <LevelChip level={n.level} />
          <span className="notice-text">
            {n.text}
            {n.until !== null && (
              <>
                {" "}
                <span className="notice-until">
                  ~ <Until ts={n.until} /> 까지
                </span>
              </>
            )}
          </span>
        </p>
      ))}
    </section>
  );
}
