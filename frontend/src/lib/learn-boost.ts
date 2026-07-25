import { countSharedLetters, normalizeEnglishKey } from "@/lib/study-utils";

// ---------------------------------------------------------------------------
// Data-driven learning boosters (2026-07-22).
// Everything below is mined from the child's real review_logs (30 days):
//  - 29.4% of word errors were fast wild guesses (<=4s, no shared letters),
//    including literal keyboard mashing ("agjjjjkjk...").
//  - Confusion pairs: the answer typed was ANOTHER learned word (are<->is,
//    me<->my, a<->an, quiet<->quite, ...). Some are form/sound similarity,
//    some are real GRAMMAR gaps (be-verbs, pronouns, articles).
//  - Phonics gaps: phone->"fone" (ph=/f/), write->"rate" (silent w),
//    feel->"fell" (ee long vowel), kind->"cand" (k vs c).
// ---------------------------------------------------------------------------

/** True when the typed text contains non-ASCII (e.g. Chinese characters —
 *  the child typed the MEANING into the English spelling field). */
export function hasNonLatinLetters(value: string): boolean {
  return /[^\x00-\x7F]/.test(value);
}

/** Keyboard-mash heuristic: long runs of one key, or long vowel-less strings
 *  (typical home-row mashing like "jkjjjlkj"). */
export function isKeyboardMash(value: string): boolean {
  const letters = normalizeEnglishKey(value).replace(/[^a-z]/g, "");
  if (letters.length >= 6 && /(.)\1{2}/.test(letters)) {
    return true;
  }
  if (letters.length >= 7 && !/[aeiou]/.test(letters)) {
    return true;
  }
  return false;
}

/** A "wild guess" = an answer so far from the target that judging it as a
 *  normal spelling error teaches nothing. These get the anchored "先想一想"
 *  treatment (meaning + first letter + chunks + audio) instead. */
