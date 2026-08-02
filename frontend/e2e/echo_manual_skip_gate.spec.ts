import { test, expect } from "@playwright/test";

// Regression: 孩子可以不发音直接点"我读完了"跳过朗读 (parent report 2026-08-01).
//
// The manual (degraded) echo mode exists as the broken-mic escape hatch —
// but the 我读完了 button used to be clickable the instant the card appeared,
// awarding +2 points and a 开口 count for ZERO reading. Worse, staying silent
// through 3 no-speech cycles deliberately degrades the whole SESSION into
// manual mode, turning every later card into a one-click free pass.
//
// Fix under test:
//  1. The button starts DISABLED ("先仔细听示范…") and only unlocks after the
//     model TTS has played AND one loudness listen window finished undetected
//     — a silent skip now costs more time than just reading aloud.
//  2. A manual click earns NO points, does NOT increment the 开口 count, and
//     is logged to the timeline as passed=false (honest like the giveup).
//  3. The manual loudness pass demands SUSTAINED voice scaled to the text
//     length (~450ms/word, no peak shortcut) — shouting only the first word
//     must not count as having read the sentence (parent report 2026-08-02).
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/echo_manual_skip_gate.spec.ts --project=chromium --workers=1

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

test("first-word shout does NOT pass; sustained reading does (manual loudness gate)", async ({ page }) => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(300000);

  // Controllable tone mic: silence by default; window.__testBurst(ms) emits
  // a loud 440 Hz burst for ms milliseconds — simulating "shout one word"
  // (500ms) vs "read the whole sentence" (3000ms sustained).
  await page.addInitScript(() => {
    const ctx = new AudioContext();
    const destination = ctx.createMediaStreamDestination();
    (window as unknown as { __testBurst: (ms: number) => void }).__testBurst = (ms: number) => {
      const oscillator = ctx.createOscillator();
      oscillator.frequency.value = 440;
      const gain = ctx.createGain();
      gain.gain.value = 0.5;
      oscillator.connect(gain);
      gain.connect(destination);
      oscillator.start();
      window.setTimeout(() => {
        try {
          oscillator.stop();
          oscillator.disconnect();
          gain.disconnect();
        } catch {
          /* already stopped */
        }
      }, ms);
    };
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      if (!constraints || constraints.audio !== true) {
        throw new Error("test mic only supports audio");
      }
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

  await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transcript: "", score: 0, passed: false, heard_speech: false }),
    });
  });
  await page.route("**/api/v1/learning/read-aloud-events", async (route) => {
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ learning_item_id: null }) });
  });
  await page.route("**/api/v1/memory/points/award", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total_points: 0 }) });
  });

  await page.goto(`${BASE}/learning/study?mode=speak`, { waitUntil: "domcontentloaded" });
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
  await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 30000 });

  // Silence through 3 no-speech cycles → manual mode.
  const manualBtn = page.locator("button", { hasText: /我读完了|先仔细听示范/ }).first();
  await expect(manualBtn).toBeVisible({ timeout: 150000 });

  // --- Phase 1: a 500ms first-word shout must NOT pass ---
  await page.getByRole("button", { name: /再听一遍/ }).click();
  await expect(page.locator("button", { hasText: "先仔细听示范…" }).first()).toBeDisabled({ timeout: 5000 });
  // Wait for the listen window ("正在听…") then shout ONE word.
  await expect(page.getByText("🎧 正在听…读出来！")).toBeVisible({ timeout: 30000 });
  await page.evaluate(() => (window as unknown as { __testBurst: (ms: number) => void }).__testBurst(500));
  // The window must end WITHOUT a pass: no success, button unlocks instead.
  await expect(page.locator("button", { hasText: "我读完了！" }).first()).toBeEnabled({ timeout: 30000 });
  await expect(page.getByText("🌟 真棒！")).toBeHidden();
  await expect(page.getByText("轮到你了！大声读出来")).toBeVisible();

  // --- Phase 2: sustained reading (3s of voice) DOES pass ---
  await page.getByRole("button", { name: /再听一遍/ }).click();
  await expect(page.getByText("🎧 正在听…读出来！")).toBeVisible({ timeout: 30000 });
  await page.evaluate(() => (window as unknown as { __testBurst: (ms: number) => void }).__testBurst(3000));
  await expect(page.getByText("🌟 真棒！")).toBeVisible({ timeout: 30000 });
});

test("manual-mode 我读完了 button is gated and earns no rewards", async ({ page }) => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(300000);

  // SILENT mic — simulates a child who never speaks (same shape as
  // echo_study_timer.spec.ts): 3 no-speech cycles degrade into manual mode.
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      if (!constraints || constraints.audio !== true) {
        return original(constraints);
      }
      const ctx = new AudioContext();
      const destination = ctx.createMediaStreamDestination();
      return destination.stream; // no source connected → pure silence
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

  // Mutating endpoints mocked: no real ASR quota, no timeline events, no points.
  const readAloudBodies: Array<{ passed?: boolean }> = [];
  let readAloudPointAwards = 0;
  await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transcript: "", score: 0, passed: false, heard_speech: false }),
    });
  });
  await page.route("**/api/v1/learning/read-aloud-events", async (route) => {
    try {
      readAloudBodies.push(route.request().postDataJSON() as { passed?: boolean });
    } catch {
      readAloudBodies.push({});
    }
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ learning_item_id: null }) });
  });
  await page.route("**/api/v1/memory/points/award", async (route) => {
    try {
      const body = route.request().postDataJSON() as { reason?: string };
      if (body?.reason === "read-aloud") {
        readAloudPointAwards += 1;
      }
    } catch {
      /* non-JSON body — not a read-aloud award */
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total_points: 0 }) });
  });

  await page.goto(`${BASE}/learning/study?mode=speak`, { waitUntil: "domcontentloaded" });
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
  await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 30000 });

  // 3 no-speech cycles (TTS + 8s start-timeout + 1.4s pause each) degrade the
  // session into manual mode → the 我读完了 button appears.
  const manualBtn = page.locator("button", { hasText: /我读完了|先仔细听示范/ }).first();
  await expect(manualBtn).toBeVisible({ timeout: 150000 });

  // Replay the model reading: the button must IMMEDIATELY lock again and stay
  // locked through TTS + the 9s listen window.
  await page.getByRole("button", { name: /再听一遍/ }).click();
  await expect(page.locator("button", { hasText: "先仔细听示范…" }).first()).toBeDisabled({ timeout: 5000 });

  // …and only unlock (as 我读完了！) once the silent listen window completed.
  const unlockedBtn = page.locator("button", { hasText: "我读完了！" }).first();
  await expect(unlockedBtn).toBeEnabled({ timeout: 60000 });

  // Click it: advances, but with the no-reward message — no points, no count.
  await unlockedBtn.click();
  await expect(page.getByText(/没听到声音，这次不计分/)).toBeVisible({ timeout: 10000 });
  await expect(page.getByText(/今天第 \d+ 次/)).toBeHidden();

  // Backend honesty: no read-aloud points were awarded, and the timeline
  // event (if any) records passed=false — never a fake pass.
  await page.waitForTimeout(1500); // let the fire-and-forget telemetry land
  expect(readAloudPointAwards, "manual skip must not award read-aloud points").toBe(0);
  const lastEvent = readAloudBodies.at(-1);
  expect(lastEvent, "a read-aloud telemetry event should have been logged").toBeTruthy();
  expect(lastEvent?.passed, "manual skip must be logged as not-passed").toBe(false);
});
