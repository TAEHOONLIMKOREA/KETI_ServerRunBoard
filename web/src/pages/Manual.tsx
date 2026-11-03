import { Marked } from "marked";
import readme from "../../../README.md?raw";

/**
 * 메뉴얼: 레포 루트의 README.md 를 빌드 때 그대로 넣어 HTML 로 보인다. README 를 고치면 웹을 다시 빌드해야 화면에 반영된다.
 *
 * - README 의 그림 `docs/img/<파일>` 은 Vite 가 자산으로 묶으므로(아래 glob) 주소를 그 자산 주소로 바꾼다.
 * - `README_DEV.md` 처럼 레포 안의 .md 로 가는 링크는 화면에 없으니 GitHub 의 그 파일로 보낸다.
 * - README 는 레포에 든 글이라(사용자 입력이 아니다) 변환한 HTML 을 그대로 넣는다.
 */
const REPO_BLOB = "https://github.com/TAEHOONLIMKOREA/KETI_ServerRunBoard/blob/master/";
const IMAGES = import.meta.glob("../../../docs/img/*", { eager: true, query: "?url", import: "default" }) as Record<
  string,
  string
>;

function imageUrl(href: string): string {
  return IMAGES[`../../../${href}`] ?? href;
}

function linkUrl(href: string): string {
  return /^(?![a-z]+:)[^#?]+\.md(#.*)?$/i.test(href) ? REPO_BLOB + href : href;
}

const md = new Marked({
  walkTokens(token) {
    if (token.type === "image") token.href = imageUrl(token.href);
    else if (token.type === "link") token.href = linkUrl(token.href);
  },
});
const HTML = md.parse(readme, { async: false });

export default function Manual() {
  return <article className="manual" dangerouslySetInnerHTML={{ __html: HTML }} />;
}
