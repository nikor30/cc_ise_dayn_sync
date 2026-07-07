/* ise-ndg-sync GUI — vanilla JS, no build step. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, opts = {}) {
  if (opts.body !== undefined && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = Object.assign({"Content-Type": "application/json"}, opts.headers);
  }
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try { msg += ": " + ((await resp.json()).detail || ""); } catch (e) { /* noop */ }
    throw new Error(msg);
  }
  return resp.json();
}

/* ------------------------------------------------ tabs */
document.querySelectorAll("#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    loaders[btn.dataset.tab]?.();
  });
});

/* ------------------------------------------------ dashboard */
async function loadDashboard() {
  try {
    const d = await api("/api/dashboard");
    $("#dash-tiles").innerHTML = `
      <div class="tile"><div class="num">${d.webhook_events_24h}</div><div class="lbl">Webhook events (24 h)</div></div>
      <div class="tile ok"><div class="num">${d.devices_updated_24h}</div><div class="lbl">Devices updated (24 h)</div></div>
      <div class="tile ${d.failures_24h ? "bad" : ""}"><div class="num">${d.failures_24h}</div><div class="lbl">Failures (24 h)</div></div>
      <div class="tile"><div class="num">${d.pending_webhooks}</div><div class="lbl">Queued (debouncing)</div></div>
      <div class="tile ${d.cc_reachable ? "ok" : "bad"}"><div class="num">${d.cc_reachable ? "✓" : "✗"}</div><div class="lbl">Catalyst Center</div></div>
      <div class="tile ${d.ise_reachable ? "ok" : "bad"}"><div class="num">${d.ise_reachable ? "✓" : "✗"}</div><div class="lbl">Cisco ISE</div></div>`;
    $("#conn-badges").innerHTML =
      `<span class="badge ${d.cc_reachable ? "ok" : "bad"}">CC</span>` +
      `<span class="badge ${d.ise_reachable ? "ok" : "bad"}">ISE</span>`;
    const r = d.last_reconcile;
    $("#dash-reconcile").innerHTML = r
      ? `${esc(r.started?.replace("T", " ").slice(0, 19))} — <b class="status-${esc(r.status)}">${esc(r.status)}</b>,
         scanned ${r.scanned}, fixed ${r.fixed}, unmatched ${r.unmatched},
         not in CC ${r.not_found_in_cc}, errors ${r.errors}`
      : "No reconciliation run yet.";
  } catch (e) { console.error(e); }
}

/* ------------------------------------------------ settings */
const SETTING_IDS = ["cc.base_url","cc.username","cc.password","cc.verify_tls",
  "ise.base_url","ise.api_flavor","ise.ers_port","ise.username","ise.password","ise.verify_tls",
  "webhook.path","webhook.token","sync.debounce_seconds","sync.retry_schedule",
  "ndg.refresh_hours","reconcile.enabled","reconcile.minutes","reconcile.mode",
  "reconcile.detail_ttl_hours","reconcile.exclude","ui.admin_password"];

async function loadSettings() {
  const s = await api("/api/settings");
  for (const key of SETTING_IDS) {
    const el = document.getElementById(`s-${key}`);
    if (!el || !s[key]) continue;
    if (key === "reconcile.mode") el.checked = s[key].value === "approve";
    else if (el.type === "checkbox") el.checked = /^(1|true|yes|on)$/i.test(s[key].value);
    else el.value = s[key].value;
    el.disabled = s[key].env_override;
    el.title = s[key].env_override ? "Overridden by environment variable" : "";
  }
}

async function saveSettings() {
  const payload = {};
  for (const key of SETTING_IDS) {
    const el = document.getElementById(`s-${key}`);
    if (!el || el.disabled) continue;
    if (key === "reconcile.mode") payload[key] = el.checked ? "approve" : "auto";
    else payload[key] = el.type === "checkbox" ? String(el.checked) : el.value;
  }
  try {
    const r = await api("/api/settings", {method: "PUT", body: payload});
    $("#save-result").textContent = r.changed.length
      ? `Saved (${r.changed.length} changed)` : "Saved (no changes)";
    setTimeout(() => { $("#save-result").textContent = ""; }, 4000);
  } catch (e) { $("#save-result").textContent = e.message; }
}

