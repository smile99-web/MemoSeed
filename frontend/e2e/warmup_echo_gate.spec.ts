import { test, expect, Page } from "@playwright/test";

// Course-learn warm-up pronunciation gate e2e against production.
//
// Parent's rule: EVERY exercise in course-package learning ends with the
// child reading aloud — including the new-word warm-up cards shown before a
// course sentence. This test mocks the course items endpoint with a synthetic
// sentence carrying focus_words so the warm-up card deterministically
// appears, then verifies:
//   1. The warm-up card starts a read-aloud echo for the warm-up word.
//   2. While the echo card is up, the warm-up's own advance button is hidden
//      and Enter cannot skip the read-aloud (gate feedback shows).
//   3. A passing read auto-advances out of warm-up into the sentence UI.
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/warmup_echo_gate.spec.ts --project=chromium --workers=1

const BASE = "https://xuehello.duckdns.org";
const TOKEN = process.env.MIGRATION_TOKEN ?? "";
const USER = {
  id: "4c9633a1-a599-406e-af96-66a816681202",
  email: "cmx@a.com",
  username: "轩轩",
};
const COURSE_ID = "ce681605-b84b-42ef-ad4d-f8ed1f361f84";
const PACKAGE_ID = "1d4f1816-ba34-422f-8b8e-231139fb5186";

const SYNTHETIC_ITEM = {
  id: "11111111-2222-4333-8444-555555555555",
  user_id: USER.id,
  item_type: "sentence",
  english_text: "I like strawberries",
  chinese_text: "我喜欢草莓。",
  phonetic: null,
  syllables: null,
  grapheme_phoneme_map: null,
  difficulty_level: 1,
  sort_order: 1,
  unit_label: null,
  source: "课程",
  focus_words: ["strawberry"],
  course_id: COURSE_ID,
  created_at: "2026-07-26T00:00:00Z",
  updated_at: "2026-07-26T00:00:00Z",
};

test.use({
  launchOptions: {
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  },
  permissions: ["microphone"],
});

async function openCourseLearnPage(page: Page) {
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      if (!constraints || constraints.audio !== true) return original(constraints);
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

  await page.route(`**/api/v1/learning/items?*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([SYNTHETIC_ITEM]),
    });
  });
  await page.route(`**/api/v1/learning/word-translations`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ translations: { strawberry: "草莓" } }),
    });
  });
  // The warmup echo pass posts an exercise-echo read-aloud event — mock it
  // so test runs don't inflate the child's 今日/每周朗读次数 on production.
  await page.route("**/api/v1/learning/read-aloud-events", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ learning_item_id: null }),
    });
  });

  await page.goto(
    `${BASE}/learning/study?course_id=${COURSE_ID}&package_id=${PACKAGE_ID}&course_name=${encodeURIComponent("测试课")}&mode=learn`,
    { waitUntil: "domcontentloaded" },
  );
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
}

test.describe("course warm-up pronunciation gate", () => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(240000);

  test("warm-up word requires read-aloud; gate blocks Enter; pass advances", async ({ page }) => {
    let checkCalls = 0;
    let lastExpected = "";
    await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
      checkCalls += 1;
      const body = route.request().postData() ?? "";
      const match = /name="expected_text"\r\n\r\n([^\r\n]+)/.exec(body);
      lastExpected = match ? match[1] : "";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ transcript: lastExpected, score: 0.95, passed: true, heard_speech: true }),
      });
    });

    await openCourseLearnPage(page);

    // 1. Warm-up card appears AND the read-aloud echo owns it immediately.
    await expect(page.getByText(/新词预热/)).toBeVisible({ timeout: 60000 });
    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 30000 });

    // 2. The warm-up's own advance button is hidden while the echo is up.
    await expect(page.locator("button").filter({ hasText: "记住了，继续" })).toHaveCount(0);

    // 3. Enter cannot skip the read-aloud — gate feedback shows, card stays.
    await page.keyboard.press("Enter");
    await expect(page.getByText("大声读出来就能前进哦")).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/新词预热/)).toBeVisible();

    // 4. Mocked pass auto-advances: warm-up finishes and the sentence typing
    //    UI (判定单词) appears. The constant 440Hz tone means the recorder
    //    runs to its 10s cap before upload, so allow generous time.
    await expect(page.getByText(/新词预热/)).toBeHidden({ timeout: 60000 });
    await expect(page.locator("button").filter({ hasText: /判定/ }).first()).toBeVisible({ timeout: 30000 });
    expect(checkCalls).toBeGreaterThanOrEqual(1);
    expect(lastExpected).toBe("strawberry");
  });
});
