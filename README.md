# MYSCHOOL — 1percent Eats

A dedicated product branch for modernizing MYSCHOOL as a 1percent product and adding school-canteen card payments.

## Product direction
- Consistent 1percent identity: deep green, restrained lime accents, clear typography, responsive layouts and accessible states.
- Canteen checkout with published menu, RWF prices, basket, NFC scan/manual UID entry and clear payment status.
- Existing school database and student records are reused; canteen records are scoped to each school.
- New SQLAlchemy tables are additive and created by the existing startup table initialization.

## Endpoints
- `GET /{school_name}/eats` — canteen checkout UI.
- `GET /{school_name}/eats/api/menu` — available menu items.
- `POST /{school_name}/eats/api/menu` — add a menu item (school admin session required).
- `POST /{school_name}/eats/api/cards/link` — link an NFC UID to a student (school admin session required).
- `POST /{school_name}/eats/api/cards/top-up` — add balance with an admin session and recorded reference.
- `POST /{school_name}/eats/api/pay` — debit a registered card and create a transaction.
- `GET /{school_name}/eats/api/transactions` — recent transaction records (school admin session required).

## Before testing against the live database
1. Set a strong random `CARD_UID_PEPPER` secret in the deployment environment. Card endpoints intentionally return HTTP 503 until this is configured.
2. Back up PostgreSQL before deploying. This branch adds tables and does not intentionally drop or rewrite existing tables.
3. Sign in through the existing school admin login before adding menu items or linking cards.
4. Publish menu items through the authenticated API. Prices are integer RWF values.
5. Web NFC requires a compatible Android device and HTTPS. Desktop browsers generally cannot read NFC.
6. Test using a dedicated test school, card and small balance before any real purchases.

## Production hardening required
This is an initial integration branch, not a certified payment terminal. Before real-world use, add role-specific staff authentication, authorized and audited top-up/refund workflows, card revocation/reissue controls, idempotency keys, rate limiting, reconciliation, backups, and hardware-specific NFC reader integration. NFC UIDs can be cloned; UID-only cards are not strong authentication. Never store balances in browser storage or trust client-supplied prices.


## Five-digit student canteen card IDs (pre-NFC)

The admin dashboard's **Manage Students** section now includes **Student canteen card IDs**. Use **Generate missing IDs** to issue a five-digit ID to every active student who does not already have one. Existing IDs are preserved. Use **Download CSV** to export the list for card printing. Newly added students receive an ID automatically.

Canteen staff can sign in at `/{school_name}/canteen-admin` and use **Charge by 5-digit student card ID** to select a published menu item and charge the student's active canteen balance. The server checks the school, student, item availability, and balance, then records an Eats transaction. IDs are scoped to a school. NFC UID linking and checkout remain available for a later hardware rollout.

**Deployment:** the application startup adds the nullable `students.canteen_card_id` column and a unique index scoped by school. Back up the production database and test with a test school before deploying. Five digits are an identifier, not a secret; payment-by-ID is restricted to an authenticated canteen staff or school-admin session. Never expose that payment endpoint to an unauthenticated student-facing checkout.

## Role-access review

Existing authenticated portals include school administration, student login, parent login/signup, teacher login, DOS timetable access (a teacher account flagged as DOS), and separate canteen staff login. Parent marks are scoped to the linked student; teacher mark entry is limited by assigned classes and subjects; timetable editing is limited to DOS/school administration.

Headmaster, patron, and matron are **not yet implemented as dedicated account roles** in this change. They should receive separate role-scoped accounts and dashboards in the next access-control pass. Do not share the school administrator account or grant unrestricted administrator permissions as a workaround.
