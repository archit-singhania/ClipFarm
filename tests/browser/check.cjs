// Run with npm ci --prefix tests/browser, then npm test --prefix tests/browser.
// Set CHROME_PATH if Chrome is installed outside the default macOS or Linux locations.
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright-core");

const root = path.resolve(__dirname, "../..");
const directory = fs.mkdtempSync(path.join(os.tmpdir(), "clipfarm-browser-"));
const python = process.env.PYTHON || "python3";
const env = { ...process.env, FRAMEFOLD_DATA: directory, CLIPFARM_DATA: directory };
const chrome = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find(file => file && fs.existsSync(file));
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function fixture(name, input, duration) {
  const file = path.join(directory, name + ".mp4");
  execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", input, "-t", duration, "-c:v", "libx264",
    "-preset", "ultrafast", "-pix_fmt", "yuv420p", file]);
  return file;
}
async function stop(child) {
  if (child && child.exitCode === null && child.signalCode === null) {
    const exit = once(child, "exit");
    child.kill("SIGTERM");
    await exit;
  }
}
async function run() {
  assert.ok(chrome, "Install Chrome or set CHROME_PATH.");
  let server, worker, browser;
  try {
    server = spawn(python, ["-u", "-m", "framefold.server", "--port", "0", "--no-worker"],
      { cwd: root, env, stdio: ["ignore", "pipe", "inherit"] });
    const first = (await once(server.stdout, "data"))[0].toString();
    const base = first.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];
    assert.ok(base, first);
    const motion = fixture("Motion study", "life=size=320x180:rate=15:random_seed=4", "12");
    const still = fixture("Quiet frame", "color=navy:size=320x180:rate=15", "5");
    const music = path.join(directory, "bed.wav");
    execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2", music]);
    browser = await chromium.launch({ executablePath: chrome, headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce",
      bypassCSP: true }); // Allows the locally bundled axe script to inspect the test page.
    const page = await context.newPage();
    const exceptions = [];
    page.on("pageerror", err => exceptions.push(err.message));
    await page.goto(base);
    await page.getByRole("heading", { name: "Clip generator" }).waitFor();
    const fonts = await page.evaluate(async () => {
      await document.fonts.ready;
      return ["600 20px 'Barlow Condensed'", "500 16px 'DM Sans'"].every(font => document.fonts.check(font));
    });
    assert.ok(fonts, "Both local font families must load.");
    const guide = page.locator(".guide-switch");
    await guide.click();
    assert.equal(await guide.getAttribute("aria-pressed"), "false");
    await guide.click();

    async function generate(file, useMusic) {
      await page.getByRole("link", { name: "Generator", exact: true }).click();
      await page.getByRole("button", { name: "Upload file" }).click();
      await page.getByLabel("Choose a video").setInputFiles(file);
      if (useMusic) {
        await page.locator("summary").click();
        await page.getByLabel("Framing", { exact: true }).selectOption("track");
        await page.locator("#music").setInputFiles(music);
      }
      await page.getByRole("button", { name: /Generate clips/ }).click();
      await page.waitForURL(/#\/runs\/[a-f0-9]{32}$/);
      return page.url().split("/").pop();
    }
    const resultId = await generate(motion, true);
    const staticId = await generate(still, false);
    const readJob = async id => {
      const response = await context.request.get(base + "/api/jobs/" + id);
      assert.equal(response.status(), 200);
      return response.json();
    };
    const options = (await readJob(resultId)).settings;
    assert.ok(options.music_id && options.framing === "track");
    worker = spawn(python, ["-m", "framefold.worker"], { cwd: root, env, stdio: "inherit" });
    async function finished(id) {
      for (let attempt = 0; attempt < 300; attempt++) {
        const job = await readJob(id);
        if (!["running", "queued"].includes(job.state)) return job;
        await wait(500);
      }
      throw Error("Processing timed out for browser test fixture.");
    }
    const job = await finished(resultId);
    assert.equal(job.state, "ready", JSON.stringify(job));
    assert.equal((await finished(staticId)).state, "no_matches");
    assert.ok(job.clips[0].artifact.soundtrack, "Selected music reaches the encoded clip.");
    await stop(worker);
    const pendingId = await generate(motion, false);

    const detailURL = base + "/#/runs/" + resultId + "/" + job.clips[0].id;
    const ratingPath = "/api/clips/" + job.clips[0].id + "/rating";
    async function openRating() {
      const loaded = page.waitForResponse(response => response.url().endsWith(ratingPath)
        && response.request().method() === "GET");
      await page.getByText("Rate this pick", { exact: true }).click();
      assert.equal((await loaded).status(), 200);
    }
    async function saveGrade(value) {
      await page.getByLabel("Would you post this clip?").selectOption(value);
      const saved = page.waitForResponse(response => response.url().endsWith(ratingPath)
        && response.request().method() === "POST");
      await page.getByRole("button", { name: "Save rating" }).click();
      const result = await saved;
      assert.equal(result.status(), 200, await result.text());
      await page.getByText("Rating saved locally.", { exact: true }).waitFor();
    }
    const permission = "Original synthetic browser fixture";
    await page.goto(detailURL);
    await openRating();
    await page.getByLabel("Source genre").selectOption("animation");
    await page.getByLabel("License or permission").fill(permission);
    await saveGrade("2");
    await page.reload();
    await openRating();
    await page.waitForFunction(() => document.querySelector("#grade").value === "2");
    assert.equal(await page.getByLabel("Source genre").inputValue(), "animation");
    assert.equal(await page.getByLabel("License or permission").inputValue(), permission);
    await saveGrade("3");
    const downloadEvent = page.waitForEvent("download");
    await page.getByRole("link", { name: /Export run ratings/ }).click();
    const download = await downloadEvent;
    assert.equal(download.suggestedFilename(), "clipfarm-ratings.jsonl");
    const records = fs.readFileSync(await download.path(), "utf8").trim().split("\n").map(line => JSON.parse(line));
    assert.equal(records.length, 1);
    assert.equal(records[0].label, 3);
    assert.equal(records[0].rights, permission);
    assert.equal(records[0].family, "animation");
    assert.ok(records[0].features.length);
    assert.equal(records[0].start_ms, job.clips[0].start_ms);
    assert.equal(records[0].end_ms, job.clips[0].end_ms);

    const routes = [
      ["generator", "#/", "#generate"], ["history", "#/runs", ".run-row"],
      ["progress", "#/runs/" + pendingId, "#run-progress:not([hidden])"],
      ["results", "#/runs/" + resultId, "#run-results:not([hidden]) .card"],
      ["detail", "#/runs/" + resultId + "/" + job.clips[0].id, "#revision"],
      ["recovery", "#/runs/" + staticId, "#run-status:not([hidden])"],
    ];
    const violations = [];
    let checked = 0;
    for (const theme of ["charcoal", "forest", "navy"]) {
      await page.locator("#theme-button").click();
      const radio = page.locator('input[value="' + theme + '"]');
      await radio.check();
      await radio.press("Escape");
      assert.ok(await page.locator("#theme-button").evaluate(el => el === document.activeElement));
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 950 });
        for (const [name, route, selector] of routes) {
          await page.goto(base + "/" + route);
          await page.locator(selector).first().waitFor();
          if (name === "detail") await openRating();
          const dimensions = await page.evaluate(() => [innerWidth, document.documentElement.scrollWidth, document.documentElement.dataset.theme]);
          assert.deepEqual(dimensions, [width, width, theme], name + " layout or theme");
          checked++;
          if (width === 1440 || (width === 320 && theme === "charcoal")) {
            if (!(await page.evaluate(() => Boolean(window.axe)))) {
              await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
            }
            const issues = await page.evaluate(async () => (await window.axe.run(document, {
              runOnly: { type: "tag", values: ["wcag2a","wcag2aa","wcag21aa"] },
            })).violations.map(issue => [issue.id, issue.nodes.map(node => node.target)]));
            if (issues.length) violations.push({ name, width, theme, issues });
          }
        }
      }
    }

    await page.goto(base + "/#/");
    await page.locator("#generate").waitFor();
    await page.locator("#theme-button").focus();
    await page.keyboard.press("Enter");
    await page.keyboard.press("ArrowUp"); // Navy -> Forest.
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("html").getAttribute("data-theme"), "forest");
    const second = await context.newPage();
    await second.goto(base + "/#/");
    assert.equal(await second.locator("html").getAttribute("data-theme"), "forest");
    await page.locator("#theme-button").click();
    await page.getByRole("radio", { name: "Charcoal" }).check();
    await page.keyboard.press("Escape");
    await second.waitForFunction(() => document.documentElement.dataset.theme === "charcoal");
    await page.reload();
    assert.equal(await page.locator("html").getAttribute("data-theme"), "charcoal");
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator(".monitor-image img").waitFor();
    await page.evaluate(async () => { await document.fonts.ready; });
    if (process.env.CLIPFARM_UI_SCREENSHOT) {
      worker = spawn(python, ["-m", "framefold.worker"], { cwd: root, env, stdio: "inherit" });
      const showcase = await browser.newContext({ viewport: { width: 1440, height: 950 } });
      const firstVisit = await showcase.newPage();
      await firstVisit.goto(base);
      await firstVisit.getByText("Local worker", { exact: true }).waitFor();
      await firstVisit.locator(".empty").waitFor();
      await firstVisit.evaluate(async () => { await document.fonts.ready; });
      await firstVisit.screenshot({ path: process.env.CLIPFARM_UI_SCREENSHOT });
      await showcase.close();
    }
    // Exercise enlarged text and controls at a 640 CSS-pixel window.
    await page.setViewportSize({ width: 640, height: 950 });
    await page.evaluate(() => { document.body.style.zoom = "200%"; });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.ok(await page.getByRole("button", { name: /Generate clips/ }).isVisible());
    const denied = await browser.newContext({ reducedMotion: "reduce" });
    await denied.addInitScript(() => Object.defineProperty(window, "localStorage", { get() { throw new Error("Storage blocked"); } }));
    const fresh = await denied.newPage();
    await fresh.goto(base);
    await fresh.locator("#theme-button").click();
    await fresh.getByRole("radio", { name: "Forest" }).check();
    assert.equal(await fresh.locator("html").getAttribute("data-theme"), "forest");
    assert.deepEqual(exceptions, []);
    assert.deepEqual(violations, [], "WCAG A/AA findings: " + JSON.stringify(violations));
    console.log(checked + " responsive theme/route checks passed; 24 axe scans including the open rating form, rating save/reload/update/JSONL download, picker, cross-tab storage, blocked storage, reduced-motion and zoom checks passed.");
  } finally {
    if (browser) await browser.close();
    await stop(worker);
    await stop(server);
    fs.rmSync(directory, { recursive: true, force: true });
  }
}
run().catch(err => { console.error(err); process.exitCode = 1; });
