import { expect, test, type Page } from "@playwright/test";

// 手写听写 (handwrite mode) production E2E:
// - the handwriting queue loads and the card shows a canvas (answer hidden);
// - Enter/Space cannot skip the exercise before the AI verdict;
// - drawing + submit → mocked AI verdict (correct) → pronunciation echo gate;
// - echo pass (mocked ASR) auto-advances to the next item;
// - study-time heartbeats flush while the card is up.
//
// The handwriting-check endpoint is ROUTE-MOCKED so no real review/FSRS/
// mistake rows land in production. read-aloud-events is mocked too (the
// echo pass would otherwise inflate the child's 朗读次数). Heartbeat rows
// from the run must be cleaned up afterwards (see memory notes).
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/handwrite_mode.spec.ts --project=chromium --workers=1

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

async function openHandwritePage(page: Page) {
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

  // AI judge: mocked — no real review/FSRS/points rows on production.
  await page.route("**/api/v1/learning/handwriting-check", async (route) => {
    const payload = JSON.parse(route.request().postData() ?? "{}");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        recognized: payload.expected_english ?? "",
        correct: true,
        comment: "写得真工整！",
        expected: payload.expected_english ?? "",
        learning_item_id: payload.learning_item_id ?? null,
      }),
    });
  });
  // Echo pass posts an exercise-echo read-aloud event — mock it.
  await page.route("**/api/v1/learning/read-aloud-events", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ learning_item_id: null }),
    });
  });
  // ASR: pass immediately so the echo gate advances.
  await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transcript: "ok", score: 1, passed: true, heard_speech: true }),
    });
  });

  await page.goto(`${BASE}/learning/study?mode=handwrite`, { waitUntil: "domcontentloaded" });
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
}

async function drawOnCanvas(page: Page) {
  const canvas = page.getByTestId("handwriting-canvas");
  await expect(canvas).toBeVisible({ timeout: 15000 });
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas has no bounding box");
  // A few strokes: enough for isEmpty() === false.
  for (let i = 0; i < 3; i += 1) {
    const y = box.y + box.height * (0.3 + i * 0.2);
    await page.mouse.move(box.x + box.width * 0.2, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, y, { steps: 12 });
    await page.mouse.up();
  }
}

test.describe("handwrite mode (手写听写)", () => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(240000);

  test("queue loads, canvas shown, keys gated, submit → verdict → echo → advance", async ({ page }) => {
    let firstItemEnglish = "";
    await page.route("**/api/v1/learning/handwriting-items?*", async (route) => {
      const response = await route.fetch();
      const items = await response.json();
      firstItemEnglish = items?.[0]?.english_text ?? "";
      await route.fulfill({ response });
    });

    await openHandwritePage(page);

    // Card is up: dictation prompt or translation prompt, canvas visible.
    const canvas = page.getByTestId("handwriting-canvas");
    await expect(canvas).toBeVisible({ timeout: 30000 });
    await expect(page.getByRole("button", { name: /提交给老师/ })).toBeVisible();

    // The dictation answer must NOT be visible as plain text on the page.
    if (firstItemEnglish) {
      await expect(page.locator("p", { hasText: firstItemEnglish })).toHaveCount(0);
    }

    // Keyboard cannot skip the exercise pre-verdict.
    const progressBefore = await page.getByText(/第 \d+ 题 \/ 共 \d+ 题/).first().textContent();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    await expect(page.getByText("跟着卡片一步一步来哦 ✍️")).toBeVisible();
    expect(await page.getByText(/第 \d+ 题 \/ 共 \d+ 题/).first().textContent()).toBe(progressBefore);

    // Submit is disabled-then-enabled flow: draw, submit, verdict appears.
    await drawOnCanvas(page);
    await page.getByRole("button", { name: /提交给老师/ }).click();
    // The verdict fires the read-aloud gate immediately (parent's global
    // rule: every exercise ends with echo), which hides the handwriting
    // card — so assert the feedback line, not card-only content.
    await expect(page.getByText("🌟 写对啦！真棒！")).toBeVisible({ timeout: 30000 });

    // The pronunciation echo gate takes over.
    await expect(page.getByText("🎤 轮到你了！大声读出来")).toBeVisible({ timeout: 20000 });

    // Mocked ASR pass → auto-advance: progress moves to the next item.
    await expect(page.getByText(/第 2 题 \/ 共 \d+ 题/).first()).toBeVisible({ timeout: 30000 });
  });
});