async function testConn(which) {
  const out = $(`#test-${which}`);
  out.textContent = "testing…"; out.className = "testresult";
  try {
    const r = await api(`/api/test/${which}`, {method: "POST"});
    out.textContent = r.ok
      ? `✓ ${r.message}${r.version ? " — v" + r.version : ""}${r.device_count != null ? " — " + r.device_count + " devices" : ""}`
      : `✗ ${r.message}${r.hint ? " — " + r.hint : ""}`;
    out.classList.add(r.ok ? "ok" : "bad");
  } catch (e) { out.textContent = "✗ " + e.message; out.classList.add("bad"); }
}

async function importConfig(input) {
  const file = input.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    const r = await api("/api/import", {method: "POST", body: data});
    alert(`Imported: ${r.rules} rules, ${r.site_mappings} site mappings, ${r.settings} settings`);
    loadSettings(); loadRules(); loadSiteMap();
  } catch (e) { alert("Import failed: " + e.message); }
  input.value = "";
}

/* ------------------------------------------------ NDG dropdown data */
let ndgCache = {device_type: [], location: []};
async function fetchNdgCache() {
  ndgCache.device_type = await api("/api/ndg?type=device_type");
  ndgCache.location = await api("/api/ndg?type=location");
}
function ndgOptions(type, selected) {
  const opts = ndgCache[type].map((n) =>
    `<option value="${esc(n.name)}" ${n.name === selected ? "selected" : ""}>${esc(n.name)}</option>`);
  const missing = selected && !ndgCache[type].some((n) => n.name === selected)
    ? `<option value="${esc(selected)}" selected>${esc(selected)} (not in cache)</option>` : "";
  return `<option value="">— none —</option>` + missing + opts.join("");
}

