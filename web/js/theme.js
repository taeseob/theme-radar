// @ts-check
/** 라이트·다크 단계. 서버가 주는 색은 라이트 단계이며, 다크에서는 같은 계열의 다크 단계로 바꾼다 (docs/02 §3.4). */

const DARK = {
  "#2a78d6": "#3987e5", "#eb6834": "#d95926", "#1baf7a": "#199e70", "#eda100": "#c98500",
  "#e87ba4": "#d55181", "#008300": "#008300", "#4a3aa7": "#9085e9", "#e34948": "#e66767",
};
const KEY = "theme-radar-theme";

export function current() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function isDark() {
  return current() === "dark";
}

/** @param {string|null|undefined} color */
export function seriesColor(color) {
  if (!color) return isDark() ? "#898781" : "#c3c2b7";
  return isDark() ? (DARK[color.toLowerCase()] || color) : color;
}

export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function init(onToggle) {
  let saved = null;
  try {
    saved = localStorage.getItem(KEY);
  } catch (error) {
    saved = null;      // 사생활 보호 모드 등에서 저장소를 못 쓸 수 있다
  }
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = saved || (prefersDark ? "dark" : "light");
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch (error) { /* 저장 못 해도 화면은 동작한다 */ }
    onToggle?.();
  });
}
