import { test, expect, request, Page } from "@playwright/test";

// Pronunciation-gate e2e against production (xuehello.duckdns.org).
//
// The echo card after completing an item is gated by real pronunciation
// checking: Enter/Space/下一句/跳过 must NOT advance while it is up;
// a passing reading auto-advances; 5 failing readings also auto-advance.
// The ASR backend call is route-mocked (the recorder still runs on the
// Chromium fake mic, so the full record→VAD→upload pipeline is exercised).
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/pronunciation_gate.spec.ts --project=chromium

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

interface ReviewItem {
  id: string;
  item_type: string;
  english_text: string;
  chinese_text: string;
  review_task_type?: string;
  review_choices?: string[];
  review_answer?: string | null;
}

function normalizeChoice(value: string): string {
  return value.trim().replace(/\s+/g, "");
}

async function fetchReviewItems(): Promise<ReviewItem[]> {
  const ctx = await request.newContext({
    baseURL: BASE,
    extraHTTPHeaders: { Authorization: `Bearer ${TOKEN}` },
  });
  const resp = await ctx.get("/api/v1/learning/review-items?limit=200&review_cap=200");
  expect(resp.ok()).toBeTruthy();
  const items = (await resp.json()) as ReviewItem[];
  await ctx.dispose();
  return items;
}

