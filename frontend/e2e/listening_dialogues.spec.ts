import { expect, test } from "@playwright/test";

// Listening dialogues （日常对话） regression: 10 AB-dialogue entries exist
// alongside the 10 stories, payloads carry alternating A/B speakers, and the
// per-role audio URLs are distinct (A/B use different TTS voices) and already
// warmed (HTTP 200). READ-ONLY: no heartbeats/events on this page.
//
// Run: MIGRATION_TOKEN=<token> PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test e2e/listening_dialogues.spec.ts --project=chromium --workers=1

const BASE = "https://xuehello.duckdns.org";
const TOKEN = process.env.MIGRATION_TOKEN ?? "";
const USER = {
  id: "4c9633a1-a599-406e-af96-66a816681202",
  email: "cmx@a.com",
  username: "轩轩",
};

test.use({ baseURL: BASE });

test("dialogue mode: list badges + A/B speakers + dual-voice audio URLs", async ({ page, request }) => {
  test.skip(!TOKEN, "no token provided");
  await page.goto("/login");
  await page.evaluate(
    ({ token, user }) => {
      window.localStorage.setItem("memoseed_access_token", token);
      window.localStorage.setItem("memoseed_user", JSON.stringify(user));
    },
    { token: TOKEN, user: USER },
  );

  await page.goto("/listening");
  await page.waitForTimeout(2500);

  // ≥10 篇对话（💬）+ ≥10 篇故事（📖）
  const dialogueCount = await page.locator("button", { hasText: "💬" }).count();
  const storyCount = await page.locator("button", { hasText: "📖" }).count();
  expect(dialogueCount).toBeGreaterThanOrEqual(10);
  expect(storyCount).toBeGreaterThanOrEqual(10);

  // 抓取对话 payload（点击第一篇对话触发播放请求）
  const payloadPromise = page.waitForResponse(
    (r) => r.url().includes("/api/v1/listening/stories/") && r.request().method() === "GET",
  );
  await page.locator("button", { hasText: "💬" }).first().click();
  const response = await payloadPromise;
  const payload = await response.json();

  expect(payload.kind).toBe("dialogue");
  const speakers = payload.sentences.map((s: { speaker?: string }) => s.speaker);
  // A 先开口，严格轮流
  for (let i = 0; i < speakers.length; i += 1) {
    expect(speakers[i]).toBe(i % 2 === 0 ? "A" : "B");
  }
  // A/B 英文音频 URL 不同（不同音色 → 不同 cache key），且都 200 命中缓存
  const aEn = payload.sentences[0].en_audio_url;
  const bEn = payload.sentences[1].en_audio_url;
  const aZh = payload.sentences[0].zh_audio_url;
  const bZh = payload.sentences[1].zh_audio_url;
  expect(aEn).not.toBe(bEn);
  expect(aZh).not.toBe(bZh);
  for (const url of [aEn, bEn, aZh, bZh]) {
    const audioResp = await request.get(url);
    expect(audioResp.status(), `audio 200: ${url}`).toBe(200);
  }

  await page.waitForTimeout(1000);
  // A 徽章可见（播放器第一句是 A）
  await expect(page.locator("span", { hasText: /^A$/ }).first()).toBeVisible();
  await page.locator("button", { hasText: "停止" }).click().catch(() => {});
});
