/* Service Worker - BF Monitor
 * - App shell en caché (funciona offline)
 * - CDN (Tailwind/Chart.js): cache-first en runtime
 * - Llamadas /api/*: siempre red (la app guarda su propia copia en localStorage)
 */
const VERSION = "bf-monitor-v2";
const SHELL = ["./", "./index.html", "./app.js", "./manifest.json", "./icons/icon-192.png", "./icons/icon-512.png", "./icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.endsWith("/data/data.json")) return; // datos: siempre red (la app guarda copia)

  // Navegación: red primero, caché si no hay conexión
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put("./index.html", copy));
        return res;
      }).catch(() => caches.match("./index.html"))
    );
    return;
  }

  // Resto: stale-while-revalidate (propio) / cache-first (CDN)
  e.respondWith(
    caches.match(req).then((cached) => {
      const net = fetch(req).then((res) => {
        if (res && (res.ok || res.type === "opaque")) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => cached);
      return cached || net;
    })
  );
});
