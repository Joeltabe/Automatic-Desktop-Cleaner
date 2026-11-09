/* Free-up-space screens: Space (junk analysis), Storage map (treemap) and Duplicates.
   Uses the helpers app.js publishes on window.DC. */
(() => {
  "use strict";
  const { $, $$, el, humanSize, baseName, dirName, timeAgo, debounce, cssVar, toast, confirmDialog, call } = window.DC;
  const DC = window.DC;
  // replaceChildren renders a literal "null" for null children; this drops them.
  const put = (node, ...children) => node.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));

  const ICONS = {
    temp: '<svg viewBox="0 0 24 24"><path d="M12 3v5M8 21h8M12 8a6 6 0 0 1 6 6c0 3-2 5-6 7-4-2-6-4-6-7a6 6 0 0 1 6-6z"/></svg>',
    browser: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
    apps: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></svg>',
    thumbs: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-8 8"/></svg>',
    crash: '<svg viewBox="0 0 24 24"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    package: '<svg viewBox="0 0 24 24"><path d="M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></svg>',
    empty: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    recycle: '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/><path d="M10 11v5M14 11v5"/></svg>',
    projects: '<svg viewBox="0 0 24 24"><path d="M8 9l-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"/></svg>',
    deps: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
    installers: '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
    downloads: '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/><path d="M4 9h16M9 9v11"/></svg>',
    system: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
    files: '<svg viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
  };
  const PALETTE = ["#6c8cff", "#a56cff", "#ff6c9f", "#3ddc84", "#ffb347", "#38bdf8", "#f472b6", "#34d399", "#fbbf24", "#e879f9", "#94a3b8", "#fb7185"];
  const colorFor = (name) => { let h = 0; for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return PALETTE[h % PALETTE.length]; };
  const pct = (part, whole) => (whole ? Math.round((part / whole) * 1000) / 10 : 0);
  const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
  const svgBtn = (title, path, onclick, cls = "") => el("button", { class: `icon-btn ${cls}`, title, onclick: (e) => { e.stopPropagation(); onclick(); }, html: `<svg viewBox="0 0 24 24">${path}</svg>` });
  const REVEAL = '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>';
  const TRASH = '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>';

  // ================================================================== SPACE
  const S = { analysis: null, volumes: [], selected: new Map(), running: false, open: new Set() };

  function heroIdle() {
    $("#heroLabel").textContent = "Ready when you are";
    $("#heroValue").textContent = "—";
    $("#heroSub").textContent = "Press Analyse to look for space you can get back.";
    $("#heroSide").hidden = true; $("#heroProgress").hidden = true;
  }
  function heroResult(a) {
    $("#heroLabel").textContent = a.reclaimable_bytes ? "You could free" : "Looking good";
    $("#heroValue").textContent = humanSize(a.reclaimable_bytes);
    const safe = a.groups.filter((g) => g.confidence === "safe").length;
    $("#heroSub").textContent = a.reclaimable_bytes
      ? `${humanSize(a.safe_bytes)} is regenerated automatically and can go right now. The rest is worth a quick look. Analysed in ${Math.round(a.duration)}s.`
      : "Nothing removable turned up. Your machine is already tidy.";
    $("#heroSafe").textContent = humanSize(a.safe_bytes); $("#heroReview").textContent = humanSize(a.review_bytes); $("#heroManual").textContent = humanSize(a.manual_bytes);
    $("#heroSide").hidden = false; $("#heroProgress").hidden = true;
    void safe;
  }

  function defaultSelection(a) {
    S.selected = new Map();
    a.groups.forEach((g) => { if (g.confidence === "safe") S.selected.set(g.id, "all"); });
  }
  function selectRecommended() {
    const a = S.analysis; if (!a) return;
    S.selected = new Map();
    a.groups.forEach((g) => {
      if (g.confidence === "safe") S.selected.set(g.id, "all");
      else if (g.id === "installers") S.selected.set(g.id, "all");
      else if (g.id === "projects") { const stale = g.items.filter((i) => i.stale).map((i) => i.path); if (stale.length) S.selected.set(g.id, new Set(stale)); }
    });
    renderGroups();
  }
  function isSelected(g, item) { const sel = S.selected.get(g.id); return sel === "all" || (sel instanceof Set && sel.has(item.path)); }
  function groupSelectedBytes(g) {
    const sel = S.selected.get(g.id); if (!sel) return 0;
    if (sel === "all") return g.size;
    return g.items.filter((i) => sel.has(i.path)).reduce((a, i) => a + i.size, 0);
  }
  function groupSelectedCount(g) {
    const sel = S.selected.get(g.id); if (!sel) return 0;
    return sel === "all" ? g.items.length + (g.more || 0) : sel.size;
  }
  function toggleGroup(g, on) {
    if (on) S.selected.set(g.id, "all"); else S.selected.delete(g.id);
    renderGroups();
  }
  function toggleItem(g, item, on) {
    let sel = S.selected.get(g.id);
    if (sel === "all") sel = new Set(g.items.map((i) => i.path));
    if (!(sel instanceof Set)) sel = new Set();
    if (on) sel.add(item.path); else sel.delete(item.path);
    if (sel.size === 0) S.selected.delete(g.id);
    else if (sel.size === g.items.length && !g.more) S.selected.set(g.id, "all");
    else S.selected.set(g.id, sel);
    renderGroups();
  }

  function renderGroups() {
    const a = S.analysis; const wrap = $("#junkGroups");
    if (!a) { put(wrap); $("#spaceToolbar").hidden = true; $("#spaceActionBar").hidden = true; $("#spaceEmpty").hidden = true; return; }
    const removable = a.groups.filter((g) => g.removable);
    $("#spaceEmpty").hidden = !!a.groups.length;
    $("#spaceToolbar").hidden = !a.groups.length;
    $("#spaceMeta").textContent = `${plural(a.groups.length, "group")} · ${a.groups.reduce((s, g) => s + g.count, 0).toLocaleString()} items`;
    put(wrap, ...a.groups.map(renderGroup));
    const bytes = removable.reduce((s, g) => s + groupSelectedBytes(g), 0);
    const count = removable.reduce((s, g) => s + groupSelectedCount(g), 0);
    const bar = $("#spaceActionBar"); bar.hidden = !removable.length;
    $("#abValue").textContent = humanSize(bytes);
    $("#abSub").textContent = count ? `${plural(count, "item")} selected` : "nothing selected";
    $("#cleanBtn").disabled = !count || S.running;
    $("#cleanBtnLabel").textContent = count ? `Free up ${humanSize(bytes)}` : "Free up space";
    $("#permanentToggle").checked = !!(DC.state && DC.state.config && DC.state.config.space && DC.state.config.space.permanent_delete);
  }

  function renderGroup(g) {
    const sel = S.selected.get(g.id);
    const partial = sel instanceof Set;
    const card = el("div", { class: `jg ${g.confidence} ${S.open.has(g.id) ? "open" : ""}` });
    const cb = el("input", { type: "checkbox", onclick: (e) => e.stopPropagation(), onchange: (e) => toggleGroup(g, e.target.checked) });
    cb.checked = sel === "all"; cb.indeterminate = partial;
    if (!g.removable) cb.style.visibility = "hidden";
    const head = el("div", { class: "jg-head", onclick: () => { S.open.has(g.id) ? S.open.delete(g.id) : S.open.add(g.id); card.classList.toggle("open"); } },
      cb,
      el("div", { class: "jg-ico", html: ICONS[g.icon] || ICONS.files }),
      el("div", { class: "jg-main" },
        el("div", { class: "jg-title" }, g.title, el("span", { class: `badge ${g.confidence}` }, g.confidence === "safe" ? "safe" : g.confidence === "review" ? "review" : "manual")),
        el("div", { class: "jg-desc" }, g.description)),
      el("div", { class: "jg-size" }, el("b", {}, humanSize(g.size)), el("div", {}, plural(g.count, "item"))),
      el("div", { class: "jg-chev", html: '<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>' }));
    const body = el("div", { class: "jg-body" });
    if (g.note) body.append(el("div", { class: "jg-note", html: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg>' }, g.note));
    g.items.forEach((item) => {
      const on = isSelected(g, item);
      const row = el("div", { class: `ji ${g.removable && !on ? "off" : ""}` });
      const icb = el("input", { type: "checkbox", onchange: (e) => toggleItem(g, item, e.target.checked) });
      icb.checked = on; if (!g.removable) icb.style.visibility = "hidden";
      row.append(icb,
        el("div", { class: "ji-main" },
          el("div", { class: "ji-name" }, item.name, item.stale ? el("span", { class: "badge stale" }, "idle") : null),
          el("div", { class: "ji-detail", title: item.path }, item.detail || item.path)),
        el("div", { class: "ji-size" }, humanSize(item.size)),
        item.kind === "recycle_bin" ? el("span") : svgBtn("Show in folder", REVEAL, () => call("reveal_path", item.path)));
      body.append(row);
    });
    if (g.more) body.append(el("div", { class: "ji-more" }, `… and ${plural(g.more, "more item")} (${humanSize(g.more_size)}) included when the whole group is selected.`));
    if (!g.removable) body.append(el("div", { class: "jg-manual-foot" },
      el("span", {}, "How: ", el("code", {}, g.hint || "see description")),
      g.hint_url ? el("button", { class: "btn btn-ghost btn-sm", onclick: () => call("open_url", g.hint_url) }, "Open Storage settings") : null));
    card.append(head, body);
    return card;
  }

  function setRunning(on, label) {
    S.running = on;
    $("#analyzeBtn").disabled = on; $("#analyzeBtnLabel").textContent = on ? (label || "Analysing…") : (S.analysis ? "Analyse again" : "Analyse");
    $("#spaceCancelBtn").hidden = !on;
    $("#cleanBtn").disabled = on || !S.analysis;
  }

  async function startAnalysis() {
    if (S.running) return;
    setRunning(true);
    S.analysis = { groups: [], safe_bytes: 0, review_bytes: 0, manual_bytes: 0, reclaimable_bytes: 0, duration: 0 };
    S.selected = new Map(); S.open = new Set();
    renderGroups();
    $("#heroLabel").textContent = "Analysing"; $("#heroValue").textContent = "0 B"; $("#heroSub").textContent = "Looking through caches, temp folders, projects and downloads…";
    $("#heroSide").hidden = true; $("#heroProgress").hidden = false; $("#analyzeBar").style.width = "4%"; $("#analyzeText").textContent = "Starting…";
    const res = await call("analyze");
    setRunning(false);
    if (!res.ok) { if (res.error === "cancelled") { toast("Analysis cancelled", "info"); } S.analysis = null; heroIdle(); renderGroups(); return; }
    S.analysis = res.analysis; S.volumes = res.volumes || S.volumes;
    defaultSelection(S.analysis);
    heroResult(S.analysis); renderGroups(); renderSpaceDrives();
    const badge = $("#spaceBadge"); badge.hidden = !S.analysis.reclaimable_bytes; badge.textContent = humanSize(S.analysis.reclaimable_bytes);
    if (S.analysis.reclaimable_bytes) toast(`Found ${humanSize(S.analysis.reclaimable_bytes)} you could free`, "ok");
  }
  DC.startAnalysis = startAnalysis;

  DC.on("analyze_progress", (p) => {
    if (!S.running) return;
    if (p.phase === "junk") {
      const frac = p.total ? (p.index || 0) / p.total : 0;
      $("#analyzeBar").style.width = `${Math.max(4, Math.round(frac * 100))}%`;
      $("#analyzeText").textContent = `${p.step || "Scanning"}${p.current ? " · " + baseName(p.current) : ""}`;
      if (p.found != null) $("#heroValue").textContent = humanSize(p.found);
    } else if (p.phase === "group" && p.group) {
      const a = S.analysis; if (!a) return;
      a.groups = a.groups.filter((g) => g.id !== p.group.id).concat([p.group]);
      a.groups.sort((x, y) => ({ safe: 0, review: 1, manual: 2 }[x.confidence] - { safe: 0, review: 1, manual: 2 }[y.confidence]) || y.size - x.size);
      const sum = (c) => a.groups.filter((g) => g.confidence === c).reduce((s, g) => s + g.size, 0);
      Object.assign(a, { safe_bytes: sum("safe"), review_bytes: sum("review"), manual_bytes: sum("manual"), reclaimable_bytes: sum("safe") + sum("review") });
      if (p.group.confidence === "safe") S.selected.set(p.group.id, "all");
      $("#heroValue").textContent = humanSize(a.reclaimable_bytes);
      renderGroups();
    }
  });

  function renderSpaceDrives() {
    put($("#spaceDrives"), ...S.volumes.map((v) => DC.driveCard(v)));
  }

  async function loadSpace() {
    const res = await call("space_overview");
    if (!res.ok) return;
    S.volumes = res.volumes || [];
    renderSpaceDrives();
    if (res.analysis && !S.running) {
      const fresh = !S.analysis || S.analysis.started_at !== res.analysis.started_at;
      S.analysis = res.analysis;
      if (fresh) defaultSelection(S.analysis);
      heroResult(S.analysis);
    } else if (!S.running) heroIdle();
    renderGroups();
  }
  DC.onShow.space = [loadSpace];

  // ---- clean
  function openCleanModal() {
    const a = S.analysis; if (!a) return;
    const permanent = $("#permanentToggle").checked;
    const rows = []; let safe = 0, review = 0, bin = false, binBytes = 0;
    a.groups.filter((g) => g.removable).forEach((g) => {
      const b = groupSelectedBytes(g); if (!b) return;
      if (g.id === "recycle") { bin = true; binBytes = b; return; }
      if (g.confidence === "safe") safe += b; else review += b;
      rows.push(el("div", { class: "plan-row" }, el("i", { style: `background:${g.confidence === "safe" ? "var(--ok)" : "var(--warn)"}` }),
        el("div", {}, g.title, el("div", { class: "muted" }, g.confidence === "safe" ? "deleted permanently (regenerates itself)" : permanent ? "deleted permanently" : "moved to the recycle bin, can be restored")),
        el("b", {}, humanSize(b))));
    });
    if (bin) rows.push(el("div", { class: "plan-row" }, el("i", { style: "background:var(--danger)" }), el("div", {}, "Recycle Bin", el("div", { class: "muted" }, "emptied for good")), el("b", {}, humanSize(binBytes))));
    const total = safe + review + binBytes;
    put($("#cleanModalBody"), 
      el("p", { class: "muted", style: "margin-bottom:10px" }, "Here is exactly what will happen:"),
      el("div", { class: "plan-rows" }, ...rows),
      el("div", { class: "plan-total" }, el("span", {}, "Space you get back"), el("b", {}, humanSize(total))),
      review && !permanent ? el("p", { class: "hint" }, "Review items land in the recycle bin, so emptying it later is what actually frees that part.") : null);
    $("#cleanConfirmBtn").textContent = `Free up ${humanSize(total)}`;
    $("#cleanModal").hidden = false;
  }
  const closeCleanModal = () => { $("#cleanModal").hidden = true; };
  $("#cleanBtn").addEventListener("click", openCleanModal);
  $("#cleanModalClose").addEventListener("click", closeCleanModal);
  $("#cleanCancelBtn").addEventListener("click", closeCleanModal);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCleanModal(); });

  $("#cleanConfirmBtn").addEventListener("click", async () => {
    closeCleanModal();
    const a = S.analysis; if (!a) return;
    const groupsAll = []; const paths = [];
    a.groups.filter((g) => g.removable).forEach((g) => { const sel = S.selected.get(g.id); if (sel === "all") groupsAll.push(g.id); else if (sel instanceof Set) paths.push(...sel); });
    const permanent = $("#permanentToggle").checked;
    setRunning(true, "Cleaning…");
    $("#heroLabel").textContent = "Cleaning"; $("#heroProgress").hidden = false; $("#heroSide").hidden = true; $("#analyzeBar").style.width = "2%"; $("#analyzeText").textContent = "Starting…";
    const res = await call("clean", groupsAll, paths, permanent);
    setRunning(false);
    if (!res.ok) { if (S.analysis) heroResult(S.analysis); return; }
    S.analysis = res.analysis; S.volumes = res.volumes || S.volumes;
    defaultSelection(S.analysis); S.selected = new Map();
    heroResult(S.analysis); renderGroups(); renderSpaceDrives();
    $("#heroLabel").textContent = "Freed just now"; $("#heroValue").textContent = res.human_freed || humanSize(res.result.freed);
    $("#heroSub").textContent = `${plural(res.result.removed, "item")} removed${res.result.failed.length ? `, ${res.result.failed.length} skipped because they were in use` : ""}. ${S.analysis.reclaimable_bytes ? humanSize(S.analysis.reclaimable_bytes) + " more is available." : ""}`;
    const badge = $("#spaceBadge"); badge.hidden = !S.analysis.reclaimable_bytes; badge.textContent = humanSize(S.analysis.reclaimable_bytes);
    toast(`Freed ${res.human_freed || humanSize(res.result.freed)} 🎉`, "ok", { ttl: 6000 });
    if (res.result.failed.length) toast(`${res.result.failed.length} item${res.result.failed.length === 1 ? " was" : "s were"} in use and skipped. Close the app that owns them and analyse again.`, "warn", { ttl: 7000 });
  });
  DC.on("clean_progress", (p) => {
    if (p.phase === "clean") {
      $("#analyzeBar").style.width = `${p.total ? Math.round((p.index / p.total) * 100) : 0}%`;
      $("#analyzeText").textContent = `Removing ${p.index} of ${p.total}: ${p.name}`;
      $("#heroValue").textContent = humanSize(p.freed || 0);
    }
  });

  $("#analyzeBtn").addEventListener("click", startAnalysis);
  $("#spaceCancelBtn").addEventListener("click", () => call("cancel_scan"));
  $("#selSafeBtn").addEventListener("click", () => { if (S.analysis) { defaultSelection(S.analysis); renderGroups(); } });
  $("#selRecommendedBtn").addEventListener("click", selectRecommended);
  $("#selAllBtn").addEventListener("click", () => { if (S.analysis) { S.selected = new Map(); S.analysis.groups.filter((g) => g.removable).forEach((g) => S.selected.set(g.id, "all")); renderGroups(); } });
  $("#selNoneBtn").addEventListener("click", () => { S.selected = new Map(); renderGroups(); });
  $("#permanentToggle").addEventListener("change", (e) => { if (DC.state && DC.state.config) { DC.state.config.space.permanent_delete = e.target.checked; call("save_config", DC.state.config); } });

  // ================================================================== STORAGE MAP
  const M = { scan: null, node: null, stack: [], tab: "folders", rects: [], hover: null, running: false, files: null };

  function mapIdleQuick() {
    const home = DC.state && DC.state.boot ? DC.state.boot.home : "";
    const sep = home.includes("\\") ? "\\" : "/";
    const quick = [home, home + sep + "Downloads", home + sep + "Documents", home + sep + "Videos", home + sep + "Pictures"].filter(Boolean);
    put($("#mapQuick"), ...quick.map((p) => el("button", { class: "btn btn-ghost btn-sm", onclick: () => { $("#mapPath").value = p; startScan(); } }, baseName(p) || p)));
    if (!$("#mapPath").value) $("#mapPath").value = home;
  }
  async function startScan() {
    if (M.running) return;
    const path = $("#mapPath").value.trim(); if (!path) return;
    M.running = true; $("#mapScanBtn").disabled = true; $("#mapCancelBtn").hidden = false;
    $("#mapProgress").hidden = false; $("#mapEmpty").hidden = true; $("#mapBody").hidden = true;
    $("#mapProgressText").textContent = "Scanning…";
    const res = await call("scan_folder", path, 2);
    M.running = false; $("#mapScanBtn").disabled = false; $("#mapCancelBtn").hidden = true; $("#mapProgress").hidden = true;
    if (!res.ok) { $("#mapEmpty").hidden = false; if (res.error === "cancelled") toast("Scan cancelled", "info"); return; }
    M.scan = res.scan; M.node = res.scan.root; M.stack = [res.scan.root]; M.tab = "folders"; M.files = null;
    $("#mapBody").hidden = false;
    renderMap();
    toast(`${humanSize(res.scan.bytes)} in ${plural(res.scan.files, "file")}`, "ok");
  }
  DC.autoScan = startScan;
  DC.on("scan_progress", (p) => {
    if (p.phase === "scan") $("#mapProgressText").textContent = `${p.files.toLocaleString()} files · ${humanSize(p.bytes)} · ${baseName(p.current || "")}`;
  });

  function renderMap() {
    const s = M.scan, n = M.node;
    put($("#mapSummary"), 
      el("span", {}, el("b", {}, humanSize(n.size)), ` in ${baseName(n.path) || n.path}`),
      el("span", {}, el("b", {}, n.files.toLocaleString()), " files"),
      el("span", {}, el("b", {}, n.dirs.toLocaleString()), " folders"),
      n === s.root ? el("span", {}, `scanned in ${s.duration}s`) : null,
      s.denied ? el("span", { class: "muted" }, `${s.denied} folders unreadable`) : null,
      s.truncated ? el("span", { style: "color:var(--warn)" }, "very large tree, partially scanned") : null,
      el("button", { class: "btn btn-ghost btn-sm", onclick: () => call("open_path", n.path) }, "Open folder"));
    put($("#mapCrumbs"), ...M.stack.flatMap((node, i) => [
      i ? el("span", { class: "crumb-sep" }, "›") : null,
      el("button", { class: `crumb ${i === M.stack.length - 1 ? "current" : ""}`, onclick: () => { M.stack = M.stack.slice(0, i + 1); M.node = node; M.files = null; renderMap(); } }, i === 0 ? node.path : node.name)]));
    $$("#mapTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === M.tab));
    drawTreemap(); renderMapTab();
  }

  async function drill(child) {
    if (!child || child.denied) { if (child && child.denied) toast("That folder could not be read", "warn"); return; }
    let node = child;
    if (!child.children) { const res = await call("scan_node", child.path, 2); if (!res.ok) return; node = res.node; }
    M.stack.push(node); M.node = node; M.files = null; renderMap();
  }

  // squarified treemap
  function squarify(items, x, y, w, h, out) {
    items = items.filter((i) => i.value > 0);
    if (!items.length) return;
    const total = items.reduce((s, i) => s + i.value, 0);
    const scale = (w * h) / total;
    let row = []; let rest = items.slice();
    const worst = (row, side) => { const s = row.reduce((a, b) => a + b.value * scale, 0); let mx = 0; for (const r of row) { const a = r.value * scale; mx = Math.max(mx, Math.max((side * side * a) / (s * s), (s * s) / (side * side * a))); } return mx; };
    while (rest.length) {
      const horizontal = w >= h; const side = horizontal ? h : w;
      const next = rest[0];
      if (!row.length || worst(row.concat([next]), side) <= worst(row, side)) { row.push(next); rest = rest.slice(1); continue; }
      ({ x, y, w, h } = layoutRow(row, x, y, w, h, scale, out)); row = [];
    }
    if (row.length) layoutRow(row, x, y, w, h, scale, out);
  }
  function layoutRow(row, x, y, w, h, scale, out) {
    const area = row.reduce((s, r) => s + r.value * scale, 0);
    if (w >= h) { const rw = area / h; let cy = y; for (const r of row) { const rh = (r.value * scale) / rw; out.push({ ...r, x, y: cy, w: rw, h: rh }); cy += rh; } return { x: x + rw, y, w: w - rw, h }; }
    const rh = area / w; let cx = x; for (const r of row) { const rw = (r.value * scale) / rh; out.push({ ...r, x: cx, y, w: rw, h: rh }); cx += rw; } return { x, y: y + rh, w, h: h - rh };
  }
  function drawTreemap() {
    const c = $("#treemap"); const n = M.node; if (!n) return;
    const dpr = window.devicePixelRatio || 1; const W = c.clientWidth || 600; const H = 480;
    c.width = W * dpr; c.height = H * dpr; c.style.height = H + "px";
    const ctx = c.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    const items = (n.children || []).map((ch) => ({ kind: "dir", node: ch, name: ch.name, value: ch.size, color: colorFor(ch.name) }));
    if (n.file_bytes > 0) items.push({ kind: "files", name: "files here", value: n.file_bytes, color: cssVar("--text-faint") });
    if (n.other && n.other.size > 0) items.push({ kind: "other", name: `${n.other.count} smaller folders`, value: n.other.size, color: cssVar("--border-strong") });
    items.sort((a, b) => b.value - a.value);
    const rects = []; squarify(items, 0, 0, W, H, rects);
    M.rects = rects;
    const bg = cssVar("--bg-2");
    const font = cssVar("--font");
    for (const r of rects) {
      const pad = 2; const x = r.x + pad, y = r.y + pad, w = Math.max(0, r.w - pad * 2), h = Math.max(0, r.h - pad * 2);
      if (w <= 0 || h <= 0) continue;
      ctx.fillStyle = r.color; ctx.globalAlpha = r.kind === "dir" ? (M.hover === r ? 1 : 0.82) : 0.35;
      ctx.beginPath(); ctx.roundRect(x, y, w, h, Math.min(8, w / 2, h / 2)); ctx.fill();
      ctx.globalAlpha = 1;
      if (r.kind === "dir" && r.node.children && r.node.children.length && w > 60 && h > 60) {
        // faint inner blocks for a sense of depth
        const inner = []; squarify(r.node.children.map((ch) => ({ name: ch.name, value: ch.size })), x + 6, y + 26, w - 12, h - 32, inner);
        ctx.fillStyle = bg; ctx.globalAlpha = 0.18;
        for (const q of inner) { if (q.w > 4 && q.h > 4) { ctx.beginPath(); ctx.roundRect(q.x + 1, q.y + 1, q.w - 2, q.h - 2, 3); ctx.fill(); } }
        ctx.globalAlpha = 1;
      }
      if (w > 48 && h > 22) {
        ctx.fillStyle = r.kind === "dir" ? "#fff" : cssVar("--text");
        ctx.font = `600 12px ${font}`; ctx.textBaseline = "top";
        const label = truncate(ctx, r.name, w - 14);
        ctx.fillText(label, x + 8, y + 7);
        if (h > 40) { ctx.font = `11px ${font}`; ctx.globalAlpha = 0.85; ctx.fillText(truncate(ctx, `${humanSize(r.value)} · ${pct(r.value, n.size)}%`, w - 14), x + 8, y + 23); ctx.globalAlpha = 1; }
      }
    }
  }
  function truncate(ctx, text, width) { if (ctx.measureText(text).width <= width) return text; let t = text; while (t.length > 1 && ctx.measureText(t + "…").width > width) t = t.slice(0, -1); return t + "…"; }
  const hitRect = (e) => { const b = $("#treemap").getBoundingClientRect(); const x = e.clientX - b.left, y = e.clientY - b.top; return M.rects.find((r) => x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) || null; };
  $("#treemap").addEventListener("mousemove", (e) => {
    const r = hitRect(e); const tip = $("#treemapTip");
    if (r !== M.hover) { M.hover = r; drawTreemap(); }
    if (!r) { tip.hidden = true; return; }
    tip.hidden = false;
    const n = M.node;
    put(tip, el("b", {}, r.name), el("span", { class: "muted" }, `${humanSize(r.value)} · ${pct(r.value, n.size)}%${r.kind === "dir" ? ` · ${plural(r.node.files, "file")}` : ""}${r.kind === "dir" ? " · click to open" : ""}`));
    const card = $(".map-canvas-card").getBoundingClientRect();
    tip.style.left = Math.min(card.width - 290, e.clientX - card.left + 14) + "px"; tip.style.top = (e.clientY - card.top + 14) + "px";
  });
  $("#treemap").addEventListener("mouseleave", () => { M.hover = null; $("#treemapTip").hidden = true; drawTreemap(); });
  $("#treemap").addEventListener("click", (e) => { const r = hitRect(e); if (r && r.kind === "dir") drill(r.node); else if (r && r.kind === "files") { M.tab = "files"; renderMap(); } });
  window.addEventListener("resize", debounce(() => M.node && !$("#view-map").hidden && drawTreemap(), 150));

  function barRow(name, size, whole, color, onclick, extra) {
    return el("div", { class: "bar-row", onclick },
      el("div", { class: "br-name" }, el("i", { style: `background:${color}` }), name, extra || null),
      el("div", { class: "br-size" }, `${humanSize(size)} · ${pct(size, whole)}%`),
      el("div", { class: "meter" }, el("i", { style: `width:${Math.min(100, pct(size, whole))}%;background:${color}` })));
  }
  function fileRow(f, afterRemove) {
    return el("div", { class: "ji" },
      el("div", { class: "ji-main" }, el("div", { class: "ji-name" }, f.name), el("div", { class: "ji-detail", title: f.path }, `${dirName(f.path)}${f.mtime ? " · " + timeAgo(f.mtime * 1000) : ""}`)),
      el("div", { class: "ji-size" }, humanSize(f.size)),
      el("div", { class: "btn-row" },
        svgBtn("Show in folder", REVEAL, () => call("reveal_path", f.path)),
        svgBtn("Move to recycle bin", TRASH, async () => {
          if (!(await confirmDialog("Move to recycle bin?", `${f.name} (${humanSize(f.size)}) will go to the recycle bin. You can restore it from there.`, { danger: "Move to bin" }))) return;
          const res = await call("remove_paths", [f.path], false, "storage_map");
          if (res.ok) { toast(`Moved to recycle bin · ${res.human_freed}`, "ok"); afterRemove && afterRemove(f); }
        }, "danger")));
  }
  async function renderMapTab() {
    const body = $("#mapTabBody"); const n = M.node; const s = M.scan;
    if (M.tab === "folders") {
      const kids = (n.children || []).slice().sort((a, b) => b.size - a.size);
      put(body, ...kids.map((ch) => barRow(ch.name, ch.size, n.size, colorFor(ch.name), () => drill(ch), ch.denied ? el("span", { class: "badge manual" }, "no access") : null)));
      if (n.file_bytes) body.append(barRow("files directly here", n.file_bytes, n.size, cssVar("--text-faint"), () => { M.tab = "files"; renderMap(); }));
      if (n.other) body.append(el("p", { class: "hint" }, `${n.other.count} smaller folders (${humanSize(n.other.size)}) not shown.`));
      if (!kids.length && !n.file_bytes) body.append(el("p", { class: "muted" }, "Empty folder."));
    } else if (M.tab === "files") {
      put(body, el("p", { class: "muted small" }, "Loading…"));
      if (!M.files || M.files.path !== n.path) { const res = await call("list_files", n.path, 300); M.files = { path: n.path, rows: res.ok ? res.files : [], total: res.ok ? res.total : 0 }; }
      const wrap = el("div", { class: "file-list" });
      M.files.rows.forEach((f) => wrap.append(fileRow(f, (gone) => { M.files.rows = M.files.rows.filter((x) => x !== gone); renderMapTab(); })));
      put(body, wrap);
      if (!M.files.rows.length) body.append(el("p", { class: "muted" }, "No files directly in this folder."));
      else if (M.files.total > M.files.rows.length) body.append(el("p", { class: "hint" }, `Showing the ${M.files.rows.length} largest of ${M.files.total} files.`));
    } else if (M.tab === "largest") {
      const prefix = n.path.toLowerCase();
      const rows = s.largest.filter((f) => f.path.toLowerCase().startsWith(prefix));
      const wrap = el("div", { class: "file-list" });
      rows.forEach((f) => wrap.append(fileRow(f, (gone) => { s.largest = s.largest.filter((x) => x !== gone); renderMapTab(); })));
      put(body, wrap);
      if (!rows.length) body.append(el("p", { class: "muted" }, "None of the largest files are under this folder."));
    } else if (M.tab === "types") {
      const total = s.bytes;
      put(body, ...s.by_type.map((t) => barRow(t.category, t.bytes, total, colorFor(t.category), null, el("span", { class: "muted small" }, plural(t.files, "file")))));
      body.append(el("p", { class: "hint" }, "Whole scan, not just the current folder."));
    } else if (M.tab === "age") {
      const total = s.bytes;
      put(body, ...s.by_age.map((t, i) => barRow(t.label, t.bytes, total, PALETTE[(i * 2) % PALETTE.length], null, el("span", { class: "muted small" }, plural(t.files, "file")))));
      body.append(el("p", { class: "hint" }, "By last modification. Old and large is where the savings hide."));
    }
  }
  $$("#mapTabs .tab").forEach((t) => t.addEventListener("click", () => { M.tab = t.dataset.tab; renderMap(); }));
  $("#mapScanBtn").addEventListener("click", startScan);
  $("#mapPath").addEventListener("keydown", (e) => { if (e.key === "Enter") startScan(); });
  $("#mapCancelBtn").addEventListener("click", () => call("cancel_scan"));
  $("#mapBrowseBtn").addEventListener("click", async () => { const res = await call("pick_folder"); if (res.ok) { $("#mapPath").value = res.path; startScan(); } });
  DC.onShow.map = [() => { mapIdleQuick(); if (M.node) requestAnimationFrame(drawTreemap); }];

  // ================================================================== DUPLICATES
  const D = { roots: [], result: null, keep: new Map(), running: false };
  function renderRoots() {
    put($("#dupesRoots"), ...D.roots.map((r) => el("span", { class: "chip path" }, r, el("button", { title: "Remove", onclick: () => { D.roots = D.roots.filter((x) => x !== r); renderRoots(); } }, "×"))));
    if (!D.roots.length) $("#dupesRoots").append(el("span", { class: "muted small" }, "no folders chosen"));
  }
  async function loadDupes() {
    if (!D.roots.length) { const res = await call("space_overview"); if (res.ok) D.roots = res.duplicate_roots || []; }
    const cfg = DC.state && DC.state.config; if (cfg && cfg.space) $("#dupesMinMb").value = cfg.space.duplicate_min_mb;
    renderRoots();
  }
  DC.onShow.dupes = [loadDupes];
  $("#dupesAddRootBtn").addEventListener("click", async () => { const res = await call("pick_folder"); if (res.ok && !D.roots.includes(res.path)) { D.roots.push(res.path); renderRoots(); } });

  async function startDupes() {
    if (D.running) return;
    if (!D.roots.length) { toast("Add at least one folder", "warn"); return; }
    D.running = true; $("#dupesScanBtn").disabled = true; $("#dupesCancelBtn").hidden = false;
    $("#dupesProgress").hidden = false; $("#dupesBar").style.width = "3%"; $("#dupesProgressText").textContent = "Collecting files…";
    $("#dupesSummary").hidden = true; $("#dupesEmpty").hidden = true; put($("#dupesGroups")); $("#dupesActionBar").hidden = true;
    const res = await call("find_duplicates", D.roots, Number($("#dupesMinMb").value) || 0);
    D.running = false; $("#dupesScanBtn").disabled = false; $("#dupesCancelBtn").hidden = true; $("#dupesProgress").hidden = true;
    if (!res.ok) { if (res.error === "cancelled") toast("Scan cancelled", "info"); return; }
    D.result = res.dupes; D.keep = new Map(); D.result.groups.forEach((g) => D.keep.set(g.digest, g.keep));
    renderDupes();
  }
  DC.autoDupes = startDupes;
  DC.on("dupes_progress", (p) => {
    if (p.phase === "collect") { $("#dupesBar").style.width = "15%"; $("#dupesProgressText").textContent = `Collecting: ${p.files.toLocaleString()} files, ${p.candidates.toLocaleString()} same-size candidates`; }
    else if (p.phase === "hash") { const f = p.total_bytes ? p.bytes / p.total_bytes : 0; $("#dupesBar").style.width = `${15 + Math.round(f * 85)}%`; $("#dupesProgressText").textContent = `Comparing contents: ${humanSize(p.bytes)} of ${humanSize(p.total_bytes)} · ${baseName(p.current || "")}`; }
  });
  function victims() { const out = []; (D.result ? D.result.groups : []).forEach((g) => g.files.forEach((f) => { if (f.path !== D.keep.get(g.digest)) out.push(f); })); return out; }
  function renderDupes() {
    const r = D.result; if (!r) return;
    const v = victims(); const bytes = v.reduce((s, f) => s + f.size, 0);
    $("#dupesSummary").hidden = !r.groups.length; $("#dupesEmpty").hidden = !!r.groups.length;
    $("#dupesValue").textContent = humanSize(bytes);
    $("#dupesSub").textContent = `${plural(r.groups.length, "group")} · ${plural(v.length, "duplicate")} · ${r.scanned_files.toLocaleString()} files compared in ${r.duration}s`;
    put($("#dupesGroups"), ...r.groups.map((g) => {
      const card = el("div", { class: "dg" });
      card.append(el("div", { class: "dg-head" },
        el("div", { class: "dg-title" }, el("div", { class: "jg-ico", style: `background:${colorFor(g.category)};width:32px;height:32px;border-radius:9px`, html: ICONS.files }), el("span", {}, baseName(g.keep))),
        el("div", { class: "dg-meta" }, el("b", {}, humanSize(g.size)), ` × ${g.copies} · reclaim `, el("b", {}, humanSize(g.size * (g.copies - 1))))));
      g.files.forEach((f) => {
        const keep = D.keep.get(g.digest) === f.path;
        const radio = el("input", { type: "radio", name: `keep-${g.digest}`, onchange: () => { D.keep.set(g.digest, f.path); renderDupes(); } }); radio.checked = keep;
        card.append(el("div", { class: `df ${keep ? "keep" : ""}` }, radio,
          el("div", { class: "df-path", title: f.path }, f.path, el("span", { class: "muted" }, ` · ${timeAgo(f.mtime * 1000)}`)),
          el("span", { class: "df-tag" }, keep ? "keep" : "remove"),
          svgBtn("Show in folder", REVEAL, () => call("reveal_path", f.path))));
      });
      return card;
    }));
    const bar = $("#dupesActionBar"); bar.hidden = !v.length;
    $("#dupesAbValue").textContent = humanSize(bytes); $("#dupesAbSub").textContent = `${plural(v.length, "duplicate")} to remove`;
    $("#dupesRemoveLabel").textContent = `Move ${v.length} to recycle bin`;
    if (r.total_groups > r.groups.length) $("#dupesGroups").append(el("p", { class: "hint" }, `Showing the ${r.groups.length} largest of ${r.total_groups} groups. Remove these and scan again for the rest.`));
  }
  $("#dupesScanBtn").addEventListener("click", startDupes);
  $("#dupesCancelBtn").addEventListener("click", () => call("cancel_scan"));
  $("#dupesRemoveBtn").addEventListener("click", async () => {
    const v = victims(); if (!v.length) return;
    const bytes = v.reduce((s, f) => s + f.size, 0);
    if (!(await confirmDialog("Remove duplicates?", `${plural(v.length, "file")} (${humanSize(bytes)}) will move to the recycle bin. One copy of each stays exactly where it is.`, { danger: `Move ${v.length} to bin` }))) return;
    const res = await call("remove_paths", v.map((f) => f.path), false, "duplicates");
    if (!res.ok) return;
    if (res.dupes) { D.result = res.dupes; D.result.groups.forEach((g) => { if (!D.keep.has(g.digest)) D.keep.set(g.digest, g.keep); }); }
    renderDupes();
    toast(`Moved ${res.result.removed} duplicate${res.result.removed === 1 ? "" : "s"} to the recycle bin · ${res.human_freed}`, "ok", { ttl: 6000 });
    if (res.result.failed.length) toast(`${res.result.failed.length} could not be moved (in use).`, "warn");
  });
})();
