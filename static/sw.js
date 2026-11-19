/* MySchool service worker.
   v4 deliberately does not intercept page navigations: a stale cached HTML shell
   must never cover the live portal or leave users on an obsolete loading screen. */
const CACHE = 'myschool-v4';

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE).then(cache => cache.add('/static/parent-portal.css?v=4'))
    );
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);

    // Never intercept document navigations. The server always supplies the current
    // HTML, and a failed/offline page request cannot be replaced by stale cached HTML.
    if (request.mode === 'navigate' || request.method !== 'GET' || url.origin !== self.location.origin) {
        return;
    }

    // Cache student data only as an offline fallback. These requests never gate
    // initial page rendering.
    if (url.pathname.includes('/students/search') || url.pathname.includes('/admin/students')) {
        event.respondWith((async () => {
            try {
                const response = await fetch(request);
                if (response.ok) {
                    const copy = response.clone();
                    caches.open(CACHE).then(cache => cache.put(request, copy)).catch(() => {});
                }
                return response;
            } catch (error) {
                const cached = await caches.match(request);
                if (cached) return cached;
                throw error;
            }
        })());
    }
});

self.addEventListener('sync', event => {
    if (event.tag === 'sync-visits') {
        event.waitUntil(self.clients.matchAll({ type: 'window' }).then(clients => {
            clients.forEach(client => client.postMessage({ type: 'flush-visit-queue' }));
        }));
    }
});
