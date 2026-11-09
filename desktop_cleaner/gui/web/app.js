/* Desktop Cleaner front end. Talks to Python through window.pywebview.api.
   When opened in a plain browser (no pywebview) a mock API with demo data is used,
   so the UI can be developed and previewed standalone. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (v === true) node.setAttribute(k, "");
      else if (v !== false && v != null) node.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null) node.append(c.nodeType ? c : document.createTextNode(String(c)));
    return node;
  };
  const humanSize = (n) => {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0; let v = Number(n) || 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return i === 0 ? `${v.toFixed(0)} B` : `${v.toFixed(1)} ${units[i]}`;
  };
  const baseName = (p) => String(p).split(/[\\/]/).pop();
  const dirName = (p) => { const parts = String(p).split(/[\\/]/); parts.pop(); return parts.join(p.includes("\\") ? "\\" : "/"); };
  const relDest = (action) => {
    const dest = dirName(action.destination);
    const src = action.source_dir;
    return dest.startsWith(src) ? dest.slice(src.length).replace(/^[\\/]/, "") || "." : dest;
  };
  const timeAgo = (iso) => {
    const d = new Date(iso); const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
    return d.toLocaleDateString();
  };
  const fmtWhen = (iso) => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const uid = () => Math.random().toString(16).slice(2, 10);
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ------------------------------------------------------------------ toasts + confirm
  function toast(message, kind = "info", { action, onAction, ttl = 4200 } = {}) {
    const node = el("div", { class: `toast ${kind}` }, el("i"), el("div", { class: "t-body", html: message }));
    if (action) node.append(el("button", { class: "btn btn-ghost", onclick: () => { onAction && onAction(); dismiss(); } }, action));
    $("#toasts").append(node);
    let gone = false;
    const dismiss = () => { if (gone) return; gone = true; node.classList.add("out"); setTimeout(() => node.remove(), 220); };
    setTimeout(dismiss, ttl);
    return dismiss;
  }
  function confirmDialog(title, text, { danger = "Confirm" } = {}) {
    return new Promise((resolve) => {
      const m = $("#confirmModal");
      $("#confirmTitle").textContent = title;
      $("#confirmText").textContent = text;
      $("#confirmYes").textContent = danger;
      m.hidden = false;
      const done = (v) => { m.hidden = true; $("#confirmYes").onclick = $("#confirmNo").onclick = null; resolve(v); };
      $("#confirmYes").onclick = () => done(true);
      $("#confirmNo").onclick = () => done(false);
    });
  }

  // ------------------------------------------------------------------ API (real or mock)
  const mockState = (() => {
    const cfg = {
      version: 2, sources: ["~/Desktop", "~/Downloads"],
      rules: [
        { id: "r1", name: "Screenshots", destination: "Images/Screenshots", enabled: true, date_subfolders: "none", color: "#ff9a7a", match: { extensions: ["png", "jpg"], patterns: ["Screenshot*"], regex: null, min_age_days: 0, min_size_mb: null, max_size_mb: null } },
        { id: "r2", name: "Images", destination: "Images", enabled: true, date_subfolders: "year-month", color: "#ff7a9a", match: { extensions: ["jpg", "png", "gif", "webp"], patterns: [], regex: null, min_age_days: 0, min_size_mb: null, max_size_mb: null } },
        { id: "r3", name: "PDFs", destination: "Documents/PDFs", enabled: true, date_subfolders: "none", color: "#ff6b6b", match: { extensions: ["pdf"], patterns: [], regex: null, min_age_days: 0, min_size_mb: null, max_size_mb: null } },
        { id: "r4", name: "Installers", destination: "Installers", enabled: false, date_subfolders: "none", color: "#f472b6", match: { extensions: ["exe", "msi"], patterns: [], regex: null, min_age_days: 7, min_size_mb: null, max_size_mb: null } },
      ],
      options: { conflict: "rename", ignore: ["desktop.ini", "*.lnk", ".*"], skip_recent_seconds: 30, move_folders: false, watch_interval_seconds: 5, unmatched_destination: null, notifications: true, theme: "dark" },
    };
    const plan = {
      actions: [
        { source: "C:\\Users\\me\\Desktop\\Screenshot 2026-03-01.png", destination: "C:\\Users\\me\\Desktop\\Images\\Screenshots\\Screenshot 2026-03-01.png", rule_id: "r1", rule_name: "Screenshots", rule_color: "#ff9a7a", size: 240000, mtime: 0, source_dir: "C:\\Users\\me\\Desktop" },
        { source: "C:\\Users\\me\\Desktop\\holiday.jpg", destination: "C:\\Users\\me\\Desktop\\Images\\2026\\03\\holiday.jpg", rule_id: "r2", rule_name: "Images", rule_color: "#ff7a9a", size: 3200000, mtime: 0, source_dir: "C:\\Users\\me\\Desktop" },
        { source: "C:\\Users\\me\\Downloads\\invoice.pdf", destination: "C:\\Users\\me\\Downloads\\Documents\\PDFs\\invoice.pdf", rule_id: "r3", rule_name: "PDFs", rule_color: "#ff6b6b", size: 88000, mtime: 0, source_dir: "C:\\Users\\me\\Downloads" },
        { source: "C:\\Users\\me\\Downloads\\contract.pdf", destination: "C:\\Users\\me\\Downloads\\Documents\\PDFs\\contract.pdf", rule_id: "r3", rule_name: "PDFs", rule_color: "#ff6b6b", size: 1200000, mtime: 0, source_dir: "C:\\Users\\me\\Downloads" },
      ],
      skipped: [
        { path: "C:\\Users\\me\\Desktop\\Chrome.lnk", reason: "ignored by pattern", is_dir: false },
        { path: "C:\\Users\\me\\Desktop\\Project", reason: "folder (folders are not moved)", is_dir: true },
        { path: "C:\\Users\\me\\Desktop\\notes.xyz", reason: "no matching rule", is_dir: false },
      ],
      sources: ["C:\\Users\\me\\Desktop", "C:\\Users\\me\\Downloads"], missing_sources: [], created_at: new Date().toISOString(),
    };
    plan.bytes_total = plan.actions.reduce((a, b) => a + b.size, 0);
    const day = (n) => new Date(Date.now() - n * 86400000).toISOString();
    const batches = [
      { type: "batch", batch_id: "f6e5d4c3b2", timestamp: day(2), sources: plan.sources, trigger: "watch", undone: true, moves: [
        { src: "C:\\Users\\me\\Downloads\\setup.exe", dest: "C:\\Users\\me\\Downloads\\Installers\\setup.exe", rule_id: "r4", rule_name: "Installers", size: 95000000, renamed: false }] },
      { type: "batch", batch_id: "c3d4e5f6a7", timestamp: day(1), sources: plan.sources, trigger: "cli", undone: false, moves: [
        { src: "C:\\Users\\me\\Downloads\\lecture-04.mp4", dest: "C:\\Users\\me\\Downloads\\Videos\\lecture-04.mp4", rule_id: "r5", rule_name: "Videos", size: 740000000, renamed: false },
        { src: "C:\\Users\\me\\Downloads\\assets.zip", dest: "C:\\Users\\me\\Downloads\\Archives\\assets.zip", rule_id: "r6", rule_name: "Archives", size: 120000000, renamed: false },
        { src: "C:\\Users\\me\\Downloads\\IMG_2231.jpg", dest: "C:\\Users\\me\\Downloads\\Images\\2026\\03\\IMG_2231.jpg", rule_id: "r2", rule_name: "Images", size: 4100000, renamed: false }] },
      { type: "batch", batch_id: "a1b2c3d4e5", timestamp: day(0), sources: plan.sources, trigger: "gui", undone: false, moves: [
        { src: "C:\\Users\\me\\Desktop\\a.png", dest: "C:\\Users\\me\\Desktop\\Images\\a.png", rule_id: "r2", rule_name: "Images", size: 50000, renamed: false },
        { src: "C:\\Users\\me\\Desktop\\b.pdf", dest: "C:\\Users\\me\\Desktop\\Documents\\PDFs\\b.pdf", rule_id: "r3", rule_name: "PDFs", size: 150000, renamed: true }] },
    ];
    const timeline = Array.from({ length: 30 }, (_, i) => ({ date: day(29 - i).slice(0, 10), files: Math.random() < 0.5 ? 0 : Math.floor(Math.random() * 14) }));
    const GB = 1024 ** 3, MB = 1024 ** 2;
    const volumes = [
      { path: "C:\\", label: "Windows", kind: "fixed", fs: "NTFS", total: 475.8 * GB, used: 398.2 * GB, free: 77.6 * GB, percent: 83.7, status: "warn", is_system: true, is_home: true },
      { path: "D:\\", label: "Data", kind: "fixed", fs: "NTFS", total: 931.5 * GB, used: 412.9 * GB, free: 518.6 * GB, percent: 44.3, status: "ok", is_system: false, is_home: false },
    ];
    const U = "C:\\Users\\me";
    const it = (path, size, detail, extra = {}) => ({ path, name: path.split(/[\\/]/).pop(), size, count: Math.max(1, Math.round(size / 48000)), is_dir: true, detail, mtime: Date.now() / 1000 - 86400 * 20, kind: "contents", stale: false, ...extra });
    const groups = [
      { id: "pkgcache", title: "Package manager & build caches", description: "pip, npm, yarn, gradle, cargo, NuGet, Playwright, JetBrains ... Downloads again on demand.", confidence: "safe", icon: "package", note: "The next install or build simply re-downloads what it needs.", items: [
        it(U + "\\AppData\\Local\\npm-cache", 6.7 * GB, "npm"), it(U + "\\AppData\\Local\\pip\\cache", 3.1 * GB, "pip"), it(U + "\\AppData\\Local\\ms-playwright", 718 * MB, "Playwright browsers"), it(U + "\\.gradle\\caches", 1.9 * GB, "gradle")] },
      { id: "browser", title: "Browser caches", description: "Pages, scripts and images your browsers keep for faster reloads. They rebuild themselves.", confidence: "safe", icon: "browser", note: "Close your browsers first for the full amount; files in use are skipped.", items: [
        it(U + "\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Cache\\Cache_Data", 2.4 * GB, "Chrome · Default"), it(U + "\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Code Cache", 749 * MB, "Chrome · Default"), it(U + "\\AppData\\Local\\Microsoft\\Edge\\User Data\\Default\\Cache\\Cache_Data", 1.1 * GB, "Edge · Default"), it(U + "\\AppData\\Local\\Mozilla\\Firefox\\Profiles\\k2x.default\\cache2", 640 * MB, "Firefox · default")] },
      { id: "apps", title: "Application caches", description: "Electron apps (VS Code, Discord, Slack, Teams, Notion, Figma, Postman ...), media and design tools.", confidence: "safe", icon: "apps", note: "Apps rebuild these on next launch. Close the app first for the full amount.", items: [
        it(U + "\\AppData\\Roaming\\Code\\CachedExtensionVSIXs", 1.4 * GB, "Code"), it(U + "\\AppData\\Roaming\\Code\\CachedData", 441 * MB, "Code"), it(U + "\\AppData\\Roaming\\discord\\Cache", 388 * MB, "discord"), it(U + "\\AppData\\Roaming\\Slack\\Cache", 212 * MB, "Slack"), it(U + "\\AppData\\Roaming\\Figma\\Cache", 160 * MB, "Figma")] },
      { id: "temp", title: "Temporary files", description: "Scratch files apps forgot to delete. Anything younger than a day is left alone.", confidence: "safe", icon: "temp", note: "Files still in use are skipped automatically.", items: [
        it(U + "\\AppData\\Local\\Temp\\vscode-stable-user-x64", 244 * MB, "modified 1 day ago"), it(U + "\\AppData\\Local\\Temp\\chrome_installer", 128 * MB, "modified 9 days ago"), it(U + "\\AppData\\Local\\Temp\\pip-unpack-9a1", 96 * MB, "modified 12 days ago")] },
      { id: "crash", title: "Crash dumps & error reports", description: "Memory dumps and diagnostic reports written when something crashed. Useful once, then dead weight.", confidence: "safe", icon: "crash", items: [
        it(U + "\\AppData\\Local\\CrashDumps", 379 * MB, "Windows crash dumps")] },
      { id: "recycle", title: "Recycle Bin", description: "Things you already deleted. Emptying it is what finally frees the space.", confidence: "review", icon: "recycle", note: "Once emptied, these cannot be restored.", items: [
        { path: "Recycle Bin", name: "Recycle Bin", size: 3.2 * GB, count: 214, is_dir: true, detail: "214 items", mtime: 0, kind: "recycle_bin", stale: false }] },
      { id: "projects", title: "Project build folders & dependencies", description: "node_modules, virtual environments and build outputs. One command recreates them. Projects you have not touched in a while come first.", confidence: "review", icon: "projects", note: "Nothing in your source code is touched, only generated folders.", items: [
        it(U + "\\Documents\\Deep-Live-Cam\\venv", 2.8 * GB, "Deep-Live-Cam · idle 92 days · restore: python -m venv venv", { kind: "path", stale: true, name: "Deep-Live-Cam/venv" }),
        it(U + "\\Documents\\old-portfolio\\node_modules", 912 * MB, "old-portfolio · idle 210 days · restore: npm install", { kind: "path", stale: true, name: "old-portfolio/node_modules" }),
        it(U + "\\Documents\\zivvo-app\\build", 640 * MB, "zivvo-app · idle 2 days · restore: rebuild the project", { kind: "path", name: "zivvo-app/build" }),
        it(U + "\\Documents\\web\\node_modules", 774 * MB, "web · idle 6 days · restore: npm install", { kind: "path", name: "web/node_modules" })] },
      { id: "downloads", title: "Large, forgotten downloads", description: "Files over 100 MB in Downloads that have not changed in 90 days. Videos, archives, datasets you probably finished with.", confidence: "review", icon: "downloads", note: "Have a look at the list; these go to the recycle bin so they can come back.", items: [
        it(U + "\\Downloads\\lecture-recording-04.mp4", 1.9 * GB, "131 days old · Downloads", { is_dir: false, kind: "path", count: 1 }), it(U + "\\Downloads\\dataset-2025.zip", 1.2 * GB, "97 days old · Downloads", { is_dir: false, kind: "path", count: 1 }), it(U + "\\Downloads\\ubuntu-24.04.iso", 5.7 * GB, "160 days old · Downloads", { is_dir: false, kind: "path", count: 1 })] },
      { id: "installers", title: "Old installers & disk images", description: "Setup files and ISOs downloaded more than 30 days ago. The program is installed; the installer is just taking up room.", confidence: "review", icon: "installers", items: [
        it(U + "\\Downloads\\VSCodeUserSetup-x64-1.131.0.exe", 221 * MB, "101 days old · Downloads", { is_dir: false, kind: "path", count: 1 }), it(U + "\\Downloads\\Git-2.55.0-64-bit.exe", 62 * MB, "97 days old · Downloads", { is_dir: false, kind: "path", count: 1 }), it(U + "\\Downloads\\node-v22.11.0-x64.msi", 31 * MB, "88 days old · Downloads", { is_dir: false, kind: "path", count: 1 })] },
      { id: "system", title: "Needs Windows tools", description: "Protected system areas Windows must clean itself. Open Storage settings and use 'Cleanup recommendations' or 'Temporary files'.", confidence: "manual", icon: "system", hint: "Settings > System > Storage > Cleanup recommendations", hint_url: "ms-settings:storagesense", items: [
        it("C:\\Windows.old", 18.4 * GB, "Previous Windows installation · removed by Storage settings"), it("C:\\hiberfil.sys", 6.4 * GB, "Hibernation file · disable with: powercfg /h off (admin)", { is_dir: false, count: 1 })] },
    ];
    groups.forEach((g) => { g.size = g.items.reduce((a, b) => a + b.size, 0); g.count = g.items.reduce((a, b) => a + b.count, 0); g.more = 0; g.more_size = 0; g.removable = g.confidence !== "manual"; g.duration = 1.2; });
    const sum = (c) => groups.filter((g) => g.confidence === c).reduce((a, g) => a + g.size, 0);
    const analysis = { groups, safe_bytes: sum("safe"), review_bytes: sum("review"), manual_bytes: sum("manual"), reclaimable_bytes: sum("safe") + sum("review"), duration: 48.2, started_at: Date.now() / 1000 };
    const dir = (name, path, size, children = [], file_bytes = 0) => ({ name, path, size, files: Math.round(size / 300000), dirs: children.length * 7, file_bytes, denied: false, newest_mtime: Date.now() / 1000, children });
    const scan = {
      root: dir("me", U, 212.4 * GB, [
        dir("AppData", U + "\\AppData", 61.2 * GB, [dir("Local", U + "\\AppData\\Local", 54.1 * GB, [], 0.2 * GB), dir("Roaming", U + "\\AppData\\Roaming", 7.1 * GB)], 0),
        dir("Documents", U + "\\Documents", 48.7 * GB, [dir("zivvo-app", U + "\\Documents\\zivvo-app", 12.1 * GB), dir("Deep-Live-Cam", U + "\\Documents\\Deep-Live-Cam", 9.8 * GB), dir("CV-guru", U + "\\Documents\\CV-guru", 4.2 * GB), dir("web", U + "\\Documents\\web", 3.9 * GB)], 1.1 * GB),
        dir("Videos", U + "\\Videos", 38.2 * GB, [dir("Captures", U + "\\Videos\\Captures", 21.3 * GB)], 16.9 * GB),
        dir("Downloads", U + "\\Downloads", 31.6 * GB, [dir("Televzr Downloads", U + "\\Downloads\\Televzr Downloads", 2.4 * GB)], 27.8 * GB),
        dir("Pictures", U + "\\Pictures", 14.3 * GB, [dir("Camera Roll", U + "\\Pictures\\Camera Roll", 9.1 * GB), dir("Screenshots", U + "\\Pictures\\Screenshots", 2.2 * GB)], 3 * GB),
        dir(".gradle", U + "\\.gradle", 6.1 * GB), dir("Music", U + "\\Music", 4.4 * GB), dir("OneDrive", U + "\\OneDrive", 3.8 * GB), dir(".nuget", U + "\\.nuget", 2.1 * GB),
      ], 2 * GB),
      largest: [["Videos\\Captures\\stream-2026-02-11.mkv", 7.2 * GB, "Video"], ["Downloads\\ubuntu-24.04.iso", 5.7 * GB, "Disk images"], ["Downloads\\lecture-recording-04.mp4", 1.9 * GB, "Video"], ["Documents\\Deep-Live-Cam\\models\\inswapper_128.onnx", 1.4 * GB, "Models & ML"], ["Downloads\\dataset-2025.zip", 1.2 * GB, "Archives"], ["Videos\\holiday-raw.mov", 980 * MB, "Video"], ["Pictures\\Camera Roll\\PXL_0042.mp4", 410 * MB, "Video"]].map(([rel, size, category]) => ({ path: U + "\\" + rel, name: rel.split("\\").pop(), size, mtime: Date.now() / 1000 - 86400 * 40, category })),
      by_type: [["Video", 71.2 * GB, 1860], ["Code", 42.1 * GB, 410200], ["Other", 36.5 * GB, 88000], ["Archives", 19.7 * GB, 420], ["Images", 15.8 * GB, 24100], ["Disk images", 9.1 * GB, 4], ["Documents", 7.4 * GB, 9100], ["Models & ML", 4.9 * GB, 31], ["Installers", 3.2 * GB, 61], ["Audio", 2.5 * GB, 1200]].map(([category, bytes, files]) => ({ category, bytes, files })),
      by_age: [["This week", 6.1 * GB, 9000], ["This month", 24.5 * GB, 61000], ["Last 6 months", 88.2 * GB, 240000], ["Last year", 61.4 * GB, 150000], ["Older", 32.2 * GB, 70000]].map(([label, bytes, files]) => ({ label, bytes, files })),
      files: 530200, dirs: 61200, bytes: 212.4 * GB, duration: 41.7, denied: 3, truncated: false,
    };
    const dupes = { groups: [
      { digest: "a1", size: 1.9 * GB, copies: 2, reclaimable: 1.9 * GB, category: "Video", files: [[U + "\\Downloads\\lecture-recording-04.mp4", 131], [U + "\\Videos\\lecture-recording-04.mp4", 20]] },
      { digest: "b2", size: 412 * MB, copies: 3, reclaimable: 824 * MB, category: "Archives", files: [[U + "\\Downloads\\assets.zip", 60], [U + "\\Documents\\web\\assets.zip", 58], [U + "\\Downloads\\assets (1).zip", 12]] },
      { digest: "c3", size: 48 * MB, copies: 2, reclaimable: 48 * MB, category: "Images", files: [[U + "\\Pictures\\Camera Roll\\IMG_2231.jpg", 300], [U + "\\Desktop\\IMG_2231.jpg", 3]] },
      { digest: "d4", size: 8.2 * MB, copies: 4, reclaimable: 24.6 * MB, category: "Documents", files: [[U + "\\Documents\\CV.pdf", 400], [U + "\\Downloads\\CV.pdf", 90], [U + "\\Downloads\\CV (1).pdf", 88], [U + "\\OneDrive\\CV.pdf", 30]] },
    ].map((g) => ({ ...g, keep: g.files[0][0], files: g.files.map(([path, daysAgo]) => ({ path, name: path.split("\\").pop(), folder: path.split("\\").slice(0, -1).join("\\"), size: g.size, mtime: Date.now() / 1000 - 86400 * daysAgo })) })),
      total_groups: 4, reclaimable: 0, duplicates: 7, scanned_files: 14798, scanned_bytes: 71 * GB, hashed_bytes: 5.2 * GB, duration: 23.8, roots: [U + "\\Desktop", U + "\\Downloads", U + "\\Documents", U + "\\Pictures"] };
    dupes.reclaimable = dupes.groups.reduce((a, g) => a + g.reclaimable, 0);
    const spaceHistory = { freed_total: 61.3 * GB, cleans: 7, removed_total: 1120, by_group: [{ id: "browser", title: "Browser caches", freed: 22 * GB }, { id: "pkgcache", title: "Package caches", freed: 18 * GB }, { id: "projects", title: "Project build folders", freed: 12 * GB }], timeline: [], last_clean: { timestamp: day(3), freed: 9.4 * GB, removed: 212, trigger: "gui" }, recent: [] };
    return { cfg, plan, batches, timeline, watching: false, volumes, analysis, scan, dupes, spaceHistory, analysed: new URLSearchParams(location.search).get("analysis") === "1" };
  })();

  const mockApi = {
    async bootstrap() { return { ok: true, version: "2.1.0", config_path: "C:\\Users\\me\\AppData\\Roaming\\DesktopCleaner\\config.json", platform: "browser", home: "C:\\Users\\me", config: mockState.cfg, watching: mockState.watching, conflict_strategies: ["rename", "skip", "overwrite"], date_modes: ["none", "year", "year-month"] }; },
    async get_config() { return { ok: true, config: mockState.cfg }; },
    async save_config(cfg) { mockState.cfg = cfg; return { ok: true, config: cfg }; },
    async reset_config() { return { ok: true, config: mockState.cfg }; },
    async import_config() { return { ok: false, error: "cancelled" }; },
    async export_config() { return { ok: true, path: "export.json" }; },
    async pick_folder() { return { ok: true, path: "C:\\Users\\me\\Pictures" }; },
    async open_path() { return { ok: true }; },
    async open_url() { return { ok: true }; },
    async source_summary() { return { ok: true, sources: [
      { path: "C:\\Users\\me\\Desktop", exists: true, files: 14, folders: 3, bytes: 52000000, top_ext: [{ ext: "lnk", count: 8 }, { ext: "png", count: 3 }, { ext: "txt", count: 2 }, { ext: "pdf", count: 1 }] },
      { path: "C:\\Users\\me\\Downloads", exists: true, files: 231, folders: 12, bytes: 9800000000, top_ext: [{ ext: "pdf", count: 30 }, { ext: "jpg", count: 30 }, { ext: "mp4", count: 28 }, { ext: "zip", count: 21 }, { ext: "exe", count: 12 }] },
    ] }; },
    async preview() { return { ok: true, plan: mockState.plan }; },
    async run(only) {
      const acts = mockState.plan.actions.filter((a) => !only || only.includes(a.source));
      for (let i = 0; i < acts.length; i++) { await new Promise((r) => setTimeout(r, 180)); window.__dc_event("progress", { index: i + 1, total: acts.length, name: baseName(acts[i].source) }); }
      const moved = acts.map((a) => ({ src: a.source, dest: a.destination, rule_id: a.rule_id, rule_name: a.rule_name, size: a.size, renamed: false }));
      mockState.batches.push({ type: "batch", batch_id: uid(), timestamp: new Date().toISOString(), sources: mockState.plan.sources, trigger: "gui", undone: false, moves: moved });
      mockState.plan.actions = mockState.plan.actions.filter((a) => !acts.includes(a));
      return { ok: true, result: { batch_id: mockState.batches.at(-1).batch_id, moved, failed: [], skipped_conflicts: [], bytes_moved: moved.reduce((s, m) => s + m.size, 0) }, plan: mockState.plan };
    },
    async undo(id) { const b = id ? mockState.batches.find((x) => x.batch_id === id) : [...mockState.batches].reverse().find((x) => !x.undone); if (!b) return { ok: false, error: "Nothing to undo." }; b.undone = true; return { ok: true, batch_id: b.batch_id, restored: b.moves.length, failed: [] }; },
    async history() { return { ok: true, batches: [...mockState.batches].reverse() }; },
    async stats() {
      const live = mockState.batches.filter((b) => !b.undone);
      const byRule = {};
      live.forEach((b) => b.moves.forEach((m) => { byRule[m.rule_name] = byRule[m.rule_name] || { rule: m.rule_name, files: 0, bytes: 0 }; byRule[m.rule_name].files++; byRule[m.rule_name].bytes += m.size; }));
      const last = mockState.batches.at(-1);
      return { ok: true, stats: { files_moved: 128 + live.reduce((s, b) => s + b.moves.length, 0), bytes_moved: 14e9, runs: 23, undone_runs: mockState.batches.filter((b) => b.undone).length, by_rule: Object.values(byRule).map((r) => ({ ...r, files: r.files + (r.rule === "Images" ? 36 : r.rule === "PDFs" ? 24 : r.rule === "Videos" ? 18 : r.rule === "Archives" ? 11 : 0) })).concat([{ rule: "Screenshots", files: 29, bytes: 2.1e8 }, { rule: "Installers", files: 9, bytes: 3.2e9 }, { rule: "Music", files: 6, bytes: 4.4e7 }]), timeline: mockState.timeline, streak_days: 4, last_run: last && { batch_id: last.batch_id, timestamp: last.timestamp, files: last.moves.length, bytes: last.moves.reduce((s, m) => s + m.size, 0), undone: last.undone, trigger: last.trigger } } };
    },
    async clear_history() { mockState.batches = []; return { ok: true }; },
    async watch_start() { mockState.watching = true; return { ok: true, watching: true }; },
    async watch_stop() { mockState.watching = false; return { ok: true, watching: false }; },
    async watch_status() { return { ok: true, watching: mockState.watching, batches: 0, files: 0 }; },
    async reveal_path() { return { ok: true }; },
    async space_overview() { return { ok: true, volumes: mockState.volumes, history: mockState.spaceHistory, analysis: mockState.analysed ? mockState.analysis : null, busy: null, duplicate_roots: mockState.dupes.roots }; },
    async analyze() {
      const steps = ["Temporary files", "Browser caches", "Application caches", "Thumbnail caches", "Crash reports & logs", "Package manager caches", "Empty folders", "Recycle bin", "Project build folders", "Dependency stores", "Old installers", "Large forgotten downloads", "System files"];
      const byStep = { "Temporary files": "temp", "Browser caches": "browser", "Application caches": "apps", "Crash reports & logs": "crash", "Package manager caches": "pkgcache", "Recycle bin": "recycle", "Project build folders": "projects", "Old installers": "installers", "Large forgotten downloads": "downloads", "System files": "system" };
      for (let i = 0; i < steps.length; i++) {
        window.__dc_event("analyze_progress", { phase: "junk", step: steps[i], index: i, total: steps.length, current: "" });
        await new Promise((r) => setTimeout(r, 160));
        const g = mockState.analysis.groups.find((x) => x.id === byStep[steps[i]]);
        if (g) window.__dc_event("analyze_progress", { phase: "group", group: g, index: i + 1, total: steps.length });
      }
      mockState.analysed = true;
      return { ok: true, analysis: mockState.analysis, volumes: mockState.volumes };
    },
    async cancel_scan() { return { ok: true }; },
    async clean(groupsAll, paths, permanent) {
      const a = mockState.analysis; let freed = 0; const todo = [];
      a.groups.forEach((g) => { if (g.confidence === "manual") return; g.items.forEach((it) => { if (groupsAll.includes(g.id) || paths.includes(it.path)) todo.push([g, it]); }); });
      for (let i = 0; i < todo.length; i++) { await new Promise((r) => setTimeout(r, 120)); freed += todo[i][1].size; window.__dc_event("clean_progress", { phase: "clean", index: i + 1, total: todo.length, name: todo[i][1].name, freed }); }
      a.groups.forEach((g) => { g.items = g.items.filter((it) => !todo.some(([, t]) => t === it)); g.size = g.items.reduce((s, x) => s + x.size, 0); g.count = g.items.reduce((s, x) => s + x.count, 0); });
      a.groups = a.groups.filter((g) => g.items.length);
      const sum = (c) => a.groups.filter((g) => g.confidence === c).reduce((s, g) => s + g.size, 0);
      Object.assign(a, { safe_bytes: sum("safe"), review_bytes: sum("review"), manual_bytes: sum("manual"), reclaimable_bytes: sum("safe") + sum("review") });
      mockState.volumes[0].used -= freed; mockState.volumes[0].free += freed; mockState.volumes[0].percent = Math.round(mockState.volumes[0].used / mockState.volumes[0].total * 1000) / 10;
      mockState.spaceHistory.freed_total += freed; mockState.spaceHistory.cleans++;
      return { ok: true, result: { clean_id: uid(), freed, removed: todo.length, failed: [], by_group: {}, modes: {}, duration: 2.1 }, analysis: a, volumes: mockState.volumes, human_freed: humanSize(freed) };
    },
    async scan_folder(path) {
      for (let i = 1; i <= 6; i++) { await new Promise((r) => setTimeout(r, 140)); window.__dc_event("scan_progress", { phase: "scan", files: i * 88000, dirs: i * 10000, bytes: i * 35 * 1024 ** 3, current: path + "\\Documents\\project-" + i }); }
      return { ok: true, scan: mockState.scan };
    },
    async scan_node(path) {
      const find = (n) => (n.path === path ? n : (n.children || []).map(find).find(Boolean));
      const n = find(mockState.scan.root);
      return n ? { ok: true, node: n } : { ok: false, error: "That folder is not part of the last scan" };
    },
    async list_files(path) { const GB = 1024 ** 3; return { ok: true, total: 5, files: [["budget-2026.xlsx", 2.1e6], ["notes.md", 32e3], ["holiday.mov", 0.9 * GB], ["backup-old.zip", 1.4 * GB], ["resume.pdf", 180e3]].map(([name, size]) => ({ path: path + "\\" + name, name, size, mtime: Date.now() / 1000 - 86400 * 12 })) }; },
    async find_duplicates() {
      for (let i = 1; i <= 5; i++) { await new Promise((r) => setTimeout(r, 160)); window.__dc_event("dupes_progress", i < 3 ? { phase: "collect", files: i * 4000, candidates: i * 60 } : { phase: "hash", done: (i - 2) * 40, total: 120, bytes: (i - 2) * 1.6 * 1024 ** 3, total_bytes: 5.2 * 1024 ** 3, current: "lecture-recording-04.mp4" }); }
      return { ok: true, dupes: mockState.dupes };
    },
    async remove_paths(paths) {
      const d = mockState.dupes; let freed = 0;
      d.groups.forEach((g) => { g.files = g.files.filter((f) => { const gone = paths.includes(f.path); if (gone) freed += f.size; return !gone; }); g.copies = g.files.length; g.reclaimable = g.size * Math.max(0, g.files.length - 1); });
      d.groups = d.groups.filter((g) => g.files.length > 1); d.reclaimable = d.groups.reduce((a, g) => a + g.reclaimable, 0); d.total_groups = d.groups.length;
      mockState.spaceHistory.freed_total += freed;
      return { ok: true, result: { clean_id: uid(), freed, removed: paths.length, failed: [] }, human_freed: humanSize(freed), dupes: d };
    },
    async space_history() { return { ok: true, history: mockState.spaceHistory }; },
    async clear_space_history() { return { ok: true }; },
  };

  let api = null;
  // Inside the real desktop app the page is loaded with ?app=1 and must never fall back to demo data.
  // Inside pywebview the page is loaded from disk (file:) and the bridge object appears on load.
  // In a browser preview it is served over http and we fall back to the mock API after a timeout.
  const IN_APP = location.protocol === "file:" || "pywebview" in window
    || new URLSearchParams(location.search).get("app") === "1";
  async function getApi() {
    if (api) return api;
    if (window.pywebview && window.pywebview.api) { api = window.pywebview.api; return api; }
    await new Promise((resolve) => {
      const t = IN_APP ? null : setTimeout(resolve, 1200);
      window.addEventListener("pywebviewready", () => { if (t) clearTimeout(t); resolve(); }, { once: true });
    });
    if (window.pywebview && window.pywebview.api) { api = window.pywebview.api; return api; }
    api = mockApi;
    document.body.classList.add("mock");
    return api;
  }
  // Forward uncaught front-end errors to the Python log so crashes leave a trace.
  const reportClientError = (message, detail) => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.log_client_error) {
      try { window.pywebview.api.log_client_error(String(message), String(detail || "")); } catch (_) { /* ignore */ }
    }
  };
  window.addEventListener("error", (e) => reportClientError(e.message, `${e.filename}:${e.lineno}`));
  window.addEventListener("unhandledrejection", (e) => reportClientError("unhandled promise rejection", e.reason && (e.reason.stack || e.reason.message || e.reason)));
  async function call(method, ...args) {
    const a = await getApi();
    try {
      const res = await a[method](...args);
      if (res && res.ok === false && res.error !== "cancelled") toast(res.error, "err");
      return res;
    } catch (e) {
      toast(`Something went wrong: ${e && e.message ? e.message : e}`, "err");
      return { ok: false, error: String(e) };
    }
  }

  // ------------------------------------------------------------------ shared with space.js
  const DC = window.DC = { $, $$, el, humanSize, baseName, dirName, timeAgo, fmtWhen, debounce, cssVar, toast, confirmDialog, call,
    onShow: {}, handlers: {}, on(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); },
    driveCard(v) {
      const ico = '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="6" rx="2"/><rect x="3" y="13" width="18" height="6" rx="2"/><path d="M7 8h.01M7 16h.01"/></svg>';
      return el("div", { class: "drive", onclick: () => call("open_path", v.path), title: "Open" },
        el("div", { class: "drive-ico", html: ico }),
        el("div", { class: "drive-main" },
          el("div", { class: "drive-name" }, v.label || "Drive", el("small", {}, v.path)),
          el("div", { class: "drive-meta" }, `${humanSize(v.free)} free of ${humanSize(v.total)}`),
          el("div", { class: `meter ${v.status}` }, el("i", { style: `width:${Math.min(100, v.percent)}%` }))),
        el("div", { class: `drive-pct ${v.status}` }, `${Math.round(v.percent)}%`));
    } };
  // ------------------------------------------------------------------ state
  const state = { boot: null, config: null, plan: null, selected: new Set(), watching: false, stats: null, dirtyRuleIndex: null };

  // ------------------------------------------------------------------ navigation
  function show(view) {
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    if (view === "dashboard") loadDashboard();
    if (view === "preview") loadPreview();
    if (view === "rules") renderRules();
    if (view === "history") loadHistory();
    if (view === "settings") renderSettings();
    (DC.onShow[view] || []).forEach((fn) => fn());
  }
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g) show(g.dataset.goto); });

  // ------------------------------------------------------------------ theme
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    if (state.config) state.config.options.theme = theme;
  }
  $("#themeBtn").addEventListener("click", async () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    if (state.config) await saveConfig();
    if (state.stats) drawCharts(state.stats);
  });

  // ------------------------------------------------------------------ config persistence
  const saveConfig = debounce(async () => {
    const res = await call("save_config", state.config);
    if (res.ok) { state.config = res.config; }
  }, 250);

  // ------------------------------------------------------------------ dashboard
  function countUp(node, target, fmt = (n) => Math.round(n).toLocaleString()) {
    const from = Number(node.dataset.count || 0); const to = Number(target) || 0;
    node.dataset.count = to;
    const start = performance.now(); const dur = 700;
    const step = (now) => { const t = Math.min(1, (now - start) / dur); const e = 1 - Math.pow(1 - t, 3); node.textContent = fmt(from + (to - from) * e); if (t < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }

  async function loadDashboard() {
    const h = new Date().getHours();
    $("#greetTime").textContent = h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
    const [statsRes, srcRes, prevRes, spaceRes] = await Promise.all([call("stats", 30), call("source_summary"), call("preview"), call("space_overview")]);
    if (spaceRes.ok) renderDashSpace(spaceRes);
    if (statsRes.ok) {
      const s = statsRes.stats; state.stats = s;
      countUp($("#statFiles"), s.files_moved);
      $("#statBytes").textContent = humanSize(s.bytes_moved);
      countUp($("#statRuns"), s.runs);
      $("#statRunsFoot").textContent = `${s.undone_runs} undone`;
      countUp($("#statStreak"), s.streak_days);
      drawCharts(s);
      const lr = $("#lastRunCard");
      if (s.last_run) {
        lr.hidden = false;
        const r = s.last_run;
        $("#lastRunBody").replaceChildren(
          el("div", { class: "lr-main" },
            el("span", { class: `lr-pill ${r.undone ? "undone" : ""}` }, r.undone ? "Undone" : "Applied"),
            el("div", {}, el("div", { style: "font-weight:600" }, `${r.files} file${r.files === 1 ? "" : "s"}, ${humanSize(r.bytes)}`),
              el("div", { class: "muted small" }, `${fmtWhen(r.timestamp)} · ${r.trigger}`))),
          r.undone ? el("span", { class: "muted small" }, "Already undone")
                   : el("button", { class: "btn btn-ghost", onclick: () => undoBatch(r.batch_id) }, "Undo this run"));
      } else lr.hidden = true;
    }
    if (srcRes.ok) renderSourceCards(srcRes.sources);
    if (prevRes.ok) {
      state.plan = prevRes.plan;
      const n = prevRes.plan.actions.length;
      const badge = $("#previewBadge"); badge.hidden = n === 0; badge.textContent = n;
      $("#dashSubtitle").textContent = n === 0 ? "Everything is tidy. Nothing is waiting to be organised." : `${n} file${n === 1 ? "" : "s"} (${humanSize(prevRes.plan.bytes_total)}) ready to be organised.`;
    }
  }

  function renderDashSpace(res) {
    $("#dashDrives").replaceChildren(...res.volumes.map((v) => DC.driveCard(v)));
    if (!res.volumes.length) $("#dashDrives").append(el("p", { class: "muted" }, "No drives found."));
    const body = $("#dashSpaceBody"); const a = res.analysis; const h = res.history;
    const badge = $("#spaceBadge");
    if (a && a.reclaimable_bytes > 0) {
      badge.hidden = false; badge.textContent = humanSize(a.reclaimable_bytes);
      body.replaceChildren(
        el("div", { class: "promo-value" }, humanSize(a.reclaimable_bytes)),
        el("div", { class: "muted small" }, "can be freed right now, based on the last analysis."),
        el("div", { class: "promo-row" },
          el("span", { class: "pill safe" }, el("b", {}, humanSize(a.safe_bytes)), " safe"),
          el("span", { class: "pill review" }, el("b", {}, humanSize(a.review_bytes)), " worth a look"),
          h && h.freed_total ? el("span", { class: "pill" }, el("b", {}, humanSize(h.freed_total)), " freed so far") : null),
        el("div", { class: "btn-row" }, el("button", { class: "btn btn-primary", onclick: () => show("space") }, "Review & free up")));
    } else {
      badge.hidden = true;
      body.replaceChildren(
        el("div", { class: "muted" }, "Caches, leftovers and forgotten downloads add up. Find out how much you can get back."),
        h && h.freed_total ? el("div", { class: "promo-row" }, el("span", { class: "pill" }, el("b", {}, humanSize(h.freed_total)), ` freed over ${h.cleans} clean-up${h.cleans === 1 ? "" : "s"}`)) : null,
        el("div", { class: "btn-row" }, el("button", { class: "btn btn-primary", onclick: () => { show("space"); DC.startAnalysis && DC.startAnalysis(); } }, "Analyse now")));
    }
  }

  function renderSourceCards(sources) {
    const folderIcon = '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
    $("#sourceCards").replaceChildren(...sources.map((s) =>
      el("div", { class: "source-card", onclick: () => call("open_path", s.path), title: "Open folder" },
        el("div", { class: `source-ico ${s.exists ? "" : "missing"}`, html: folderIcon }),
        el("div", { class: "source-main" },
          el("div", { class: "source-path" }, s.path),
          el("div", { class: "source-meta" }, s.exists ? `${s.files} files · ${s.folders} folders · ${humanSize(s.bytes)}` : "Folder not found"),
          s.exists && s.top_ext.length ? el("div", { class: "chips" }, s.top_ext.map((t) => el("span", { class: "chip ext" }, `.${t.ext} ${t.count}`))) : null))));
    if (!sources.length) $("#sourceCards").append(el("p", { class: "muted" }, "No folders yet. Add one in Settings."));
  }

  function drawCharts(stats) {
    const text = cssVar("--text-dim");
    // timeline bars
    const c = $("#timelineChart"); const dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth || c.parentElement.clientWidth || 400; const H = 140;
    c.width = W * dpr; c.height = H * dpr; c.style.width = W + "px"; c.style.height = H + "px";
    const ctx = c.getContext("2d"); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, W, H);
    const data = stats.timeline || []; const max = Math.max(1, ...data.map((d) => d.files));
    const gap = 4; const bw = Math.max(2, (W - gap * (data.length - 1)) / Math.max(1, data.length));
    const grad = ctx.createLinearGradient(0, 0, 0, H); grad.addColorStop(0, "#6c8cff"); grad.addColorStop(1, "#a56cff");
    data.forEach((d, i) => {
      const h = d.files === 0 ? 3 : Math.max(6, (d.files / max) * (H - 28));
      const x = i * (bw + gap); const y = H - 20 - h;
      ctx.fillStyle = d.files === 0 ? cssVar("--border-strong") : grad;
      ctx.beginPath(); ctx.roundRect(x, y, bw, h, 3); ctx.fill();
    });
    ctx.fillStyle = text; ctx.font = "11px " + cssVar("--font");
    if (data.length) { ctx.fillText(data[0].date.slice(5), 0, H - 4); const last = data.at(-1).date.slice(5); ctx.fillText(last, W - ctx.measureText(last).width, H - 4); }
    // donut
    const d = $("#donutChart"); d.width = 140 * dpr; d.height = 140 * dpr; d.style.width = "140px"; d.style.height = "140px";
    const dc = d.getContext("2d"); dc.scale(dpr, dpr); dc.clearRect(0, 0, 140, 140);
    const rows = (stats.by_rule || []).slice(0, 7); const total = rows.reduce((s, r) => s + r.files, 0);
    const colours = {}; (state.config ? state.config.rules : []).forEach((r) => { colours[r.name] = r.color; });
    const palette = ["#6c8cff", "#a56cff", "#ff6c9f", "#3ddc84", "#ffb347", "#38bdf8", "#f472b6"];
    let start = -Math.PI / 2;
    dc.lineWidth = 16;
    if (!total) { dc.strokeStyle = cssVar("--border-strong"); dc.beginPath(); dc.arc(70, 70, 54, 0, Math.PI * 2); dc.stroke(); }
    rows.forEach((r, i) => {
      const ang = (r.files / total) * Math.PI * 2;
      dc.strokeStyle = colours[r.rule] || palette[i % palette.length];
      dc.beginPath(); dc.arc(70, 70, 54, start + 0.03, start + ang - 0.03); dc.stroke(); start += ang;
    });
    dc.fillStyle = cssVar("--text"); dc.font = "700 20px " + cssVar("--font"); dc.textAlign = "center"; dc.textBaseline = "middle";
    dc.fillText(total.toLocaleString(), 70, 66);
    dc.fillStyle = text; dc.font = "11px " + cssVar("--font"); dc.fillText("files", 70, 84);
    $("#donutLegend").replaceChildren(...rows.map((r, i) => el("li", {}, el("i", { style: `background:${colours[r.rule] || palette[i % palette.length]}` }), r.rule, el("b", {}, r.files))));
    if (!rows.length) $("#donutLegend").append(el("li", { class: "muted" }, "Run a clean to see categories"));
  }
  window.addEventListener("resize", debounce(() => state.stats && drawCharts(state.stats), 150));

  // ------------------------------------------------------------------ preview
  async function loadPreview() {
    const res = await call("preview");
    if (!res.ok) return;
    state.plan = res.plan;
    state.selected = new Set(res.plan.actions.map((a) => a.source));
    renderPreview();
  }
  function renderPreview() {
    const plan = state.plan; if (!plan) return;
    const n = plan.actions.length;
    $("#previewEmpty").hidden = n > 0;
    $("#previewToolbar").hidden = n === 0;
    const badge = $("#previewBadge"); badge.hidden = n === 0; badge.textContent = n;
    const miss = $("#previewMissing");
    miss.hidden = !plan.missing_sources.length;
    miss.textContent = plan.missing_sources.length ? `Not found: ${plan.missing_sources.join(", ")}` : "";
    const groups = {};
    plan.actions.forEach((a) => { (groups[a.rule_name] = groups[a.rule_name] || { color: a.rule_color, dest: relDest(a), items: [] }).items.push(a); });
    $("#previewGroups").replaceChildren(...Object.entries(groups).map(([name, g]) =>
      el("div", { class: "group" },
        el("div", { class: "group-head" },
          el("span", { class: "group-dot", style: `background:${g.color}` }),
          el("span", { class: "group-title" }, name), el("span", { class: "group-dest" }, `→ ${g.dest}/`),
          el("span", { class: "group-count" }, `${g.items.length} · ${humanSize(g.items.reduce((s, a) => s + a.size, 0))}`)),
        ...g.items.map((a) => {
          const on = state.selected.has(a.source);
          const cb = el("input", { type: "checkbox" }); cb.checked = on;
          cb.addEventListener("change", () => { cb.checked ? state.selected.add(a.source) : state.selected.delete(a.source); row.classList.toggle("off", !cb.checked); updateRunButton(); });
          const row = el("div", { class: `file-row ${on ? "" : "off"}`, title: a.source }, cb,
            el("span", { class: "file-name" }, baseName(a.source)),
            el("span", { class: "file-arrow" }, relDest(a) === g.dest ? "" : `→ ${relDest(a)}/`),
            el("span", { class: "file-size" }, humanSize(a.size)));
          return row;
        }))));
    const sk = $("#skippedBox"); sk.hidden = !plan.skipped.length;
    $("#skippedCount").textContent = plan.skipped.length;
    $("#skippedList").replaceChildren(...plan.skipped.flatMap((s) => [el("span", { class: "sk-name", title: s.path }, (s.is_dir ? "📁 " : "") + baseName(s.path)), el("span", {}, s.reason)]));
    updateRunButton();
  }
  function updateRunButton() {
    const plan = state.plan; if (!plan) return;
    const chosen = plan.actions.filter((a) => state.selected.has(a.source));
    const bytes = chosen.reduce((s, a) => s + a.size, 0);
    $("#runBtn").disabled = chosen.length === 0;
    $("#runBtnLabel").textContent = chosen.length === 0 ? "Nothing selected" : `Move ${chosen.length} file${chosen.length === 1 ? "" : "s"}`;
    $("#previewSummary").textContent = `${chosen.length} of ${plan.actions.length} selected · ${humanSize(bytes)}`;
    const all = $("#selectAll"); all.checked = chosen.length === plan.actions.length; all.indeterminate = chosen.length > 0 && chosen.length < plan.actions.length;
  }
  $("#selectAll").addEventListener("change", (e) => { state.selected = new Set(e.target.checked ? state.plan.actions.map((a) => a.source) : []); renderPreview(); });
  $("#refreshPreviewBtn").addEventListener("click", loadPreview);
  $("#dashCleanBtn").addEventListener("click", () => show("preview"));

  async function runClean() {
    const chosen = state.plan.actions.filter((a) => state.selected.has(a.source)).map((a) => a.source);
    if (!chosen.length) return;
    const all = chosen.length === state.plan.actions.length;
    $("#runBtn").disabled = true;
    const prog = $("#progress"); prog.hidden = false; $("#progressBar").style.width = "0%"; $("#progressText").textContent = "Starting…";
    const res = await call("run", all ? null : chosen);
    prog.hidden = true;
    if (!res.ok) { updateRunButton(); return; }
    const r = res.result;
    if (r) {
      const moved = r.moved.length;
      const bits = [`Moved <b>${moved}</b> file${moved === 1 ? "" : "s"} (${humanSize(r.bytes_moved)})`];
      if (r.skipped_conflicts.length) bits.push(`${r.skipped_conflicts.length} skipped (already exist)`);
      if (r.failed.length) bits.push(`${r.failed.length} failed`);
      toast(bits.join(" · "), r.failed.length ? "warn" : "ok", { action: moved ? "Undo" : null, onAction: () => undoBatch(r.batch_id), ttl: 8000 });
      r.failed.forEach((f) => toast(`${baseName(f.action.source)}: ${f.error}`, "err", { ttl: 7000 }));
    }
    await loadPreview();
  }
  $("#runBtn").addEventListener("click", runClean);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && $("#view-preview").classList.contains("active")) runClean();
    if (e.key === "Escape") { $("#ruleModal").hidden = true; if (!$("#confirmModal").hidden) $("#confirmNo").click(); }
  });

  async function undoBatch(batchId) {
    const res = await call("undo", batchId || null);
    if (!res.ok) return;
    toast(`Restored ${res.restored} file${res.restored === 1 ? "" : "s"}` + (res.failed.length ? `, ${res.failed.length} could not be restored` : ""), res.failed.length ? "warn" : "ok");
    const active = $(".view.active").id.replace("view-", "");
    if (active === "history") loadHistory(); else if (active === "dashboard") loadDashboard(); else if (active === "preview") loadPreview();
  }

  // ------------------------------------------------------------------ rules
  function criteriaChips(r) {
    const m = r.match; const chips = [];
    m.extensions.forEach((e) => chips.push(el("span", { class: "chip ext" }, `.${e}`)));
    m.patterns.forEach((p) => chips.push(el("span", { class: "chip" }, p)));
    if (m.regex) chips.push(el("span", { class: "chip" }, `/${m.regex}/`));
    if (m.min_age_days) chips.push(el("span", { class: "chip" }, `older than ${m.min_age_days}d`));
    if (m.min_size_mb != null) chips.push(el("span", { class: "chip" }, `≥ ${m.min_size_mb} MB`));
    if (m.max_size_mb != null) chips.push(el("span", { class: "chip" }, `≤ ${m.max_size_mb} MB`));
    if (r.date_subfolders !== "none") chips.push(el("span", { class: "chip" }, r.date_subfolders === "year" ? "by year" : "by year/month"));
    if (!chips.length) chips.push(el("span", { class: "chip", style: "color:var(--danger)" }, "matches nothing yet"));
    return chips;
  }
  function renderRules() {
    const list = $("#rulesList"); const rules = state.config.rules;
    list.replaceChildren(...rules.map((r, i) => {
      const card = el("div", { class: `rule ${r.enabled ? "" : "disabled"}`, draggable: "true", "data-index": i },
        el("div", { class: "rule-handle", title: "Drag to reorder", html: '<svg viewBox="0 0 24 24"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>' }),
        el("span", { class: "rule-color", style: `background:${r.color}` }),
        el("div", { class: "rule-main" },
          el("div", { class: "rule-title" }, el("span", { class: "rule-name" }, r.name), el("span", { class: "rule-dest" }, `→ ${r.destination}`)),
          el("div", { class: "rule-criteria" }, criteriaChips(r))),
        el("div", { class: "rule-actions" },
          el("label", { class: "switch", title: r.enabled ? "Enabled" : "Disabled" }, (() => { const i2 = el("input", { type: "checkbox" }); i2.checked = r.enabled; i2.addEventListener("change", () => { r.enabled = i2.checked; card.classList.toggle("disabled", !r.enabled); saveConfig(); }); return i2; })(), el("span", { class: "slider" })),
          el("button", { class: "icon-btn", title: "Edit", onclick: () => openRuleEditor(i), html: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>' }),
          el("button", { class: "icon-btn danger", title: "Delete", onclick: async () => { if (await confirmDialog("Delete rule?", `"${r.name}" will be removed. Files already moved stay where they are.`, { danger: "Delete" })) { rules.splice(i, 1); saveConfig(); renderRules(); } }, html: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>' })));
      // drag and drop reordering
      card.addEventListener("dragstart", (e) => { card.classList.add("dragging"); e.dataTransfer.setData("text/plain", i); e.dataTransfer.effectAllowed = "move"; });
      card.addEventListener("dragend", () => { card.classList.remove("dragging"); $$(".rule").forEach((c) => c.classList.remove("drop-before", "drop-after")); });
      card.addEventListener("dragover", (e) => { e.preventDefault(); const rect = card.getBoundingClientRect(); const before = e.clientY < rect.top + rect.height / 2; card.classList.toggle("drop-before", before); card.classList.toggle("drop-after", !before); });
      card.addEventListener("dragleave", () => card.classList.remove("drop-before", "drop-after"));
      card.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer.getData("text/plain")); const rect = card.getBoundingClientRect();
        let to = i + (e.clientY < rect.top + rect.height / 2 ? 0 : 1);
        if (from < to) to--;
        if (from === to) return;
        const [moved] = rules.splice(from, 1); rules.splice(to, 0, moved);
        saveConfig(); renderRules();
      });
      return card;
    }));
    if (!rules.length) list.append(el("div", { class: "empty" }, el("div", { class: "empty-art" }, "🧩"), el("h3", {}, "No rules yet"), el("p", { class: "muted" }, "Add a rule or restore the defaults.")));
  }
  function openRuleEditor(index) {
    state.dirtyRuleIndex = index;
    const r = index == null ? { name: "", destination: "", color: "#6c8cff", enabled: true, date_subfolders: "none", match: { extensions: [], patterns: [], regex: null, min_age_days: 0, min_size_mb: null, max_size_mb: null } } : state.config.rules[index];
    $("#ruleModalTitle").textContent = index == null ? "New rule" : `Edit “${r.name}”`;
    $("#rName").value = r.name; $("#rColor").value = r.color; $("#rDest").value = r.destination;
    $("#rExt").value = r.match.extensions.join(", "); $("#rPatterns").value = r.match.patterns.join(", ");
    $("#rRegex").value = r.match.regex || ""; $("#rAge").value = r.match.min_age_days || 0;
    $("#rMin").value = r.match.min_size_mb ?? ""; $("#rMax").value = r.match.max_size_mb ?? ""; $("#rDate").value = r.date_subfolders;
    $("#ruleError").hidden = true;
    $("#ruleModal").hidden = false; $("#rName").focus();
  }
  function closeRuleEditor() { $("#ruleModal").hidden = true; }
  $("#ruleModalClose").addEventListener("click", closeRuleEditor);
  $("#ruleCancelBtn").addEventListener("click", closeRuleEditor);
  $("#ruleModal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeRuleEditor(); });
  $("#addRuleBtn").addEventListener("click", () => openRuleEditor(null));
  $("#ruleSaveBtn").addEventListener("click", () => {
    const split = (s) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
    const name = $("#rName").value.trim(); const dest = $("#rDest").value.trim();
    const exts = split($("#rExt").value).map((e) => e.replace(/^\./, "").toLowerCase());
    const pats = $("#rPatterns").value.split(",").map((x) => x.trim()).filter(Boolean);
    const regex = $("#rRegex").value.trim() || null;
    const err = $("#ruleError");
    const fail = (msg) => { err.textContent = msg; err.hidden = false; };
    if (!name) return fail("Give the rule a name.");
    if (!dest) return fail("Choose a destination folder.");
    if (!exts.length && !pats.length && !regex) return fail("Add at least one extension, pattern or regular expression, otherwise the rule can never match.");
    if (regex) { try { new RegExp(regex); } catch (e) { return fail(`That regular expression is not valid: ${e.message}`); } }
    const rule = {
      id: state.dirtyRuleIndex == null ? uid() : state.config.rules[state.dirtyRuleIndex].id,
      name, destination: dest, color: $("#rColor").value, enabled: state.dirtyRuleIndex == null ? true : state.config.rules[state.dirtyRuleIndex].enabled,
      date_subfolders: $("#rDate").value,
      match: { extensions: exts, patterns: pats, regex, min_age_days: Number($("#rAge").value) || 0,
        min_size_mb: $("#rMin").value === "" ? null : Number($("#rMin").value), max_size_mb: $("#rMax").value === "" ? null : Number($("#rMax").value) },
    };
    if (state.dirtyRuleIndex == null) state.config.rules.push(rule); else state.config.rules[state.dirtyRuleIndex] = rule;
    saveConfig(); renderRules(); closeRuleEditor();
    toast(`Rule “${name}” saved`, "ok");
  });
  $("#resetRulesBtn").addEventListener("click", async () => {
    if (!(await confirmDialog("Restore default rules?", "Your current rules will be replaced with the built-in set. Folders and other settings are kept.", { danger: "Restore" }))) return;
    const res = await call("reset_config");
    if (res.ok) { const keep = state.config; state.config = res.config; state.config.sources = keep.sources; state.config.options = keep.options; await call("save_config", state.config); renderRules(); toast("Default rules restored", "ok"); }
  });

  // ------------------------------------------------------------------ history
  async function loadHistory() {
    const res = await call("history", 100);
    if (!res.ok) return;
    const list = $("#historyList");
    list.replaceChildren(...res.batches.map((b) => {
      const bytes = b.moves.reduce((s, m) => s + m.size, 0);
      const card = el("div", { class: "batch" });
      card.append(
        el("div", { class: "batch-head", onclick: () => card.classList.toggle("open") },
          el("span", { class: `lr-pill ${b.undone ? "undone" : ""}` }, b.undone ? "Undone" : "Applied"),
          el("div", {}, el("div", { class: "batch-when" }, fmtWhen(b.timestamp)), el("div", { class: "batch-sub" }, `${b.moves.length} file${b.moves.length === 1 ? "" : "s"} · ${humanSize(bytes)} · ${b.trigger} · ${timeAgo(b.timestamp)}`)),
          el("code", { class: "muted" }, b.batch_id),
          b.undone ? el("span") : el("button", { class: "btn btn-ghost btn-sm", onclick: (e) => { e.stopPropagation(); undoBatch(b.batch_id); } }, "Undo")),
        el("div", { class: "batch-body" }, ...b.moves.map((m) => el("div", { class: "batch-move" }, el("span", { class: "m-src", title: m.src }, baseName(m.src)), el("span", {}, "→"), el("span", { class: "m-dest", title: m.dest }, m.dest + (m.renamed ? "  (renamed)" : ""))))));
      return card;
    }));
    if (!res.batches.length) list.append(el("div", { class: "empty" }, el("div", { class: "empty-art" }, "🗂️"), el("h3", {}, "No runs yet"), el("p", { class: "muted" }, "Your first clean will show up here, ready to undo.")));
  }
  $("#clearHistoryBtn").addEventListener("click", async () => {
    if (!(await confirmDialog("Clear history?", "The undo journal will be deleted. Files are not touched, but earlier runs can no longer be undone.", { danger: "Clear" }))) return;
    await call("clear_history"); loadHistory(); toast("History cleared", "ok");
  });

  // ------------------------------------------------------------------ settings
  function renderSettings() {
    renderSpaceSettings();
    const o = state.config.options;
    $("#sourceList").replaceChildren(...state.config.sources.map((s, i) =>
      el("li", {}, el("span", { title: s }, s),
        el("button", { class: "icon-btn", title: "Open", onclick: () => call("open_path", s), html: '<svg viewBox="0 0 24 24"><path d="M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>' }),
        el("button", { class: "icon-btn danger", title: "Remove", onclick: () => { state.config.sources.splice(i, 1); saveConfig(); renderSettings(); }, html: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>' }))));
    if (!state.config.sources.length) $("#sourceList").append(el("li", { class: "muted" }, "No folders. Add one to get started."));
    $("#optConflict").value = o.conflict; $("#optRecent").value = o.skip_recent_seconds; $("#optInterval").value = o.watch_interval_seconds;
    $("#optUnmatched").value = o.unmatched_destination || ""; $("#optMoveFolders").checked = !!o.move_folders; $("#optIgnore").value = (o.ignore || []).join("\n");
    $("#configPathLabel").textContent = state.boot.config_path;
  }
  $("#addSourceBtn").addEventListener("click", async () => {
    const res = await call("pick_folder");
    if (res.ok && res.path && !state.config.sources.includes(res.path)) { state.config.sources.push(res.path); saveConfig(); renderSettings(); toast("Folder added", "ok"); }
  });
  $("#optConflict").addEventListener("change", (e) => { state.config.options.conflict = e.target.value; saveConfig(); });
  $("#optRecent").addEventListener("change", (e) => { state.config.options.skip_recent_seconds = Math.max(0, Number(e.target.value) || 0); saveConfig(); });
  $("#optInterval").addEventListener("change", (e) => { state.config.options.watch_interval_seconds = Math.max(1, Number(e.target.value) || 1); saveConfig(); });
  $("#optUnmatched").addEventListener("change", (e) => { state.config.options.unmatched_destination = e.target.value.trim() || null; saveConfig(); });
  $("#optMoveFolders").addEventListener("change", (e) => { state.config.options.move_folders = e.target.checked; saveConfig(); });
  $("#optIgnore").addEventListener("input", debounce((e) => { state.config.options.ignore = e.target.value.split("\n").map((x) => x.trim()).filter(Boolean); saveConfig(); }, 400));
  function renderSpaceSettings() {
    const sp = state.config.space || (state.config.space = { project_roots: [], stale_project_days: 60, installer_age_days: 30, large_file_mb: 100, old_download_days: 90, duplicate_roots: [], duplicate_min_mb: 1, exclude: [], permanent_delete: false });
    $("#spStale").value = sp.stale_project_days; $("#spInstaller").value = sp.installer_age_days; $("#spOldDl").value = sp.old_download_days;
    $("#spLarge").value = sp.large_file_mb; $("#spDupMin").value = sp.duplicate_min_mb; $("#spRoots").value = (sp.project_roots || []).join("\n");
    $("#spPermanent").checked = !!sp.permanent_delete;
  }
  const bindSpaceNum = (id, key, min = 0) => $(id).addEventListener("change", (e) => { state.config.space[key] = Math.max(min, Number(e.target.value) || 0); saveConfig(); });
  bindSpaceNum("#spStale", "stale_project_days"); bindSpaceNum("#spInstaller", "installer_age_days"); bindSpaceNum("#spOldDl", "old_download_days");
  bindSpaceNum("#spLarge", "large_file_mb", 1); bindSpaceNum("#spDupMin", "duplicate_min_mb");
  $("#spRoots").addEventListener("input", debounce((e) => { state.config.space.project_roots = e.target.value.split("\n").map((x) => x.trim()).filter(Boolean); saveConfig(); }, 400));
  $("#spPermanent").addEventListener("change", (e) => { state.config.space.permanent_delete = e.target.checked; saveConfig(); });
  $("#openConfigDirBtn").addEventListener("click", () => call("open_path", dirName(state.boot.config_path)));
  $("#importBtn").addEventListener("click", async () => { const res = await call("import_config"); if (res.ok) { state.config = res.config; renderSettings(); toast("Configuration imported", "ok"); } });
  $("#exportBtn").addEventListener("click", async () => { const res = await call("export_config"); if (res.ok) toast(`Exported to ${res.path}`, "ok"); });

  // ------------------------------------------------------------------ watch mode
  function setWatching(on) {
    state.watching = on;
    $("#watchToggle").checked = on;
    $("#watchCard").classList.toggle("on", on);
    $("#watchSub").textContent = on ? "Watching for new files" : "Off";
  }
  $("#watchToggle").addEventListener("change", async (e) => {
    const res = await call(e.target.checked ? "watch_start" : "watch_stop");
    if (res.ok) { setWatching(res.watching); toast(res.watching ? "Watch mode on. New files will be organised as they settle." : "Watch mode off", res.watching ? "ok" : "info"); }
    else e.target.checked = state.watching;
  });

  // events pushed from Python
  window.__dc_event = (name, payload) => {
    if (name === "progress") {
      const pct = payload.total ? Math.round((payload.index / payload.total) * 100) : 0;
      $("#progressBar").style.width = pct + "%";
      $("#progressText").textContent = `Moving ${payload.index} of ${payload.total}: ${payload.name}`;
    } else if (name === "watch_batch") {
      toast(`Watch mode organised ${payload.moved} file${payload.moved === 1 ? "" : "s"}: ${payload.names.join(", ")}`, "ok", { ttl: 6000 });
      const active = $(".view.active").id.replace("view-", "");
      if (active === "dashboard") loadDashboard(); else if (active === "preview") loadPreview(); else if (active === "history") loadHistory();
    } else if (name === "watch_tick") {
      const badge = $("#previewBadge"); badge.hidden = !payload.pending; badge.textContent = payload.pending;
    }
    (DC.handlers[name] || []).forEach((fn) => { try { fn(payload); } catch (e) { reportClientError(`handler ${name}: ${e && e.message}`, e && e.stack); } });
  };

  // ------------------------------------------------------------------ boot
  (async () => {
    const boot = await call("bootstrap");
    if (!boot.ok) return;
    state.boot = boot; state.config = boot.config; DC.state = state; DC.show = show;
    const params = new URLSearchParams(location.search);
    const mock = document.body.classList.contains("mock");
    applyTheme(params.get("theme") || boot.config.options.theme || "dark");
    if (mock && params.get("watch") === "1") setWatching(true); else setWatching(!!boot.watching);
    $("#versionLabel").textContent = `v${boot.version}` + (mock && !params.has("clean") ? " · browser preview" : "");
    const views = ["dashboard", "preview", "rules", "history", "space", "map", "dupes", "settings"];
    const wanted = location.hash.replace("#", "");
    show(views.includes(wanted) ? wanted : "dashboard");
    if (mock && params.get("modal") === "rule") setTimeout(() => openRuleEditor(0), 300);
    if (params.get("scan") === "1" && DC.autoScan) setTimeout(DC.autoScan, 200);
    if (params.get("dupes") === "1" && DC.autoDupes) setTimeout(DC.autoDupes, 200);
  })();
})();