/* ------------------------------------------------ rules */
let rulesData = [];
async function loadRules() {
  await fetchNdgCache().catch(() => {});
  rulesData = await api("/api/rules");
  const tbody = $("#rules-table tbody");
  tbody.innerHTML = rulesData.map((r, i) => {
    const crit = [
      r.match_tag && `tag~${r.match_tag}`, r.match_hostname && `host~${r.match_hostname}`,
      r.match_family && `family~${r.match_family}`, r.match_series && `series~${r.match_series}`,
      r.match_platform && `platform~${r.match_platform}`, r.match_site && `site~${r.match_site}`,
    ].filter(Boolean).join(" AND ") || "(any device)";
    const loc = r.location_mode === "derive"
      ? `derive from CC site (${esc(r.derive_fallback)}${r.auto_create_location ? ", auto-create" : ""})`
      : esc(r.location_ndg || "—");
    return `<tr>
      <td class="arrows">
        <button onclick="moveRule(${i},-1)" ${i === 0 ? "disabled" : ""}>▲</button>
        <button onclick="moveRule(${i},1)" ${i === rulesData.length - 1 ? "disabled" : ""}>▼</button>
      </td>
      <td>${r.priority}</td>
      <td>${esc(r.description)}</td>
      <td class="rule-crit">${esc(crit)}</td>
      <td><code>${esc(r.device_type_ndg || "—")}</code></td>
      <td>${loc}</td>
      <td><input type="checkbox" ${r.enabled ? "checked" : ""} onchange="toggleRule(${r.id}, this.checked)"></td>
      <td><button onclick="editRule(${r.id})">Edit</button>
          <button class="danger" onclick="deleteRule(${r.id})">✕</button></td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="hint">No rules yet — create one.</td></tr>`;
}

async function moveRule(index, dir) {
  const order = rulesData.map((r) => r.id);
  const [id] = order.splice(index, 1);
  order.splice(index + dir, 0, id);
  await api("/api/rules/reorder", {method: "POST", body: {order}});
  loadRules();
}

async function toggleRule(id, enabled) {
  await api(`/api/rules/${id}`, {method: "PUT", body: {enabled}});
}

async function deleteRule(id) {
  if (!confirm("Delete this rule?")) return;
  await api(`/api/rules/${id}`, {method: "DELETE"});
  loadRules();
}

function editRule(id) {
  const r = rulesData.find((x) => x.id === id) || {
    priority: (rulesData.length + 1) * 10, description: "", enabled: true,
    match_tag: "", match_hostname: "", match_family: "", match_series: "",
    match_platform: "", match_site: "", device_type_ndg: "", location_mode: "fixed",
    location_ndg: "", derive_fallback: "skip", auto_create_location: false};
  const ed = $("#rule-editor");
  ed.classList.remove("hidden");
  ed.innerHTML = `
    <h3>${id ? "Edit rule #" + id : "New rule"}</h3>
    <div class="cols">
      <div>
        <label>Description <input id="r-description" value="${esc(r.description)}"></label>
        <label>Priority <input id="r-priority" type="number" value="${r.priority}"></label>
        <label class="chk"><input id="r-enabled" type="checkbox" ${r.enabled ? "checked" : ""}> Enabled</label>
        <h4>Match criteria (regex, empty = any)</h4>
        <label>CC device tag <input id="r-match_tag" value="${esc(r.match_tag)}" placeholder="exact name or regex"></label>
        <label>Hostname pattern <input id="r-match_hostname" value="${esc(r.match_hostname)}" placeholder="^SW-SCH-.*"></label>
        <label>Device family <input id="r-match_family" value="${esc(r.match_family)}" placeholder="Switches and Hubs"></label>
        <label>Device series <input id="r-match_series" value="${esc(r.match_series)}"></label>
        <label>Platform ID <input id="r-match_platform" value="${esc(r.match_platform)}" placeholder="IE-3300-8P2S"></label>
        <label>Site path pattern <input id="r-match_site" value="${esc(r.match_site)}" placeholder="^Global/DE/Schierling/.*"></label>
      </div>
      <div>
        <h4>Actions</h4>
        <label>Device Type NDG <select id="r-device_type_ndg">${ndgOptions("device_type", r.device_type_ndg)}</select></label>
        <label>Location mode
          <select id="r-location_mode" onchange="$('#r-derive-opts').classList.toggle('hidden', this.value!=='derive')">
            <option value="fixed" ${r.location_mode === "fixed" ? "selected" : ""}>Fixed value</option>
            <option value="derive" ${r.location_mode === "derive" ? "selected" : ""}>Derive from CC site (site mapping table)</option>
          </select></label>
        <label>Location NDG (fixed value / default fallback)
          <select id="r-location_ndg">${ndgOptions("location", r.location_ndg)}</select></label>
        <div id="r-derive-opts" class="${r.location_mode === "derive" ? "" : "hidden"}">
          <label>If no site mapping matches
            <select id="r-derive_fallback">
              <option value="skip" ${r.derive_fallback === "skip" ? "selected" : ""}>Skip (leave location unchanged)</option>
              <option value="default" ${r.derive_fallback === "default" ? "selected" : ""}>Use default (Location NDG above)</option>
              <option value="create" ${r.derive_fallback === "create" ? "selected" : ""}>Auto-create Location NDG in ISE</option>
            </select></label>
          <label class="chk"><input id="r-auto_create_location" type="checkbox" ${r.auto_create_location ? "checked" : ""}>
            Auto-create derived Location NDG if missing in ISE</label>
        </div>
        <div class="actions">
          <button class="primary" onclick="saveRule(${id ?? "null"})">Save rule</button>
          <button onclick="$('#rule-editor').classList.add('hidden')">Cancel</button>
        </div>
      </div>
    </div>`;
  ed.scrollIntoView({behavior: "smooth"});
}

async function saveRule(id) {
  const val = (f) => {
    const el = document.getElementById(`r-${f}`);
    return el.type === "checkbox" ? el.checked : el.value;
  };
  const payload = {};
  ["priority","description","enabled","match_tag","match_hostname","match_family",
   "match_series","match_platform","match_site","device_type_ndg","location_mode",
   "location_ndg","derive_fallback","auto_create_location"].forEach((f) => { payload[f] = val(f); });
  payload.priority = parseInt(payload.priority, 10) || 100;
  await api(id ? `/api/rules/${id}` : "/api/rules",
            {method: id ? "PUT" : "POST", body: payload});
  $("#rule-editor").classList.add("hidden");
  loadRules();
}

/* ------------------------------------------------ dry-run */
async function loadCcDevices() {
  const sel = $("#dryrun-device");
  sel.innerHTML = `<option value="">loading…</option>`;
  try {
    const devs = await api("/api/cc/devices");
    sel.innerHTML = `<option value="">— pick a device —</option>` + devs.map((d) =>
      `<option value="${esc(d.id)}">${esc(d.hostname)} (${esc(d.ip)}, ${esc(d.platform)})</option>`).join("");
  } catch (e) { sel.innerHTML = `<option value="">failed: ${esc(e.message)}</option>`; }
}

async function runDryRun() {
  const id = $("#dryrun-device").value;
  const manual = $("#dryrun-manual").value.trim();
  const body = id ? {id} : (/^\d+\.\d+\.\d+\.\d+$/.test(manual) ? {ip: manual} : {hostname: manual});
  const out = $("#dryrun-result");
  out.classList.remove("hidden");
  out.textContent = "simulating…";
  try { out.textContent = JSON.stringify(await api("/api/dryrun", {method: "POST", body}), null, 2); }
  catch (e) { out.textContent = e.message; }
}

async function testWebhookPayload() {
  const out = $("#webhook-test-result");
  out.classList.remove("hidden");
  try {
    const body = JSON.parse($("#webhook-test-body").value);
    out.textContent = JSON.stringify(await api("/api/webhook-test", {method: "POST", body}), null, 2);
  } catch (e) { out.textContent = "Error: " + e.message; }
}

/* ------------------------------------------------ site mappings */
async function loadSiteMap() {
  await fetchNdgCache().catch(() => {});
  $("#sm-location").innerHTML = ndgOptions("location", "");
  const rows = await api("/api/sitemap");
  $("#sitemap-table tbody").innerHTML = rows.map((m) => `<tr>
      <td>${m.priority}</td><td><code>${esc(m.site_pattern)}</code></td>
      <td><code>${esc(m.location_ndg)}</code></td>
      <td><button class="danger" onclick="deleteSiteMap(${m.id})">✕</button></td>
    </tr>`).join("") || `<tr><td colspan="4" class="hint">No site mappings yet.</td></tr>`;
}

async function addSiteMap() {
  try {
    await api("/api/sitemap", {method: "POST", body: {
      priority: parseInt($("#sm-priority").value, 10) || 100,
      site_pattern: $("#sm-pattern").value.trim(),
      location_ndg: $("#sm-location").value}});
    $("#sm-pattern").value = "";
    loadSiteMap();
  } catch (e) { alert(e.message); }
}

async function deleteSiteMap(id) {
  await api(`/api/sitemap/${id}`, {method: "DELETE"});
  loadSiteMap();
}

/* ------------------------------------------------ NDG page */
async function loadNdg() {
  await fetchNdgCache().catch(() => {});
  $("#ndg-devicetypes").innerHTML = ndgCache.device_type.map((n) => `<li>${esc(n.name)}</li>`).join("")
    || `<li class="hint">empty — refresh from ISE</li>`;
  $("#ndg-locations").innerHTML = ndgCache.location.map((n) => `<li>${esc(n.name)}</li>`).join("")
    || `<li class="hint">empty — refresh from ISE</li>`;
}

async function refreshNdg() {
  $("#ndg-status").textContent = "refreshing…";
  try {
    const r = await api("/api/ndg/refresh", {method: "POST"});
    $("#ndg-status").textContent =
      `✓ ${r.total} NDGs (${r.device_types} device types, ${r.locations} locations)`;
    loadNdg();
  } catch (e) { $("#ndg-status").textContent = "✗ " + e.message; }
}

/* ------------------------------------------------ reconciliation */
async function loadReconcile() {
  loadBlacklist().catch(() => {});
  const d = await api("/api/reconcile");
  $("#reconcile-status").textContent = d.running ? "⏳ running…" : "";
  const pending = d.pending || [];
  $("#pending-card").classList.toggle("hidden", pending.length === 0);
  $("#pending-count").textContent = `${pending.length} change(s) waiting`;
  $("#pending-table tbody").innerHTML = pending.map((p) => `<tr>
      <td>${esc((p.created || "").replace("T", " ").slice(0, 19))}</td>
      <td>${esc(p.device_name)}${p.device_ip ? "<br><span class='hint'>" + esc(p.device_ip) + "</span>" : ""}</td>
      <td>${esc(p.rule)}</td>
      <td><div class="ndg-diff"><span class="ndg-old">${esc(p.old_ndgs.join(", "))}</span><br>
          <span class="ndg-new">${esc(p.new_ndgs.join(", "))}</span></div></td>
      <td><button class="primary" onclick="approvePending(${p.id})">Approve</button>
          <button class="danger" onclick="rejectPending(${p.id})">Reject</button>
          <button title="Reject and never touch this device again"
            onclick="blacklistPending(${p.id}, '${esc(p.device_name)}')">Blacklist</button></td>
    </tr>`).join("");
  $("#reconcile-table tbody").innerHTML = d.runs.map((r) => `<tr>
      <td>${esc((r.started || "").replace("T", " ").slice(0, 19))}</td>
      <td>${esc((r.finished || "").replace("T", " ").slice(0, 19))}</td>
      <td class="status-${esc(r.status)}">${esc(r.status)}${r.message ? " — " + esc(r.message) : ""}</td>
      <td>${r.scanned}</td><td>${r.fixed}</td><td>${r.pending ?? 0}</td><td>${r.unmatched}</td>
      <td>${r.not_found_in_cc}</td><td>${r.excluded ?? 0}</td><td>${r.errors}</td>
    </tr>`).join("") || `<tr><td colspan="10" class="hint">No runs yet.</td></tr>`;
}

async function approvePending(id) {
  const r = await api(`/api/pending/${id}/approve`, {method: "POST"});
  if (r.status !== "success") alert("Apply failed: " + (r.message || "unknown error"));
  loadReconcile();
}

async function rejectPending(id) {
  await api(`/api/pending/${id}/reject`, {method: "POST"});
  loadReconcile();
}

async function approveAllPending() {
  if (!confirm("Apply ALL pending changes to ISE?")) return;
  const r = await api("/api/pending/approve-all", {method: "POST"});
  alert(`Applied: ${r.applied}, failed: ${r.failed}`);
  loadReconcile();
}

async function blacklistPending(id, name) {
  if (!confirm(`Blacklist ${name}? The device will never be modified again.`)) return;
  await api(`/api/pending/${id}/blacklist`, {method: "POST"});
  loadReconcile();
}

/* ------------------------------------------------ blacklist */
async function loadBlacklist() {
  const rows = await api("/api/blacklist");
  $("#blacklist-table tbody").innerHTML = rows.map((b) => `<tr>
      <td>${esc((b.created || "").replace("T", " ").slice(0, 19))}</td>
      <td>${esc(b.name || "—")}</td><td>${esc(b.ip || "—")}</td><td>${esc(b.note)}</td>
      <td><button class="danger" onclick="removeBlacklist(${b.id})">✕</button></td>
    </tr>`).join("") || `<tr><td colspan="5" class="hint">No blacklisted devices.</td></tr>`;
}

async function addBlacklist() {
  const name = $("#bl-name").value.trim(), ip = $("#bl-ip").value.trim();
  if (!name && !ip) { alert("Enter a device name or IP."); return; }
  await api("/api/blacklist", {method: "POST",
    body: {name, ip, note: $("#bl-note").value.trim()}});
  $("#bl-name").value = $("#bl-ip").value = $("#bl-note").value = "";
  loadBlacklist();
}

async function removeBlacklist(id) {
  await api(`/api/blacklist/${id}`, {method: "DELETE"});
  loadBlacklist();
}

async function runReconcile() {
  await api("/api/reconcile/run", {method: "POST"});
  $("#reconcile-status").textContent = "⏳ started…";
  setTimeout(loadReconcile, 2500);
}

/* ------------------------------------------------ audit */
async function loadAudit() {
  const params = new URLSearchParams({limit: "200"});
  if ($("#audit-status").value) params.set("status", $("#audit-status").value);
  if ($("#audit-trigger").value) params.set("trigger", $("#audit-trigger").value);
  const d = await api(`/api/audit?${params}`);
  $("#audit-count").textContent = `${d.items.length} of ${d.total} entries`;
  $("#audit-table tbody").innerHTML = d.items.map((r) => {
    const diff = (r.old_ndgs.length || r.new_ndgs.length)
      ? `<div class="ndg-diff"><span class="ndg-old">${esc(r.old_ndgs.join(", "))}</span><br>
         <span class="ndg-new">${esc(r.new_ndgs.join(", "))}</span></div>` : "";
    const raw = r.raw ? `<details class="raw"><summary>raw</summary><pre class="result">${esc(r.raw)}</pre></details>` : "";
    return `<tr>
      <td>${esc((r.ts || "").replace("T", " ").slice(0, 19))}</td>
      <td>${esc(r.trigger)}</td>
      <td>${esc(r.device_name)}${r.device_ip ? "<br><span class='hint'>" + esc(r.device_ip) + "</span>" : ""}</td>
      <td>${esc(r.rule)}</td>
      <td>${diff}</td>
      <td class="status-${esc(r.status)}">${esc(r.status)}</td>
      <td>${esc(r.message)}${raw}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="hint">No audit entries.</td></tr>`;
}

/* ------------------------------------------------ boot */
const loaders = {dashboard: loadDashboard, settings: loadSettings, rules: loadRules,
  sitemap: loadSiteMap, ndg: loadNdg, reconcile: loadReconcile, audit: loadAudit};
api("/healthz").then((h) => {
  if (h.version) $("#app-version").textContent = `v${h.version}`;
}).catch(() => {});
loadDashboard();
setInterval(() => { if ($("#tab-dashboard").classList.contains("active")) loadDashboard(); }, 30000);
