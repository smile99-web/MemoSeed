import { expect, test, type Page, type Route } from "@playwright/test";

const courseId = "course-e2e";
const packageId = "package-e2e";
const now = "2026-06-05T08:00:00.000Z";

const meanings: Record<string, string> = {
  bird: "\u5c0f\u9e1f",
  book: "\u4e66",
  school: "\u5b66\u6821",
  student: "\u5b66\u751f",
  teacher: "\u8001\u5e08",
  apple: "\u82f9\u679c",
  friend: "\u670b\u53cb",
};

function makeItem(overrides: Partial<LearningItemFixture> = {}): LearningItemFixture {
  return {
    id: "item-1",
    user_id: "user-e2e",
    item_type: "sentence",
    english_text: "I am a student.",
    chinese_text: "\u6211\u662f\u4e00\u540d\u5b66\u751f\u3002",
    phonetic: null,
    syllables: null,
    grapheme_phoneme_map: null,
    difficulty_level: 1,
    sort_order: 1,
    unit_label: null,
    source: null,
    course_id: courseId,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

test.beforeEach(async ({ page }) => {
  await installBrowserFakes(page);
});

test("listen spell review allows typing and submitting the heard word", async ({ page }) => {
  await mockStudyApis(page, {
    reviewItems: [
      makeItem({
        id: "review-listen-spell",
        item_type: "word",
        english_text: "bird",
        chinese_text: "\u542c\u82f1\u6587\u53d1\u97f3\u540e\u62fc\u5199",
        source_item_id: "source-bird",
        review_task_id: "task-listen-spell",
        review_task_type: "listen_spell",
        review_answer: "bird",
        focus_words: ["bird"],
        course_id: null,
      }),
    ],
  });

  await openStudyPage(page);

  const wordInput = page.locator("input").first();
  await expect(wordInput).toBeEditable();

  await wordInput.fill("bird");
  await expect(wordInput).toHaveValue("bird");

  await wordInput.press("Space");
  await expect(wordInput).toHaveValue("bird");
});

test("choice review only accepts the actual Chinese meaning", async ({ page }) => {
  await mockStudyApis(page, {
    reviewItems: [
      makeItem({
        id: "review-english-to-chinese",
        item_type: "word",
        english_text: "bird",
        chinese_text: meanings.bird,
        source_item_id: "source-bird",
        review_task_id: "task-meaning",
        review_task_type: "english_to_chinese",
        review_choices: [meanings.book, meanings.bird, meanings.school, meanings.student, meanings.teacher, meanings.apple],
        review_answer: meanings.bird,
        focus_words: ["bird"],
        course_id: null,
      }),
    ],
  });

  await openStudyPage(page);

  const wrongChoice = page.getByRole("button", { name: new RegExp(meanings.book) });
  const correctChoice = page.getByRole("button", { name: new RegExp(meanings.bird) });

  await wrongChoice.click();
  await page.keyboard.press("Space");
  await expect(correctChoice).toBeEnabled({ timeout: 2_000 });

  await correctChoice.click();
  await page.keyboard.press("Space");
  await expect(correctChoice).toBeDisabled();
});

async function openStudyPage(page: Page): Promise<void> {
  await page.goto(`/learning/study?course_id=${courseId}&package_id=${packageId}&course_name=%E7%AC%AC1%E8%AF%BE`);
  await expect(page.locator("main")).toBeVisible();
}

async function installBrowserFakes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("memoseed_access_token", "access-e2e");
    window.localStorage.setItem("memoseed_refresh_token", "refresh-e2e");
    window.localStorage.setItem(
      "memoseed_user",
      JSON.stringify({
        id: "user-e2e",
        email: "e2e@example.com",
        username: "e2e",
        is_active: true,
        created_at: "2026-06-05T08:00:00.000Z",
      }),
    );

    class FakeSpeechSynthesisUtterance {
      text: string;
      lang = "";
      rate = 1;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(text: string) {
        this.text = text;
      }
    }

    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      configurable: true,
      value: FakeSpeechSynthesisUtterance,
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        cancel() {},
        speak(utterance: FakeSpeechSynthesisUtterance) {
          window.setTimeout(() => utterance.onend?.(), 0);
        },
      },
    });

    class FakeAudioContext {
      state = "running";
      destination = {};
      resume() {
        return Promise.resolve();
      }
      decodeAudioData() {
        return Promise.resolve({ duration: 0.01 });
      }
      createBufferSource() {
        const source = {
          buffer: null,
          onended: null as (() => void) | null,
          playbackRate: { value: 1 },
          connect() {},
          disconnect() {},
          start() {
            window.setTimeout(() => source.onended?.(), 0);
          },
          stop() {},
        };
        return source;
      }
    }

    Object.defineProperty(window, "AudioContext", { configurable: true, value: FakeAudioContext });
  });
}

