import { test, expect, request, Page } from "@playwright/test";

// Speak-mode ("语音练习") e2e against production (xuehello.duckdns.org).
//
// ?mode=speak serves familiar sentences/phrases as dedicated read-aloud
// questions: the pronunciation-gated echo card IS the whole exercise —
// no typing, no choices. Enter/下一句 must NOT advance while the card is
// up; a passing reading auto-advances and logs a read-aloud event; 5
// failing readings auto-advance with a giveup event.
//
// The ASR call (pronunciation-check) and the event log (read-aloud-events)
// are route-mocked: the recorder still runs on the tone-injected mic, so
// the real record→VAD→encode→upload pipeline is exercised, but no daily
// speak quota is consumed and no LearningEvent is written by the tests.
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/speak_mode.spec.ts --project=chromium

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

interface SpeakItem {
  id: string;
  item_type: string;
  english_text: string;
  chinese_text: string;
  review_task_type?: string;
}

interface ReadAloudEventCall {
  learning_item_id?: string;
  english_text?: string;
  passed?: boolean;
  duration_seconds?: number;
  transcript?: string;
}

/** The speak-items endpoint is read-only, so the page's queue order matches. */
async function fetchSpeakItems(): Promise<SpeakItem[]> {
  const ctx = await request.newContext({
    baseURL: BASE,
    extraHTTPHeaders: { Authorization: `Bearer ${TOKEN}` },
  });
  const resp = await ctx.get("/api/v1/learning/speak-items?limit=20");
  expect(resp.ok()).toBeTruthy();
  const items = (await resp.json()) as SpeakItem[];
  await ctx.dispose();
  return items;
}

async function openSpeakPage(page: Page) {
  // Continuous 440 Hz tone mic — deterministic "speech" through the real
  // record→VAD→encode→upload pipeline (see pronunciation_gate.spec.ts).
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
  await page.goto(`${BASE}/learning/study?mode=speak`, { waitUntil: "domcontentloaded" });
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  await page.waitForTimeout(8000);
  if (await page.getByText("今天没有可练的口语句子啦").isVisible().catch(() => false)) {
    throw new Error("speak queue is empty — token expired, or all items already spoken today");
  }
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
}

function mockEventCapture(page: Page, calls: ReadAloudEventCall[]) {
  return page.route("**/api/v1/learning/read-aloud-events", async (route) => {
    calls.push((route.request().postDataJSON() ?? {}) as ReadAloudEventCall);
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ learning_item_id: null }),
    });
  });
}

test.describe("speak mode: dedicated read-aloud questions", () => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(300000);

  test("echo card is the whole exercise; gate holds; pass auto-advances + logs event", async ({ page }) => {
    await openSpeakPage(page);
    const items = await fetchSpeakItems();
    expect(items.length, "need at least 2 speak items for this test").toBeGreaterThanOrEqual(2);
    expect(items[0].review_task_type).toBe("read_aloud");

    const eventCalls: ReadAloudEventCall[] = [];
    await mockEventCapture(page, eventCalls);

    let checkCalls = 0;
    const expectedTexts: string[] = [];
    await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
      checkCalls += 1;
      const body = route.request().postData() ?? "";
      const match = /name="expected_text"\r\n\r\n([^\r\n]+)/.exec(body);
      const expected = match ? match[1] : "";
      expectedTexts.push(expected);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ transcript: expected, score: 0.95, passed: true, heard_speech: true }),
      });
    });

    // The echo card comes up immediately (no typing step) showing item[0].
    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 30000 });
    await expect(page.getByText(items[0].english_text, { exact: true }).first()).toBeVisible();

    // Gate: Enter must not advance — card stays, hint appears.
    await page.keyboard.press("Enter");
    await expect(page.getByText("大声读出来就能前进哦")).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible();

    // Mocked pass → auto-advance to the next item's echo card.
    await expect(page.getByText(items[1].english_text, { exact: true }).first()).toBeVisible({ timeout: 60000 });
    expect(checkCalls).toBeGreaterThanOrEqual(1);
    expect(expectedTexts[0]).toBe(items[0].english_text);

    // The pass was logged with the right item + the ASR transcript.
    expect(eventCalls.length).toBe(1);
    expect(eventCalls[0].learning_item_id).toBe(items[0].id);
    expect(eventCalls[0].passed).toBe(true);
    expect(eventCalls[0].english_text).toBe(items[0].english_text);
    expect(eventCalls[0].transcript).toBe(items[0].english_text);
    expect(eventCalls[0].duration_seconds ?? 0).toBeGreaterThanOrEqual(1);
  });

  test("5 failed readings auto-advance with a giveup event", async ({ page }) => {
    test.setTimeout(600000);
    await openSpeakPage(page);
    const items = await fetchSpeakItems();
    expect(items.length, "need at least 2 speak items for this test").toBeGreaterThanOrEqual(2);

    const eventCalls: ReadAloudEventCall[] = [];
    await mockEventCapture(page, eventCalls);

    let checkCalls = 0;
    await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
      checkCalls += 1;
      console.log(`[speak-giveup-test] pronunciation-check call #${checkCalls}`);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ transcript: "banana orange purple monkey", score: 0.1, passed: false, heard_speech: true }),
      });
    });

    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 30000 });

    // 5 mocked failures (each a ~10 s recording plus a TTS replay that can
    // hang in headless) → warm giveup message, then auto-advance.
    await expect(page.getByText("这句有点难")).toBeVisible({ timeout: 420000 });
    await expect(page.getByText(items[1].english_text, { exact: true }).first()).toBeVisible({ timeout: 30000 });
    expect(checkCalls).toBe(5);

    expect(eventCalls.length).toBe(1);
    expect(eventCalls[0].learning_item_id).toBe(items[0].id);
    expect(eventCalls[0].passed).toBe(false);
  });
});
