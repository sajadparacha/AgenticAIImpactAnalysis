document.documentElement.classList.add("js");

document.querySelectorAll("[data-demo-package]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const textarea = document.querySelector("#packages");
    if (!textarea) return;
    const name = chip.dataset.demoPackage || "";
    const current = textarea.value.split("\n").map((line) => line.trim()).filter(Boolean);
    if (!current.includes(name)) current.push(name);
    textarea.value = current.join("\n");
    textarea.focus();
  });
});

const showAllRuns = document.querySelector("[data-show-all-runs]");
if (showAllRuns) {
  const table = document.querySelector("[data-runs-table]");
  const extras = table ? [...table.querySelectorAll("tr.is-extra")] : [];
  let expanded = false;
  const render = () => {
    extras.forEach((row) => (row.hidden = !expanded));
    showAllRuns.textContent = expanded ? "Show fewer" : `Show all ${extras.length + 10} runs`;
  };
  render();
  showAllRuns.hidden = false;
  showAllRuns.addEventListener("click", () => {
    expanded = !expanded;
    render();
  });
}

const navToggle = document.querySelector("[data-nav-toggle]");
const siteNav = document.querySelector("[data-site-nav]");
if (navToggle && siteNav) {
  const setNavOpen = (open) => {
    navToggle.setAttribute("aria-expanded", String(open));
    siteNav.classList.toggle("is-open", open);
    navToggle.classList.toggle("is-open", open);
  };
  navToggle.addEventListener("click", () => setNavOpen(navToggle.getAttribute("aria-expanded") !== "true"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && navToggle.getAttribute("aria-expanded") === "true") {
      setNavOpen(false);
      navToggle.focus();
    }
  });
}

const defaultStageOrder = ["queued", "snapshotting", "comparing", "dependencies", "investigating", "validating", "completed"];
const defaultStageLabels = {
  queued: "Queued",
  snapshotting: "Source snapshot",
  comparing: "Code comparison",
  dependencies: "Dependencies",
  investigating: "AI investigation",
  validating: "Recommendation",
  completed: "Complete",
  failed: "Failed",
};

// The page lists stages in run order; agentic runs investigate before the snapshot.
const pageStages = [...document.querySelectorAll("[data-stage]")];
const stageOrder = pageStages.length ? pageStages.map((item) => item.dataset.stage) : defaultStageOrder;
const stageLabels = { ...defaultStageLabels };
pageStages.forEach((item) => {
  stageLabels[item.dataset.stage] = item.textContent.trim();
});

function paintProgress(payload) {
  const current = payload.status || "";
  const currentIndex = stageOrder.indexOf(current);
  document.querySelectorAll("[data-stage]").forEach((item) => {
    const index = stageOrder.indexOf(item.dataset.stage);
    item.classList.toggle("done", current === "completed" || (currentIndex > index && index >= 0));
    item.classList.toggle("current", item.dataset.stage === current && current !== "completed" && current !== "failed");
  });
  const label = document.querySelector("[data-status-label]");
  if (label) {
    label.textContent = payload.detail || stageLabels[current] || current;
  }
  renderSteps(payload);
  renderActivity(payload);
  const spinner = document.querySelector("[data-spinner]");
  if (spinner) {
    const running = current !== "completed" && current !== "failed";
    spinner.classList.toggle("hidden", !running || current === "investigating");
  }
  const badge = document.querySelector(".page-intro .badge");
  if (badge && current) {
    badge.textContent = current;
    badge.className = "badge badge-" + current;
  }
}

function renderActivity(payload) {
  const panel = document.querySelector("[data-model-activity]");
  if (!panel) {
    return;
  }
  const items = payload.activity || [];
  const investigating = payload.status === "investigating";
  panel.classList.toggle("hidden", !investigating && items.length === 0);
  const thought = [...items].reverse().find((item) => item.kind === "thought" || item.kind === "action" || item.kind === "status");
  const thoughtNode = panel.querySelector("[data-model-thought]");
  if (thoughtNode) {
    thoughtNode.textContent = thought ? thought.text : "The investigator is deciding what to read.";
  }
  const list = panel.querySelector("[data-model-actions]");
  if (list) {
    list.replaceChildren();
    for (const item of items.filter((entry) => entry.kind === "action")) {
      const row = document.createElement("li");
      row.textContent = item.text;
      list.append(row);
    }
  }
  const thinking = panel.querySelector("[data-model-thinking]");
  if (thinking) {
    thinking.classList.toggle("hidden", !investigating);
  }
}

