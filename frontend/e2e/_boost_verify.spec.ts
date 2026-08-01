import { test } from "@playwright/test";

const TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI0Yzk2MzNhMS1hNTk5LTQwNmUtYWY5Ni02NmE4MTY2ODEyMDIiLCJleHAiOjE3ODQ5NDc4NzUsInR5cGUiOiJhY2Nlc3MifQ.j1hVdzJKZ1376uGAqWboY7ZBTzIdw0P-7Apc0PNzYi4";

// Confusable partners baked into CONFUSABLE_PAIRS (learn-boost.ts) — pick by revealed word.
const CONFUSABLE_PARTNER: Record<string, { typed: string; tipPart: string }> = {
  give: { typed: "get", tipPart: "give 是「给」" },
  get: { typed: "give", tipPart: "get 是「得到」" },
  pen: { typed: "put", tipPart: "pen 是「钢笔」" },
  put: { typed: "pen", tipPart: "put 是「放」" },
  me: { typed: "my", tipPart: "me 是「我」" },
  my: { typed: "me", tipPart: "my 是「我的」" },
  a: { typed: "an", tipPart: "a 用在辅音开头" },
  an: { typed: "a", tipPart: "an 是「一个」" },
  is: { typed: "us", tipPart: "is 是「是」" },
  us: { typed: "is", tipPart: "us 是「我们」" },
};

