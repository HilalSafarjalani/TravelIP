import { chromium } from "playwright";

const consoleErrors = [];
const pageErrors = [];

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => pageErrors.push(String(err)));

await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);

await page.fill("#target-input", "8.8.8.8");
await page.click("#go-btn");

// Sample frequently to catch a moving-packet/comet-trail frame.
for (let i = 0; i < 24; i++) {
  await page.waitForTimeout(700);
  await page.screenshot({ path: `m4_frame_${String(i).padStart(2, "0")}.png` });
}

let waitError = null;
try {
  await page.waitForFunction(
    () => document.getElementById("status-bar")?.textContent?.includes("Trace complete"),
    { timeout: 20000 }
  );
} catch (err) {
  waitError = String(err);
}
await page.waitForTimeout(4000);
await page.screenshot({ path: "m4_frame_final.png" });

console.log(JSON.stringify({ waitError, consoleErrors, pageErrors }, null, 2));
await browser.close();
