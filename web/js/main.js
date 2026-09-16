// @ts-check
/** 화면 조립 (docs/06). 상태는 URL이 단일 출처이고, 상태가 바뀔 때마다 필요한 부분만 다시 그린다. */
import { ApiError, get } from "./api.js";
import * as bump from "./bump-chart.js";
import * as drilldown from "./drilldown.js";
import * as events from "./events.js";
import { escapeHtml } from "./format.js";
import * as snapshot from "./snapshot.js";
import * as state from "./state.js";
import * as theme from "./theme.js";

const NARROW = 768;                 // 이 아래에서는 표시 섹터 수를 상위 5로 강제한다 (docs/06 §8)
const MAX_PERIODS = { W: 260, M: 120 };
const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const sel = (id) => /** @type {HTMLSelectElement} */ (document.getElementById(id));

/** 마지막으로 성공한 응답. API 오류가 나도 화면을 비우지 않는다 (docs/06 §6) */
const cache = {
  universes: [], schemes: [], groups: new Map(), currency: "KRW",
  /** @type {any} */ calendar: null, /** @type {any} */ ranks: null, /** @type {any} */ history: null,
  /** @type {Record<string, string>|null} */ state: null,
};
let requestSeq = 0;

// ── 컨트롤 ────────────────────────────────────────────────────────────────────

function fillSelect(select, items, value) {
  select.innerHTML = items.map((i) => `<option value="${escapeHtml(i.value)}">${escapeHtml(i.label)}</option>`).join("");
  select.value = value;
}

function segmented(id, value, onPick) {
  for (const button of $(id).querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.value === value));
    if (!button.dataset.bound) {
      button.dataset.bound = "1";
      button.addEventListener("click", () => onPick(button.dataset.value));
    }
  }
}

function effectiveTop(top) {
  const n = Number(top) || 0;
  if (window.innerWidth < NARROW) return n ? Math.min(n, 5) : 5;
  return n;
}

// ── 상태 표시 ─────────────────────────────────────────────────────────────────

function banner(message, { kind = "warn", retry = false } = {}) {
  const element = $("banner");
  element.hidden = !message;
  if (!message) return;
  element.className = `banner${kind === "error" ? " error" : ""}`;
  element.innerHTML = escapeHtml(message) + (retry ? ' <button type="button" class="ghost" id="retry">다시 시도</button>' : "");
  if (retry) $("retry").addEventListener("click", () => refresh(true));
}

function emptyMessage(target, message, hint) {
  target.innerHTML = `<p class="empty">${escapeHtml(message)}${hint ? `<br><span class="muted">${escapeHtml(hint)}</span>` : ""}</p>`;
}

// ── 메타 적재 ─────────────────────────────────────────────────────────────────

async function loadUniverses(current) {
  if (!cache.universes.length) cache.universes = (await get("/meta/universes")).data;
  const known = cache.universes.find((u) => u.universe === current) || cache.universes[0];
  fillSelect(sel("universe"), cache.universes.map((u) => ({ value: u.universe, label: u.name })), known.universe);
  cache.currency = known.currency;
  document.body.dataset.market = known.market;
  return known;
}

async function loadSchemes(universe, current) {
  cache.schemes = (await get("/meta/schemes", { universe })).data;
  // 시장과 스킴의 불일치는 컨트롤 단계에서 막는다. API 400을 화면에서 유발하지 않는다 (docs/06 §2)
  const known = cache.schemes.find((s) => s.scheme === current)
    || cache.schemes.find((s) => s.exclusive) || cache.schemes[0];
  fillSelect(sel("scheme"), cache.schemes.map((s) => ({ value: s.scheme, label: `${s.name} (${s.group_count})` })), known.scheme);
  const groups = (await get("/meta/groups", { universe, scheme: known.scheme })).data;
  cache.groups = new Map(groups.map((g) => [g.group_code, g]));
  return known.scheme;
}

// ── 그리기 ────────────────────────────────────────────────────────────────────

