import type { LearningItem } from "@/lib/learning";

export function tokenizeEnglish(value: string): string[] {
  return value.trim().split(/\s+/).filter(Boolean);
}

export function normalizeTypedWord(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/^[^a-z0-9']+|[^a-z0-9']+$/g, "");
}

export function normalizeEnglishKey(value: string): string {
  return value.trim().toLowerCase().replace(/^[^a-z0-9']+|[^a-z0-9']+$/g, "");
}

export function cleanEnglishSpeechWord(value: string): string {
  return value.trim().replace(/^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$/g, "");
}

export function buildChunkedEnglishSpeechText(value: string): string {
  const words = tokenizeEnglish(value).map(cleanEnglishSpeechWord).filter(Boolean);
  if (words.length <= 3) {
    return words.join(" ");
  }

  const chunks: string[] = [];
  for (let index = 0; index < words.length; index += 3) {
    chunks.push(words.slice(index, index + 3).join(" "));
  }
  return chunks.join(". ");
}

export function buildWordByWordEnglishSpeechText(value: string): string {
  return tokenizeEnglish(value).map(cleanEnglishSpeechWord).filter(Boolean).join(". ");
}

export function buildFocusedWordSpeechText(word: string): string {
  const cleanWord = cleanEnglishSpeechWord(word);
  if (!cleanWord) {
    return "";
  }
  return `${cleanWord}. ${cleanWord}.`;
}

export function getFirstLetter(value: string): string {
  return normalizeEnglishKey(value).charAt(0);
}

export function getMatchedPrefixLength(expectedWord: string, typedWord: string): number {
  const normalizedExpected = normalizeEnglishKey(expectedWord);
  const normalizedTyped = normalizeEnglishKey(typedWord);
  let matchedPrefixLength = 0;
  while (
    matchedPrefixLength < normalizedExpected.length
    && matchedPrefixLength < normalizedTyped.length
    && normalizedExpected[matchedPrefixLength] === normalizedTyped[matchedPrefixLength]
  ) {
    matchedPrefixLength += 1;
  }

  return matchedPrefixLength;
}

export function countSharedLetters(expectedWord: string, typedWord: string): number {
  const expectedLetters = new Map<string, number>();
  normalizeEnglishKey(expectedWord)
    .split("")
    .forEach((letter) => {
      expectedLetters.set(letter, (expectedLetters.get(letter) ?? 0) + 1);
    });
  return normalizeEnglishKey(typedWord)
    .split("")
    .reduce((count, letter) => {
      const availableCount = expectedLetters.get(letter) ?? 0;
      if (availableCount <= 0) {
        return count;
      }
      expectedLetters.set(letter, availableCount - 1);
      return count + 1;
    }, 0);
}

export function isAdjacentLetterSwap(expectedWord: string, typedWord: string): boolean {
  const normalizedExpected = normalizeEnglishKey(expectedWord);
  const normalizedTyped = normalizeEnglishKey(typedWord);
  if (normalizedExpected.length !== normalizedTyped.length || normalizedExpected.length < 2) {
    return false;
  }
  const differentIndexes = normalizedExpected
    .split("")
    .map((letter, index) => (letter === normalizedTyped[index] ? -1 : index))
    .filter((index) => index >= 0);
  return (
    differentIndexes.length === 2
    && differentIndexes[1] === differentIndexes[0] + 1
    && normalizedExpected[differentIndexes[0]] === normalizedTyped[differentIndexes[1]]
    && normalizedExpected[differentIndexes[1]] === normalizedTyped[differentIndexes[0]]
  );
}

export function classifySpellingError(expectedWord: string, typedWord: string): string {
  const normalizedExpected = normalizeEnglishKey(expectedWord);
  const normalizedTyped = normalizeEnglishKey(typedWord);
  const sharedLetterRatio = normalizedExpected.length === 0 ? 0 : countSharedLetters(normalizedExpected, normalizedTyped) / normalizedExpected.length;
  if (normalizedTyped.length >= 2 && sharedLetterRatio < 0.35) {
    return "unknown";
  }
  if (getFirstLetter(normalizedExpected) !== getFirstLetter(normalizedTyped)) {
    return "first-letter";
  }
  if (isAdjacentLetterSwap(normalizedExpected, normalizedTyped)) {
    return "sequence";
  }
  if (normalizedExpected.length !== normalizedTyped.length) {
    return normalizedTyped.length < normalizedExpected.length ? "missing-letter" : "extra-letter";
  }
  const matchedPrefixLength = getMatchedPrefixLength(normalizedExpected, normalizedTyped);
  if (matchedPrefixLength >= Math.max(1, normalizedExpected.length - 2)) {
    return "ending";
  }
  if (matchedPrefixLength <= Math.max(1, Math.floor(normalizedExpected.length / 2))) {
    return "middle";
  }
  return "sequence";
}

