// @ts-check
/** 화면 조립 (docs/06). 상태는 URL이 단일 출처이고, 상태가 바뀔 때마다 필요한 부분만 다시 그린다. */
import { ApiError, get } from "./api.js";
import * as bump from "./bump-chart.js";
import * as capChart from "./cap-chart.js";
import * as drilldown from "./drilldown.js";
import * as events from "./events.js";
import { escapeHtml } from "./format.js";
import * as ma from "./ma.js";
import * as snapshot from "./snapshot.js";
import * as state from "./state.js";
import * as theme from "./theme.js";

const NARROW = 768;                 // 이 아래에서는 표시 섹터 수를 상위 5로 강제한다 (docs/06 §8)
const MAX_PERIODS = { W: 260, M: 120 };
const HEIGHT_KEY = "theme-radar-bump-height";
const MIN_HEIGHT = 240;             // app.css의 .chart-scroll min-height와 같다
const MA_RANGE = [2, 52];           // 이동평균 기간 수로 고를 수 있는 범위 (docs/06 §5.4)
const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const sel = (id) => /** @type {HTMLSelectElement} */ (document.getElementById(id));

/** 마지막으로 성공한 응답. API 오류가 나도 화면을 비우지 않는다 (docs/06 §6) */
const cache = {
  universes: [], schemes: [], groups: new Map(), currency: "KRW",
  /** @type {any} */ calendar: null, /** @type {any} */ ranks: null, /** @type {any} */ history: null,
  /** @type {Map<string, any>|null} */ caps: null,     // 섹터별 이동평균·상승률·이격도 (ma.js build)
  capsError: "",
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

/**
 * 섹터 전체의 기간 말 시총을 받아 이동평균·상승률·이격도를 만든다 (docs/03 §14.3).
 * 순위 기준(이동평균)과 스냅샷의 이격도 열이 이 값을 쓴다. 못 받으면 두 자리만 비우고 나머지는 그대로 그린다.
 */
async function loadCaps(current) {
  try {
    const payload = await get("/sectors/market-caps", { universe: current.universe, scheme: current.scheme,
                                                        period: current.period, from: cache.calendar.extended[0].period_id,
                                                        to: cache.calendar.to });
    cache.caps = ma.build(payload, cache.calendar.extended, maLength(current.ma_n));
    cache.capsError = "";
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    cache.caps = null;
    cache.capsError = error.message;
  }
}

/** 이동평균 기준의 순위·상승률 (docs/06 §3.5). 값은 화면이 섹터 시총으로 계산한다 */
function maRanks(current, periodId) {
  const periods = cache.calendar.extended;
  const at = periods.findIndex((p) => p.period_id === periodId);
  return ma.toRanks(cache.caps, periods, cache.calendar.rows.length,
                    { topN: effectiveTop(current.top), above: Boolean(current.above),
                      at: at < 0 ? periods.length - 1 : at });
}

function drawBump(current, periodId) {
  const asTable = current.view === "table";
  $("bump-wrap").hidden = asTable;
  $("bump-table").hidden = !asTable;
  $("table-toggle").textContent = asTable ? "차트로 보기" : "표로 보기";
  if (current.basis === "ma" && !cache.caps) {
    bump.dispose();
    emptyMessage(asTable ? $("bump-table") : $("bump"), "섹터 시가총액이 아직 계산되지 않았습니다.", cache.capsError);
    $("bump-hint").textContent = "";
    return;
  }
  const payload = current.basis === "ma" ? maRanks(current, periodId) : cache.ranks;
  const length = maLength(current.ma_n);
  if (!payload.data.series.length) {
    // 이동평균 위 섹터가 하나도 없거나(전 섹터가 이동평균 아래), 상승률을 낼 앞 기간이 모자란 경우다
    bump.dispose();
    emptyMessage(asTable ? $("bump-table") : $("bump"),
                 current.above ? "이동평균 위에 있는 섹터가 없습니다." : "선택한 구간에 그릴 섹터가 없습니다.",
                 current.basis === "ma" && !current.above ? `${length}기간 이동평균을 낼 앞 기간이 모자랍니다.` : "");
    $("bump-hint").textContent = "";
    return;
  }
  if (asTable) $("bump-table").innerHTML = bump.tableHtml(payload, current.mode, current.basis);
  else bump.render($("bump"), payload, { mode: current.mode, basis: current.basis, maLength: length,
                                         currency: cache.currency, calendar: cache.calendar.map, onPick: pick });
  const others = payload.data.others_count;
  $("bump-hint").innerHTML = "선이나 점에 마우스를 올리면 그 기간의 값이 뜨고, 클릭하면 아래에 섹터 상세가 열린다."
    + " 차트 오른쪽 아래 모서리를 끌면 높이가 바뀐다."
    + (current.basis === "ma" ? ` <span class="muted">${length}기간 이동평균의 상승률이 기준이다.</span>` : "")
    + (others ? ` <span class="muted">표시 기준 밖 ${others}개 섹터는 감춰져 있다.</span>` : "");
}

/** 선택 기간의 섹터별 이동평균·이격도. 시총을 못 받았으면 null이고, 표는 그 칸을 비운다 */
function maPoints(current, periodId) {
  if (!cache.caps) return null;
  const index = cache.calendar.extended.findIndex((p) => p.period_id === periodId);
  if (index < 0) return null;
  return { length: maLength(current.ma_n), points: ma.pointsAt(cache.caps, index) };
}

async function drawSnapshot(current, periodId) {
  // 이격도는 서버에 없어(docs/03 §14.3) 정렬을 화면이 한다. API에는 기본 정렬을 받아 온다
  const sort = sel("snapshot-sort").value;
  const payload = await get("/sectors/returns", { universe: current.universe, scheme: current.scheme,
                                                  period: current.period, period_id: periodId,
                                                  sort: sort === "disparity" ? "return" : sort });
  $("snapshot-title").textContent = `${payload.meta.period_id} 섹터별${payload.meta.is_provisional ? " (잠정)" : ""}`;
  snapshot.render($("snapshot"), payload, current.group, maPoints(current, periodId), sort);
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
  await drawMarketCap(current, periodId);
}

/** @param {string} value */
function maLength(value) {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? Math.min(MA_RANGE[1], Math.max(MA_RANGE[0], n)) : Number(state.DEFAULTS.ma_n);
}

/** 이 섹터에 속한 종목의 특이사항 (docs/05 §6.3). 못 받아도 시가총액 차트는 그린다 */
async function groupEvents(current) {
  const rows = cache.calendar.rows;
  try {
    return (await get("/events", { universe: current.universe, group_code: current.group, sort: "date", limit: 500,
                                   from: rows[0].cal_start, to: rows[rows.length - 1].cal_end })).data;
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return [];
  }
}

/**
 * 섹터 시가총액 차트 (docs/06 §5.4). 이동평균은 화면이 계산하므로 조회 구간 앞 기간이 필요한데,
 * 달력을 받을 때 이미 붙여 뒀다. 데이터가 없으면 이 차트만 안내 문구로 바꾼다.
 */
async function drawMarketCap(current, periodId) {
  const container = $("cap-chart");
  const periods = cache.calendar.extended;
  const base = { universe: current.universe, period: current.period };
  try {
    const [payload, sectorEvents] = await Promise.all([
      get(`/sectors/${encodeURIComponent(current.group)}/market-cap`,
          { ...base, scheme: current.scheme, from: periods[0].period_id, to: cache.calendar.to }),
      groupEvents(current),
    ]);
    capChart.render(container, payload, { periods, visible: cache.calendar.rows.length, kind: current.cap,
                                          maLength: current.ma ? maLength(current.ma_n) : 0, selected: periodId,
                                          color: cache.groups.get(current.group)?.color, events: sectorEvents });
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    const text = error.status === 409 ? "섹터 시가총액이 아직 계산되지 않았습니다." : "섹터 시가총액을 불러오지 못했습니다.";
    capChart.message(container, `<p class="empty">${escapeHtml(text)}<br><span class="muted">${escapeHtml(error.message)}</span></p>`);
  }
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
  const reload = changed("universe", "scheme", "period", "range", "top", "ma_n") || !cache.ranks;

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
      const limit = MAX_PERIODS[current.period] || 260;
      const count = Math.min(Number(current.range) || 26, limit);
      // 이동평균은 화면이 계산한다. 구간 첫 기간부터 상승률이 있도록 앞 기간을 더 받아 둔다 (docs/03 §14.3)
      const extended = (await get("/meta/periods", { universe: current.universe, period: current.period,
                                                     last: Math.min(count + maLength(current.ma_n), limit) })).data;
      if (outdated()) return;
      if (!extended.length) {
        emptyMessage($("bump"), "선택한 구간에 계산된 데이터가 없습니다.");
        return;
      }
      const rows = extended.slice(-count);
      cache.calendar = { rows, extended, map: new Map(rows.map((r) => [r.period_id, r])),
                         from: rows[0].period_id, to: rows[rows.length - 1].period_id };
      cache.ranks = await get("/sectors/ranks", { universe: current.universe, scheme: current.scheme, period: current.period,
                                                  from: cache.calendar.from, to: cache.calendar.to,
                                                  top_n: effectiveTop(current.top) });
      if (outdated()) return;
      await loadCaps(current);
      if (outdated()) return;
      bump.hideLoading();
    }

    const periodId = cache.calendar.map.has(current.pid) ? current.pid : cache.calendar.to;
    const meta = cache.ranks.meta;
    $("calc-info").textContent = meta.calculated_at
      ? `· 계산 ${meta.calc_version} / ${new Date(meta.calculated_at).toLocaleString("ko-KR")}` : "";
    if (meta.stale) banner("데이터 재계산 중 — 값이 변경될 수 있습니다.");
    drawBump(current, periodId);

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

/** 차트 높이는 사용자가 끌어 정하고, 다음에 열 때도 유지한다 (docs/06 §3.2) */
function bindChartHeight() {
  const wrap = $("bump-wrap");
  try {
    const saved = Number(localStorage.getItem(HEIGHT_KEY));
    if (saved >= MIN_HEIGHT) wrap.style.height = `${saved}px`;
  } catch (error) { /* 저장소를 못 써도 기본 높이로 동작한다 */ }
  let pending = 0;
  let width = 0;
  new ResizeObserver(() => {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => {
      bump.repaint();            // 높이가 바뀌면 끝단 라벨을 다시 고른다
      // 폭이 같이 바뀌었으면 창 크기 변화다. 사용자가 끈 높이만 기억한다
      const dragged = width === wrap.clientWidth;
      width = wrap.clientWidth;
      try {
        if (dragged) localStorage.setItem(HEIGHT_KEY, String(Math.round(wrap.clientHeight)));
      } catch (error) { /* 저장 못 해도 화면은 동작한다 */ }
    });
  }).observe(wrap);
}

