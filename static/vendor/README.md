# Vendored browser libraries

## jsQR 1.4.0

- File: `jsQR.js` (unmodified dist build)
- Source: https://unpkg.com/jsqr@1.4.0/dist/jsQR.js
- Project: https://github.com/cozmo/jsQR
- License: Apache-2.0

Used by the SCH-003 attendance station as the QR decoder for browsers that do not
expose the `BarcodeDetector` API (Firefox and Safari). It is served locally so the
gate station keeps scanning when the school network blocks third-party CDNs.