function drawBump(current) {
  const payload = cache.ranks;
  const asTable = current.view === "table";
  $("bump").hidden = asTable;
  $("bump-table").hidden = !asTable;
  $("table-toggle").textContent = asTable ? "차트로 보기" : "표로 보기";
  if (asTable) $("bump-table").innerHTML = bump.tableHtml(payload, current.mode);
  else bump.render($("bump"), payload, { mode: current.mode, calendar: cache.calendar.map, onPick: pick });
  const others = payload.data.others_count;
  $("bump-hint").innerHTML = "선을 클릭하면 아래에 섹터 상세가 열린다. 점선 구간은 아직 끝나지 않은 기간(잠정)이다."
    + (others ? ` <span class="muted">표시 기준 밖 ${others}개 섹터는 감춰져 있다.</span>` : "");
}

async function drawSnapshot(current, periodId) {
  const payload = await get("/sectors/returns", { universe: current.universe, scheme: current.scheme,
                                                  period: current.period, period_id: periodId, sort: sel("snapshot-sort").value });
  $("snapshot-title").textContent = `${payload.meta.period_id} 섹터별${payload.meta.is_provisional ? " (잠정)" : ""}`;
  snapshot.render($("snapshot"), payload, current.group);
}

async function drawDrilldown(current, periodId) {
  const card = $("drilldown-card");
  card.hidden = !current.group;
  if (!current.group) return;
  const base = { universe: current.universe, scheme: current.scheme, period: current.period };
  const path = `/sectors/${encodeURIComponent(current.group)}`;
  const [breakdown, history] = await Promise.all([
    get(`${path}/breakdown`, { ...base, period_id: periodId, limit: 10, side: "both" }),
    get(`${path}/history`, { ...base, from: cache.calendar.from, to: cache.calendar.to }),
  ]);
  cache.history = history;
  $("drilldown-title").innerHTML = drilldown.title(
    { name: breakdown.meta.name, color: cache.groups.get(current.group)?.color }, breakdown.meta.period_id);
  drilldown.renderConcentration($("concentration"), breakdown.data.summary);
  drilldown.renderMembers($("members"), breakdown);
  drilldown.renderSparklines(history);
}

async function drawEvents(current) {
  const payload = await get("/events", { universe: current.universe, type: current.event_type,
                                         from: cache.calendar.rows[0].cal_start,
                                         to: cache.calendar.rows[cache.calendar.rows.length - 1].cal_end, limit: 50 });
  events.decorateTypes(sel("event-type"), payload.meta.by_type || {});
  events.render($("events"), payload, cache.currency);
}

// ── 새로 고침 ─────────────────────────────────────────────────────────────────

