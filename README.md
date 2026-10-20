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
