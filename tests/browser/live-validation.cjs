// Delayed field feedback and stale-response regression checks. No uploads or mail.
// Run against the demo form. FORM_HTML optionally supplies an offline rendered page.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.env.BASE_URL || "http://127.0.0.1:8000";

(async () => {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let requests = 0, holdNext = false, heldRoute;
  if (process.env.FORM_HTML) {
    await page.route(`${base}/f/demo/`, (route) => route.fulfill({
      contentType: "text/html", body: fs.readFileSync(process.env.FORM_HTML, "utf8"),
    }));
    await page.route("**/static/**", (route) => route.fulfill({
      contentType: "text/javascript",
      body: route.request().url().endsWith("/form.js")
        ? fs.readFileSync(path.join(__dirname, "../../portal/static/portal/form.js"), "utf8") : "",
    }));
  }
  await page.route("**/f/demo/validate/", async (route) => {
    requests++;
    if (holdNext) {
      holdNext = false;
      heldRoute = route;
      return;
    }
    const data = route.request().postDataJSON();
    const fields = {};
    if (/\d/.test(data.full_name)) fields.full_name = [{ message: "Bitte prüfe deinen Namen." }];
    if (data.iban && data.iban !== "DE89370400440532013000")
      fields.iban = [{ message: "Bitte gib eine gültige IBAN ein." }];
    if (data.country === "AT" && data.postal_code.length !== 4)
      fields.postal_code = [{ message: "Bitte gib eine vierstellige Postleitzahl ein." }];
    await route.fulfill({ json: { fields } });
  });
  const input = async (selector, value) => page.locator(selector).fill(value);
  try {
    await page.goto(`${base}/f/demo/`);
    await page.waitForTimeout(800);
    assert.equal(requests, 0, "No validation before the participant edits a field");
    await input("#id_full_name", "Alex2");
    await page.waitForTimeout(200);
    await input("#id_full_name", "Alex23");
    await page.waitForTimeout(300);
    assert.equal(requests, 0, "Typing resets the debounce delay");
    await page.locator('#id_full_name[aria-invalid="true"]').waitFor();
    assert.equal(await page.locator("#error-full_name").textContent(), "Bitte prüfe deinen Namen.");
    assert.equal(await page.evaluate(() => document.activeElement.id), "id_full_name");
    assert.equal(await page.locator("#error-email").textContent(), "");
    await input("#id_full_name", "Alex Beispiel");
    await page.waitForFunction(() => !document.getElementById("id_full_name").hasAttribute("aria-invalid"));
    assert.equal(await page.locator("#error-full_name").textContent(), "");

    holdNext = true;
    await input("#id_iban", "invalid");
    await page.waitForRequest("**/f/demo/validate/");
    const corrected = page.waitForResponse("**/f/demo/validate/");
    await input("#id_iban", "DE89370400440532013000");
    await corrected;
    await heldRoute.fulfill({ json: { fields: { iban: [{ message: "Obsolete error" }] } } });
    await page.waitForTimeout(100);
    assert.equal(await page.locator("#error-iban").textContent(), "");

    await input("#id_postal_code", "12345");
    await page.locator("#id_country").selectOption("AT");
    await page.locator('#id_postal_code[aria-invalid="true"]').waitFor();
    await input("#id_postal_code", "1010");
    await page.waitForFunction(() => !document.getElementById("id_postal_code").hasAttribute("aria-invalid"));

    await page.locator("#add-expense").click();
    const amount = page.locator('[data-field="amount"]');
    await amount.fill("0");
    await page.locator('[data-field="amount"][aria-invalid="true"]').waitFor();
    const errorId = await amount.getAttribute("aria-describedby");
    assert.match(await page.locator(`[id="${errorId}"]`).textContent(), /positiven Betrag/);
    await amount.fill("12,50");
    await page.waitForFunction(() => !document.querySelector('[data-field="amount"]').hasAttribute("aria-invalid"));
    assert.equal(await page.locator(`[id="${errorId}"]`).textContent(), "");

    await page.locator("#review-button").click();
    await page.locator('#id_email[aria-invalid="true"]').waitFor();
    assert.equal(await page.locator("#details-panel").isVisible(), true);
    assert.deepEqual(errors, []);
    console.log("Live validation passed: debounce, focus, corrections, dependencies, stale responses, expense amounts, and review checks.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