async function refresh(force = false) {
  const seq = ++requestSeq;
  const current = state.read();
  const previous = cache.state;
  const outdated = () => seq !== requestSeq;
  const changed = (...keys) => force || !previous || keys.some((key) => previous[key] !== current[key]);
  const reload = changed("universe", "scheme", "period", "range", "top") || !cache.ranks;

  banner("");
  if (reload) bump.showLoading($("bump"));
  try {
    const universe = await loadUniverses(current.universe);
    if (outdated()) return;
    const scheme = changed("universe", "scheme") || !cache.groups.size
      ? await loadSchemes(universe.universe, current.scheme) : current.scheme;
    if (outdated()) return;
    if (universe.universe !== current.universe || scheme !== current.scheme) {
      // 시장이 바뀌면 섹터·기간 선택은 의미를 잃는다. 스킴만 기본값으로 채운 경우는 선택을 유지한다
      const moved = universe.universe !== current.universe;
      Object.assign(current, { universe: universe.universe, scheme },
                    moved ? { group: "", pid: "" } : {});
      state.update({ universe: current.universe, scheme, ...(moved ? { group: "", pid: "" } : {}) }, { silent: true });
    }

    if (reload) {
      const count = Math.min(Number(current.range) || 26, MAX_PERIODS[current.period] || 260);
      const rows = (await get("/meta/periods", { universe: current.universe, period: current.period, last: count })).data;
      if (outdated()) return;
      if (!rows.length) {
        emptyMessage($("bump"), "선택한 구간에 계산된 데이터가 없습니다.");
        return;
      }
      cache.calendar = { rows, map: new Map(rows.map((r) => [r.period_id, r])),
                         from: rows[0].period_id, to: rows[rows.length - 1].period_id };
      cache.ranks = await get("/sectors/ranks", { universe: current.universe, scheme: current.scheme, period: current.period,
                                                  from: cache.calendar.from, to: cache.calendar.to,
                                                  top_n: effectiveTop(current.top) });
      if (outdated()) return;
      bump.hideLoading();
    }

    const periodId = cache.calendar.map.has(current.pid) ? current.pid : cache.calendar.to;
    const meta = cache.ranks.meta;
    $("calc-info").textContent = meta.calculated_at
      ? `· 계산 ${meta.calc_version} / ${new Date(meta.calculated_at).toLocaleString("ko-KR")}` : "";
    if (meta.stale) banner("데이터 재계산 중 — 값이 변경될 수 있습니다.");
    drawBump(current);

    const summary = await get("/market/summary", { universe: current.universe, scheme: current.scheme,
                                                   period: current.period, period_id: periodId });
    if (outdated()) return;
    $("market-summary").innerHTML = snapshot.summaryHtml(summary.data, summary.data.period_id);

    await drawSnapshot(current, periodId);
    if (outdated()) return;
    await drawDrilldown(current, periodId);
    if (outdated()) return;
    if (changed("universe", "event_type", "period", "range")) await drawEvents(current);
    if (outdated()) return;
    cache.state = current;
  } catch (error) {
    if (outdated()) return;
    bump.hideLoading();
    if (!(error instanceof ApiError)) throw error;
    if (error.status === 409) {
      emptyMessage(cache.ranks ? $("snapshot") : $("bump"), "해당 기간 집계가 아직 완료되지 않았습니다.",
                   cache.ranks?.meta.calculated_at
                     ? `최종 계산 ${new Date(cache.ranks.meta.calculated_at).toLocaleString("ko-KR")}` : "");
      banner(error.message, { retry: true });
      return;
    }
    banner(`${error.message} (${error.code})`, { kind: "error", retry: true });
    if (!cache.ranks) emptyMessage($("bump"), "데이터를 불러오지 못했습니다.");
  }
}

// ── 이벤트 연결 ───────────────────────────────────────────────────────────────

/** 같은 섹터를 다시 고르면 선택을 해제한다 (docs/06 §3.2) */
function pick(groupCode, periodId) {
  const current = state.read();
  const same = current.group === groupCode && (!periodId || current.pid === periodId || periodId === cache.calendar?.to);
  state.update({ group: same ? "" : groupCode, pid: periodId || current.pid });
}

function bind() {
  sel("universe").addEventListener("change", () =>
    state.update({ universe: sel("universe").value, scheme: "", group: "", pid: "" }));
  sel("scheme").addEventListener("change", () => state.update({ scheme: sel("scheme").value, group: "" }));
  sel("range").addEventListener("change", () => state.update({ range: sel("range").value, pid: "" }));
  sel("top").addEventListener("change", () => state.update({ top: sel("top").value }));
  sel("snapshot-sort").addEventListener("change", () => refresh(true));
  sel("event-type").addEventListener("change", () => state.update({ event_type: sel("event-type").value }));
  $("drilldown-close").addEventListener("click", () => state.update({ group: "" }));
  $("table-toggle").addEventListener("click", () =>
    state.update({ view: state.read().view === "table" ? "chart" : "table" }));
  $("snapshot").addEventListener("click", (e) => {
    const row = /** @type {HTMLElement} */ (e.target).closest("[data-group]");
    if (row) pick(/** @type {HTMLElement} */ (row).dataset.group);
  });
  $("snapshot").addEventListener("keydown", (e) => {
    const event = /** @type {KeyboardEvent} */ (e);
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = /** @type {HTMLElement} */ (event.target).closest("[data-group]");
    if (!row) return;
    event.preventDefault();
    pick(/** @type {HTMLElement} */ (row).dataset.group);
  });
  window.addEventListener("resize", () => drilldown.resize());
}

function syncControls(current) {
  segmented("period", current.period, (value) => state.update({ period: value, pid: "" }));
  segmented("chart-mode", current.mode, (value) => state.update({ mode: value }));
  sel("range").value = current.range;
  sel("top").value = current.top;
  sel("event-type").value = current.event_type;
}

theme.init(() => {
  bump.repaint();
  drilldown.repaint(cache.history);
});
bind();
state.onChange((current) => {
  syncControls(current);
  refresh();
});
syncControls(state.read());
refresh();