/** 스냅샷 머리글의 정렬 열. 컨트롤의 정렬 선택과 같은 값을 쓴다 (docs/06 §4) */
function sortSnapshot(target) {
  const cell = /** @type {HTMLElement|null} */ (target.closest("[data-sort]"));
  if (!cell) return false;
  sel("snapshot-sort").value = cell.dataset.sort;
  refresh(true);
  return true;
}

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
  $("ma-show").addEventListener("change", (e) =>
    state.update({ ma: /** @type {HTMLInputElement} */ (e.target).checked ? "1" : "" }));
  $("above-ma").addEventListener("change", (e) =>
    state.update({ above: /** @type {HTMLInputElement} */ (e.target).checked ? "1" : "" }));
  $("ma-len").addEventListener("change", (e) => {
    const input = /** @type {HTMLInputElement} */ (e.target);
    input.value = String(maLength(input.value));
    state.update({ ma_n: input.value });
  });
  $("table-toggle").addEventListener("click", () =>
    state.update({ view: state.read().view === "table" ? "chart" : "table" }));
  $("snapshot").addEventListener("click", (e) => {
    const target = /** @type {HTMLElement} */ (e.target);
    if (sortSnapshot(target)) return;
    const row = target.closest("[data-group]");
    if (row) pick(/** @type {HTMLElement} */ (row).dataset.group);
  });
  $("snapshot").addEventListener("keydown", (e) => {
    const event = /** @type {KeyboardEvent} */ (e);
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = /** @type {HTMLElement} */ (event.target);
    const row = target.closest("[data-sort], [data-group]");
    if (!row) return;
    event.preventDefault();
    if (sortSnapshot(target)) return;
    pick(/** @type {HTMLElement} */ (row).dataset.group);
  });
  bindChartHeight();
  window.addEventListener("resize", () => {
    drilldown.resize();
    capChart.resize();
  });
}

