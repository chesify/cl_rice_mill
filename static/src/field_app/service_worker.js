/* Service worker of the Rice Mill field app: keeps the app shell available offline.
 * Data requests (POST) always go to the network; the page queues them itself. */
"use strict";

const CACHE = "cl-rice-field-v1";
const SHELL = [
    "/rice/field/",
    "/rice/field/manifest.webmanifest",
    "/cl_rice_mill/static/src/field_app/field_app.js",
    "/cl_rice_mill/static/src/field_app/field_app.css",
    "/cl_rice_mill/static/description/icon.png",
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") {
        return;
    }
    const url = new URL(request.url);
    if (url.pathname === "/rice/field/") {
        // the page: network first (to stay up to date and keep the session alive), cache when offline
        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (response.ok && !response.redirected) {
                        const copy = response.clone();
                        caches.open(CACHE).then((cache) => cache.put(request, copy));
                    }
                    return response;
                })
                .catch(() => caches.match(request))
        );
        return;
    }
    if (SHELL.includes(url.pathname)) {
        event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
    }
});