async function openStudyPage(page: Page) {
  // Chromium's fake mic only beeps intermittently — too sparse for the VAD
  // window. Replace getUserMedia with a continuous 440 Hz tone stream so the
  // recorder deterministically captures "speech" (the mocked ASR decides
  // pass/fail; this exercises the real record→VAD→encode→upload pipeline).
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
  await page.goto(`${BASE}/learning/study?mode=review`, { waitUntil: "domcontentloaded" });
  await expect(page).not.toHaveURL(/login/, { timeout: 15000 });
  // Fail fast with a clear message when the token expired (empty queue page).
  await page.waitForTimeout(8000);
  if (await page.getByText("当前课程还没有导入内容").isVisible().catch(() => false)) {
    throw new Error("study queue is empty — the access token probably expired; mint a fresh one");
  }
  // Study items load asynchronously — wait until the action bar is up.
  await expect(page.getByRole("button", { name: "跳过", exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("正在加载学习内容")).toBeHidden({ timeout: 60000 });
}

/**
 * Advance through the review queue (skipping non-choice items) until any
 * choice review task is on screen, then complete it. Identification by
 * option-set match against the fetched queue (the endpoint's ordering is
 * not stable across calls, so index-based navigation is unreliable). When
 * no item matches (frontend fallback option pool), click options one by
 * one — a wrong choice is non-destructive (re-selection is allowed after
 * ~800 ms) and the correct one completes the item.
 */
async function completeFirstChoiceItem(page: Page, items: ReviewItem[]) {
  const skipButton = page.getByRole("button", { name: "跳过", exact: true });
  const optionButtons = page.locator("button").filter({ hasText: /^\d+\. / });

  for (let step = 0; step < 60; step += 1) {
    if ((await optionButtons.count()) > 0) {
      const displayed: string[] = [];
      for (let i = 0; i < (await optionButtons.count()); i += 1) {
        displayed.push(normalizeChoice((await optionButtons.nth(i).innerText()).replace(/^\d+\.\s*/, "")));
      }
      const displayedSet = new Set(displayed);
      const matched = items.find((item) => {
        const choices = item.review_choices ?? [];
        if (choices.length === 0 || choices.length !== displayed.length) {
          return false;
        }
        return choices.every((choice) => displayedSet.has(normalizeChoice(choice)));
      });
      if (matched && (matched.review_answer || matched.chinese_text)) {
        await clickCorrectChoice(page, matched.review_answer || matched.chinese_text);
        return matched;
      }
      // Fallback pool: brute-force the options until one is correct.
      for (let i = 0; i < displayed.length; i += 1) {
        await optionButtons.nth(i).click();
        const correct = await page.getByText("选择正确").first().isVisible().catch(() => false);
        if (correct) {
          return undefined;
        }
        await page.waitForTimeout(1000);
      }
      throw new Error("no correct option found by brute force");
    }
    await expect(skipButton).toBeVisible({ timeout: 30000 });
    await skipButton.click();
    await page.waitForTimeout(1200);
  }
  throw new Error("no choice review item found within 60 skips");
}

/** Click the choice option whose text matches the expected answer. */
async function clickCorrectChoice(page: Page, answer: string) {
  const wanted = normalizeChoice(answer);
  const buttons = page.locator("button").filter({ hasText: /^\d+\. / });
  await expect(buttons.first()).toBeVisible({ timeout: 30000 });
  const count = await buttons.count();
  for (let i = 0; i < count; i += 1) {
    const text = (await buttons.nth(i).innerText()).replace(/^\d+\.\s*/, "");
    if (normalizeChoice(text) === wanted && wanted) {
      await buttons.nth(i).click();
      return;
    }
  }
  throw new Error(`correct choice "${answer}" not found among ${count} options`);
}

test.describe("pronunciation gate on the echo card", () => {
  test.skip(!TOKEN, "no token provided");
  test.setTimeout(300000);

  test("Enter/下一句 are blocked while echo is up; a passing reading auto-advances", async ({ page }) => {
    await openStudyPage(page);
    const items = await fetchReviewItems();
    expect(
      items.some((item) => item.review_task_type && (item.review_answer || item.chinese_text)),
      "no answerable review item available",
    ).toBe(true);

    let checkCalls = 0;
    await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
      checkCalls += 1;
      const body = route.request().postData() ?? "";
      const match = /name="expected_text"\r\n\r\n([^\r\n]+)/.exec(body);
      const expected = match ? match[1] : "";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ transcript: expected, score: 0.95, passed: true, heard_speech: true }),
      });
    });

    await completeFirstChoiceItem(page, items);

    // Echo card appears after the correct choice.
    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 20000 });

    // Gate: Enter must not advance — card stays, hint appears.
    await page.keyboard.press("Enter");
    await expect(page.getByText("大声读出来就能前进哦")).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible();

    // Gate: 下一句 must not advance either.
    const nextButton = page.getByRole("button", { name: "下一句", exact: true });
    if (await nextButton.isVisible().catch(() => false)) {
      await nextButton.click();
      await expect(page.getByText("轮到你了！大声读出来")).toBeVisible();
    }

    // The fake mic keeps the recorder going to the 10 s cap, then the
    // mocked ASR pass must auto-advance: the echo card disappears with no
    // further input.
    await expect(page.getByText("轮到你了！大声读出来")).toBeHidden({ timeout: 60000 });
    expect(checkCalls).toBeGreaterThanOrEqual(1);
  });

  test("5 failed readings auto-advance (giveup path)", async ({ page }) => {
    test.setTimeout(600000);
    await openStudyPage(page);
    const items = await fetchReviewItems();
    expect(
      items.some((item) => item.review_task_type && (item.review_answer || item.chinese_text)),
      "no answerable review item available",
    ).toBe(true);

    let checkCalls = 0;
    await page.route("**/api/v1/learning/pronunciation-check", async (route) => {
      checkCalls += 1;
      console.log(`[giveup-test] pronunciation-check call #${checkCalls}`);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ transcript: "banana orange purple monkey", score: 0.1, passed: false, heard_speech: true }),
      });
    });

    await completeFirstChoiceItem(page, items);
    await expect(page.getByText("轮到你了！大声读出来")).toBeVisible({ timeout: 20000 });

    // 5 mocked failures (each a ~10 s recording plus a TTS replay that can
    // hang up to the 30 s playback timeout in headless) → warm giveup
    // message, then auto-advance with no clicks.
    await expect(page.getByText("这句有点难")).toBeVisible({ timeout: 420000 });
    await expect(page.getByText("轮到你了！大声读出来")).toBeHidden({ timeout: 30000 });
    expect(checkCalls).toBe(5);
  });
});
