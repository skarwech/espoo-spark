const CACHE = "spark-event-pass-v1";
const SHELL = ["/event-pass/placeholder", "/static/app.css", "/static/event_pass.js", "/static/sw.js", "/reports/base.css"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL.filter((path) => !path.includes("placeholder")))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("message", (event) => {
  if (event.data?.type !== "CACHE_PASS_PAGE") return;
  const url = new URL(event.data.url, self.location.origin);
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/event-pass/")) return;
  event.waitUntil(fetch(url.href).then((response) => {
    if (response.ok) return caches.open(CACHE).then((cache) => cache.put(url.href, response));
  }));
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/passes/")) {
    event.respondWith(fetch(event.request).then((response) => {
      if (response.ok) event.waitUntil(caches.open(CACHE).then((cache) => cache.put(event.request, response.clone())));
      return response;
    }).catch(async () => (await caches.match(event.request)) || Response.error()));
    return;
  }
  if (url.pathname.startsWith("/event-pass/") || url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
      if (response.ok) event.waitUntil(caches.open(CACHE).then((cache) => cache.put(event.request, response.clone())));
      return response;
    })));
  }
});