export function maskWordByIndexes(word: string, visibleIndexes: Set<number>): string {
  const normalizedWord = normalizeEnglishKey(word);
  return normalizedWord
    .split("")
    .map((letter, index) => (visibleIndexes.has(index) ? letter : "_"))
    .join(" ");
}

export function buildReviewTaskInstruction(taskType: string | undefined, word: string): string {
  const normalizedWord = normalizeEnglishKey(word);
  if (!taskType || !normalizedWord) {
    return "";
  }
  if (taskType === "listen_spell") {
    return "听英文发音后拼写。";
  }
  if (taskType === "listen_choose_chinese") {
    return "只听英文发音，选择中文意思。";
  }
  if (taskType === "missing_letter") {
    const visibleIndexes = new Set<number>([0, Math.max(normalizedWord.length - 1, 0)]);
    return `补全缺失字母：${maskWordByIndexes(normalizedWord, visibleIndexes)}`;
  }
  if (taskType === "hidden_recall") {
    return "看 5 秒后会隐藏，再凭记忆重拼。";
  }
  if (taskType === "cloze_sentence") {
    return "只填写错过的单词。";
  }
  if (taskType === "chinese_to_english") {
    return "根据中文意思拼写英文。";
  }
  return "";
}

export function getReviewTaskModeLabel(taskType: string | undefined): string {
  const labels: Record<string, string> = {
    chinese_to_english: "看中文拼英文",
    listen_spell: "听英文拼英文",
    listen_choose_chinese: "听英文选中文",
    english_to_chinese: "英文选中文",
    match_translation: "中英文配对",
    missing_letter: "缺字母填空",
    cloze_sentence: "短句填空",
    hidden_recall: "隐藏重拼",
  };
  return taskType ? labels[taskType] ?? "专项复习" : "";
}

export function rotateChoices(choices: string[], seed: string): string[] {
  const uniqueChoices = Array.from(new Set(choices.map((choice) => choice.trim()).filter(Boolean)));
  if (uniqueChoices.length <= 1) {
    return uniqueChoices;
  }
  const shift = seed.split("").reduce((total, letter) => total + letter.charCodeAt(0), 0) % uniqueChoices.length;
  return [...uniqueChoices.slice(shift), ...uniqueChoices.slice(0, shift)];
}

export function splitIntoSyllables(word: string, cachedSyllables?: string[] | null): string[] {
  if (cachedSyllables && cachedSyllables.length > 0) {
    return cachedSyllables;
  }

  const normalized = normalizeEnglishKey(word);
  const len = normalized.length;
  if (len <= 3) {
    return [normalized];
  }
  if (len <= 6) {
    const mid = Math.ceil(len / 2);
    return [normalized.slice(0, mid), normalized.slice(mid)];
  }
  const chunkSize = Math.ceil(len / 3);
  const first = normalized.slice(0, chunkSize);
  const third = normalized.slice(-chunkSize);
  const second = normalized.slice(chunkSize, len - chunkSize);
  if (!second) {
    return [first, third];
  }
  return [first, second, third];
}

