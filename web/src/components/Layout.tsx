import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

type Theme = "auto" | "light" | "dark";

function readTheme(): Theme {
  try {
    const v = localStorage.getItem("runboard.theme");
    if (v === "light" || v === "dark") return v;
  } catch {
    /* 저장소를 못 쓰는 환경 */
  }
  return "auto";
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = theme;
  try {
    if (theme === "auto") localStorage.removeItem("runboard.theme");
    else localStorage.setItem("runboard.theme", theme);
  } catch {
    /* 무시 */
  }
}

const NEXT: Record<Theme, Theme> = { auto: "dark", dark: "light", light: "auto" };
const LABEL: Record<Theme, string> = { auto: "테마: 자동", dark: "테마: 다크", light: "테마: 라이트" };

export default function Layout() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => applyTheme(theme), [theme]);

  return (
    <div className="container">
      <header className="topbar">
        <NavLink to="/" className="brand">
          RunBoard
        </NavLink>
        <nav className="nav">
          <NavLink to="/" end>
            대시보드
          </NavLink>
          <NavLink to="/users">사용자</NavLink>
        </nav>
        <span className="spacer" />
        <button className="btn" onClick={() => setTheme(NEXT[theme])} title="테마 전환">
          {LABEL[theme]}
        </button>
      </header>
      <Outlet />
    </div>
  );
}
