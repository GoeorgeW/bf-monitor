/* BF Monitor - PWA sin servidor: lee data/data.json (lo actualiza GitHub Actions) */
"use strict";

// Se detecta solo si la web está en https://USUARIO.github.io/REPO/
// Si usas otro dominio, escribe aquí tu usuario y repositorio de GitHub:
const REPO_OVERRIDE = ""; // ejemplo: "pepe/bf-monitor"

const LS = { cache: "bfm_cache2", hideInstall: "bfm_hide_install" };
const $ = (id) => document.getElementById(id);
const state = { data: null, products: [], tab: "monitor", open: new Set(), charts: {}, busy: false, editId: null };

const REPO = (() => {
  if (REPO_OVERRIDE) return REPO_OVERRIDE;
  const m = location.hostname.match(/^([^.]+)\.github\.io$/i);
  const seg = location.pathname.split("/").filter(Boolean)[0];
  return m ? `${m[1]}/${seg || m[1] + ".github.io"}` : "";
})();
const GH = REPO ? `https://github.com/${REPO}` : "";

// ---------- utilidades ----------
const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* modo privado */ } },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const money = (v, cur = "EUR") => {
  if (v == null || isNaN(v)) return "—";
  try { return new Intl.NumberFormat("es-ES", { style: "currency", currency: cur || "EUR" }).format(v); }
  catch { return Number(v).toFixed(2) + " €"; }
};
const timeAgo = (iso) => {
  if (!iso) return "nunca";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "ahora mismo";
  if (m < 60) return `hace ${m} min`;
  if (m < 1440) return `hace ${Math.round(m / 60)} h`;
  return `hace ${Math.round(m / 1440)} d`;
};
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };

let toastTimer;
function toast(msg, ms = 3000) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

function issueUrl(template, fields) {
  if (!GH) { toast("No encuentro tu repositorio de GitHub (revisa REPO_OVERRIDE en app.js)", 6000); return null; }
  const q = new URLSearchParams({ template });
  Object.entries(fields).forEach(([k, v]) => { if (v !== "" && v != null) q.set(k, String(v)); });
  return `${GH}/issues/new?${q}`;
}
function openGitHub(url) { if (url) window.open(url, "_blank", "noopener"); }

// ---------- navegación ----------
function setTab(tab) {
  state.tab = tab;
  ["monitor", "add", "settings"].forEach((t) => $("view-" + t).classList.toggle("hidden", t !== tab));
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("tab-active", b.dataset.tab === tab));
  $("title").textContent = tab === "monitor" ? "🔥 BF Monitor" : tab === "add" ? "➕ Nuevo producto" : "⚙️ Ajustes";
  if (tab === "settings") fillSettings();
  window.scrollTo({ top: 0 });
}

// ---------- render ----------
const BADGES = {
  CHOLLO: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  LEVE: "bg-orange-500/15 text-orange-300 border-orange-500/40",
  INFLADO: "bg-red-500/15 text-red-300 border-red-500/40",
  OOS: "bg-slate-600/30 text-slate-300 border-slate-500/40",
  NEUTRAL: "bg-slate-600/20 text-slate-300 border-slate-500/30",
  NODATA: "bg-slate-600/20 text-slate-400 border-slate-500/30",
};
const ICONS = { CHOLLO: "✅", LEVE: "🟠", INFLADO: "⚠️", OOS: "⛔", NEUTRAL: "➖", NODATA: "…" };

