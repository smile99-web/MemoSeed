import { test, expect } from "@playwright/test";

// Regression: 语音练习 / 跟读时没有计时 (parent report 2026-07-27).
//
// The study timer auto-pauses after 10s without keydown/pointer/touch
// events (STUDY_IDLE_TIMEOUT_MS). Reading aloud produces NONE of those —
// the child listens to the model then reads into the mic — so the timer
// flipped to "⏸ 已暂停" 10 seconds into every echo card. Fix: while the
// echo prompt is up, the idle auto-pause is skipped.
//
// This test holds the echo card up for 15 idle seconds (route-mocked
// pronunciation-check keeps failing, so the card stays in the retry loop
// without advancing) and asserts the timer never pauses and keeps counting.
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/echo_study_timer.spec.ts --project=chromium

const BASE = "https://xuehello.duckdns.org";
const TOKEN = process.env.MIGRATION_TOKEN ?? "";
const USER = {
  id: "4c9633a1-a599-406e-af96-66a816681202",
  email: "cmx@a.com",
  username: "轩轩",
};

test.use({
  launchOptions: {
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  },
  permissions: ["microphone"],
});

/** Parse "m:ss" / "h:mm:ss" into total seconds. */
function parseDuration(text: string): number {
  const parts = text.trim().split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n))) return -1;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

test("echo card active: study timer keeps counting through 15s of silence (no pause)", async ({ page }) => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(180000);

  // Tone mic — deterministic "speech" through the real record→VAD→upload
  // pipeline (see pronunciation_gate.spec.ts).
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      if (!constraints || constraints.audio !== true) {
        return original(constraints);
      }
      const ctx = new AudioContext();
      const oscillator = ctx.createOscillator();
      oscillator.frequency.value = 440;
      const gain = ctx.createGain();
      gain.gain.value = 0.5;
      const destination = ctx.createMediaStreamDestination();
      oscillator.connect(gain);
      gain.connect(destination);
      oscillator.start();
      return destination.stream;
    };
  });
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ({ token, user }) => {
      window.localStorage.setItem("memoseed_access_token", token);
      window.localStorage.setItem("memoseed_user", JSON.stringify(user));
    },
    { token: TOKEN, user: USER },
  );

  // ASR always "fails" → the echo card stays up in its retry loop without
  // advancing (5 attempts ≈ 25s, longer than our 15s observation window),
  // and no real ASR quota / LearningEvent is consumed.
  await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transcript: "hmm", score: 0.1, passed: false, heard_speech: true }),
    });
  });
  await page.route("**/api/v1/learning/read-aloud-events", async (route) => {
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ learning_item_id: null }) });
  });

  await page.goto(`${BASE}/learning/study?mode=speak`, { waitUntil: "domcontentloaded" });
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
  await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 30000 });

  const timer = page.locator("p.font-mono", { hasText: /^\d+:\d{2}/ }).first();
  await expect(timer).toBeVisible();

  const t0 = parseDuration((await timer.textContent()) ?? "");
  expect(t0, "timer shows a parseable duration").toBeGreaterThanOrEqual(0);

  // 15 seconds with ZERO input events — far past the 10s idle timeout.
  // waitForTimeout synthesizes no keydown/pointer/touch, exactly like a
  // child silently listening + reading aloud.
  await page.waitForTimeout(15000);

  // Old bug: "⏸ 已暂停" would replace the 学习时长 label after 10 idle s.
  await expect(page.getByText("⏸ 已暂停")).toBeHidden();
  await expect(page.getByText("学习时长")).toBeVisible();

  // …and the clock must actually have advanced (not just unpaused).
  const t1 = parseDuration((await timer.textContent()) ?? "");
  expect(t1, `timer advanced during read-aloud (was ${t0}s, now ${t1}s)`).toBeGreaterThan(t0);
});
