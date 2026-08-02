import { expect, test, type Page } from "@playwright/test";

// 每日一测 (test mode) production E2E (家长 2026-08-02: "每天听写20个单词，
// 让孩子写正确英文和中文，才能检查学习效果"):
// - the daily-test queue loads and shows the combined canvas (英文四线格 +
//   中文米字格 on ONE card) with NO answer leak (neither the English word
//   nor the Chinese gloss may be visible before the verdict);
// - draw + submit → mocked per-part verdict (英文✓ 中文✗) → reference shows
//   BOTH answers → read-aloud gate → mocked ASR pass advances;
// - empty queue (mocked) prompts the child to go learn new courses/words
//   (家长补充: "如果每日一测中没有单词进行测试，就提示孩子学习新的课程和新的单词").
//
// handwriting-check / read-aloud-events / pronunciation-check are ROUTE-
// MOCKED so no real review/FSRS/points rows land in production. Heartbeat
// rows from the run must be cleaned up afterwards (see memory notes).
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/daily_test.spec.ts --project=chromium --workers=1

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

async function login(page: Page) {
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
}

async function mockEchoAndAsr(page: Page) {
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
}

async function drawOnCanvas(page: Page) {
  const canvas = page.getByTestId("handwriting-canvas");
  await expect(canvas).toBeVisible({ timeout: 15000 });
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas has no bounding box");
  // Strokes in BOTH regions: upper english grid + lower chinese row.
  for (let i = 0; i < 3; i += 1) {
    const y = box.y + box.height * (0.15 + i * 0.3);
    await page.mouse.move(box.x + box.width * 0.2, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, y, { steps: 12 });
    await page.mouse.up();
  }
}

test.describe("daily test mode (每日一测)", () => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(240000);

  test("both-grid card: no answer leak, per-part verdict, echo advances", async ({ page }) => {
    let firstEnglish = "";
    let firstChinese = "";
    await page.route("**/api/v1/learning/daily-test-items?*", async (route) => {
      const response = await route.fetch();
      const items = await response.json();
      firstEnglish = items?.[0]?.english_text ?? "";
      firstChinese = items?.[0]?.chinese_text ?? "";
      await route.fulfill({ response });
    });
    // AI judge: mocked — 英文对、中文错的双关判定，无真实 review/FSRS 落库。
    await page.route("**/api/v1/learning/handwriting-check", async (route) => {
      const payload = JSON.parse(route.request().postData() ?? "{}");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          recognized: `${payload.expected_english ?? ""} / 香蕉`,
          correct: false,
          comment: "中文意思写错了",
          expected: payload.expected_english ?? "",
          learning_item_id: payload.learning_item_id ?? null,
          english_ok: true,
          chinese_ok: false,
        }),
      });
    });
    await mockEchoAndAsr(page);
    await login(page);

    await page.goto(`${BASE}/learning/study?mode=test`, { waitUntil: "domcontentloaded" });
    await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
    await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });

    // Card is up: the 每日一测 prompt + combined canvas.
    const canvas = page.getByTestId("handwriting-canvas");
    await expect(canvas).toBeVisible({ timeout: 30000 });
    await expect(page.getByText("📝 听发音，写出英文和中文意思").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /提交给老师/ })).toBeVisible();

    // LEAK GUARDS: neither the English word nor the Chinese gloss may be
    // visible as plain text before the verdict — both ARE the answer.
    if (firstEnglish) {
      await expect(page.locator("p", { hasText: firstEnglish })).toHaveCount(0);
    }
    if (firstChinese) {
      await expect(page.locator("p", { hasText: firstChinese })).toHaveCount(0);
    }

    // Draw + submit → per-part verdict chips appear on the card.
    await drawOnCanvas(page);
    await page.getByRole("button", { name: /提交给老师/ }).click();
    await expect(page.getByText("英文 ✓ 写对了")).toBeVisible({ timeout: 30000 });
    await expect(page.getByText("中文 ✗ 写错了")).toBeVisible();

    // Wrong answer → reference shows BOTH the English and the Chinese.
    if (firstEnglish) {
      await expect(page.getByText(new RegExp(`正确写法：.*${firstEnglish.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`))).toBeVisible();
    }

    // Read-aloud gate → mocked ASR pass → advance to the next item.
    await page.getByRole("button", { name: /知道了，读一遍正确答案/ }).click();
    await expect(page.getByText("🎤 轮到你了！大声读出来")).toBeVisible({ timeout: 20000 });
    await expect(page.getByText(/第 2 题 \/ 共 \d+ 题/).first()).toBeVisible({ timeout: 30000 });
  });

  test("empty queue prompts learning new courses and words", async ({ page }) => {
    // 家长要求：没有可测单词时引导孩子去学新课程新单词。直接 mock 空队列。
    await page.route("**/api/v1/learning/daily-test-items?*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
    });
    await login(page);

    await page.goto(`${BASE}/learning/study?mode=test`, { waitUntil: "domcontentloaded" });
    await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
    await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });

    await expect(page.getByText("今天没有可测的单词啦！")).toBeVisible({ timeout: 30000 });
    await expect(page.getByText(/去学习新的课程和新的单词/)).toBeVisible();
    const learnLink = page.getByRole("link", { name: /去学习新单词/ });
    await expect(learnLink).toBeVisible();
    await expect(learnLink).toHaveAttribute("href", "/learning");
  });
});