function cardHTML(p) {
  const st = p.status || { code: "NODATA", label: "SIN DATOS", hint: "" };
  const isOpen = state.open.has(p.id);
  const gap = p.price != null && p.target_price != null ? p.price - p.target_price : null;
  const stock = p.in_stock === true ? '<span class="text-emerald-400">● En stock</span>'
    : p.in_stock === false ? '<span class="text-red-400">● Agotado</span>' : '<span class="text-slate-500">● Stock desconocido</span>';
  const ph = '<div class="w-20 h-20 rounded-xl bg-slate-800 shrink-0 flex items-center justify-center text-2xl">🛍️</div>';
  const img = p.image
    ? `<img src="${esc(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" class="w-20 h-20 rounded-xl object-cover bg-slate-800 shrink-0" onerror="this.outerHTML='${ph.replace(/"/g, "&quot;")}'">`
    : ph;
  return `
  <article class="rounded-2xl bg-card border border-line p-4" data-id="${p.id}">
    <div class="flex gap-3">
      ${img}
      <div class="min-w-0 flex-1">
        <div class="flex items-start justify-between gap-2">
          <h3 class="font-semibold leading-tight truncate">${esc(p.name)}</h3>
          ${p.size ? `<span class="shrink-0 text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">${esc(p.size)}</span>` : ""}
        </div>
        <p class="text-xs text-slate-500 truncate">#${p.id} · ${esc(hostOf(p.url))}</p>
        <div class="mt-1.5 inline-flex items-center gap-1 text-[11px] font-bold tracking-wide px-2 py-0.5 rounded-full border ${BADGES[st.code] || BADGES.NEUTRAL}">
          ${ICONS[st.code] || ""} ${esc(st.label)}
        </div>
      </div>
    </div>
    <div class="mt-3 flex items-end justify-between">
      <div>
        <div class="text-3xl font-extrabold tracking-tight ${st.code === "CHOLLO" ? "text-emerald-400" : st.code === "INFLADO" ? "text-red-400" : ""}">${money(p.price, p.currency)}</div>
        ${gap != null ? `<div class="text-xs ${gap <= 0 ? "text-emerald-400" : "text-slate-400"}">${gap <= 0 ? "Objetivo alcanzado 🎯" : `Faltan ${money(gap, p.currency)} para tu objetivo`}</div>` : ""}
      </div>
      <div class="text-right text-xs">${stock}</div>
    </div>
    <div class="mt-3 grid grid-cols-3 gap-2 text-center">
      <div class="rounded-xl bg-bg py-2"><div class="text-[10px] uppercase text-slate-500">Objetivo</div><div class="text-sm font-semibold">${money(p.target_price, p.currency)}</div></div>
      <div class="rounded-xl bg-bg py-2"><div class="text-[10px] uppercase text-slate-500">Mín. histórico</div><div class="text-sm font-semibold">${money(p.min_price, p.currency)}</div></div>
      <div class="rounded-xl bg-bg py-2"><div class="text-[10px] uppercase text-slate-500">Media</div><div class="text-sm font-semibold">${money(p.avg_price, p.currency)}</div></div>
    </div>
    ${st.hint ? `<p class="mt-2 text-xs text-slate-400">${esc(st.hint)}</p>` : ""}
    ${p.coupon ? `<p class="mt-2 text-xs rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 px-2 py-1">🎟️ Posible promo en la web: ${esc(p.coupon)}</p>` : ""}
    ${p.size_matched === false ? `<p class="mt-2 text-xs text-amber-300">⚠ No pude confirmar la talla ${esc(p.size)} en la web; el precio/stock puede ser de otra variante.</p>` : ""}
    ${p.last_error ? `<p class="mt-2 text-xs text-red-300">⚠ ${esc(p.last_error)}</p>` : ""}
    <p class="mt-2 text-[11px] text-slate-600">Comprobado ${timeAgo(p.last_checked)}${p.source ? " · " + esc(p.source) : ""}</p>
    <div class="mt-3 grid grid-cols-4 gap-2 text-sm">
      <a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer" class="col-span-2 text-center rounded-xl bg-emerald-500 text-slate-950 font-bold py-2.5">Comprar ↗</a>
      <button data-act="chart" class="rounded-xl bg-slate-700 py-2.5" aria-label="Gráfico">${isOpen ? "▲" : "📈"}</button>
      <button data-act="menu" class="rounded-xl bg-slate-700 py-2.5" aria-label="Editar o borrar">⋯</button>
    </div>
    <div class="${isOpen ? "" : "hidden"} mt-3 rounded-xl bg-bg p-3">
      ${p.history && p.history.length > 1 ? `<div class="h-44"><canvas id="chart-${p.id}"></canvas></div>`
        : '<p class="text-xs text-slate-500 text-center py-6">Necesito al menos 2 lecturas distintas para dibujar la evolución.</p>'}
    </div>
  </article>`;
}

function render() {
  Object.values(state.charts).forEach((c) => c.destroy());
  state.charts = {};
  $("list").innerHTML = state.products.map(cardHTML).join("");
  $("empty").classList.toggle("hidden", state.products.length > 0);
  state.products.forEach((p) => { if (state.open.has(p.id)) drawChart(p); });
  const upd = state.data && state.data.updated;
  $("last-sync").textContent = upd ? `Última revisión automática: ${timeAgo(upd)}` : "";
}

function drawChart(p) {
  const cv = $("chart-" + p.id);
  if (!cv || !window.Chart || !p.history || p.history.length < 2) return;
  const labels = p.history.map((h) => new Date(h.t).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }));
  const datasets = [{ label: "Precio", data: p.history.map((h) => h.p), borderColor: "#34d399", backgroundColor: "rgba(52,211,153,.12)",
    fill: true, tension: 0.25, pointRadius: 2, borderWidth: 2 }];
  if (p.target_price) datasets.push({ label: "Objetivo", data: p.history.map(() => p.target_price), borderColor: "#fbbf24",
    borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5, fill: false });
  state.charts[p.id] = new Chart(cv, {
    type: "line", data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.parsed.y, p.currency)}` } } },
      scales: {
        x: { ticks: { color: "#64748b", maxTicksLimit: 4, font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { color: "#64748b", font: { size: 10 }, callback: (v) => v + "€" }, grid: { color: "#1f2a3b" } },
      },
    },
  });
}

// ---------- datos ----------
function setConn(ok, text) {
  const el = $("conn");
  el.textContent = text;
  el.className = "text-xs px-2 py-1 rounded-full " + (ok ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300");
}
function showBanner(msg) { const b = $("banner"); b.textContent = msg || ""; b.classList.toggle("hidden", !msg); }

function apply(data) {
  state.data = data;
  state.products = data.products || [];
  render();
}

async function loadData(manual = false) {
  if (state.busy) return;
  state.busy = true;
  if (manual) { $("spinner").classList.remove("hidden"); $("refresh-label").textContent = "Cargando…"; }
  try {
    const res = await fetch(`data/data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    store.set(LS.cache, data);
    apply(data); setConn(true, "En línea"); showBanner("");
    if (manual) toast("Datos al día ✔ (GitHub revisa cada 30 min)");
  } catch {
    const c = store.get(LS.cache);
    if (c) { apply(c); showBanner("Sin conexión · mostrando los últimos datos guardados"); }
    setConn(false, "Sin conexión");
  } finally {
    state.busy = false;
    $("spinner").classList.add("hidden"); $("refresh-label").textContent = "Actualizar precios ahora";
  }
}

