# TODO - Offline sync + offline viewing

- [ ] Add backend SQLite mirror for cached data (offline_cache.db)
- [ ] Add backend endpoints to fetch cached snapshots from SQLite when offline (and to update cache when online)
- [ ] Update parent portal (templates/index.html):
  - [ ] Add localStorage caching for students list + today visits
  - [ ] Add offline search using cache
  - [ ] Queue visit submissions while offline; flush when back online
- [ ] Update admin dashboard (templates/admin.html): use cached snapshots when offline
- [ ] Add basic sync scheduler in JS (periodic refresh when online)
- [ ] Smoke test: online load -> toggle offline -> verify UI works

