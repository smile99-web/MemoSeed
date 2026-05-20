"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { KeyboardEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getAccessToken, getAuthUser } from "@/lib/auth";
import { generateDynamicSentence, LearningItem, listLearningItems, logWordMistake, translateLearningText } from "@/lib/learning";
import { recordStudyTime, scheduleMemoryReview } from "@/lib/memory";
import { ModelSettings, defaultModelSettings, getModelSettings, loadPersistedModelSettings } from "@/lib/model-settings";
import { playAudioBlob, synthesizeKokoroSpeech, synthesizeVolcengineSpeech } from "@/lib/tts";

const itemTypeLabels: Record<LearningItem["item_type"], string> = {
  word: "单词",
  phrase: "短语",
  sentence: "句子",
};

const annotationColors = ["border-primary", "border-emerald-500", "border-sky-500", "border-violet-500", "border-amber-500"] as const;
const STUDY_IDLE_TIMEOUT_MS = 20_000;
const STUDY_TIME_FLUSH_SECONDS = 10;
const commonPartLabels: Record<string, string> = {
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

type AnswerState = "typing" | "mistake-word-practice" | "sentence-complete" | "word-translations-shown";
type WordStatus = "idle" | "correct" | "incorrect";
type MistakePracticeStatus = "idle" | "incorrect";

function tokenizeEnglish(value: string): string[] {
  return value.trim().split(/\s+/).filter(Boolean);
}

function normalizeTypedWord(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/^[^a-z0-9']+|[^a-z0-9']+$/g, "");
}

function normalizeEnglishKey(value: string): string {
  return value.trim().toLowerCase().replace(/^[^a-z0-9']+|[^a-z0-9']+$/g, "");
}

function cleanWordForTranslation(value: string): string {
  return value.trim().replace(/^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$/g, "");
}