// ---------- eventos ----------
$("btn-refresh").addEventListener("click", () => loadData(true));

$("list").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.closest("article").dataset.id);
  const p = state.products.find((x) => x.id === id);
  if (btn.dataset.act === "chart") {
    state.open.has(id) ? state.open.delete(id) : state.open.add(id);
    render();
  } else if (btn.dataset.act === "menu" && p) {
    state.editId = id;
    $("e-name").value = p.name; $("e-size").value = p.size || ""; $("e-target").value = p.target_price ?? "";
    $("dlg-edit").showModal();
  }
});

$("e-cancel").addEventListener("click", () => $("dlg-edit").close());
$("dlg-edit").addEventListener("click", (e) => { if (e.target === $("dlg-edit")) $("dlg-edit").close(); });
$("form-edit").addEventListener("submit", (e) => {
  e.preventDefault();
  openGitHub(issueUrl("editar.yml", { title: `[Editar] #${state.editId}`, id: state.editId,
    nombre: $("e-name").value.trim(), talla: $("e-size").value.trim(), precio: $("e-target").value.replace(",", ".") }));
  $("dlg-edit").close();
  toast("Confirma en GitHub. Verás el cambio en 1-2 min.", 5000);
});
(function addDeleteButton() {
  const del = document.createElement("button");
  del.type = "button"; del.textContent = "🗑 Eliminar producto";
  del.className = "col-span-2 rounded-xl bg-red-500/15 text-red-300 border border-red-500/30 py-3 font-semibold";
  $("e-cancel").parentElement.appendChild(del);
  del.addEventListener("click", () => {
    openGitHub(issueUrl("borrar.yml", { title: `[Borrar] #${state.editId}`, id: state.editId }));
    $("dlg-edit").close();
    toast("Confirma en GitHub para borrarlo.", 5000);
  });
})();

$("btn-paste").addEventListener("click", async () => {
  try { $("f-url").value = (await navigator.clipboard.readText()).trim(); }
  catch { toast("Mantén pulsado el campo y elige «Pegar»."); }
});

$("form-add").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = $("f-name").value.trim();
  const url = issueUrl("anadir.yml", { title: `[Añadir] ${name}`.slice(0, 100), url: $("f-url").value.trim(),
    nombre: name, talla: $("f-size").value.trim(), precio: $("f-target").value.replace(",", ".") });
  if (!url) return;
  openGitHub(url);
  $("form-add").reset();
  setTab("monitor");
  toast("Pulsa el botón verde en GitHub. Aparecerá aquí en 1-2 min.", 6000);
  setTimeout(() => loadData(), 90000);
  setTimeout(() => loadData(), 180000);
});

// ---------- ajustes ----------
function fillSettings() {
  const cfg = (state.data && state.data.config) || {};
  $("s-topic").value = cfg.ntfy_topic || "(se verá cuando carguen los datos)";
  $("s-updated").textContent = state.data && state.data.updated ? `Última revisión: ${timeAgo(state.data.updated)}` : "";
  $("lnk-run").href = GH ? `${GH}/actions/workflows/monitor.yml` : "#";
  $("s-repo").textContent = REPO ? `Repositorio: ${REPO}` : "";
}
$("btn-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("s-topic").value); toast("Copiado ✔ Ahora pégalo en ntfy"); }
  catch { $("s-topic").select(); toast("Mantén pulsado y copia"); }
});

// ---------- instalación ----------
(function iosHint() {
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = window.navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  if (ios && !standalone && !store.get(LS.hideInstall)) {
    store.set(LS.hideInstall, 1);
    setTimeout(() => toast("Para instalar: botón Compartir ⬆ → «Añadir a pantalla de inicio»", 9000), 1500);
  }
})();

// ---------- arranque ----------
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
window.addEventListener("online", () => loadData());
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadData(); });
setInterval(() => { if (!document.hidden) loadData(); }, 5 * 60 * 1000);
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js").catch(() => {}));
}
(function init() {
  const c = store.get(LS.cache);
  if (c) apply(c); else render();
  setTab("monitor");
  loadData();
})();
