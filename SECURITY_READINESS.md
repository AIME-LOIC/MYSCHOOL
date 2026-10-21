# MYSCHOOL security and readiness notes

## Changes in this branch

- CORS is disabled for cross-origin sites by default. Configure `CORS_ALLOWED_ORIGINS` only for explicitly trusted frontend origins.
- The admin session signing secret no longer has a predictable fallback. Admin login requires a `SECRET_KEY` of at least 32 characters.
- Admin credentials are configured per school through `MYSCHOOL_ADMIN_PASSWORDS`, a JSON object keyed by the exact `school_name` route value. Each password must be at least 12 characters.
- Admin sessions are signed, expire after seven days, and use an HttpOnly cookie with the Secure flag on HTTPS.
- Timetable headers and the period column stay visible while scrolling, and lesson controls have larger touch targets.
- Local `.env` and database files are excluded from future commits. The tracked local `.env` and `school.db` files are removed from this branch.

## Required before deployment

1. Configure `DATABASE_URL`, a random `SECRET_KEY` (32+ characters), a separate random `CARD_UID_PEPPER`, and a unique password for every school in `MYSCHOOL_ADMIN_PASSWORDS`. Do not paste secrets into GitHub or chat.
2. Because credentials and a database file were previously tracked, removing them in this commit does not remove them from Git history. Rotate any secrets that were present and review repository access/history. Treat any student data in the old database as potentially exposed.
3. Back up the production database and verify migrations against a staging database before deploying.
4. Test administrator login/logout, expiry, timetable read/write, parent meeting submissions, canteen checkout, and all role permission boundaries in staging.

## Remaining functional audit

This branch is an initial hardening pass, not a claim that all roles are implemented. Dedicated backend identities and permissions still need a full review for headmaster, DOS, patron, matron, parent, student, teacher, and canteen staff. In particular, ensure staff cannot inherit full administrator permissions and that every school-scoped query checks the authenticated user's school. Review the legacy student-search and visit submission endpoints for authorization and abuse controls. Add automated integration tests and run a browser-level responsive review before production sign-off.