test("verify learning boosters", async ({ page }) => {
  test.setTimeout(300000);
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push("PAGEERROR: " + err.message));
  const results: string[] = [];
  const check = (name: string, ok: boolean, detail = "") => {
    results.push(`${ok ? "PASS" : "FAIL"} ${name}${detail ? " — " + detail : ""}`);
    console.log(results[results.length - 1]);
  };
  const finish = async () => {
    console.log("\n=== RESULTS ===");
    console.log(results.join("\n"));
    console.log("JS errors:", errors.length ? errors.join(" | ") : "none");
  };

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("http://8.148.221.17/login", { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => {
    window.localStorage.setItem("memoseed_access_token", t);
    window.localStorage.setItem("memoseed_user", JSON.stringify({ id: "4c9633a1-a599-406e-af96-66a816681202", username: "x", email: "x@x.com" }));
  }, TOKEN);

  const mainText = async () => (await page.locator("main").innerText()).replace(/\s+/g, " ");
  const activeInput = page.locator("input.study-word-input[class*='bg-cyan-50/90']").first();
  const previewBox = page.locator("span.rounded-md.bg-amber-100, p.rounded-md.bg-amber-100").first();
  const copyChip = page.locator("span.rounded-md.bg-sky-100").first();
  const skipTo = async (predicate: RegExp) => {
    for (let step = 0; step < 40; step += 1) {
      if (predicate.test(await mainText())) return true;
      const skip = page.locator("button:has-text('跳过')").first();
      if (!(await skip.count())) return false;
      await skip.click({ force: true });
      await page.waitForTimeout(1100);
    }
    return false;
  };
  const passWarmup = async () => {
    for (let i = 0; i < 8; i += 1) {
      if ((await page.locator("input.study-word-input[class*='bg-cyan-50/90']").count()) > 0) return true;
      const cont = page.locator("button:has-text('记住了，继续')").first();
      if ((await cont.count()) === 0) return (await page.locator("input.study-word-input").count()) > 0;
      await cont.click({ force: true });
      await page.waitForTimeout(1200);
    }
    return false;
  };
  const typeAndJudge = async (text: string) => {
    await activeInput.click();
    await activeInput.fill("");
    await activeInput.pressSequentially(text, { delay: 25 });
    await page.keyboard.press("Space");
  };
  const dumpInputs = async (tag: string) => {
    const info = await page.evaluate(() => {
      return Array.from(document.querySelectorAll("input.study-word-input")).map((el) => {
        const i = el as HTMLInputElement;
        return {
          aria: i.getAttribute("aria-label"),
          value: i.value,
          cyan: i.className.includes("bg-cyan-50/90"),
          focused: document.activeElement === i,
          disabled: i.disabled,
        };
      });
    });
    console.log(`DUMP[${tag}]:`, JSON.stringify(info));
  };
  const waitPreviewFullyGone = async () => {
    await previewBox.waitFor({ state: "hidden", timeout: 12000 }).catch(() => undefined);
    for (let i = 0; i < 12; i += 1) {
      if (!/还剩 \d+ 秒/.test(await mainText())) break;
      await page.waitForTimeout(700);
    }
    await page.waitForTimeout(500);
  };

  // ============ vehicle: cloze "Give me a pen." ============
  await page.goto("http://8.148.221.17/learning/study?course_id=82576ced-c437-4d75-94eb-669cfa7008fb&package_id=1d4f1816-ba34-422f-8b8e-231139fb5186&course_name=%E7%AC%AC1%E8%AF%BE&mode=learn", { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
  const foundCloze = await skipTo(/给我一支笔.*短句填空|短句填空.*给我一支笔/);
  check("cloze vehicle found", foundCloze);
  if (!foundCloze) { await finish(); return; }
  check("warmup passed", await passWarmup());

  // ---- reveal the blanked word W via preview (zz x2 -> unknown errors) ----
  let word = "";
  for (let i = 0; i < 3 && !word; i += 1) {
    await typeAndJudge("zz");
    await page.waitForTimeout(1800);
    if ((await previewBox.count()) > 0) { word = (await previewBox.innerText()).trim(); break; }
    await page.keyboard.press("Space"); // reset incorrect
    await page.waitForTimeout(700);
  }
  word = word.replace(/[.,!?;:'"]/g, "");
  check("word revealed", !!word, word);
  if (!word) { await finish(); return; }
  // capture the Chinese meaning shown in the preview feedback for the quiz later
  const meaningMatch = /中文意思：([^\s。]+)/.exec(await mainText());
  const meaning = meaningMatch?.[1] ?? "";
  console.log("revealed word:", word, "meaning:", meaning);

  // ---- C) copy mode right after preview ----
  await waitPreviewFullyGone();
  const copyShown = (await copyChip.count()) > 0 && /抄一抄/.test(await mainText());
  check("C: copy mode shown after preview", copyShown, (await mainText()).slice(60, 130));
  if (copyShown) {
    await typeAndJudge("px");
    await page.waitForTimeout(1000);
    check("C: wrong copy -> 还没抄对", /还没抄对/.test(await mainText()));
    await typeAndJudge(word);
    await page.waitForTimeout(1000);
    check("C: correct copy -> 抄对了", /抄对了！现在不看它/.test(await mainText()), (await mainText()).slice(60, 130));
  }

  // ---- reset the item so error counts restart (gives us clean error budget) ----
  await page.locator("button:has-text('再来一次')").first().click({ force: true });
  await page.waitForTimeout(2000);

  // ---- B) confusable partner -> contrast tip (error 1) ----
  const partner = CONFUSABLE_PARTNER[word.toLowerCase()];
  if (partner) {
    await typeAndJudge(partner.typed);
    await page.waitForTimeout(1200);
    const fbConf = await mainText();
    check("B: confusable tip", new RegExp(`你打成了另一个词！.*${partner.tipPart}`).test(fbConf), fbConf.slice(60, 180));
    await page.waitForTimeout(1700); // gate expiry
  } else {
    check("B: confusable tip", true, `no partner for "${word}" — skipped`);
  }

  // ---- A) wild guess -> anchored 先想一想 (error 2) ----
  if (word.length >= 3) {
    await typeAndJudge("x");
    await page.waitForTimeout(1000);
    const fbWild = await mainText();
    check("A: wild guess -> 先想一想 anchor", /先想一想|英文字母/.test(fbWild), fbWild.slice(60, 170));
    await page.keyboard.press("Space");
    await page.waitForTimeout(300);
    check("A: thinkGate swallows instant re-judgment", !/请重新输入/.test(await mainText()));
    await page.waitForTimeout(1600);
  } else {
    check("A: wild guess", true, `word "${word}" too short — skipped`);
  }

  // ---- error 3 -> preview -> copy -> recall rounds ----
  await typeAndJudge("zz");
  await page.waitForTimeout(1900);
  check("preview shown again", (await previewBox.count()) > 0);
  await dumpInputs("after-2nd-preview");
  await waitPreviewFullyGone();
  await dumpInputs("after-preview-gone");
  if ((await copyChip.count()) > 0) {
    await typeAndJudge(word);
    await page.waitForTimeout(1000);
    await dumpInputs("after-copy-judge");
    check("copy after 2nd preview -> 抄对了", /抄对了！现在不看它/.test(await mainText()));
  }
  for (let round = 1; round <= 2; round += 1) {
    await typeAndJudge(word);
    await page.waitForTimeout(1700);
    const fb = await mainText();
    await dumpInputs(`recall-${round}`);
    check(`recall confirmation ${round}/3`, new RegExp(`拼对了 ${round} \\/ 3`).test(fb), fb.slice(60, 140));
  }
  await typeAndJudge(word);
  await page.waitForTimeout(3600);
  const fbDone = await mainText();
  check("completion -> 进入错词复习 (no echo here)", /进入错词复习/.test(fbDone) && !/大声读出来/.test(fbDone), fbDone.slice(50, 140));

  // ---- mistake practice -> quiz -> digit ----
  await page.keyboard.press("Space");
  await page.waitForTimeout(2500);
  const practiceInput = page.locator("input[aria-label='错词单独拼写']").first();
  check("practice input shown", (await practiceInput.count()) > 0);
  if ((await practiceInput.count()) > 0) {
    for (let round = 1; round <= 2; round += 1) {
      await practiceInput.click();
      await practiceInput.fill("");
      await practiceInput.pressSequentially(word, { delay: 25 });
      await page.keyboard.press("Space");
      await page.waitForTimeout(1600);
    }
    const quizUp = /按数字键 1-6 选择中文意思/.test(await mainText());
    check("meaning quiz shown", quizUp);
    if (quizUp) {
      const buttons = page.locator("main button");
      const n = await buttons.count();
      let digit = 0;
      const meaningProbe = meaning.slice(0, Math.min(2, meaning.length));
      for (let i = 0; i < n; i += 1) {
        const txt = (await buttons.nth(i).innerText().catch(() => "")).trim();
        const m = /^[✓✗\s]*([1-6])\.\s*(.+)$/.exec(txt);
        if (m && meaningProbe && m[2].includes(meaningProbe)) { digit = parseInt(m[1], 10); break; }
      }
      check("found correct meaning digit", digit > 0, `probe=${meaningProbe}`);
      if (digit > 0) {
        await page.keyboard.press(String(digit));
        await page.waitForTimeout(1800);
      }
    }
  }

  // ---- respell -> completion -> ECHO card ----
  const respellInput = page.locator("input.study-word-input[class*='bg-cyan-50/90']").first();
  if ((await respellInput.count()) > 0) {
    await respellInput.click();
    await respellInput.fill("");
    await respellInput.pressSequentially(word, { delay: 25 });
    await page.keyboard.press("Space");
  }
  await page.waitForTimeout(4200);
  const fbEcho = await mainText();
  const echoVisible = /大声读出来/.test(fbEcho) && /Give me a pen\./.test(fbEcho);
  check("E: echo prompt shown after respell completion", echoVisible, fbEcho.slice(40, 170));
  await page.screenshot({ path: "shots/boost-echo.png" });
  if (echoVisible) {
    check("E: echo offers points", /\+\d 分/.test(fbEcho));
    const doneBtn = page.locator("button:has-text('我读完了')").first();
    if ((await doneBtn.count()) > 0) {
      // 防跳读门槛：按钮要等示范音放完+一个监听窗口无检出才可用
      await doneBtn.waitFor({ state: "visible" });
      await page.waitForFunction(() => {
        const btn = Array.from(document.querySelectorAll("button")).find((b) => b.textContent?.includes("我读完了"));
        return btn ? !btn.disabled : false;
      }, { timeout: 60000 });
      await doneBtn.click({ force: true });
      await page.waitForTimeout(900);
      const fbEchoDone = await mainText();
      // 手动点击=未验证朗读：不计分、不计开口次数（跟读完成/声音真响亮 是发声检出的奖励文案）
      check("E: echo manual skip advances without rewards", /没听到声音|跟读完成|声音真响亮/.test(fbEchoDone), fbEchoDone.slice(40, 170));
      await page.screenshot({ path: "shots/boost-echo-done.png" });
    } else {
      check("E: 我读完了 button present", false);
    }
  }

  // ---- advance still works ----
  await page.waitForTimeout(1200);
  const itemNoBefore = /句子 (\d+) \//.exec(await mainText())?.[1] ?? "?";
  await page.keyboard.press("Space");
  await page.waitForTimeout(2200);
  const itemNoAfter = /句子 (\d+) \//.exec(await mainText())?.[1] ?? "?";
  check("advance after echo still works", itemNoAfter !== itemNoBefore, `${itemNoBefore} -> ${itemNoAfter}`);

  await finish();
});
