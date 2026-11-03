/*
 * End-to-end walkthrough of the client demo story.
 *   BASE_URL=http://localhost:3000 ADMIN_EMAIL=... ADMIN_PASSWORD=... node e2e/demo-walkthrough.cjs [screenshotDir]
 * Fails (exit 1) on any page error, 5xx response or failed step.
 */
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE_URL || "http://localhost:3000";
const OUT = process.argv[2] || path.join(__dirname, "..", "test-results", "walkthrough");
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@platform.test";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "Admin@123456";
fs.mkdirSync(OUT, { recursive: true });

const problems = [];
const results = [];

function watch(page, label) {
  page.on("pageerror", (e) => problems.push(`[${label}] pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/WebSocket|favicon|Failed to load resource: the server responded with a status of 4/.test(m.text())) problems.push(`[${label}] console: ${m.text()}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 500) problems.push(`[${label}] HTTP ${r.status()} ${r.url()}`);
  });
}

async function step(name, fn) {
  const t = Date.now();
  try {
    await fn();
    results.push({ name, ok: true, ms: Date.now() - t });
    console.log(`✓ ${name} (${Date.now() - t}ms)`);
  } catch (e) {
    results.push({ name, ok: false, error: e.message });
    console.log(`✗ ${name}: ${e.message.split("\n")[0]}`);
  }
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
}

