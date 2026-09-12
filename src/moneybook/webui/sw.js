/* 极简离线缓存：仅缓存界面外壳，数据始终走网络 */
var CACHE = "moneybook-shell-v1";
var SHELL = ["/", "/index.html", "/style.css", "/app.js", "/manifest.webmanifest", "/icon-192.png"];
self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).catch(function () {}));
  self.skipWaiting();
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.map(function (k) { return k === CACHE ? null : caches.delete(k); }));
  }));
});
self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.indexOf("/api/") === 0) { return; }
  e.respondWith(caches.match(e.request).then(function (hit) { return hit || fetch(e.request); }));
});
