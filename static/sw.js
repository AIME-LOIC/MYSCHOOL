const CACHE = 'myschool-v2';

self.addEventListener('install', e => {
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ));
    self.clients.claim();
});

self.addEventListener('fetch', e => {
    const url = new URL(e.request.url);

    // Cache student search and student list responses
    if (e.request.method === 'GET' && (
        url.pathname.includes('/students/search') ||
        url.pathname.includes('/admin/students')
    )) {
        e.respondWith(
            fetch(e.request)
                .then(res => {
                    const clone = res.clone();
                    caches.open(CACHE).then(c => c.put(e.request, clone));
                    return res;
                })
                .catch(() => caches.match(e.request))
        );
        return;
    }

    // For everything else, network first, fallback to cache
    if (e.request.method === 'GET') {
        e.respondWith(
            fetch(e.request).catch(() => caches.match(e.request))
        );
    }
});

// Background sync
self.addEventListener('sync', e => {
    if (e.tag === 'sync-visits') {
        e.waitUntil(
            self.clients.matchAll().then(clients =>
                clients.forEach(c => c.postMessage({ type: 'flush-visit-queue' }))
            )
        );
    }
});
