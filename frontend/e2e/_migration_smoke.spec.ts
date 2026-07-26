import { test, expect } from "@playwright/test";

const BASE = "https://xuehello.duckdns.org";
const TOKEN = process.env.MIGRATION_TOKEN ?? "";
const USER = {
  id: "4c9633a1-a599-406e-af96-66a816681202",
  email: "cmx@a.com",
  username: "轩轩",
};

test.describe("migration smoke: memoseed on xuehello.duckdns.org", () => {
  test("homepage loads over HTTPS with valid cert", async ({ page }) => {
    const resp = await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    expect(resp?.status()).toBe(200);
    await expect(page.locator("body")).toBeVisible();
    // must be a secure context so the read-aloud mic (voice-echo) can activate
    expect(await page.evaluate(() => window.isSecureContext)).toBe(true);
    expect(await page.evaluate(() => !!navigator.mediaDevices?.getUserMedia)).toBe(true);
  });

  test("study page renders with real account token", async ({ page }) => {
    test.skip(!TOKEN, "no token provided");
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await page.evaluate(
      ({ token, user }) => {
        window.localStorage.setItem("memoseed_access_token", token);
        window.localStorage.setItem("memoseed_user", JSON.stringify(user));
      },
      { token: TOKEN, user: USER },
    );
    const resp = await page.goto(`${BASE}/learning/study`, { waitUntil: "domcontentloaded" });
    expect(resp?.status()).toBe(200);
    // study page should leave the login screen and render learning chrome
    await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
    await expect(page.locator("body")).toContainText(/学习|复习|课程|单词/, { timeout: 20000 });
  });
});
