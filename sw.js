// SPLOG service worker: deixa o app instalável e abre a tela mesmo sem sinal.
// Sempre busca a versão mais nova na internet primeiro; o cache é só reserva.
// Os dados (Firebase) não passam por aqui.
const CACHE = "splog-v4";
const SHELL = ["./", "./index.html", "./config.js", "./manifest.webmanifest", "./favicon.svg", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  event.respondWith(
    fetch(req)
      .then(res => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
        return res;
      })
      .catch(() => caches.match(req).then(hit => hit || (req.mode === "navigate" ? caches.match("./index.html") : Response.error())))
  );
});