function renderSteps(payload) {
  const container = document.querySelector("[data-progress-steps]");
  if (!container || !payload.steps) {
    return;
  }
  const current = payload.status || "";
  const steps = payload.steps || {};
  const open = new Set([...container.querySelectorAll("details[open]")].map((item) => item.dataset.step));
  const seen = new Set([...container.querySelectorAll("details")].map((item) => item.dataset.step));
  container.replaceChildren();
  stageOrder.forEach((stage) => {
    const step = steps[stage];
    if (!step) {
      return;
    }
    const lines = step.lines && step.lines.length ? step.lines : ["No additional detail was recorded."];
    const details = document.createElement("details");
    details.className = "step";
    details.dataset.step = stage;
    const isCurrent = stage === current && current !== "completed" && current !== "failed";
    if (open.has(stage) || (isCurrent && !seen.has(stage))) {
      details.open = true;
    }
    const summary = document.createElement("summary");
    const name = document.createElement("span");
    name.textContent = stageLabels[stage] || stage;
    summary.append(name, document.createTextNode(step.summary || ""));
    const list = document.createElement("ul");
    for (const line of lines) {
      const item = document.createElement("li");
      item.textContent = line;
      list.append(item);
    }
    details.append(summary, list);
    container.append(details);
  });
}

const root = document.querySelector("[data-poll-status]");
if (root) {
  paintProgress({
    status: root.dataset.current,
    detail: document.querySelector("[data-status-label]")?.textContent || "",
  });
  if (root.dataset.terminal === "no") {
    const tick = async () => {
      const response = await fetch(root.dataset.pollStatus, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        window.setTimeout(tick, 1200);
        return;
      }
      const payload = await response.json();
      paintProgress(payload);
      if (payload.status === "completed") {
        window.location.assign(root.dataset.reportUrl);
        return;
      }
      if (payload.status === "failed") {
        window.location.reload();
        return;
      }
      window.setTimeout(tick, 1200);
    };
    window.setTimeout(tick, 1200);
  }
}

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const block = button.closest(".sql-snippet");
    const pre = button.dataset.copy ? document.querySelector(button.dataset.copy) : block ? block.querySelector("pre") : null;
    const text = pre ? pre.textContent : "";
    if (pre) {
      const range = document.createRange();
      range.selectNodeContents(pre);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch (error) {
      button.textContent = "Selected";
    }
  });
});

const reportRoot = document.querySelector("[data-report]");
if (reportRoot && !reportRoot.classList.contains("is-print")) {
  initReport(reportRoot);
}