async function mockStudyApis(
  page: Page,
  options: {
    reviewItems?: LearningItemFixture[];
    courseItems?: LearningItemFixture[];
  },
): Promise<void> {
  const courseItems = options.courseItems ?? [makeItem()];
  const reviewItems = options.reviewItems ?? [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "GET" && path.endsWith("/settings/model")) {
      await fulfillJson(route, {
        settings: {
          modelMode: "local",
          llmProvider: "ollama",
          llmBaseUrl: "http://localhost:11434",
          llmModel: "test",
          ttsProvider: "kokoro",
          ttsApiUrl: "http://localhost:8880",
          ttsEnglishVoice: "af_heart",
          ttsChineseVoice: "zf_xiaobei",
        },
      });
      return;
    }

    if (request.method() === "GET" && path.endsWith("/learning/review-items")) {
      await fulfillJson(route, reviewItems);
      return;
    }

    if (request.method() === "GET" && path.endsWith("/learning/items")) {
      await fulfillJson(route, courseItems);
      return;
    }

    if (request.method() === "GET" && path.endsWith("/courses/courses")) {
      await fulfillJson(route, [{ id: courseId, name: "\u7b2c1\u8bfe", package_id: packageId, prerequisite_course_id: null }]);
      return;
    }

    if (request.method() === "POST" && path.endsWith("/learning/word-translations")) {
      const body = request.postDataJSON() as { words?: string[] };
      await fulfillJson(route, {
        translations: Object.fromEntries((body.words ?? []).map((word) => [word, meanings[word.toLowerCase()] ?? word])),
      });
      return;
    }

    if (request.method() === "POST" && path.endsWith("/learning/translations")) {
      const body = request.postDataJSON() as { english_text?: string };
      await fulfillJson(route, {
        english_text: body.english_text ?? "",
        chinese_text: meanings[(body.english_text ?? "").toLowerCase()] ?? "\u6d4b\u8bd5\u4e2d\u6587",
      });
      return;
    }

    if (request.method() === "POST" && path.endsWith("/learning/dynamic-sentences")) {
      await fulfillJson(route, {
        english_text: "A bird can fly.",
        chinese_text: "\u4e00\u53ea\u5c0f\u9e1f\u4f1a\u98de\u3002",
        focus_words: ["bird"],
        known_words: ["a", "can"],
        weak_words: ["bird"],
      });
      return;
    }

    if (request.method() === "POST" && path.endsWith("/learning/encouragements")) {
      await fulfillJson(route, {
        chinese_text: "\u4eca\u5929\u4f60\u5f88\u4e13\u6ce8\uff01",
        english_text: "You stayed focused today!",
      });
      return;
    }

    if (request.method() === "POST" && path.includes("/tts/")) {
      await route.fulfill({ body: Buffer.from([1, 2, 3, 4]), contentType: "audio/mpeg", status: 200 });
      return;
    }

    if (request.method() === "POST" || request.method() === "PUT" || request.method() === "PATCH") {
      await fulfillJson(route, {});
      return;
    }

    await fulfillJson(route, {});
  });
}

async function fulfillJson(route: Route, json: unknown): Promise<void> {
  await route.fulfill({
    contentType: "application/json",
    json,
    status: 200,
  });
}

interface LearningItemFixture {
  id: string;
  user_id: string;
  item_type: "word" | "phrase" | "sentence";
  english_text: string;
  chinese_text: string;
  phonetic: string | null;
  syllables: string[] | null;
  grapheme_phoneme_map: Record<string, string> | null;
  difficulty_level: number;
  sort_order: number;
  unit_label: string | null;
  source: string | null;
  source_item_id?: string;
  review_task_id?: string;
  review_task_type?: string;
  review_prompt?: string | null;
  review_choices?: string[];
  review_answer?: string | null;
  focus_words?: string[];
  course_id: string | null;
  created_at: string;
  updated_at: string;
}