// Static speakable-text approximation for common phonics phonemes.
// TTS reads these as short sound approximations for phonics instruction.
export const PHONEME_SPEAKABLE_MAP: Record<string, string> = {
  // Short vowels
  a: "ah", e: "eh", i: "ih", o: "aw", u: "uh",
  // Long vowels
  ā: "ay", ē: "ee", ī: "eye", ō: "oh", ū: "yoo",
  // Consonants — continuous sounds work well as letter repetition
  b: "buh", k: "kuh", d: "duh", f: "fff", g: "guh", h: "huh",
  j: "juh", l: "lll", m: "mmm", n: "nnn", p: "puh",
  r: "rrr", s: "sss", t: "tuh", v: "vvv", w: "wuh",
  y: "yuh", z: "zzz",
  // Digraphs
  sh: "shhh", ch: "chuh", th: "thhh", wh: "wh", ng: "ng",
  ck: "ck", ph: "fff",
  // R-controlled
  ar: "arr", er: "err", ir: "irr", or: "awr", ur: "urr",
  // Diphthongs
  oo: "oo", ow: "ow", oy: "oy", ou: "ow", aw: "aw", au: "aw",
  // Silent-e markers (long vowel markers)
  a_e: "ay", i_e: "eye", o_e: "oh", u_e: "yoo",
  // Additional
  kn: "nnn", wr: "rrr", mb: "mmm", gh: "",
  tion: "shun", sion: "zhun",
};

export function getPhonemeSpeakableText(phoneme: string): string {
  const cleaned = phoneme.trim().toLowerCase().replace(/[^a-zāēīōū_]/g, "");
  if (!cleaned) return "";
  return PHONEME_SPEAKABLE_MAP[cleaned] ?? cleaned;
}

// Split a syllable chunk into its constituent graphemes using the gpMap.
// Uses longest-match-first greedy matching against the chunk.
export function getGraphemesForChunk(chunk: string, gpMap: Record<string, string>): string[] {
  const graphemes = Object.keys(gpMap).sort((a, b) => b.length - a.length);
  const result: string[] = [];
  let remaining = chunk;
  while (remaining.length > 0) {
    let matched = false;
    for (const grapheme of graphemes) {
      if (remaining.startsWith(grapheme)) {
        result.push(grapheme);
        remaining = remaining.slice(grapheme.length);
        matched = true;
        break;
      }
    }
    if (!matched) {
      // Single-letter fallback
      result.push(remaining[0]);
      remaining = remaining.slice(1);
    }
  }
  return result;
}

// Find rhyming/family words from the current session's word list.
// Uses suffix-based matching (same rime pattern) for analogical transfer.
export function findWordFamily(word: string, wordBank: string[]): string[] {
  const clean = normalizeEnglishKey(word);
  const cleanBank = wordBank.map(normalizeEnglishKey).filter(Boolean);
  const wordSet = new Set(cleanBank);
  wordSet.delete(clean);

  const family: string[] = [];
  const minSuffixLen = Math.max(2, clean.length > 4 ? 3 : 2);
  for (let suffixLen = clean.length - 1; suffixLen >= minSuffixLen; suffixLen -= 1) {
    const suffix = clean.slice(-suffixLen);
    for (const candidate of wordSet) {
      if (candidate.endsWith(suffix) && family.indexOf(candidate) < 0) {
        family.push(candidate);
      }
      if (family.length >= 4) break;
    }
    if (family.length >= 3) break;
  }
  return family.slice(0, 4);
}

export interface ChildFriendlyHintData {
  text: string;
  word: string;
  chunks: string[];
  matchedPrefixLength: number;
}

export function buildChildFriendlyHint(word: string, errorType: string, matchedPrefixLength: number, cachedSyllables?: string[] | null): ChildFriendlyHintData {
  const normalized = normalizeEnglishKey(word);
  const chunks = splitIntoSyllables(word, cachedSyllables);
  const chunksStr = chunks.join(" — ");

  if (errorType === "unknown") {
    return {
      text: `先看 5 秒建立印象，再遮住重拼。这个单词可以分成 ${chunks.length} 小段：${chunksStr}`,
      word,
      chunks,
      matchedPrefixLength: 0,
    };
  }

  if (errorType === "first-letter") {
    return {
      text: `先确认中文意思，再听首音。首字母是 ${normalized[0] ?? ""}。`,
      word,
      chunks,
      matchedPrefixLength: 0,
    };
  }

  if (matchedPrefixLength > 0 && matchedPrefixLength < normalized.length) {
    const matchedPart = normalized.slice(0, matchedPrefixLength);
    return {
      text: `你拼对了前面 ${matchedPart}，后面的部分不太对，再试一次！`,
      word,
      chunks,
      matchedPrefixLength,
    };
  }

  return {
    text: `这个单词可以分成 ${chunks.length} 小段：${chunksStr}`,
    word,
    chunks,
    matchedPrefixLength: 0,
  };
}