function initReport(root) {
  const PAGE = 50;
  const FILTER_KEYS = ["q", "sev", "type", "cat", "src", "ev", "ec", "blk", "env", "chg", "drivers"];
  const SELECT_KEYS = ["sev", "type", "cat", "src", "ev", "ec", "blk", "env", "chg"];
  const DEP_KEYS = ["dq", "dir", "impact"];
  const EDGE_KEYS = ["eq", "edir", "eprov", "etype"];
  const LEGACY = { comparison: "source", changes: "findings", dependencies: "dependencies", ai: "validation", risk: "overview", limitations: "validation", validation: "validation", audit: "audit", overview: "overview",
    objects: "objects", compilation: "objects", transactions: "objects", coverage: "validation", approval: "validation",
    "policy-items": "validation", "regression-tests": "validation", performance: "validation", history: "audit" };
  const EVIDENCE_ORDER = { confirmed: 0, potential: 1, unknown: 2 };
  const base = root.dataset.base;
  const tabs = [...root.querySelectorAll("[data-tab-link]")];
  const panels = new Map([...root.querySelectorAll("[data-panel]")].map((panel) => [panel.dataset.panel, panel]));
  const drawer = root.querySelector("[data-drawer]");
  const backdrop = root.querySelector("[data-drawer-backdrop]");
  const tbody = root.querySelector("[data-findings-table] tbody");
  const rows = tbody ? [...tbody.querySelectorAll("[data-row]")] : [];
  const depRows = [...root.querySelectorAll("[data-dep]")];
  const edgeRows = [...root.querySelectorAll("[data-edge]")];
  const highlighted = new WeakSet();
  const state = {
    tab: root.dataset.tab || "overview",
    finding: root.dataset.finding || "",
    object: root.dataset.object || "",
    line: root.dataset.line || "",
    side: root.dataset.side || "t",
    filters: {},
    dep: {},
    edge: {},
    sort: { key: "rank", dir: 1 },
    limit: PAGE,
  };
  let lastTrigger = null;

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    state.tab = panels.has(params.get("tab")) ? params.get("tab") : state.tab;
    state.finding = params.get("finding") || "";
    state.object = params.get("object") || state.object;
    state.line = params.get("line") || "";
    state.side = params.get("side") === "p" ? "p" : "t";
    FILTER_KEYS.forEach((key) => { state.filters[key] = params.get(key) || ""; });
    DEP_KEYS.forEach((key) => { state.dep[key] = params.get(key) || ""; });
    EDGE_KEYS.forEach((key) => { state.edge[key] = params.get(key) || ""; });
    const sort = params.get("sort");
    if (sort) {
      state.sort = { key: sort.replace(/^-/, ""), dir: sort.startsWith("-") ? -1 : 1 };
    }
  }

  function writeUrl(push) {
    const params = new URLSearchParams();
    if (state.tab !== "overview") params.set("tab", state.tab);
    if (state.finding) params.set("finding", state.finding);
    if (state.tab === "source" && state.object) {
      params.set("object", state.object);
      if (state.line) {
        params.set("line", state.line);
        params.set("side", state.side);
      }
    }
    FILTER_KEYS.forEach((key) => { if (state.filters[key]) params.set(key, state.filters[key]); });
    DEP_KEYS.forEach((key) => { if (state.dep[key]) params.set(key, state.dep[key]); });
    EDGE_KEYS.forEach((key) => { if (state.edge[key]) params.set(key, state.edge[key]); });
    if (state.sort.key !== "rank" || state.sort.dir !== 1) params.set("sort", (state.sort.dir < 0 ? "-" : "") + state.sort.key);
    const query = params.toString();
    const url = base + (query ? "?" + query : "");
    if (url === window.location.pathname + window.location.search) return;
    window.history[push ? "pushState" : "replaceState"]({}, "", url);
  }

  function showTab(key, options = {}) {
    if (!panels.has(key)) return;
    state.tab = key;
    tabs.forEach((tab) => {
      const active = tab.dataset.tabLink === key;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel, name) => { panel.hidden = name !== key; });
    if (key === "source") showObject(state.object, { line: state.line, side: state.side, quiet: true });
    if (options.focus) panels.get(key).focus({ preventScroll: true });
    if (options.write !== false) writeUrl(Boolean(options.push));
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      state.line = "";
      showTab(tab.dataset.tabLink, { push: true });
    });
    tab.addEventListener("keydown", (event) => {
      const moves = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 };
      if (!(event.key in moves)) return;
      event.preventDefault();
      const next = tabs[(moves[event.key] + tabs.length) % tabs.length];
      next.focus();
      showTab(next.dataset.tabLink, { push: true });
    });
  });

  function rowMatches(row) {
    const f = state.filters;
    if (f.q && !row.dataset.search.includes(f.q.toLowerCase())) return false;
    for (const key of SELECT_KEYS) {
      if (f[key] && row.dataset[key] !== f[key]) return false;
    }
    if (f.drivers && !row.dataset.drivers) return false;
    return true;
  }

  function sortValue(row) {
    switch (state.sort.key) {
      case "id": return row.dataset.id;
      case "object": return row.dataset.object + " " + row.dataset.rank.padStart(5, "0");
      case "evidence": return String(EVIDENCE_ORDER[row.dataset.ev] ?? 9) + row.dataset.rank.padStart(5, "0");
      default: return row.dataset.rank.padStart(5, "0");
    }
  }

  function applyFindings() {
    if (!tbody) return;
    const sorted = [...rows].sort((a, b) => (sortValue(a) < sortValue(b) ? -1 : sortValue(a) > sortValue(b) ? 1 : 0) * state.sort.dir);
    sorted.forEach((row) => tbody.append(row));
    let matched = 0;
    sorted.forEach((row) => {
      const ok = rowMatches(row);
      if (ok) matched += 1;
      row.hidden = !ok || matched > state.limit;
    });
    const shown = Math.min(matched, state.limit);
    const count = root.querySelector("[data-result-count]");
    if (count) count.textContent = `Showing ${shown} of ${matched} matching findings (${rows.length} total)`;
    const empty = root.querySelector("[data-no-results]");
    if (empty) empty.hidden = matched > 0;
    const pager = root.querySelector("[data-pager]");
    if (pager) {
      pager.hidden = matched <= state.limit;
      const more = pager.querySelector("[data-page-more]");
      if (more) more.textContent = `Show ${Math.min(PAGE, matched - state.limit)} more`;
    }
    root.querySelectorAll("[data-sort]").forEach((button) => {
      const th = button.closest("th");
      if (button.dataset.sort === state.sort.key) th.setAttribute("aria-sort", state.sort.dir > 0 ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    });
  }

  function syncFilterInputs() {
    root.querySelectorAll("[data-filter]").forEach((input) => {
      const value = state.filters[input.dataset.filter] || "";
      if (input.type === "checkbox") input.checked = Boolean(value);
      else input.value = value;
    });
    root.querySelectorAll("[data-dep-filter]").forEach((input) => {
      input.value = state.dep[input.dataset.depFilter === "q" ? "dq" : input.dataset.depFilter] || "";
    });
    root.querySelectorAll("[data-edge-filter]").forEach((input) => {
      input.value = state.edge[input.dataset.edgeFilter] || "";
    });
  }

  function applyEdges() {
    if (!edgeRows.length) return;
    let shown = 0;
    edgeRows.forEach((row) => {
      const ok = (!state.edge.eq || row.dataset.search.includes(state.edge.eq.toLowerCase()))
        && (!state.edge.edir || row.dataset.dir === state.edge.edir)
        && (!state.edge.eprov || row.dataset.prov === state.edge.eprov)
        && (!state.edge.etype || row.dataset.type === state.edge.etype);
      row.hidden = !ok;
      if (ok) shown += 1;
    });
    const count = root.querySelector("[data-edge-count]");
    if (count) count.textContent = `Showing ${shown} of ${edgeRows.length} edges`;
    const empty = root.querySelector("[data-edge-empty]");
    if (empty) empty.hidden = shown > 0;
  }
  root.querySelectorAll("[data-edge-filter]").forEach((input) => {
    input.addEventListener(input.type === "search" ? "input" : "change", () => {
      state.edge[input.dataset.edgeFilter] = input.value.trim();
      applyEdges();
      writeUrl(false);
    });
  });

  root.querySelectorAll("[data-filter]").forEach((input) => {
    const handler = () => {
      state.filters[input.dataset.filter] = input.type === "checkbox" ? (input.checked ? "1" : "") : input.value.trim();
      state.limit = PAGE;
      applyFindings();
      writeUrl(false);
    };
    input.addEventListener(input.type === "search" ? "input" : "change", handler);
  });
  root.querySelectorAll("[data-filter-reset]").forEach((button) => {
    button.addEventListener("click", () => {
      FILTER_KEYS.forEach((key) => { state.filters[key] = ""; });
      state.limit = PAGE;
      syncFilterInputs();
      applyFindings();
      writeUrl(false);
    });
  });
  root.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
      applyFindings();
      writeUrl(false);
    });
  });
  const more = root.querySelector("[data-page-more]");
  if (more) {
    more.addEventListener("click", () => {
      state.limit += PAGE;
      applyFindings();
    });
  }
  root.querySelectorAll("[data-filter-drivers]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      FILTER_KEYS.forEach((key) => { state.filters[key] = ""; });
      state.filters.drivers = "1";
      syncFilterInputs();
      applyFindings();
      showTab("findings", { push: true });
    });
  });

  function focusables() {
    return [...drawer.querySelectorAll("a[href], button:not([disabled]), input:not([type='hidden']):not([disabled]), select, textarea, summary, [tabindex='0']")]
      .filter((node) => !node.closest("[hidden]") && !(node.closest("details:not([open])") && node.tagName !== "SUMMARY"));
  }

  function openFinding(id, options = {}) {
    if (!drawer) return;
    const detail = drawer.querySelector(`[data-detail="${CSS.escape(id)}"]`);
    if (!detail) return;
    drawer.querySelectorAll("[data-detail]").forEach((item) => { item.hidden = item !== detail; });
    drawer.hidden = false;
    if (backdrop) backdrop.hidden = false;
    document.body.style.overflow = "hidden";
    rows.forEach((row) => row.classList.toggle("is-selected", row.dataset.id === id));
    state.finding = id;
    if (options.trigger) lastTrigger = options.trigger;
    const heading = detail.querySelector("h3");
    if (heading) heading.focus();
    drawer.querySelector(".drawer-body").scrollTop = 0;
    if (options.write !== false) writeUrl(Boolean(options.push));
  }

  function closeFinding(options = {}) {
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    if (backdrop) backdrop.hidden = true;
    document.body.style.overflow = "";
    const closed = state.finding;
    state.finding = "";
    if (options.write !== false) writeUrl(Boolean(options.push));
    if (options.restoreFocus !== false) {
      const target = lastTrigger && document.contains(lastTrigger) && !lastTrigger.closest("[hidden]")
        ? lastTrigger
        : root.querySelector(`[data-row][data-id="${CSS.escape(closed)}"] [data-open-finding]`);
      if (target) target.focus();
    }
  }

  if (drawer) {
    drawer.querySelector("[data-drawer-close]").addEventListener("click", () => closeFinding({ push: true }));
    if (backdrop) backdrop.addEventListener("click", () => closeFinding({ push: true }));
    drawer.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeFinding({ push: true });
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  root.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-open-finding]");
    if (opener && root.contains(opener)) {
      event.preventDefault();
      openFinding(opener.dataset.openFinding, { push: true, trigger: opener });
      return;
    }
    const lineLink = event.target.closest("[data-goto-line]");
    if (lineLink) {
      event.preventDefault();
      const [key, side, line] = lineLink.dataset.gotoLine.split("|");
      closeFinding({ write: false, restoreFocus: false });
      state.object = key;
      state.line = line || "";
      state.side = side === "p" ? "p" : "t";
      showTab("source", { push: true });
      return;
    }
    const objectLink = event.target.closest("[data-open-object]");
    if (objectLink) {
      event.preventDefault();
      state.object = objectLink.dataset.openObject;
      state.line = "";
      showTab("source", { push: true });
      return;
    }
    const depLink = event.target.closest("[data-goto-dep]");
    if (depLink) {
      event.preventDefault();
      closeFinding({ write: false, restoreFocus: false });
      showTab("dependencies", { push: true });
      focusDependency(depLink.dataset.gotoDep);
      return;
    }
    const tabLink = event.target.closest("[data-goto-tab]");
    if (tabLink && !tabLink.hasAttribute("data-filter-drivers")) {
      event.preventDefault();
      showTab(tabLink.dataset.gotoTab, { push: true, focus: true });
      panels.get(tabLink.dataset.gotoTab).scrollIntoView({ block: "start" });
    }
  });

  function showObject(key, options = {}) {
    const views = [...root.querySelectorAll("[data-diff-view]")];
    if (!views.length) return;
    const view = views.find((item) => item.dataset.key === key) || views[0];
    state.object = view.dataset.key;
    views.forEach((item) => { item.hidden = item !== view; });
    root.querySelectorAll("[data-object-item]").forEach((item) => {
      const link = item.querySelector("a");
      const current = item.dataset.key === state.object;
      if (current) {
        link.setAttribute("aria-current", "true");
        item.hidden = false;
      } else {
        link.removeAttribute("aria-current");
      }
    });
    highlight(view);
    root.querySelectorAll("tr.is-target").forEach((row) => row.classList.remove("is-target"));
    if (options.line) {
      const side = options.side === "p" ? "p" : "t";
      const target = side === "t"
        ? view.querySelector(`tr[data-t="${CSS.escape(String(options.line))}"]`)
        : view.querySelector(`tr[data-p="${CSS.escape(String(options.line))}"]`);
      if (target) {
        if (target.classList.contains("far")) setDiffMode(view, "full");
        target.classList.add("is-target");
        window.requestAnimationFrame(() => target.scrollIntoView({ block: "center" }));
      }
    } else if (!options.quiet) {
      view.scrollIntoView({ block: "start" });
    }
  }

  function setDiffMode(view, mode) {
    const table = view.querySelector("[data-diff-table]");
    if (table) table.classList.toggle("changes-only", mode === "changes");
    view.querySelectorAll("[data-diff-mode]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.diffMode === mode));
    });
  }

  root.querySelectorAll("[data-diff-view]").forEach((view) => {
    view.querySelectorAll("[data-diff-mode]").forEach((button) => {
      button.addEventListener("click", () => setDiffMode(view, button.dataset.diffMode));
    });
    let index = -1;
    const step = (delta) => {
      const hunks = [...view.querySelectorAll("tr.hunk")];
      if (!hunks.length) return;
      index = (index + delta + hunks.length) % hunks.length;
      view.querySelectorAll("tr.is-target").forEach((row) => row.classList.remove("is-target"));
      hunks[index].classList.add("is-target");
      hunks[index].scrollIntoView({ block: "center" });
    };
    const next = view.querySelector("[data-next-change]");
    const prev = view.querySelector("[data-prev-change]");
    if (next) next.addEventListener("click", () => step(1));
    if (prev) prev.addEventListener("click", () => step(-1));
  });

  const objectSearch = root.querySelector("[data-object-search]");
  const objectChanged = root.querySelector("[data-object-changed]");
  function filterObjects() {
    const query = objectSearch ? objectSearch.value.trim().toLowerCase() : "";
    const changedOnly = objectChanged ? objectChanged.checked : false;
    let visible = 0;
    root.querySelectorAll("[data-object-item]").forEach((item) => {
      const ok = (!query || item.dataset.search.includes(query)) && (!changedOnly || item.dataset.state !== "unchanged");
      item.hidden = !ok && item.dataset.key !== state.object;
      if (!item.hidden) visible += 1;
    });
    const empty = root.querySelector("[data-object-empty]");
    if (empty) empty.hidden = visible > 0;
  }
  if (objectSearch) objectSearch.addEventListener("input", filterObjects);
  if (objectChanged) objectChanged.addEventListener("change", filterObjects);

  function applyDependencies() {
    if (!depRows.length) return;
    let shown = 0;
    depRows.forEach((row) => {
      const ok = (!state.dep.dq || row.dataset.search.includes(state.dep.dq.toLowerCase()))
        && (!state.dep.dir || row.dataset.dir === state.dep.dir)
        && (!state.dep.impact || row.dataset.impact === state.dep.impact);
      row.hidden = !ok;
      if (ok) shown += 1;
    });
    const count = root.querySelector("[data-dep-count]");
    if (count) count.textContent = `Showing ${shown} of ${depRows.length} edges`;
    const empty = root.querySelector("[data-dep-empty]");
    if (empty) empty.hidden = shown > 0;
  }
  root.querySelectorAll("[data-dep-filter]").forEach((input) => {
    input.addEventListener(input.type === "search" ? "input" : "change", () => {
      state.dep[input.dataset.depFilter === "q" ? "dq" : input.dataset.depFilter] = input.value.trim();
      applyDependencies();
      writeUrl(false);
    });
  });

  function focusDependency(id) {
    const row = document.getElementById(id);
    if (!row) return;
    DEP_KEYS.forEach((key) => { state.dep[key] = ""; });
    syncFilterInputs();
    applyDependencies();
    depRows.forEach((item) => item.classList.toggle("is-target", item === row));
    row.tabIndex = -1;
    row.focus({ preventScroll: true });
    row.scrollIntoView({ block: "center" });
  }

  const KEYWORDS = new Set(("ALTER AND AS AUTONOMOUS_TRANSACTION BEGIN BETWEEN BODY BOOLEAN BULK BY CASE CLOSE COLLECT COMMIT CONSTANT CREATE CURSOR " +
    "DATE DECLARE DEFAULT DELETE DESC DISTINCT ELSE ELSIF END EXCEPTION EXECUTE EXISTS EXIT FETCH FOR FORALL FROM FUNCTION GROUP " +
    "HAVING IF IMMEDIATE IN INDEX INSERT INTO IS JOIN LIKE LOOP MERGE NOT NULL NUMBER OF ON OPEN OR ORDER OTHERS OUT PACKAGE " +
    "PRAGMA PROCEDURE RAISE RAISE_APPLICATION_ERROR RECORD REPLACE RETURN RETURNING ROLLBACK ROWTYPE SAVEPOINT SELECT SET " +
    "TABLE THEN TO TRIGGER TYPE UPDATE USING VALUES VARCHAR2 WHEN WHERE WHILE WITH").split(" "));
  const TOKEN = /(--.*$)|(\/\*.*?(?:\*\/|$))|('(?:[^']|'')*'?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$#]*)/g;

  function paint(code) {
    const text = code.textContent;
    if (!text || !/[A-Za-z0-9'-]/.test(text)) return;
    const fragment = document.createDocumentFragment();
    let last = 0;
    let match;
    let changed = false;
    TOKEN.lastIndex = 0;
    while ((match = TOKEN.exec(text))) {
      let cls = "";
      if (match[1] || match[2]) cls = "tok-com";
      else if (match[3]) cls = "tok-str";
      else if (match[4]) cls = "tok-num";
      else if (match[5] && KEYWORDS.has(match[5].toUpperCase())) cls = "tok-kw";
      if (!cls) continue;
      if (match.index > last) fragment.append(text.slice(last, match.index));
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = match[0];
      fragment.append(span);
      last = match.index + match[0].length;
      changed = true;
    }
    if (!changed) return;
    if (last < text.length) fragment.append(text.slice(last));
    code.replaceChildren(fragment);
  }

  function highlight(view) {
    if (highlighted.has(view)) return;
    highlighted.add(view);
    const cells = [...view.querySelectorAll("td.code code")];
    let position = 0;
    const chunk = () => {
      const end = Math.min(position + 300, cells.length);
      for (; position < end; position += 1) paint(cells[position]);
      if (position < cells.length) window.setTimeout(chunk, 0);
    };
    chunk();
  }

  function applyAll() {
    syncFilterInputs();
    applyFindings();
    applyDependencies();
    applyEdges();
    filterObjects();
    showTab(state.tab, { write: false });
    if (state.finding) openFinding(state.finding, { write: false });
    else closeFinding({ write: false, restoreFocus: false });
  }

  window.addEventListener("popstate", () => {
    readUrl();
    applyAll();
  });

  readUrl();
  const params = new URLSearchParams(window.location.search);
  const hash = window.location.hash.slice(1);
  if (hash && !params.get("tab")) {
    if (hash.startsWith("issue-")) {
      state.tab = "findings";
    } else if (hash.startsWith("dep-")) {
      state.tab = "dependencies";
    } else if (LEGACY[hash]) {
      state.tab = LEGACY[hash];
    }
  }
  applyAll();
  if (hash) {
    const target = document.getElementById(hash);
    if (target && hash.startsWith("dep-")) focusDependency(hash);
    else if (target) target.scrollIntoView({ block: "start" });
  }
}