function getWordInputWidth(word: string): string {
  const normalizedLength = Math.max(normalizeEnglishKey(word).length, 2);
  return `${Math.min(Math.max(normalizedLength * 2.1, 4.5), 18)}rem`;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function speakWithBrowser(text: string, language: string): Promise<void> {
  if (!text.trim() || !("speechSynthesis" in window)) {
    return Promise.resolve();
  }

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = language;
  utterance.rate = 0.85;
  return new Promise((resolve) => {
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    window.speechSynthesis.speak(utterance);
  });
}

async function speakWithKokoro(text: string, voice: string, language: string, settings: ModelSettings) {
  if (!text.trim()) {
    return;
  }

  try {
    const audioBlob = settings.ttsProvider === "volcark" ? await speakWithVolcengine(text, voice, language, settings) : await speakWithKokoroApi(text, voice, settings);
    await playAudioBlob(audioBlob);
  } catch (error) {
    console.warn("课程学习 TTS 播放失败，已回退到浏览器语音。", error);
    await speakWithBrowser(text, language);
  }
}

function formatStudyDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const paddedMinutes = String(minutes).padStart(2, "0");
  const paddedSeconds = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${paddedMinutes}:${paddedSeconds}` : `${minutes}:${paddedSeconds}`;
}

function getStudyProgressKey(courseId: string): string {
  const userId = getAuthUser()?.id ?? "anonymous";
  return `memoseed_study_progress_${userId}_${courseId}`;
}

function getDynamicReviewWords(item: LearningItem | null): string[] {
  if (!item?.id.startsWith("generated-") || !item.source?.startsWith("AI 动态复习：")) {
    return [];
  }
  return item.source
    .replace("AI 动态复习：", "")
    .split(",")
    .map(normalizeEnglishKey)
    .filter(Boolean);
}

async function speakWithKokoroApi(text: string, voice: string, settings: ModelSettings): Promise<Blob> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error("Login required for Kokoro TTS");
  }

  return synthesizeKokoroSpeech(text, voice, settings, accessToken);
}

async function speakWithVolcengine(text: string, voice: string, language: string, settings: ModelSettings): Promise<Blob> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error("Login required for Volcengine TTS");
  }

  return synthesizeVolcengineSpeech(text, voice, language, settings, accessToken);
}

function StudyContent() {
  const searchParams = useSearchParams();
  const courseId = searchParams.get("course_id") ?? "";
  const [items, setItems] = useState<LearningItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [wordAnswers, setWordAnswers] = useState<string[]>([]);
  const [activeWordIndex, setActiveWordIndex] = useState(0);
  const [answerState, setAnswerState] = useState<AnswerState>("typing");
  const [wordStatuses, setWordStatuses] = useState<WordStatus[]>([]);
  const [wordTranslations, setWordTranslations] = useState<string[]>([]);
  const [mistakenWords, setMistakenWords] = useState<string[]>([]);
  const [mistakePracticeWords, setMistakePracticeWords] = useState<string[]>([]);
  const [mistakePracticeIndex, setMistakePracticeIndex] = useState(0);
  const [mistakePracticeAnswer, setMistakePracticeAnswer] = useState("");
  const [mistakePracticeStatus, setMistakePracticeStatus] = useState<MistakePracticeStatus>("idle");
  const [mistakePracticeErrorCount, setMistakePracticeErrorCount] = useState(0);
  const [mistakePracticeTranslations, setMistakePracticeTranslations] = useState<Record<string, string>>({});
  const [deferredDynamicWords, setDeferredDynamicWords] = useState<string[]>([]);
  const [hasFinalizedCurrentItem, setHasFinalizedCurrentItem] = useState(false);
  const [startedAt, setStartedAt] = useState<number>(() => Date.now());
  const [isTranslatingWords, setIsTranslatingWords] = useState(false);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);
  const [activeStudySeconds, setActiveStudySeconds] = useState(0);
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const mistakePracticeInputRef = useRef<HTMLInputElement | null>(null);
  const activeStudyMsRef = useRef(0);
  const pendingStudyMsRef = useRef(0);
  const lastStudyTickAtRef = useRef(Date.now());
  const lastKeyboardAtRef = useRef(Date.now());
  const isFlushingStudyTimeRef = useRef(false);
  const voiceSequenceRef = useRef(0);
  const modelSettingsRef = useRef<ModelSettings>(defaultModelSettings);

  const currentItem = items[currentIndex] ?? null;
  const currentWords = useMemo(() => tokenizeEnglish(currentItem?.english_text ?? ""), [currentItem]);
  const dynamicReviewWords = useMemo(() => getDynamicReviewWords(currentItem), [currentItem]);
  const dynamicReviewWordIndexes = useMemo(() => {
    const reviewWordSet = new Set(dynamicReviewWords);
    return currentWords.map((word, index) => (reviewWordSet.has(normalizeEnglishKey(word)) ? index : -1)).filter((index) => index >= 0);
  }, [currentWords, dynamicReviewWords]);
  const isDynamicFillBlankItem = dynamicReviewWordIndexes.length > 0;
  const currentMistakePracticeWord = mistakePracticeWords[mistakePracticeIndex] ?? "";
  const currentMistakePracticeTranslation = mistakePracticeTranslations[currentMistakePracticeWord] ?? "";
  const progressPercent = items.length > 0 ? ((currentIndex + 1) / items.length) * 100 : 0;

  useEffect(() => {
    setModelSettings(getModelSettings());
    void loadPersistedModelSettings().then(setModelSettings);
  }, []);

  useEffect(() => {
    modelSettingsRef.current = modelSettings;
  }, [modelSettings]);

  const startVoiceSequence = useCallback(function startVoiceSequence(): number {
    voiceSequenceRef.current += 1;
    window.speechSynthesis?.cancel();
    return voiceSequenceRef.current;
  }, []);

  const isCurrentVoiceSequence = useCallback(function isCurrentVoiceSequence(sequenceId: number): boolean {
    return voiceSequenceRef.current === sequenceId;
  }, []);

  const waitInVoiceSequence = useCallback(async function waitInVoiceSequence(sequenceId: number, ms: number): Promise<boolean> {
    await wait(ms);
    return isCurrentVoiceSequence(sequenceId);
  }, [isCurrentVoiceSequence]);

  const speakInVoiceSequence = useCallback(async function speakInVoiceSequence(sequenceId: number, text: string, voice: string, language: string, settings: ModelSettings): Promise<boolean> {
    if (!isCurrentVoiceSequence(sequenceId)) {
      return false;
    }
    await speakWithKokoro(text, voice, language, settings);
    return isCurrentVoiceSequence(sequenceId);
  }, [isCurrentVoiceSequence]);

  const playCurrentItemIntro = useCallback(async function playCurrentItemIntro(item: LearningItem, sequenceId: number) {
    const settings = modelSettingsRef.current;
    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    if (!(await speakInVoiceSequence(sequenceId, item.chinese_text, settings.ttsChineseVoice, "zh-CN", settings))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    if (!(await speakInVoiceSequence(sequenceId, item.english_text, settings.ttsEnglishVoice, "en-US", settings))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 2000))) {
      return;
    }
    await speakInVoiceSequence(sequenceId, item.english_text, settings.ttsEnglishVoice, "en-US", settings);
  }, [speakInVoiceSequence, waitInVoiceSequence]);

  const playChineseThenEnglish = useCallback(async function playChineseThenEnglish(chineseText: string, englishText: string, sequenceId = startVoiceSequence()) {
    const settings = modelSettingsRef.current;
    if (!(await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    await speakInVoiceSequence(sequenceId, englishText, settings.ttsEnglishVoice, "en-US", settings);
  }, [speakInVoiceSequence, startVoiceSequence, waitInVoiceSequence]);

  const handleSpeakEnglish = useCallback(function handleSpeakEnglish() {
    if (!currentItem?.english_text) {
      return;
    }
    const sequenceId = startVoiceSequence();
    const settings = modelSettingsRef.current;
    void speakInVoiceSequence(sequenceId, currentItem.english_text, settings.ttsEnglishVoice, "en-US", settings);
  }, [currentItem, speakInVoiceSequence, startVoiceSequence]);

  const flushStudyTime = useCallback(async function flushStudyTime(force = false) {
    if (isFlushingStudyTimeRef.current) {
      return;
    }

    const durationSeconds = Math.floor(pendingStudyMsRef.current / 1000);
    if (durationSeconds <= 0 || (!force && durationSeconds < STUDY_TIME_FLUSH_SECONDS)) {
      return;
    }

    pendingStudyMsRef.current -= durationSeconds * 1000;
    const accessToken = getAccessToken();
    if (!accessToken) {
      pendingStudyMsRef.current += durationSeconds * 1000;
      return;
    }

    isFlushingStudyTimeRef.current = true;
    try {
      await recordStudyTime(accessToken, durationSeconds, courseId);
    } catch {
      pendingStudyMsRef.current += durationSeconds * 1000;
    } finally {
      isFlushingStudyTimeRef.current = false;
    }
  }, [courseId]);

  useEffect(() => {
    activeStudyMsRef.current = 0;
    pendingStudyMsRef.current = 0;
    lastStudyTickAtRef.current = Date.now();
    lastKeyboardAtRef.current = Date.now();
    setActiveStudySeconds(0);
  }, [courseId]);

  useEffect(() => {
    function markKeyboardActivity() {
      lastKeyboardAtRef.current = Date.now();
    }

    function tickStudyTime() {
      const now = Date.now();
      const elapsedMs = Math.max(0, now - lastStudyTickAtRef.current);
      lastStudyTickAtRef.current = now;
      if (now - lastKeyboardAtRef.current > STUDY_IDLE_TIMEOUT_MS) {
        return;
      }

      activeStudyMsRef.current += elapsedMs;
      pendingStudyMsRef.current += elapsedMs;
      const nextSeconds = Math.floor(activeStudyMsRef.current / 1000);
      setActiveStudySeconds((current) => (current === nextSeconds ? current : nextSeconds));
      if (pendingStudyMsRef.current >= STUDY_TIME_FLUSH_SECONDS * 1000) {
        void flushStudyTime();
      }
    }

    function flushWhenLeaving() {
      void flushStudyTime(true);
    }

    window.addEventListener("keydown", markKeyboardActivity);
    window.addEventListener("pagehide", flushWhenLeaving);
    document.addEventListener("visibilitychange", flushWhenLeaving);
    const intervalId = window.setInterval(tickStudyTime, 1000);

    return () => {
      window.removeEventListener("keydown", markKeyboardActivity);
      window.removeEventListener("pagehide", flushWhenLeaving);
      document.removeEventListener("visibilitychange", flushWhenLeaving);
      window.clearInterval(intervalId);
      void flushStudyTime(true);
    };
  }, [flushStudyTime]);

  useEffect(() => {
    setWordAnswers(currentWords.map(() => ""));
    setWordStatuses(currentWords.map(() => "idle"));
    setWordTranslations(currentWords.map(() => ""));
    setMistakenWords([]);
    setMistakePracticeWords([]);
    setMistakePracticeIndex(0);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeTranslations({});
    setHasFinalizedCurrentItem(false);
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    setAnswerState("typing");
    setIsTranslatingWords(false);
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]?.focus(), 0);
    const sequenceId = startVoiceSequence();
    if (currentItem?.chinese_text && currentItem.english_text) {
      void playCurrentItemIntro(currentItem, sequenceId);
    }
  }, [currentItem, currentWords, dynamicReviewWordIndexes, playCurrentItemIntro, startVoiceSequence]);

  useEffect(() => {
    if (answerState === "mistake-word-practice") {
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
    }
  }, [answerState, mistakePracticeIndex]);

  useEffect(() => {
    async function loadStudyItems() {
      const accessToken = getAccessToken();
      if (!accessToken) {
        setErrorMessage("请先登录后再学习");
        setIsLoading(false);
        return;
      }
      if (!courseId) {
        setErrorMessage("缺少课程信息，请从课程目录选择课程后开始学习");
        setIsLoading(false);
        return;
      }

      try {
        const nextItems = await listLearningItems(accessToken, courseId);
        const savedIndex = Number(window.localStorage.getItem(getStudyProgressKey(courseId)) ?? "0");
        const safeIndex = Number.isInteger(savedIndex) && savedIndex >= 0 && savedIndex < nextItems.length ? savedIndex : 0;
        setItems(nextItems);
        setCurrentIndex(safeIndex);
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "读取学习内容失败");
      } finally {
        setIsLoading(false);
      }
    }

    void loadStudyItems();
  }, [courseId]);

  function resetAnswer() {
    setWordAnswers(currentWords.map(() => ""));
    setWordStatuses(currentWords.map(() => "idle"));
    setWordTranslations(currentWords.map(() => ""));
    setMistakenWords([]);
    setMistakePracticeWords([]);
    setMistakePracticeIndex(0);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeTranslations({});
    setHasFinalizedCurrentItem(false);
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    setAnswerState("typing");
    setIsTranslatingWords(false);
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]?.focus(), 0);
  }

  const handleNextItem = useCallback(function handleNextItem() {
    if (items.length === 0) {
      return;
    }

    setCurrentIndex((index) => {
      const nextIndex = (index + 1) % items.length;
      window.localStorage.setItem(getStudyProgressKey(courseId), String(nextIndex));
      return nextIndex;
    });
  }, [courseId, items.length]);

  function isGeneratedItem(item: LearningItem): boolean {
    return item.id.startsWith("generated-");
  }

  function calculateSentenceScore(errorCount: number): number {
    if (errorCount === 0) {
      return 5;
    }
    if (errorCount === 1) {
      return 3;
    }
    return 2;
  }

  function getUniqueMistakenWords() {
    return Array.from(new Set(mistakenWords.map(normalizeEnglishKey).filter(Boolean)));
  }

  async function translateMistakePracticeWords(words: string[]) {
    const accessToken = getAccessToken();
    const translations: Record<string, string> = {};
    if (!accessToken) {
      return translations;
    }

    await Promise.all(
      words.map(async (word) => {
        try {
          const response = await translateLearningText(word, accessToken, modelSettings);
          translations[word] = response.chinese_text;
        } catch {
          translations[word] = "";
        }
      }),
    );
    return translations;
  }

  async function finalizeCurrentSentenceReview() {
    if (!currentItem || hasFinalizedCurrentItem) {
      return;
    }

    setHasFinalizedCurrentItem(true);
    const accessToken = getAccessToken();
    const uniqueMistakenWords = getUniqueMistakenWords();
    if (accessToken && !isGeneratedItem(currentItem)) {
      try {
        await scheduleMemoryReview(accessToken, {
          learning_item_id: currentItem.id,
          score: calculateSentenceScore(uniqueMistakenWords.length),
          review_mode: "sentence-spelling",
          response_text: uniqueMistakenWords.length > 0 ? `错词：${uniqueMistakenWords.join(", ")}` : "全部拼写正确",
          duration_seconds: Math.max(1, Math.round((Date.now() - startedAt) / 1000)),
        });
      } catch {
        // Learning flow should not stop when telemetry fails.
      }
    }
  }

  async function insertDynamicSentenceAfterCurrent(words: string[]) {
    if (!currentItem) {
      return false;
    }

    const uniqueMistakenWords = Array.from(new Set(words.map(normalizeEnglishKey).filter(Boolean)));
    const accessToken = getAccessToken();
    if (!accessToken || uniqueMistakenWords.length === 0) {
      return false;
    }
    if (uniqueMistakenWords.length === 0) {
      return false;
    }

    try {
      const generatedSentence = await generateDynamicSentence(
        {
          course_id: currentItem.course_id,
          current_sentence: currentItem.english_text,
          mistaken_words: uniqueMistakenWords,
        },
        accessToken,
        modelSettings,
      );
      const dynamicItem: LearningItem = {
        id: `generated-${Date.now()}`,
        user_id: currentItem.user_id,
        course_id: currentItem.course_id,
        item_type: "sentence",
        english_text: generatedSentence.english_text,
        chinese_text: generatedSentence.chinese_text,
        phonetic: null,
        difficulty_level: Math.min(Math.max(currentItem.difficulty_level, 1), 5),
        source: `AI 动态复习：${uniqueMistakenWords.join(", ")}`,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setItems((current) => {
        const nextItems = [...current];
        nextItems.splice(currentIndex + 1, 0, dynamicItem);
        return nextItems;
      });
      return true;
    } catch {
      return false;
    }
  }

  async function completeCurrentSentence() {
    if (!currentItem) {
      return;
    }

    const uniqueMistakenWords = getUniqueMistakenWords();
    inputRefs.current.forEach((input) => input?.blur());
    await finalizeCurrentSentenceReview();

    let dynamicSentenceFeedback: string | null = null;
    if (deferredDynamicWords.length > 0) {
      const didInsertDynamicSentence = await insertDynamicSentenceAfterCurrent(deferredDynamicWords);
      setDeferredDynamicWords([]);
      dynamicSentenceFeedback = didInsertDynamicSentence
        ? "上一轮错词复习句已生成，稍后会进入新的 5 到 7 个单词复习句。"
        : "上一轮错词复习句生成失败，请稍后再试。";
    }

    if (uniqueMistakenWords.length > 0) {
      setFeedbackMessage("正在准备错词中文提示...");
      const practiceTranslations = await translateMistakePracticeWords(uniqueMistakenWords);
      setMistakePracticeWords(uniqueMistakenWords);
      setMistakePracticeTranslations(practiceTranslations);
      setMistakePracticeIndex(0);
      setMistakePracticeAnswer("");
      setMistakePracticeStatus("idle");
      setMistakePracticeErrorCount(0);
      setAnswerState("mistake-word-practice");
      setFeedbackMessage(dynamicSentenceFeedback ? `${dynamicSentenceFeedback} 现在先单独拼写本句错词。` : "本句完成。先单独拼写错词，正确后再进入下一句。");
      const firstTranslation = practiceTranslations[uniqueMistakenWords[0]];
      if (firstTranslation) {
        await playChineseThenEnglish(firstTranslation, uniqueMistakenWords[0]);
      }
      return;
    }

    setAnswerState("sentence-complete");
    setFeedbackMessage(dynamicSentenceFeedback ? `${dynamicSentenceFeedback} 按空格查看每个单词的中文意思。` : "整句拼写正确。按空格查看每个单词的中文意思。");
    await playChineseThenEnglish(currentItem.chinese_text, currentItem.english_text);
  }

  function updateWordAnswer(index: number, value: string) {
    setWordAnswers((current) => {
      const nextAnswers = [...current];
      nextAnswers[index] = value.replace(/\s/g, "");
      return nextAnswers;
    });
    setFeedbackMessage(null);
    if (wordStatuses[index] === "incorrect") {
      setWordStatuses((current) => {
        const nextStatuses = [...current];
        nextStatuses[index] = "idle";
        return nextStatuses;
      });
    }
  }

  function resetIncorrectWord(index: number) {
    setWordAnswers((current) => {
      const nextAnswers = [...current];
      nextAnswers[index] = "";
      return nextAnswers;
    });
    setWordStatuses((current) => {
      const nextStatuses = [...current];
      nextStatuses[index] = "idle";
      return nextStatuses;
    });
    setFeedbackMessage("请重新输入这个单词。");
    window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
  }

  async function handleIncorrectWord(expectedWord: string) {
    let translatedWord = "";
    const accessToken = getAccessToken();
    if (accessToken) {
      try {
        const response = await translateLearningText(expectedWord, accessToken, modelSettings);
        translatedWord = response.chinese_text;
      } catch {
        translatedWord = "";
      }
    }

    setFeedbackMessage(translatedWord ? `正确单词：${expectedWord}，中文：${translatedWord}` : `正确单词：${expectedWord}`);
    await playChineseThenEnglish(translatedWord, expectedWord);
  }

  function checkWord(index: number) {
    if (isDynamicFillBlankItem && !dynamicReviewWordIndexes.includes(index)) {
      const firstBlankIndex = dynamicReviewWordIndexes[0] ?? 0;
      setActiveWordIndex(firstBlankIndex);
      window.setTimeout(() => inputRefs.current[firstBlankIndex]?.focus(), 0);
      return;
    }

    const expectedWord = currentWords[index];
    if (!expectedWord) {
      return;
    }

    const isCorrect = normalizeTypedWord(wordAnswers[index] ?? "") === normalizeTypedWord(expectedWord);
    setWordStatuses((current) => {
      const nextStatuses = [...current];
      nextStatuses[index] = isCorrect ? "correct" : "incorrect";
      return nextStatuses;
    });

    if (!isCorrect) {
      const actualWord = wordAnswers[index] ?? "";
      const normalizedExpectedWord = normalizeEnglishKey(expectedWord);
      setMistakenWords((current) => (current.includes(normalizedExpectedWord) ? current : [...current, normalizedExpectedWord]));
      const accessToken = getAccessToken();
      if (accessToken && currentItem && !isGeneratedItem(currentItem)) {
        void logWordMistake(currentItem.id, expectedWord, actualWord, accessToken).catch(() => undefined);
      }
      setFeedbackMessage(`正在翻译并朗读：${expectedWord}`);
      void handleIncorrectWord(expectedWord);
      return;
    }

    const nextIndex = isDynamicFillBlankItem ? dynamicReviewWordIndexes.find((wordIndex) => wordIndex > index) ?? currentWords.length : index + 1;
    if (nextIndex < currentWords.length) {
      setActiveWordIndex(nextIndex);
      window.setTimeout(() => inputRefs.current[nextIndex]?.focus(), 0);
      return;
    }

    void completeCurrentSentence();
  }

  function handleWordKeyDown(event: KeyboardEvent<HTMLInputElement>, index: number) {
    if (event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      if ((wordStatuses[index] ?? "idle") === "incorrect") {
        resetIncorrectWord(index);
        return;
      }
      checkWord(index);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      if ((wordStatuses[index] ?? "idle") === "incorrect") {
        resetIncorrectWord(index);
        return;
      }
      checkWord(index);
    }
  }

  async function checkMistakePracticeWord() {
    const expectedWord = mistakePracticeWords[mistakePracticeIndex];
    if (!expectedWord) {
      return;
    }

    const isCorrect = normalizeTypedWord(mistakePracticeAnswer) === normalizeTypedWord(expectedWord);
    if (!isCorrect) {
      const nextErrorCount = mistakePracticeErrorCount + 1;
      if (nextErrorCount >= 3) {
        setDeferredDynamicWords([normalizeEnglishKey(expectedWord)]);
        setMistakePracticeWords([]);
        setMistakePracticeAnswer("");
        setMistakePracticeStatus("idle");
        setMistakePracticeErrorCount(0);
        setFeedbackMessage("这个错词已连续 3 次错误，先进入下一句；下一句完成后会生成 5 到 7 个单词的新复习句。");
        handleNextItem();
        return;
      }

      setMistakePracticeErrorCount(nextErrorCount);
      setMistakePracticeStatus("incorrect");
      setMistakePracticeAnswer("");
      setFeedbackMessage(`正确单词：${expectedWord}。请再拼一次，连续错误 ${nextErrorCount}/3。`);
      await playChineseThenEnglish(currentMistakePracticeTranslation, expectedWord);
      return;
    }

    const nextIndex = mistakePracticeIndex + 1;
    if (nextIndex < mistakePracticeWords.length) {
      const nextWord = mistakePracticeWords[nextIndex];
      setMistakePracticeIndex(nextIndex);
      setMistakePracticeAnswer("");
      setMistakePracticeStatus("idle");
      setMistakePracticeErrorCount(0);
      setFeedbackMessage("正确。继续拼写下一个错词。");
      const nextTranslation = mistakePracticeTranslations[nextWord];
      if (nextTranslation) {
        await playChineseThenEnglish(nextTranslation, nextWord);
      }
      return;
    }

    setMistakePracticeWords([]);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setFeedbackMessage("错词单独拼写完成，进入下一句。");
    handleNextItem();
  }

  function handleMistakePracticeKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      void checkMistakePracticeWord();
    }
  }

  const revealWordTranslations = useCallback(async function revealWordTranslations() {
    if (!currentItem || isTranslatingWords) {
      return;
    }

    const accessToken = getAccessToken();
    if (!accessToken) {
      setFeedbackMessage("请先登录后再调用 LLM 翻译单词。");
      return;
    }

    setIsTranslatingWords(true);
    setFeedbackMessage("正在调用 LLM 翻译每个单词...");
    const translationCache = new Map<string, string>();
    const translations = await Promise.all(
      currentWords.map(async (word) => {
        const cleanWord = cleanWordForTranslation(word);
        const cacheKey = normalizeEnglishKey(cleanWord);
        if (!cleanWord) {
          return "";
        }
        if (translationCache.has(cacheKey)) {
          return translationCache.get(cacheKey) ?? "";
        }
        try {
          const response = await translateLearningText(cleanWord, accessToken, modelSettings);
          translationCache.set(cacheKey, response.chinese_text);
          return response.chinese_text;
        } catch {
          translationCache.set(cacheKey, "翻译失败");
          return "翻译失败";
        }
      }),
    );
    setWordTranslations(translations);
    setAnswerState("word-translations-shown");
    setFeedbackMessage("已显示逐词中文。再次按空格进入下一句。");
    setIsTranslatingWords(false);
  }, [currentItem, currentWords, isTranslatingWords, modelSettings]);

  useEffect(() => {
    function handleWindowKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== " " || answerState === "typing" || answerState === "mistake-word-practice") {
        return;
      }
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
        return;
      }
      event.preventDefault();
      if (answerState === "sentence-complete") {
        void revealWordTranslations();
        return;
      }
      if (answerState === "word-translations-shown") {
        handleNextItem();
      }
    }

    window.addEventListener("keydown", handleWindowKeyDown);
    return () => window.removeEventListener("keydown", handleWindowKeyDown);
  }, [answerState, revealWordTranslations, handleNextItem]);

  return (
    <main className="flex min-h-screen flex-col bg-white">
      <header className="border-b bg-white px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="rounded-md border bg-slate-50 px-3 py-2 text-left">
              <p className="text-xs text-muted-foreground">学习时长</p>
              <p className="font-mono text-lg font-semibold text-slate-900">{formatStudyDuration(activeStudySeconds)}</p>
            </div>
            <Link className="text-sm font-medium text-primary hover:underline" href="/learning/import">
              返回课程目录
            </Link>
            <div>
              <p className="text-lg font-semibold">课程学习</p>
              <p className="text-sm text-muted-foreground">{items.length > 0 ? `第 ${currentIndex + 1} 题 / 共 ${items.length} 题` : "拼写练习"}</p>
            </div>
          </div>
          <div className="text-lg font-bold text-slate-700">单词输入完成按空格判定，整句完成后按空格查看逐词中文</div>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progressPercent}%` }} />
        </div>
      </header>

      <section className="flex flex-1 items-center justify-center px-6 py-12">
        {isLoading ? <p className="text-lg font-bold text-muted-foreground">正在加载学习内容...</p> : null}
        {!isLoading && errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{errorMessage}</p> : null}
        {!isLoading && !errorMessage && !currentItem ? <p className="text-lg font-bold text-muted-foreground">当前课程还没有导入内容。</p> : null}

        {!isLoading && !errorMessage && currentItem ? (
          <div className="w-full max-w-5xl space-y-10 text-center">
            <div className="flex justify-center gap-3 text-sm text-muted-foreground">
              <span className="rounded-full border px-3 py-1">{itemTypeLabels[currentItem.item_type]}</span>
              <span className="rounded-full border px-3 py-1">{currentIndex + 1} / {items.length}</span>
            </div>

            <div className="space-y-10">
              <p className="text-4xl font-semibold tracking-tight text-slate-900">{currentItem.chinese_text}</p>
              {answerState === "mistake-word-practice" ? (
                <div className="mx-auto flex max-w-xl flex-col items-center gap-5 rounded-lg border bg-slate-50 px-6 py-7">
                  <div className="space-y-2">
                    <p className="text-base font-bold text-muted-foreground">错词单独拼写 {mistakePracticeIndex + 1} / {mistakePracticeWords.length}</p>
                    <p className="text-sm font-bold text-muted-foreground">根据中文拼写正确英文</p>
                    <p className="text-3xl font-bold text-slate-900">{currentMistakePracticeTranslation || "中文提示准备中"}</p>
                  </div>
                  <input
                    ref={mistakePracticeInputRef}
                    aria-label="错词单独拼写"
                    autoComplete="off"
                    className={`h-16 w-full max-w-sm border-0 border-b-4 bg-transparent px-1 text-center text-4xl outline-none transition ${mistakePracticeStatus === "incorrect" ? "border-red-500 text-red-700" : "border-primary"}`}
                    value={mistakePracticeAnswer}
                    onChange={(event) => {
                      setMistakePracticeAnswer(event.target.value.replace(/\s/g, ""));
                      setMistakePracticeStatus("idle");
                      setFeedbackMessage(null);
                    }}
                    onKeyDown={handleMistakePracticeKeyDown}
                  />
                  <p className="text-lg font-bold text-muted-foreground">按空格或回车判定，连续 3 次错误后先进入下一句。</p>
                </div>
              ) : (
                <div className="flex flex-wrap items-end justify-center gap-x-4 gap-y-6">
                  {currentWords.map((word, index) => {
                    const normalizedWord = normalizeEnglishKey(word);
                    const status = wordStatuses[index] ?? "idle";
                    const isDynamicBlank = isDynamicFillBlankItem && dynamicReviewWordIndexes.includes(index);
                    const shouldShowInput = !isDynamicFillBlankItem || isDynamicBlank;
                    const isActive = activeWordIndex === index && answerState === "typing" && shouldShowInput;
                    return (
                      <div className="flex flex-col items-center gap-2" key={`${word}-${index}`} style={{ minWidth: getWordInputWidth(word) }}>
                        {answerState !== "typing" && shouldShowInput ? (
                          <span className="rounded-full border px-3 py-0.5 text-xs text-muted-foreground">
                            {commonPartLabels[normalizedWord] ?? itemTypeLabels[currentItem.item_type]}
                          </span>
                        ) : null}
                        {shouldShowInput ? (
                          <input
                            ref={(element) => {
                              inputRefs.current[index] = element;
                            }}
                            aria-label={isDynamicBlank ? `填空：${currentItem.chinese_text}` : `第 ${index + 1} 个单词`}
                            autoComplete="off"
                            className={`h-16 border-0 border-b-4 bg-transparent px-1 text-center text-4xl outline-none transition ${status === "correct" ? "border-emerald-500 text-emerald-700" : ""} ${status === "incorrect" ? "border-red-500 text-red-700" : ""} ${status === "idle" ? annotationColors[index % annotationColors.length] : ""} ${isActive ? "bg-primary/5" : ""}`}
                            disabled={answerState !== "typing"}
                            placeholder={isDynamicBlank ? "____" : undefined}
                            style={{ width: getWordInputWidth(word) }}
                            value={wordAnswers[index] ?? ""}
                            onChange={(event) => updateWordAnswer(index, event.target.value)}
                            onFocus={() => setActiveWordIndex(index)}
                            onKeyDown={(event) => handleWordKeyDown(event, index)}
                          />
                        ) : (
                          <span className="flex h-16 items-center text-4xl font-semibold text-slate-900">{word}</span>
                        )}
                        {answerState !== "typing" && shouldShowInput ? <span className="text-2xl font-medium text-slate-900">{word}</span> : null}
                        {answerState === "word-translations-shown" ? (
                          <span className="max-w-44 rounded-md bg-secondary px-3 py-1.5 text-lg font-bold text-slate-800">
                            {wordTranslations[index] || "翻译失败"}
                          </span>
                        ) : null}
                        {status === "incorrect" ? <span className="text-base font-bold text-red-600">正确：{word}</span> : null}
                      </div>
                    );
                  })}
                </div>
              )}
              {feedbackMessage ? <p className={answerState !== "typing" ? "text-lg font-bold text-emerald-600" : "text-lg font-bold text-red-600"}>{feedbackMessage}</p> : null}
              <div className="flex flex-wrap justify-center gap-3">
                {answerState === "mistake-word-practice" ? (
                  <>
                    <Button onClick={() => void checkMistakePracticeWord()} type="button">判定错词</Button>
                    <Button onClick={handleSpeakEnglish} type="button" variant="secondary">朗读英文</Button>
                  </>
                ) : (
                  <>
                    <Button disabled={answerState !== "typing"} onClick={() => checkWord(activeWordIndex)} type="button">
                      {isDynamicFillBlankItem ? "判定填空" : "判定当前单词"}
                    </Button>
                    <Button onClick={handleSpeakEnglish} type="button" variant="secondary">朗读英文</Button>
                  </>
                )}
              </div>
            </div>

            <div className="flex flex-wrap justify-center gap-3">
              {answerState === "word-translations-shown" ? <Button onClick={handleNextItem} type="button">下一句</Button> : null}
              <Button onClick={resetAnswer} type="button" variant="secondary">
                再来一次
              </Button>
              <Button onClick={handleNextItem} type="button" variant="outline">
                跳过
              </Button>
            </div>
          </div>
        ) : null}
      </section>
    </main>
  );
}

export default function StudyPage() {
  return (
    <Suspense fallback={<main className="flex min-h-screen items-center justify-center bg-white text-sm text-muted-foreground">正在加载学习内容...</main>}>
      <StudyContent />
    </Suspense>
  );
}