function syncControls(current) {
  segmented("period", current.period, (value) => state.update({ period: value, pid: "" }));
  segmented("chart-mode", current.mode, (value) => state.update({ mode: value }));
  segmented("chart-basis", current.basis, (value) => state.update({ basis: value }));
  segmented("cap-kind", current.cap, (value) => state.update({ cap: value }));
  // 이동평균 기준에서는 값이 기간 수익률이 아니라 이동평균의 상승률이다
  $("chart-mode").querySelector('[data-value="return"]').textContent = current.basis === "ma" ? "상승률" : "수익률";
  $("above-filter").hidden = current.basis !== "ma";
  /** @type {HTMLInputElement} */ ($("above-ma")).checked = Boolean(current.above);
  const maShow = /** @type {HTMLInputElement} */ ($("ma-show"));
  const maLen = /** @type {HTMLInputElement} */ ($("ma-len"));
  maShow.checked = Boolean(current.ma);
  maLen.value = String(maLength(current.ma_n));
  sel("range").value = current.range;
  sel("top").value = current.top;
  sel("event-type").value = current.event_type;
}

theme.init(() => {
  bump.repaint();
  drilldown.repaint(cache.history);
  capChart.repaint();
});
bind();
state.onChange((current) => {
  syncControls(current);
  refresh();
});
syncControls(state.read());
refresh();
