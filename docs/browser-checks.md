# Participant browser verification

Use the local demo event, local email delivery, and synthetic receipts/signatures.
Run the worker alongside the web application. Never use production receipts.

1. Open `/forms/demo/` at a desktop width and a 390-pixel mobile width. Check that
   fields, expense cards, buttons, and totals fit without horizontal scrolling.
2. Fill name, international multiline address, email, test IBAN
   `DE89370400440532013000`, statutory-purpose explanation, place, and trip dates.
3. Add two positions, each with merchant, purpose, receipt reference, and amount.
   Enter `10,10` and `20,20`; confirm a `30,30 EUR` total. Duplicate references
   should warn without blocking submission.
4. Upload mixed JPG/PNG/PDF receipts, including a multipage PDF. Wait for validation.
   Move rows and receipt files. Confirm the file ownership and displayed order.
5. Try invalid amounts, missing fields, missing receipts, and an empty signature.
   Check readable errors and keyboard focus. Unsupported/oversized files must not
   become ready. A scanner outage must leave files pending.
6. Review every value, acknowledge the declaration/privacy notice, and upload a
   synthetic signature. Also verify drawing with mouse or touch. Returning to edit
   must clear both signature and declaration before another review.
7. Submit once and try a double click. The receipt page must show one reference,
   processing progress, and no banking details. Reload it and confirm the reference
   remains the same. Another browser session must receive a 404 for that page.
8. Inspect the private local `.eml` attachment. Check summary fields, signature,
   exact totals, page completeness, row/file order, PDF bookmarks, and readability.
   Confirm the full serialized PDF is within the output limit.

Tests in `tests/` independently verify the server contract, document pipeline,
compression, webhook signatures, delivery failures, cleanup, and PostgreSQL races.
This checklist records the additional visual and browser interaction checks.

The repeatable interaction smoke test is `tests/browser/smoke.cjs`. With Playwright
available to Node, set `BASE_URL`, `FIXTURES_DIR` and `OUTPUT_DIR`, then run it with
Node. Supply only synthetic `train.png`, two-page `hotel.pdf`, and `signature.png`
fixtures. It verifies desktop/mobile completion, both signature paths, review
reset, row/file association, totals, delivery, and cross-session receipt denial.

Run `tests/browser/signature-recovery.cjs` with the same `BASE_URL` and
`FIXTURES_DIR` to inject signature validation failures, polling errors, polling
timeouts, and a busy deletion response. It checks that uploaded and drawn
signatures can be replaced repeatedly without exhausting the three-file limit.
Upload and submission endpoints are intercepted in this test, so it sends no mail.