export function buildTypedSpellingHint(expectedWord: string, typedWord: string, _errorCount: number, errorType: string, cachedSyllables?: string[] | null): string {
  const matchedPrefixLength = getMatchedPrefixLength(expectedWord, typedWord);
  const hint = buildChildFriendlyHint(expectedWord, errorType, matchedPrefixLength, cachedSyllables);
  return hint.text;
}

export function getWordInputWidth(word: string): string {
  const normalizedLength = Math.max(normalizeEnglishKey(word).length, 2);
  return `${Math.min(Math.max(normalizedLength * 3, 8), 24)}rem`;
}

export function getDynamicReviewWords(item: LearningItem | null): string[] {
  if (item?.focus_words && item.focus_words.length > 0) {
    return item.focus_words.map(normalizeEnglishKey).filter(Boolean);
  }
  if (!item?.source?.startsWith("AI 动态复习：")) {
    return [];
  }
  return item.source
    .replace("AI 动态复习：", "")
    .split(",")
    .map(normalizeEnglishKey)
    .filter(Boolean);
}

export function mergeLearningQueues(reviewItems: LearningItem[], courseItems: LearningItem[]): LearningItem[] {
  const seenItemIds = new Set<string>();
  return [...reviewItems, ...courseItems].filter((item) => {
    if (seenItemIds.has(item.id)) {
      return false;
    }
    seenItemIds.add(item.id);
    return true;
  });
}

export function formatStudyDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const paddedMinutes = String(minutes).padStart(2, "0");
  const paddedSeconds = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${paddedMinutes}:${paddedSeconds}` : `${minutes}:${paddedSeconds}`;
}

export function getStudyProgressKey(courseId: string): string {
  const userId = getAuthUserId();
  return `memoseed_study_progress_${userId}_${courseId}`;
}

/** Lightweight auth user ID — avoids pulling in the full auth module for just user ID lookup. */
function getAuthUserId(): string {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const auth = require("@/lib/auth");
    return auth.getAuthUser()?.id ?? "anonymous";
  } catch {
    return "anonymous";
  }
}

export function getFallbackEncouragement(courseName: string): { chineseText: string; englishText: string } {
  const fallbackEncouragements = [
    {
      chineseText: `${courseName}完成得很棒，继续保持这份认真！`,
      englishText: "Great work finishing this lesson. Keep going with confidence!",
    },
    {
      chineseText: `你又向前走了一步，下一课也一定可以！`,
      englishText: "You took another step forward. You can do the next lesson too!",
    },
    {
      chineseText: `今天的努力会变成明天的进步，做得好！`,
      englishText: "Today's effort will become tomorrow's progress. Well done!",
    },
  ];
  return fallbackEncouragements[Math.floor(Math.random() * fallbackEncouragements.length)];
}

export function calculateSentenceScore(errorCount: number): number {
  if (errorCount === 0) {
    return 5;
  }
  if (errorCount === 1) {
    return 3;
  }
  return 2;
}

export function getTtsSpeechRate(settings: { ttsSpeedPreference?: number }): number {
  void settings;
  return 0;
}

export function getAdaptivePreviewDuration(word: string): number {
  // P17: preview budgets slimmed (was 5/8/10/15s by length — the whole-word
  // stare is the least efficient part of the encoding chain). Still scaled by
  // word length, capped at 6s.
  const len = normalizeEnglishKey(word).length;
  if (len <= 3) return 3000;
  if (len <= 5) return 4000;
  if (len <= 7) return 5000;
  return 6000;
}


export const commonPartLabels: Record<string, string> = {
  i: "代词",
  you: "代词",
  he: "代词",
  she: "代词",
  it: "代词",
  we: "代词",
  they: "代词",
  me: "代词",
  him: "代词",
  her: "代词",
  us: "代词",
  them: "代词",
  a: "冠词",
  an: "冠词",
  the: "冠词",
  am: "动词",
  is: "动词",
  are: "动词",
  was: "动词",
  were: "动词",
  have: "动词",
  has: "动词",
  had: "动词",
  do: "动词",
  does: "动词",
  did: "动词",
  can: "情态动词",
  will: "情态动词",
  new: "形容词",
  old: "形容词",
  good: "形容词",
  bad: "形容词",
};

export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