async function placeFromTicket(page, { side, amount, ack = false }) {
  const ticket = page.locator("aside").filter({ has: page.getByRole("button", { name: /Review (buy|sell) order/ }) }).first();
  if (side) await ticket.getByRole("button", { name: new RegExp(`^${side}`) }).first().click();
  if (amount !== undefined) {
    const input = ticket.locator('input[type="number"]').first();
    await input.fill(String(amount));
  }
  const review = ticket.getByRole("button", { name: /Review (buy|sell) order/ });
  await review.waitFor();
  await page.waitForFunction((el) => !el.disabled, await review.elementHandle(), { timeout: 10000 });
  await review.click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  if (ack) await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: /Confirm (buy|sell)/ }).click();
  await ticket.getByRole("status").filter({ hasText: /Order executed|Limit order open|Order rejected/ }).waitFor({ timeout: 10000 });
  const txt = await ticket.getByRole("status").filter({ hasText: /Order/ }).first().innerText();
  if (!/Order executed|Limit order open/.test(txt)) throw new Error(`Order not executed: ${txt}`);
  return txt;
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: Number(process.env.DSF || 1) });
  const page = await ctx.newPage();
  watch(page, "desktop");

  await step("01 landing page renders", async () => {
    await page.goto(BASE + "/");
    await page.getByRole("heading", { name: /One account/ }).waitFor();
    await shot(page, "01-landing");
  });

  await step("02 login with demo account", async () => {
    await page.goto(BASE + "/login");
    await page.getByRole("button", { name: /Continue with the demo account/ }).click();
    await page.waitForURL("**/dashboard");
    await page.getByText("Total account value").waitFor();
    await page.waitForTimeout(1500);
    await shot(page, "02-dashboard");
  });

  await step("03 stock market list", async () => {
    await page.goto(BASE + "/stocks");
    await page.getByRole("link", { name: /RELIANCE/ }).first().waitFor();
    await shot(page, "03-stocks");
  });

  await step("04 stock detail + chart", async () => {
    await page.getByRole("link", { name: /RELIANCE/ }).first().click();
    await page.waitForURL("**/stocks/RELIANCE");
    await page.locator("canvas").first().waitFor({ timeout: 10000 });
    await page.waitForTimeout(800);
    await shot(page, "04-stock-detail");
  });

  await step("05 buy stock (market order)", async () => {
    const txt = await placeFromTicket(page, { side: "Buy", amount: 3 });
    await shot(page, "05-stock-filled");
    if (!/RELIANCE/.test(txt)) throw new Error(txt);
  });

  await step("06 options chain", async () => {
    await page.goto(BASE + "/options");
    await page.getByText("Calls (CE)").waitFor();
    await page.getByText(/SPOT/).first().waitFor();
    await page.waitForTimeout(600);
    await shot(page, "06-option-chain");
  });

  await step("07 buy an option (long call)", async () => {
    const cell = page.getByRole("button", { name: /^Select NIFTY-.*-CE$/ }).nth(14);
    await cell.scrollIntoViewIfNeeded();
    await cell.click();
    await page.getByText("Payoff at expiry").waitFor();
    await placeFromTicket(page, { side: "Buy", amount: 1 });
    await shot(page, "07-option-bought");
  });

  await step("08 sell (write) an option with risk acknowledgement", async () => {
    const cell = page.getByRole("button", { name: /^Select NIFTY-.*-PE$/ }).nth(6);
    await cell.scrollIntoViewIfNeeded();
    await cell.hover();
    await cell.locator("xpath=ancestor::td[1]").getByRole("button", { name: /^Sell NIFTY-/ }).click();
    await page.getByText(/Short 1 lot/).waitFor();
    await placeFromTicket(page, { side: "Sell", amount: 1, ack: true });
    await shot(page, "08-option-sold");
  });

  await step("09 crypto market", async () => {
    await page.goto(BASE + "/crypto");
    await page.getByRole("link", { name: /BTC/ }).first().waitFor();
    await shot(page, "09-crypto");
  });

  await step("10 buy Bitcoin", async () => {
    await page.goto(BASE + "/crypto/BTC");
    await page.locator("canvas").first().waitFor({ timeout: 10000 });
    await placeFromTicket(page, { side: "Buy", amount: "0.002" });
    await shot(page, "10-btc-bought");
  });

  await step("11 forex market", async () => {
    await page.goto(BASE + "/forex");
    await page.getByRole("link", { name: /USDINR/ }).first().waitFor();
    await shot(page, "11-forex");
  });

  await step("12 open a currency position (short EUR/USD)", async () => {
    await page.goto(BASE + "/forex/EURUSD");
    await page.locator("canvas").first().waitFor({ timeout: 10000 });
    await placeFromTicket(page, { side: "Sell", amount: 1 });
    await shot(page, "12-forex-position");
  });

  await step("13 portfolio", async () => {
    await page.goto(BASE + "/portfolio");
    await page.getByText("Asset allocation").waitFor();
    await page.waitForTimeout(1200);
    await shot(page, "13-portfolio");
  });

  await step("13b positions", async () => {
    await page.goto(BASE + "/positions");
    await page.getByText("Total M2M (unrealised)").waitFor();
    await page.waitForTimeout(800);
    await shot(page, "13b-positions");
  });

  await step("14 orders + transactions", async () => {
    await page.goto(BASE + "/orders");
    await page.locator("table").getByText("EURUSD").first().waitFor();
    await shot(page, "14-orders");
    await page.goto(BASE + "/transactions");
    await page.getByText(/Simulated brokerage/).first().waitFor();
    await shot(page, "14b-transactions");
  });

  await step("14c wallet deposit", async () => {
    await page.goto(BASE + "/wallet");
    await page.getByRole("button", { name: /Add demo funds/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: /^Add/ }).click();
    await page.getByText("Demo funds added").waitFor();
    await shot(page, "14c-wallet");
  });

  await step("14d other user pages render", async () => {
    for (const p of ["/markets", "/watchlist", "/profile", "/settings"]) {
      await page.goto(BASE + p);
      await page.locator("main h1").first().waitFor();
    }
  });

  await step("14e normal user is blocked from admin", async () => {
    await page.goto(BASE + "/admin");
    await page.getByText("Administrator access required").waitFor();
    const r = await page.request.get(BASE + "/api/admin/stats");
    if (r.status() !== 403) throw new Error(`expected 403, got ${r.status()}`);
  });

  await step("15 admin dashboard", async () => {
    await page.request.post(BASE + "/api/auth/logout", { headers: { "x-kairos-client": "web" } });
    await page.goto(BASE + "/login");
    await page.getByLabel("Email").fill(ADMIN_EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: /^Sign in$/ }).click();
    await page.waitForURL("**/admin");
    await page.getByText("Registered users").waitFor();
    await page.waitForTimeout(1000);
    await shot(page, "15-admin");
    for (const p of ["users", "kyc", "assets", "orders", "transactions", "reports", "settings"]) {
      await page.goto(`${BASE}/admin/${p}`);
      await page.locator("main h1").first().waitFor();
      await page.waitForTimeout(400);
      await shot(page, `15-admin-${p}`);
    }
  });

  // Mobile
  const m = await browser.newContext({ ...devices["iPhone 13"] });
  const mp = await m.newPage();
  watch(mp, "mobile");
  await step("16 mobile: dashboard, options, stock ticket", async () => {
    await mp.goto(BASE + "/login");
    await mp.getByRole("button", { name: /Continue with the demo account/ }).click();
    await mp.waitForURL("**/dashboard");
    await mp.getByText("Total account value").waitFor();
    await mp.waitForTimeout(1200);
    await mp.screenshot({ path: path.join(OUT, "16-mobile-dashboard.png") });
    await mp.goto(BASE + "/options");
    await mp.getByText("Calls (CE)").waitFor();
    await mp.waitForTimeout(600);
    await mp.screenshot({ path: path.join(OUT, "16-mobile-options.png") });
    await mp.goto(BASE + "/stocks/TCS");
    await mp.getByRole("button", { name: "Buy", exact: true }).click();
    await mp.getByRole("dialog").getByRole("button", { name: /Review buy order/ }).waitFor();
    await mp.waitForTimeout(600);
    await mp.screenshot({ path: path.join(OUT, "16-mobile-ticket.png") });
    const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 1) throw new Error(`horizontal overflow ${overflow}px`);
  });

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, "results.json"), JSON.stringify({ base: BASE, results, problems }, null, 2));
  console.log(`\n${results.length - failed.length}/${results.length} steps passed; ${problems.length} runtime problems`);
  problems.forEach((p) => console.log("  ! " + p));
  process.exit(failed.length || problems.length ? 1 : 0);
})();