export function isLikelyWildGuess(expectedWord: string, typedWord: string): boolean {
  const typed = normalizeEnglishKey(typedWord);
  const expected = normalizeEnglishKey(expectedWord);
  if (!typed || !expected) {
    return false;
  }
  if (hasNonLatinLetters(typedWord)) {
    return true;
  }
  if (isKeyboardMash(typed)) {
    return true;
  }
  if (typed.length <= 1 && expected.length >= 3) {
    return true;
  }
  if (expected.length >= 4 && typed.length <= expected.length - 3) {
    return true;
  }
  if (typed.length >= 2) {
    const sharedRatio = countSharedLetters(expected, typed) / expected.length;
    if (sharedRatio < 0.3 && Math.abs(typed.length - expected.length) >= 2) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Confusable pairs — expected word -> typed word -> kid-friendly contrast tip.
// Built from pairs observed >= 10 times in 30 days of real answers.
// ---------------------------------------------------------------------------

interface ConfusablePair {
  a: string;
  b: string;
  tip: string;
}

const CONFUSABLE_PAIRS: ConfusablePair[] = [
  // --- grammar pairs (be-verbs / pronouns / articles) ---
  { a: "are", b: "is", tip: "is 跟着「一个人」（he/she/it），are 跟着 you 或「多个人」——它们都是「是」！" },
  { a: "be", b: "is", tip: "be 是「是」的原形；is 跟着「一个人」（he/she/it is）。" },
  { a: "a", b: "an", tip: "a 用在辅音开头的词前（a pen）；an 用在元音开头的词前（an apple）。" },
  { a: "a", b: "the", tip: "a 是「一个」；the 是「这个/那个」。a 只有一个字母，the 是 t-h-e。" },
  { a: "i", b: "me", tip: "I 是「我」，放在句子开头做事（I like）；me 也是「我」，放在动作后面（give me）。" },
  { a: "me", b: "my", tip: "me 是「我」（help me）；my 是「我的」（my pen）。" },
  { a: "her", b: "she", tip: "she 是「她」，她做事用 she（she is）；her 是「她的」（her book）。" },
  { a: "he", b: "his", tip: "he 是「他」（h-e）；his 是「他的」（h-i-s）。" },
  { a: "you", b: "your", tip: "you 是「你」（y-o-u）；your 是「你的」（y-o-u-r）。" },
  { a: "are", b: "our", tip: "are 是「是」（a-r-e）；our 是「我们的」（o-u-r）。先想意思再动手！" },
  { a: "is", b: "us", tip: "is 是「是」（i-s）；us 是「我们」（u-s）。首字母听清楚！" },
  // --- form / sound pairs ---
  { a: "the", b: "this", tip: "the 是最常用的小词（t-h-e）；this 是「这个」，里面有 is。" },
  { a: "at", b: "the", tip: "at 是「在」（a-t）；the 是「这个/那个」（t-h-e）。" },
  { a: "us", b: "use", tip: "us 是「我们」（u-s）；use 是「使用」（u-s-e，多个 e）。" },
  { a: "quiet", b: "quite", tip: "quiet 是「安静的」（qu-i-et）；quite 是「很、非常」（qu-i-te）。et 和 te 别搞反！" },
  { a: "nice", b: "night", tip: "nice 是「好的」（n-i-c-e）；night 是「夜晚」（n-i-gh-t，gh 不发音）。" },
  { a: "home", b: "house", tip: "home 是「家」（温暖的家）；house 是「房子」（那个建筑）。" },
  { a: "live", b: "love", tip: "live 是「居住」（l-i-v-e）；love 是「爱」（l-o-v-e）。中间字母不一样！" },
  { a: "live", b: "low", tip: "live 是「居住」（l-i-v-e）；low 是「低的」（l-o-w）。" },
  { a: "want", b: "went", tip: "want 是「想要」（w-a-n-t）；went 是「去了」（w-e-n-t）。" },
  { a: "want", b: "what", tip: "want 是「想要」（w-a-n-t）；what 是「什么」（w-h-a-t，有 h）。" },
  { a: "an", b: "and", tip: "an 是「一个」（a-n）；and 是「和」（a-n-d，多个 d）。" },
  { a: "feel", b: "fell", tip: "feel 是「感觉」（f-ee-l，ee 发长音「衣」）；fell 是「摔倒了」。" },
  { a: "weak", b: "wake", tip: "weak 是「弱的」（w-ea-k）；wake 是「醒来」（w-a-k-e）。" },
  { a: "get", b: "give", tip: "get 是「得到」（g-e-t）；give 是「给」（g-i-v-e）。" },
  { a: "books", b: "book", tip: "book 是「一本书」；books 是「好多书」，词尾有 s。" },
  { a: "big", b: "beg", tip: "big 是「大的」（b-i-g）；beg 是「请求」（b-e-g）。i 和 e 听清楚！" },
  { a: "leave", b: "live", tip: "leave 是「离开」（l-ea-ve）；live 是「住」（l-i-v-e）。" },
  { a: "talk", b: "take", tip: "talk 是「说话」（t-a-l-k）；take 是「拿走」（t-a-k-e）。" },
  { a: "here", b: "her", tip: "here 是「这里」（h-e-r-e）；her 是「她的」（h-e-r）。" },
  { a: "look", b: "like", tip: "look 是「看」（l-oo-k）；like 是「喜欢」（l-i-k-e）。" },
  { a: "same", b: "some", tip: "same 是「相同的」（s-a-m-e）；some 是「一些」（s-o-m-e）。" },
  { a: "day", b: "do", tip: "day 是「日子」（d-a-y）；do 是「做」（d-o）。" },
  { a: "put", b: "pen", tip: "put 是「放」（p-u-t）；pen 是「钢笔」（p-e-n）。" },
  { a: "bird", b: "board", tip: "bird 是「鸟」（b-ir-d）；board 是「板子」（b-oa-rd）。" },
];

const CONFUSABLE_TIP_MAP: Record<string, Record<string, string>> = {};
for (const { a, b, tip } of CONFUSABLE_PAIRS) {
  (CONFUSABLE_TIP_MAP[a] ??= {})[b] = tip;
  (CONFUSABLE_TIP_MAP[b] ??= {})[a] = tip;
}

/** When the child typed ANOTHER learned word instead of the target, return a
 *  contrast tip explaining the difference (grammar or form). Null otherwise. */
export function lookupConfusableTip(expectedWord: string, typedWord: string): string | null {
  const expected = normalizeEnglishKey(expectedWord);
  const typed = normalizeEnglishKey(typedWord);
  if (!expected || !typed || expected === typed) {
    return null;
  }
  return CONFUSABLE_TIP_MAP[expected]?.[typed] ?? null;
}

// ---------------------------------------------------------------------------
// Phonics tips — per-word spelling rules for the child's worst words.
// ---------------------------------------------------------------------------

const WORD_PHONICS_TIPS: Record<string, string> = {
  write: "wr 里的 w 不发音！读作 r：wr-i-te。",
  phone: "ph 发 f 的音：ph-o-n-e。不是 f 开头哦！",
  feel: "ee 发长音「衣」：f-ee-l。",
  weak: "ea 发长音「衣」：w-ea-k。",
  please: "ea 发长音「衣」：p-l-ea-se。",
  leave: "ea 发长音「衣」：l-ea-ve。",
  kind: "是 k 不是 c！k-i-n-d，i 发「爱」。",
  night: "igh 发「爱」，gh 不发音：n-igh-t。",
  early: "ear 发「厄」：ear-ly。e-a-r-l-y 五个字母慢慢来。",
  often: "分两段：of-ten。o-f-t-e-n。",
  beautiful: "分三段：beau-ti-ful。beau 是特殊拼法，要记住。",
  woman: "分两段：wo-man。w-o-m-a-n。",
  talk: "al 发「奥」：t-a-l-k。",
  together: "分三段：to-ge-ther。",
  outside: "out + side 两个词拼起来：out-side。",
  clearly: "clear 加 ly：clear-ly。",
  flower: "flow 加 er：flow-er。",
  class: "词尾是两个 s！c-l-a-s-s。",
  careful: "care 加 ful（一个 l）：care-ful。",
  the: "t-h-e，最常用的词，看着字母慢慢打。",
  then: "th 要咬舌头：th-e-n。",
  there: "th-ere，比 here 多一个 t。",
  here: "h-e-r-e，比 her 多一个 e。",
  show: "sh-ow：s-h-o-w。",
  also: "分两段：al-so。",
  true: "tr-ue：t-r-u-e。",
  large: "ge 发「知」：l-ar-ge。",
  age: "a-g-e，先 a、再 g、再 e。",
  books: "book 后面加 s：b-o-o-k-s。",
  bird: "ir 发「厄」：b-ir-d。",
  old: "o-l-d，中间是 l。",
  tomorrow: "分三段：to-mor-row，中间两个 r。",
  good: "oo 发「乌」：g-oo-d。",
  look: "oo 发「乌」：l-oo-k。",
  see: "ee 发长音「衣」：s-ee。",
  day: "ay 发「诶」：d-ay。",
  come: "c-o-m-e，o 发「阿」。",
  home: "h-o-m-e，结尾的 e 不发音。",
  name: "n-a-m-e，结尾的 e 不发音。",
  take: "t-a-k-e，结尾的 e 不发音。",
  make: "m-a-k-e，结尾的 e 不发音。",
  live: "l-i-v-e。",
  give: "g-i-v-e。",
  have: "h-a-v-e。",
  same: "s-a-m-e。",
  loud: "ou 发「奥」：l-ou-d。",
  one: "读作「万」，但拼作 o-n-e。",
  again: "分两段：a-gain。",
  want: "w-a-n-t。",
  very: "分两段：ve-ry。",
  hard: "h-ar-d。",
  us: "u-s，先想意思「我们」再动手。",
  are: "a-r-e，是「是」，先想意思再动手。",
  our: "o-u-r，是「我们的」。",
};

/** Phonics/spelling rule tip for a word, or null. */
export function getPhonicsTip(word: string): string | null {
  return WORD_PHONICS_TIPS[normalizeEnglishKey(word)] ?? null;
}

// ---------------------------------------------------------------------------
// Echo (read-aloud) daily counter — localStorage, per calendar day.
// ---------------------------------------------------------------------------

const ECHO_COUNT_KEY_PREFIX = "memoseed_echo_count_";
export const ECHO_DAILY_POINTS_CAP = 20;
export const ECHO_POINTS_PER_READ = 2;

function echoTodayKey(): string {
  const now = new Date();
  return `${ECHO_COUNT_KEY_PREFIX}${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

export function getEchoCountToday(): number {
  try {
    return Number(window.localStorage.getItem(echoTodayKey()) ?? "0") || 0;
  } catch {
    return 0;
  }
}

export function incrementEchoCountToday(): number {
  const next = getEchoCountToday() + 1;
  try {
    window.localStorage.setItem(echoTodayKey(), String(next));
  } catch {
    // private mode — the counter is cosmetic, ignore
  }
  return next;
}

/** Points budget left today (each echo = ECHO_POINTS_PER_READ points). */
export function getEchoPointsLeftToday(): number {
  return Math.max(0, ECHO_DAILY_POINTS_CAP - getEchoCountToday() * ECHO_POINTS_PER_READ);
}
