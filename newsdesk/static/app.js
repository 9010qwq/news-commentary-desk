/* Local-only UI. No credentials are kept in browser storage. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const defaults = {
    sources: ["cctv", "people", "bjnews", "xinhua", "thepaper"], target_count: 5,
    daily_time: "20:00", timezone: "Asia/Shanghai", auto_collect: false,
    weekly_email_enabled: false, smtp_host: "", smtp_port: 465, smtp_mode: "ssl",
    smtp_username: "", smtp_from_addr: "", smtp_to_addr: "", max_message_mb: 20,
    api_enabled: false, api_base_url: "https://api.deepseek.com", api_model: "", api_daily_limit: 30, media_balance: true,
    include_keywords: [], exclude_keywords: [], custom_source_urls: []
  };
  const sourceFallback = [
    {id:"cctv",name:"央视新闻"}, {id:"people",name:"人民日报"},
    {id:"bjnews",name:"新京报"}, {id:"xinhua",name:"新华网"},
    {id:"thepaper",name:"澎湃新闻"}
  ];
  const ui = {token: null, state: null, settings: {...defaults}, sources: sourceFallback,
    running: false, locked: false, formDirty: false, loaded: false, articles: [],
    articleRequest: 0, refreshing: false, poll: null, proposal: null, sendRange: null, lastFiles: null};
  const jobButtons = ["collect", "backfill", "export", "send", "save-settings", "propose-rule", "apply-rule"];
  const lines = (value) => [...new Set(String(value || "").split(/\r?\n/).map(x => x.trim()).filter(Boolean))];
  const list = (value) => Array.isArray(value) ? value : [];
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined && text !== null) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  };
  function safeURL(value, local = false) {
    try {
      const result = new URL(String(value || ""), location.origin);
      if (!value || !["http:","https:"].includes(result.protocol) || result.username || result.password) return null;
      if (local && result.origin !== location.origin) return null;
      return result.href;
    } catch (_) { return null; }
  }
  function originalLink(text, href) {
    const url = safeURL(href);
    const el = node(url ? "a" : "span", text);
    if (url) { el.href = url; el.target = "_blank"; el.rel = "noopener noreferrer"; }
    return el;
  }
  function localDay(zone = "Asia/Shanghai") {
    try {
      const parts = new Intl.DateTimeFormat("en-CA", {timeZone: zone, year:"numeric", month:"2-digit", day:"2-digit"}).formatToParts(new Date());
      const values = Object.fromEntries(parts.map(x => [x.type, x.value]));
      return `${values.year}-${values.month}-${values.day}`;
    } catch (_) { return new Date().toISOString().slice(0, 10); }
  }
  function shiftDay(day, shift) {
    const date = new Date(`${day}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + shift);
    return date.toISOString().slice(0,10);
  }
  function weekRange() {
    const range = ui.state && ui.state.week_range;
    if (Array.isArray(range) && range.length >= 2) return {start:range[0], end:range[1]};
    if (range && range.start && range.end) return {start:range.start, end:range.end};
    const today = localDay("Asia/Shanghai");
    return {start:shiftDay(today,-7), end:shiftDay(today,-1)};
  }
  function timeLabel(value, zone) {
    if (!value) return "未记录";
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return `${value}（仅日期）`;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value);
    try { return new Intl.DateTimeFormat("zh-CN", {timeZone:zone || ui.settings.timezone || "Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date); }
    catch (_) { return String(value); }
  }
  function feedback(text, type = "info") {
    $("message").textContent = text;
    $("message").className = `notice message ${type}`;
    $("message").hidden = false;
  }
  function connection(online) {
    $("connection").className = `connection ${online ? "online" : "offline"}`;
    $("connection").lastElementChild.textContent = online ? "本机服务已连接" : "本机服务未连接";
  }
  async function api(path, body) {
    const headers = {"X-Local-Token": ui.token || ""};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try { response = await fetch(path, {method:body === undefined ? "GET" : "POST",headers,body:body === undefined ? undefined : JSON.stringify(body),credentials:"same-origin",cache:"no-store"}); }
    catch (_) { connection(false); throw new Error("无法连接本机程序。请确认启动窗口仍在运行，再点击「刷新」。"); }
    let data;
    try { data = await response.json(); } catch (_) { throw new Error(`本机服务返回了无法识别的结果（HTTP ${response.status}），请查看程序运行记录。`); }
    if (!response.ok || data.ok === false || data.error) {
      const error = data.error || data.detail || data.message;
      let message = typeof error === "string" ? error : error && error.message;
      if (!message && Array.isArray(error)) message = error.map(x => x.msg || "参数不正确").join("；");
      if (response.status === 401 || response.status === 403) message = "本机验证已失效。请刷新整个页面后重试。";
      throw new Error(message || `操作未完成（HTTP ${response.status}），请查看运行记录。`);
    }
    connection(true);
    return data;
  }
  function setButtons() {
    for (const id of jobButtons) $(id).disabled = !ui.loaded || ui.locked || ui.running;
    $("refresh").disabled = ui.refreshing;
  }
  async function action(button, work) {
    if (ui.locked || ui.running || !ui.loaded) return;
    ui.locked = true;
    const label = button.textContent;
    button.textContent = "处理中…";
    setButtons();
    try { await work(); }
    catch (error) { feedback(error.message || "操作未完成，请重试。", "error"); }
    finally { ui.locked = false; button.textContent = label; setButtons(); }
  }
  function normalizeSettings(raw = {}) {
    const settings = {...defaults, ...raw};
    // Permit existing installations with nested SMTP/API settings.
    if (raw.smtp) for (const field of ["host","port","mode","username","from_addr","to_addr"]) if (raw.smtp[field] !== undefined) settings[`smtp_${field}`] = raw.smtp[field];
    if (raw.api) for (const field of ["enabled","base_url","model"]) if (raw.api[field] !== undefined) settings[`api_${field}`] = raw.api[field];
    if (raw.source_mode) settings.media_balance = raw.source_mode === "balanced";
    settings.sources = list(settings.sources);
    return settings;
  }
  function renderSources(fillForm = false) {
    $("active-sources").replaceChildren();
    if (fillForm) $("source-options").replaceChildren();
    for (const source of ui.sources) {
      const selected = ui.settings.sources.includes(source.id);
      if (selected) { const li = node("li"); li.append(originalLink(source.name, source.url)); $("active-sources").append(li); }
      if (fillForm) {
        const label = node("label", undefined, "source-option");
        const input = node("input"); input.type = "checkbox"; input.name = "source"; input.value = source.id; input.checked = selected;
        label.append(input, node("span", source.name)); $("source-options").append(label);
      }
    }
    if (!$("active-sources").children.length) $("active-sources").append(node("li", "尚未选择媒体"));
  }
  const fieldMap = {"target-count":"target_count", "daily-time":"daily_time", "timezone":"timezone", "smtp-host":"smtp_host", "smtp-port":"smtp_port", "smtp-mode":"smtp_mode", "smtp-username":"smtp_username", "smtp-from":"smtp_from_addr", "smtp-to":"smtp_to_addr", "max-message-mb":"max_message_mb", "api-base-url":"api_base_url", "api-model":"api_model", "api-daily-limit":"api_daily_limit"};
  const checkMap = {"auto-collect":"auto_collect", "weekly-email":"weekly_email_enabled", "api-enabled":"api_enabled", "media-balance":"media_balance"};
  const arrayMap = {"include-keywords":"include_keywords", "exclude-keywords":"exclude_keywords", "custom-source-urls":"custom_source_urls"};
  function fillSettings() {
    for (const [id, key] of Object.entries(fieldMap)) $(id).value = ui.settings[key] ?? "";
    for (const [id, key] of Object.entries(checkMap)) $(id).checked = Boolean(ui.settings[key]);
    for (const [id, key] of Object.entries(arrayMap)) $(id).value = list(ui.settings[key]).join("\n");
    $("remember-secrets").checked = Boolean(ui.settings.remember_secrets);
    renderSources(true);
  }
  function renderSecrets() {
    const status = (ui.state && ui.state.secret_status) || {};
    const hasSMTP = status.smtp || status.smtp_password || status.smtp_password_set || status.smtp_password_available;
    const hasAPI = status.api || status.api_key || status.api_key_set || status.api_key_available;
    $("smtp-secret-status").textContent = hasSMTP ? "已配置授权码 / 密码；留空不替换，页面不会返回明文" : "尚未配置。通常需填写邮箱授权码，而非网页登录密码";
    $("api-secret-status").textContent = hasAPI ? "已配置 API Key；留空不替换，页面不会返回明文" : "尚未配置 API Key；仅用于你指定的 API 服务";
    const available = status.credential_manager_available ?? status.keyring_available ?? status.persistent_available;
    const storage = status.storage || status.storage_mode;
    $("credential-status").textContent = available === false ? "这台电脑当前无法使用 Windows 凭据管理器；请使用仅本次运行方式。" : (storage ? `当前凭据存储：${storage === "ephemeral" ? "仅本次程序运行" : storage}` : "");
    $("remember-secrets").disabled = available === false;
    if (available === false) $("remember-secrets").checked = false;
  }
  function renderRoutine() {
    $("daily-schedule").textContent = `每天 ${ui.settings.daily_time} · ${ui.settings.timezone === "Asia/Shanghai" ? "北京时间" : ui.settings.timezone}`;
    for (const [id, enabled] of [["daily-enabled", ui.settings.auto_collect],["weekly-enabled", ui.settings.weekly_email_enabled]]) {
      $(id).textContent = enabled ? "已开启 · 程序运行时生效" : "未开启";
      $(id).className = `badge ${enabled ? "success" : "neutral"}`;
    }
    const range = weekRange();
    $("week-range").textContent = `${range.start} 至 ${range.end}`;
    const schedule = (ui.state && ui.state.schedule) || {};
    const next = [];
    if (schedule.next_daily || schedule.daily) next.push(`下次采集：${timeLabel(schedule.next_daily || schedule.daily)}`);
    if (schedule.next_weekly || schedule.weekly) next.push(`下次周报：${timeLabel(schedule.next_weekly || schedule.weekly, "Asia/Shanghai")}`);
    $("next-schedule").textContent = next.join(" · ");
    $("today-label").textContent = "北京时间";
    $("today-value").textContent = localDay("Asia/Shanghai").replaceAll("-", " / ");
    renderSources();
  }
  function renderJob() {
    const wasRunning = ui.running;
    const status = (ui.state && ui.state.status) || {};
    const job = status.job || status.current_job || status;
    const stage = typeof status === "string" ? status : status.state || status.status;
    const running = Boolean(status.running || status.busy || job.running || ["running","queued","collecting","exporting","sending"].includes(stage));
    ui.running = running;
    $("job-panel").hidden = !running;
    const titles = {collect:"正在采集",backfill:"正在补采",export:"正在整理资料",send:"正在发送邮件",weekly:"正在整理周报"};
    $("job-title").textContent = titles[job.kind || job.type] || "任务正在运行";
    $("job-detail").textContent = job.message || status.message || "请保持程序运行，结果会自动更新";
    if (wasRunning && !running) {
      const message = status.message || "任务已结束，请核对文章和截图状态。";
      const latest = list(ui.state && ui.state.events)[0];
      feedback(message, message.startsWith("已完成") ? "success" : latest && latest.level === "error" ? "error" : "info");
    }
    const result = status.last_result;
    if (result && result.files && JSON.stringify(result.files) !== ui.lastFiles) {
      ui.lastFiles = JSON.stringify(result.files);
      renderDownloads(result);
    }
    setButtons();
  }
  function renderEvents() {
    const events = list(ui.state && ui.state.events).slice(0,30);
    $("events").replaceChildren();
    if (!events.length) $("events").append(node("li", "暂无运行记录。完成采集后可在这里查看结果。"));
    for (const event of events) {
      const li = node("li");
      if (typeof event === "string") li.textContent = event;
      else {
        const time = node("time", timeLabel(event.ts || event.timestamp || event.created_at || event.time));
        const text = node("span", event.message || event.detail || event.type || "运行记录", event.level === "error" ? "error" : "");
        li.append(time,text);
      }
      $("events").append(li);
    }
  }
  function shotSuccess(article) { return ["ok","success","saved","complete","completed","captured"].includes(String(article.screenshot_status || "").toLowerCase()); }
  function renderArticles(articles) {
    ui.articles = list(articles);
    const container = $("article-list");
    container.setAttribute("aria-busy", "false");
    container.replaceChildren();
    $("count").replaceChildren(document.createTextNode(String(ui.articles.length)),node("small", " 篇"));
    const target = Number(ui.settings.target_count) || 5;
    $("target").replaceChildren(document.createTextNode(String(target)),node("small", " 篇"));
    const complete = ui.articles.filter(shotSuccess).length;
    const failed = ui.articles.length - complete;
    $("shot-failed").replaceChildren(document.createTextNode(String(failed)),node("small", " 篇"));
    const missing = Math.max(0, target - complete);
    $("shortfall").className = `collection-notice ${missing ? "warning" : "success"}`;
    $("shortfall").textContent = missing ? `实际收集 ${ui.articles.length} 篇，成功截图 ${complete} 篇，距完整资料目标还差 ${missing} 篇。未成功截图的文章不计达标。` : `实际收集 ${ui.articles.length} 篇，成功截图 ${complete} 篇，达到当天总目标。${failed ? `另有 ${failed} 篇截图尚未成功，请查看下方状态。` : ""}`;
    if (!ui.articles.length) {
      const empty = node("div", undefined, "empty-state");
      empty.append(node("span", "▤"),node("h3", "这一天，还没有留存的评论"),node("p", "点击「立即采集」开始；也可以换个日期，查看以前的积累。"));
      container.append(empty); return;
    }
    for (const article of ui.articles) {
      const row = node("article", undefined, "article");
      const top = node("div", undefined, "article-top");
      const media = ui.sources.find(x=>x.id === article.media);
      top.append(node("span", media ? media.name : article.media || "来源未标明", "media-tag"),node("span", article.author || "作者未标明", "article-author"));
      const title = node("h3"); title.append(originalLink(article.title || "无标题文章",article.url));
      const meta = node("div", undefined, "article-meta");
      meta.append(node("span", `发表：${article.published_at ? timeLabel(article.published_at) : article.date || "未记录"}`),node("span", `采集：${timeLabel(article.captured_at)}`));
      const bottom = node("div", undefined, "article-bottom");
      const status = String(article.screenshot_status || "pending").toLowerCase();
      const success = shotSuccess(article);
      const isFailed = ["failed","error","blocked","unavailable"].includes(status);
      bottom.append(node("span", success ? "✓ 截图已保存" : isFailed ? "截图失败" : "截图未完成", `badge ${success ? "success" : isFailed ? "error" : "warning"}`));
      const links = node("div", undefined, "article-links");
      links.append(originalLink("阅读原文 ↗", article.url));
      const screenshot = safeURL(article.screenshot_url, true);
      if (screenshot && success) {
        const preview = node("button", "查看截图", "text-button"); preview.type = "button";
        preview.addEventListener("click", () => {
          $("shot-title").textContent = article.title || "网页截图";
          $("shot-caption").textContent = `文章发表：${article.published_at || article.date || "未记录"} · 截图采集：${article.captured_at || "未记录"}`;
          $("shot-image").src = screenshot;
          $("shot-image").onerror = () => { $("shot-caption").textContent = "截图文件暂时无法读取，请检查本机资料目录或重新采集。"; };
          $("shot-dialog").showModal();
        }); links.append(preview);
      }
      bottom.append(links); row.append(top,title,meta,bottom);
      if (article.error) row.append(node("p",article.error,"article-error"));
      if (article.status && ["failed","error"].includes(article.status)) row.append(node("p","文章采集状态：失败，请核对原文与运行记录","article-error"));
      container.append(row);
    }
  }
  async function loadArticles() {
    const id = ++ui.articleRequest;
    const day = $("day").value;
    if (!day) return;
    $("article-list").setAttribute("aria-busy", "true");
    try {
      const result = await api(`/api/articles?date=${encodeURIComponent(day)}`);
      if (id === ui.articleRequest && day === $("day").value) renderArticles(result.articles);
    } catch (error) {
      if (id === ui.articleRequest) { $("article-list").setAttribute("aria-busy", "false"); feedback(error.message, "error"); }
    }
  }
  async function refresh({fill = false, articles = true} = {}) {
    if (ui.refreshing) return;
    ui.refreshing = true; setButtons();
    try {
      ui.state = await api("/api/state");
      ui.settings = normalizeSettings(ui.state.settings);
      if (list(ui.state.sources).length) ui.sources = ui.state.sources;
      if (fill || (!ui.loaded && !ui.formDirty)) fillSettings();
      ui.loaded = true;
      renderRoutine(); renderSecrets(); renderJob(); renderEvents();
      if (articles) await loadArticles();
    } finally { ui.refreshing = false; setButtons(); schedulePoll(); }
  }
  function schedulePoll() {
    clearTimeout(ui.poll);
    ui.poll = setTimeout(async () => {
      try { await refresh(); } catch (error) { connection(false); }
    }, ui.running ? 2200 : 15000);
  }
  function readRange() {
    const start = $("range-start").value, end = $("range-end").value;
    if (!start || !end) throw new Error("请填写开始日期和结束日期。");
    if (start > end) throw new Error("开始日期不能晚于结束日期。");
    if (end > localDay(ui.settings.timezone)) throw new Error("结束日期不能在未来。");
    return {start,end};
  }
  function useWeek() { const range=weekRange(); $("range-start").value=range.start; $("range-end").value=range.end; }
  function renderDownloads(result) {
    const container = $("downloads"); container.replaceChildren(); container.hidden = false;
    container.append(node("strong", "资料已生成"));
    const counts = result.counts;
    let countText = "";
    if (typeof counts === "number") countText = `共 ${counts} 篇文章`;
    else if (counts && typeof counts === "object") {
      const total = counts.total ?? counts.articles ?? counts.article_count;
      countText = total !== undefined ? `共 ${total} 篇文章${counts.screenshots !== undefined ? `，${counts.screenshots} 张可用截图` : ""}` : Object.entries(counts).map(([date,count])=>`${date}：${count} 篇`).join("；");
    }
    if (countText) container.append(node("p",countText,"export-summary"));
    let links = 0;
    for (const file of list(result.files)) {
      const url = safeURL(file.url,true); if (!url) continue;
      const link = node("a", `↓ ${file.name || "下载文件"}`); link.href=url; link.download=file.name || ""; container.append(link); links++;
    }
    if (!links) container.append(node("p", "未收到可用的本机下载链接，请查看运行记录。", "export-shortfall"));
    const shortfalls = Array.isArray(result.shortfalls) ? result.shortfalls.filter(item => typeof item === "string" || Number(item.shortfall ?? item.missing ?? 0) > 0) : result.shortfalls;
    if (shortfalls && (Array.isArray(shortfalls) ? shortfalls.length : Object.keys(shortfalls).length)) {
      let text;
      if (Array.isArray(shortfalls)) text = shortfalls.map(x=>typeof x === "string" ? x : `${x.date || ""}：实际 ${x.articles ?? x.actual ?? x.count ?? 0} 篇${x.screenshots !== undefined ? `，成功截图 ${x.screenshots} 篇` : ""}，缺 ${x.shortfall ?? x.missing ?? 0} 篇`).join("；");
      else text = Object.entries(shortfalls).map(([day,value])=>`${day}：${typeof value === "object" ? `实际 ${value.actual ?? value.count ?? 0} 篇` : `缺 ${value} 篇`}`).join("；");
      container.append(node("p",`部分日期未达目标：${text}`,"export-shortfall"));
    }
  }
  async function startJob(path, body, text) {
    const result = await api(path, body);
    if (result.files) renderDownloads(result);
    feedback(result.message || text, "info");
    await refresh();
  }
  $("collect").addEventListener("click", () => action($("collect"), async () => {
    const date = $("day").value;
    if (!date) throw new Error("请先选择文章发表日期。");
    if (date > localDay(ui.settings.timezone)) throw new Error("不能采集未来日期的文章。");
    const urls = lines($("article-urls").value);
    if (urls.some(url=>!safeURL(url) || !/^https?:\/\//i.test(url))) throw new Error("补充链接须是以 https:// 或 http:// 开头的完整网址，每行一个。");
    await startJob("/api/collect", {date, urls}, "采集任务已开始。请保持程序运行，结果将自动显示。");
  }));
  $("day").addEventListener("change", loadArticles);
  $("refresh").addEventListener("click", async () => { try { ui.token ? await refresh() : await boot(); } catch(error) { feedback(error.message,"error"); } });
  $("use-week").addEventListener("click", useWeek);
  $("backfill").addEventListener("click", () => action($("backfill"), async () => startJob("/api/backfill",readRange(),"补采任务已开始。历史网页仍可访问时才能采集，截图时间会如实记录。")));
  $("export").addEventListener("click", () => action($("export"), async () => {
    const result = await api("/api/export", readRange());
    if (result.files) { renderDownloads(result); feedback("资料已生成，请在下方点击下载。", "success"); }
    else feedback(result.message || "资料正在生成，请保持程序运行。", "info");
    await refresh();
  }));
  $("send").addEventListener("click", () => {
    try {
      const range = readRange();
      if (!ui.settings.smtp_host || !ui.settings.smtp_from_addr || !ui.settings.smtp_to_addr) {
        $("settings-details").open = true;
        throw new Error("请先在设置中填写并保存 SMTP 服务器、发件邮箱和收件邮箱，再发送资料。");
      }
      ui.sendRange = {...range, expected_to:ui.settings.smtp_to_addr, expected_from:ui.settings.smtp_from_addr};
      $("confirm-from").textContent = ui.settings.smtp_from_addr;
      $("confirm-to").textContent = ui.settings.smtp_to_addr;
      $("confirm-range").textContent = `${range.start} 至 ${range.end}`;
      $("confirm-send-check").checked = false; $("confirm-send").disabled = true;
      $("send-dialog").showModal();
    } catch(error) { feedback(error.message,"error"); }
  });
  $("confirm-send-check").addEventListener("change", () => { $("confirm-send").disabled = !$("confirm-send-check").checked; });
  $("confirm-send").addEventListener("click", () => {
    if (!$("confirm-send-check").checked || !ui.sendRange) return;
    const range = {...ui.sendRange};
    $("send-dialog").close();
    action($("send"), () => startJob("/api/send", {...range, confirm:true}, "邮件发送任务已开始，请查看运行记录确认结果。"));
  });
  $("send-dialog").addEventListener("close", () => { ui.sendRange = null; });
  $("close-shot").addEventListener("click", () => $("shot-dialog").close());
  $("shot-dialog").addEventListener("close", () => { $("shot-image").removeAttribute("src"); });
  $("settings-form").addEventListener("input", () => { ui.formDirty = true; });
  $("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    action($("save-settings"), async () => {
      const settings = {};
      for (const [id,key] of Object.entries(fieldMap)) settings[key] = $(id).type === "number" ? Number($(id).value) : $(id).value.trim();
      for (const [id,key] of Object.entries(checkMap)) settings[key] = $(id).checked;
      for (const [id,key] of Object.entries(arrayMap)) settings[key] = lines($(id).value);
      settings.sources = [...document.querySelectorAll('input[name="source"]:checked')].map(x=>x.value);
      if (!settings.sources.length) throw new Error("请至少选择一家媒体来源。");
      if (settings.weekly_email_enabled && (!settings.smtp_host || !settings.smtp_from_addr || !settings.smtp_to_addr)) throw new Error("开启周报前，请填写 SMTP 服务器、发件邮箱和收件邮箱。");
      if (settings.api_enabled && (!settings.api_base_url || !settings.api_model)) throw new Error("启用 AI 辅助前，请填写 API Base URL 与模型名称。");
      const body = {settings, remember_secrets:$("remember-secrets").checked};
      if ($("api-key").value.trim()) body.api_key = $("api-key").value.trim();
      if ($("smtp-password").value) body.smtp_password = $("smtp-password").value;
      const result = await api("/api/settings",body);
      $("api-key").value = ""; $("smtp-password").value = ""; ui.formDirty = false;
      await refresh({fill:true});
      feedback(result.message || "设置已保存。定时任务仅在电脑开机、程序运行时生效。", "success");
    });
  });
  $("propose-rule").addEventListener("click", () => action($("propose-rule"), async () => {
    const message = $("rule-message").value.trim();
    if (!message) throw new Error("先写一句你想调整的偏好，例如「多关注教育，排除体育」。");
    if (!ui.settings.api_enabled) {
      $("settings-details").open = true;
      throw new Error("生成自然语言建议需要先在设置中配置并启用 AI 服务。也可以直接手动填写关键词和补充栏目。");
    }
    const result = await api("/api/rules/propose",{message});
    ui.proposal = result.proposal_id || null;
    $("apply-rule").hidden = !ui.proposal;
    $("rule-summary").textContent = result.summary || "请核对以下调整";
    $("rule-changes").replaceChildren();
    const labels = {include_keywords:"包含关键词", exclude_keywords:"排除关键词", custom_source_urls:"补充栏目网址"};
    for (const [key,value] of Object.entries(result.changes || {})) {
      if (!labels[key]) continue;
      $("rule-changes").append(node("li", `${labels[key]}：${Array.isArray(value) ? value.join("、") || "清空" : String(value)}`));
    }
    $("rule-proposal").hidden = false;
    feedback(ui.proposal ? "建议已生成，请核对后点击「应用这些调整」。" : "已根据本机记录给出说明，本次没有修改规则。", "info");
  }));
  $("apply-rule").addEventListener("click", () => action($("apply-rule"), async () => {
    if (!ui.proposal) return;
    if (ui.formDirty) throw new Error("设置里还有未保存的修改。请先保存设置，再应用这份建议，避免覆盖你的修改。");
    await api("/api/rules/apply",{proposal_id:ui.proposal});
    ui.proposal = null; $("rule-proposal").hidden = true;
    await refresh({fill:true});
    feedback("采集偏好已更新，将用于后续采集。", "success");
  }));
  $("dismiss-rule").addEventListener("click", () => { ui.proposal = null; $("rule-proposal").hidden = true; });
  $("rule-message").addEventListener("input", () => { ui.proposal = null; $("rule-proposal").hidden = true; });
  $("hide-welcome").addEventListener("click", () => { $("welcome").hidden = true; try { localStorage.setItem("newsdesk-intro-hidden","1"); } catch (_) {} });
  $("show-welcome").addEventListener("click", () => { $("welcome").hidden = false; try { localStorage.removeItem("newsdesk-intro-hidden"); } catch (_) {} $("welcome").scrollIntoView({behavior:"smooth"}); });
  document.querySelectorAll('a[href="#settings"]').forEach(link => link.addEventListener("click", () => { $("settings-details").open = true; }));
  async function boot() {
    setButtons();
    const response = await fetch("/api/bootstrap",{credentials:"same-origin",cache:"no-store"});
    if (!response.ok) throw new Error("无法连接本机服务。请重新运行启动程序，再刷新页面。");
    const data = await response.json();
    ui.token = data.token || data.local_token;
    if (!ui.token) throw new Error("未取得本机验证信息，请重新打开程序提供的页面。");
    await refresh({fill:!ui.formDirty});
    useWeek();
  }
  $("day").value = localDay();
  $("day").max = localDay();
  $("range-end").max = localDay();
  $("range-start").max = localDay();
  useWeek(); renderSources(true);
  try { $("welcome").hidden = localStorage.getItem("newsdesk-intro-hidden") === "1"; } catch (_) {}
  boot().catch(error => { connection(false); $("article-list").setAttribute("aria-busy","false"); feedback(error.message || "无法连接本机程序，请重新启动后刷新。","error"); setButtons(); });
})();
