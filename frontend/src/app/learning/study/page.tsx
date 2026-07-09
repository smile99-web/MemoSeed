"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { KeyboardEvent, MouseEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useDevice } from "@/hooks/use-device";
import { getAccessToken } from "@/lib/auth";
import { useRouter, usePathname } from "next/navigation";
import { addCourseCompletion } from "@/lib/course-progress";
import { Course, listCoursePackages, listCourses } from "@/lib/courses";
import { generateDynamicSentence, generateLearningEncouragement, getWordTranslations, LearningItem, listDueReviewItems, listLearningItems, logWordMistake, logWordReview, translateLearningText } from "@/lib/learning";
import { fetchWithAuth, parseApiError } from "@/lib/api";
import { getApiBaseUrl } from "@/lib/api-base-url";
import { recordCourseCompletion, recordStudyTime, rotateFocusWord, scheduleMemoryReview, getReviewAdvice } from "@/lib/memory";
import { ModelSettings, defaultModelSettings, getModelSettings, loadPersistedModelSettings } from "@/lib/model-settings";
import { playAudioBlob, prefetchCourseAudio, stopAudioPlayback, synthesizeCosyVoiceSpeech, synthesizeKokoroSpeech, synthesizeVolcengineSpeech, TtsSynthesisOptions } from "@/lib/tts";

import {
  tokenizeEnglish,
  normalizeTypedWord,
  normalizeEnglishKey,
  buildChunkedEnglishSpeechText,
  buildWordByWordEnglishSpeechText,
  buildFocusedWordSpeechText,
  getFirstLetter,
  getMatchedPrefixLength,
  classifySpellingError,
  buildReviewTaskInstruction,
  getReviewTaskModeLabel,
  rotateChoices,
  splitIntoSyllables,
  getPhonemeSpeakableText,
  getGraphemesForChunk,
  buildChildFriendlyHint,
  wait,
  getDynamicReviewWords,
  mergeLearningQueues,
  formatStudyDuration,
  getStudyProgressKey,
  getFallbackEncouragement,
  calculateSentenceScore,
  getTtsSpeechRate,
  getAdaptivePreviewDuration,
  commonPartLabels,
  ChildFriendlyHintData,
} from "@/lib/study-utils";

import HintDisplay from "./components/HintDisplay";
import { WORD_DICTIONARY } from "@/lib/word-dictionary";
import MiniCelebrationConfetti from "./components/MiniCelebrationConfetti";
import CelebrationModal, { type CelebrationSummary, type NextCourseTarget } from "./components/CelebrationModal";
import WordInput from "./components/WordInput";

const itemTypeLabels: Record<LearningItem["item_type"], string> = {
  word: "单词",
  phrase: "短语",
  sentence: "句子",
};

const STUDY_IDLE_TIMEOUT_MS = 10_000;
const STUDY_TIME_FLUSH_SECONDS = 10;
const WORD_PREVIEW_MS = 5000;
const WORD_MEANING_REVIEW_MS = 3000;
const WORD_TRANSLATION_TIMEOUT_MS = 2500;
const DIFFICULT_WORD_CONFIRMATION_COUNT = 3;
const INITIAL_REVIEW_QUEUE_LIMIT = 100;
const REFILL_REVIEW_QUEUE_LIMIT = 50;
const SPELLING_REVIEW_TASK_TYPES = new Set(["listen_spell", "chinese_to_english", "missing_letter", "hidden_recall"]);
const BASIC_WORD_MEANINGS: Record<string, string> = {
  ...WORD_DICTIONARY,
};
const NATURAL_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: 0,
};
const SLOW_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: 0,
};
const WORD_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: 0,
};

function normalizeChoiceAnswer(value: string | null | undefined): string {
  return (value ?? "").trim().replace(/\s+/g, "");
}

function hasChineseText(value: string | null | undefined): boolean {
  return /[\u4e00-\u9fff]/.test(value ?? "");
}

function isCorrectChoiceAnswer(choice: string, answer: string | null | undefined): boolean {
  const normalizedChoice = normalizeChoiceAnswer(choice);
  const normalizedAnswer = normalizeChoiceAnswer(answer);
  return Boolean(normalizedChoice && normalizedAnswer && normalizedChoice === normalizedAnswer);
}

type AnswerState = "typing" | "mistake-word-practice" | "mistake-meaning-quiz" | "word-meaning-review" | "sentence-complete";
type EncodingStage = "meaning_intro" | "listen" | "chunk_trace" | "whole_recall";
type WordStatus = "idle" | "correct" | "incorrect" | "skipped";
type MistakePracticeStatus = "idle" | "incorrect";
type StudyMode = "mix" | "review" | "learn";

function normalizeStudyMode(value: string | null | undefined): StudyMode {
  if (value === "review" || value === "learn" || value === "mix") {
    return value;
  }
  return "mix";
}




function speakWithBrowser(text: string, language: string, playbackRate = 1): Promise<void> {
  if (!text.trim() || !("speechSynthesis" in window)) {
    return Promise.resolve();
  }

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = language;
  utterance.rate = Math.max(0.1, Math.min(playbackRate, 10));
  return new Promise((resolve) => {
    utterance.onend = () => resolve();
    utterance.onerror = () => resolve();
    window.speechSynthesis.speak(utterance);
  });
}

function playSyllableAudio(word: string, chunkIndex: number, cachedSyllables?: string[] | null, onTtsPlay?: ((blob: Blob) => void)): void {
  const chunks = splitIntoSyllables(word, cachedSyllables);
  const chunk = chunks[chunkIndex];
  if (!chunk) {
    return;
  }

  // Try professional TTS first
  if (onTtsPlay) {
    const accessToken = getAccessToken();
    if (accessToken) {
      const settings = getModelSettings();
      try {
        void synthesizeVolcengineSpeech(chunk, settings.ttsEnglishVoice, "en-US", settings, accessToken, { speechRate: 0 }).then((blob) => {
          onTtsPlay(blob);
        }).catch(() => {
          // Fallback to browser
          speakSyllableWithBrowser(chunk);
        });
        return;
      } catch {
        // Fall through to browser
      }
    }
  }

  speakSyllableWithBrowser(chunk);
}

function speakSyllableWithBrowser(chunk: string): void {
  if (!("speechSynthesis" in window)) {
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(chunk);
  utterance.lang = "en-US";
  utterance.rate = 1;
  window.speechSynthesis.speak(utterance);
}

// Phonics audio cache: phoneme -> audio Blob
const phonicsAudioCache = new Map<string, Blob>();

async function synthesizeAndCachePhonemeAudio(phoneme: string): Promise<Blob | null> {
  const cached = phonicsAudioCache.get(phoneme);
  if (cached) return cached;

  const speakableText = getPhonemeSpeakableText(phoneme);
  if (!speakableText) return null;

  const accessToken = getAccessToken();
  if (!accessToken) return null;

  const settings = getModelSettings();
  try {
    const blob = await synthesizeVolcengineSpeech(speakableText, settings.ttsEnglishVoice, "en-US", settings, accessToken, { speechRate: 0 });
    phonicsAudioCache.set(phoneme, blob);
    return blob;
  } catch {
    return null;
  }
}

async function playPhonicsAudio(word: string, chunkIndex: number, gpMap: Record<string, string> | null | undefined, cachedSyllables?: string[] | null, onTtsPlay?: ((blob: Blob) => void) | null): Promise<void> {
  const chunks = splitIntoSyllables(word, cachedSyllables);
  const chunk = chunks[chunkIndex];
  if (!chunk) {
    return;
  }

  // If no grapheme-phoneme map is available, fall back to letter name spelling via browser
  if (!gpMap || Object.keys(gpMap).length === 0) {
    playLegacyLetterAudio(chunk, onTtsPlay ?? undefined);
    return;
  }

  // Find graphemes that make up this chunk
  const graphemes = getGraphemesForChunk(chunk, gpMap);

  stopAudioPlayback();

  // Use Volcengine TTS for each phoneme
  for (let i = 0; i < graphemes.length; i += 1) {
    const grapheme = graphemes[i];
    const phoneme = gpMap[grapheme];
    if (!phoneme) continue;

    const blob = await synthesizeAndCachePhonemeAudio(phoneme);
    if (blob) {
      if (onTtsPlay) {
        onTtsPlay(blob);
      } else {
        await playAudioBlob(blob);
      }
      if (i < graphemes.length - 1) {
        await wait(120);
      }
    }
  }
}

function playLegacyLetterAudio(chunk: string, onTtsPlay?: (blob: Blob) => void): void {
  if (!("speechSynthesis" in window)) return;

  // Try professional TTS for single letters first
  if (onTtsPlay) {
    const accessToken = getAccessToken();
    if (accessToken) {
    const settings = getModelSettings();
    const letters = chunk.split("").join(". ");
    try {
        void synthesizeVolcengineSpeech(letters, settings.ttsEnglishVoice, "en-US", settings, accessToken, { speechRate: 0 }).then((blob) => {
          onTtsPlay(blob);
        }).catch(() => {
          speakLettersWithBrowser(chunk);
        });
        return;
      } catch {
        // Fall through to browser
      }
    }
  }

  speakLettersWithBrowser(chunk);
}

function speakLettersWithBrowser(chunk: string): void {
  window.speechSynthesis.cancel();
  const letters = chunk.split("").join(".");
  const utterance = new SpeechSynthesisUtterance(letters);
  utterance.lang = "en-US";
  utterance.rate = 1;
  window.speechSynthesis.speak(utterance);
}



async function speakWithKokoro(
  text: string,
  voice: string,
  language: string,
  settings: ModelSettings,
  options: TtsSynthesisOptions = {},
  shouldContinue: () => boolean = () => true,
) {
  if (!text.trim() || !shouldContinue()) {
    return;
  }

  try {
    const audioBlob = settings.ttsProvider === "volcark"
      ? await speakWithVolcengine(text, voice, language, settings, options)
      : settings.ttsProvider === "cosyvoice"
        ? await speakWithCosyVoice(text, voice, settings)
        : await speakWithKokoroApi(text, voice, settings, options);
    if (!shouldContinue()) {
      return;
    }
    await playAudioBlob(audioBlob);
  } catch (error) {
    if (!shouldContinue()) {
      return;
    }
    console.warn("课程学习 TTS 播放失败，已回退到浏览器语音。", error);
    await speakWithBrowser(text, language, options.speed ?? 1);
  }
}

async function speakWithKokoroApi(text: string, voice: string, settings: ModelSettings, options: TtsSynthesisOptions = {}): Promise<Blob> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error("Login required for Kokoro TTS");
  }

  return synthesizeKokoroSpeech(text, voice, settings, accessToken, options);
}

async function speakWithCosyVoice(text: string, voice: string, settings: ModelSettings): Promise<Blob> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error("Login required for CosyVoice TTS");
  }
  const speaker = voice === settings.ttsEnglishVoice ? settings.cosyvoiceEnglishSpeaker : settings.cosyvoiceChineseSpeaker;
  return synthesizeCosyVoiceSpeech(text, speaker, settings, accessToken);
}

async function speakWithVolcengine(text: string, voice: string, language: string, settings: ModelSettings, options: TtsSynthesisOptions = {}): Promise<Blob> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error("Login required for Volcengine TTS");
  }

  return synthesizeVolcengineSpeech(text, voice, language, settings, accessToken, options);
}

function playCelebrationSound() {
  const AudioContextCtor = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) {
    return;
  }

  const audioContext = new AudioContextCtor();
  const now = audioContext.currentTime;
  [523.25, 659.25, 783.99, 1046.5].forEach((frequency, index) => {
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "triangle";
    oscillator.frequency.value = frequency;
    oscillator.connect(gain);
    gain.connect(audioContext.destination);

    const startAt = now + index * 0.12;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(0.16, startAt + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.28);
    oscillator.start(startAt);
    oscillator.stop(startAt + 0.3);
  });

  window.setTimeout(() => void audioContext.close().catch(() => undefined), 900);
}

function playCorrectDing() {
  const AudioContextCtor = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return;
  const ctx = new AudioContextCtor();
  const now = ctx.currentTime;
  [523.25, 659.25].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    osc.connect(gain);
    gain.connect(ctx.destination);
    const startAt = now + i * 0.1;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(0.18, startAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.18);
    osc.start(startAt);
    osc.stop(startAt + 0.2);
  });
  setTimeout(() => void ctx.close().catch(() => undefined), 600);
}

function playIncorrectTap() {
  const AudioContextCtor = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return;
  const ctx = new AudioContextCtor();
  const now = ctx.currentTime;
  const osc = ctx.createOscillator();
  const lpFilter = ctx.createBiquadFilter();
  const gain = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = 220;
  lpFilter.type = "lowpass";
  lpFilter.frequency.value = 400;
  osc.connect(lpFilter);
  lpFilter.connect(gain);
  gain.connect(ctx.destination);
  gain.gain.setValueAtTime(0.14, now);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.1);
  osc.start(now);
  osc.stop(now + 0.1);
  setTimeout(() => void ctx.close().catch(() => undefined), 300);
}

function playLevelUpSound() {
  const AudioContextCtor = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return;
  const ctx = new AudioContextCtor();
  const now = ctx.currentTime;
  [523.25, 659.25, 783.99, 1046.5, 1318.5].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.value = freq;
    osc.connect(gain);
    gain.connect(ctx.destination);
    const startAt = now + i * 0.08;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(0.15, startAt + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.45);
    osc.start(startAt);
    osc.stop(startAt + 0.5);
  });
  setTimeout(() => void ctx.close().catch(() => undefined), 1200);
}

function StudyContent() {
  const searchParams = useSearchParams();
  const courseId = searchParams.get("course_id") ?? "";
  const packageIdFromUrl = searchParams.get("package_id") ?? "";
  const courseName = searchParams.get("course_name") ?? "本课";
  const studyMode = normalizeStudyMode(searchParams.get("mode"));
  const device = useDevice();
  const router = useRouter();
  const pathname = usePathname();
  const [items, setItems] = useState<LearningItem[]>([]);
  const [packageCourses, setPackageCourses] = useState<Course[]>([]);
  const [resolvedPackageId, setResolvedPackageId] = useState("");
  const [currentIndex, setCurrentIndex] = useState(0);
  const [wordAnswers, setWordAnswers] = useState<string[]>([]);
  const [activeWordIndex, setActiveWordIndex] = useState(0);
  const [answerState, setAnswerState] = useState<AnswerState>("typing");
  const [wordStatuses, setWordStatuses] = useState<WordStatus[]>([]);
  const [wordErrorCounts, setWordErrorCounts] = useState<number[]>([]);
  const [previewWordIndex, setPreviewWordIndex] = useState<number | null>(null);
  const [previewMistakePracticeWord, setPreviewMistakePracticeWord] = useState(false);
  const [mistakenWords, setMistakenWords] = useState<string[]>([]);
  const [mistakePracticeWords, setMistakePracticeWords] = useState<string[]>([]);
  const [mistakePracticeIndex, setMistakePracticeIndex] = useState(0);
  const [mistakePracticeAnswer, setMistakePracticeAnswer] = useState("");
  const [mistakePracticeStatus, setMistakePracticeStatus] = useState<MistakePracticeStatus>("idle");
  const [mistakePracticeErrorCount, setMistakePracticeErrorCount] = useState(0);
  const [wordConsecutiveCorrect, setWordConsecutiveCorrect] = useState<number[]>([]);
  const [mistakePracticeConsecutiveCorrect, setMistakePracticeConsecutiveCorrect] = useState(0);
  const MISTAKE_PRACTICE_REQUIRED_CONSECUTIVE = 3;
  // Meaning-quiz sub-state: after the child spells the same word correctly
  // 3 times in a row, we show a Chinese-meaning multiple-choice question
  // for that word before advancing to the next one.
  const [mistakeMeaningQuizOptions, setMistakeMeaningQuizOptions] = useState<string[]>([]);
  const [mistakeMeaningQuizSelected, setMistakeMeaningQuizSelected] = useState<string | null>(null);
  const [mistakeMeaningQuizCorrect, setMistakeMeaningQuizCorrect] = useState<string>("");
  const buildMeaningQuizOptions = useCallback(function buildMeaningQuizOptions(correctTranslation: string): string[] {
    // Reuse the same 6-option pattern as the existing choice-review
    // tasks (see choiceReviewOptions memo above). The pool of
    // child-friendly Chinese distractors mirrors the frontend fallback
    // used for english_to_chinese / listen_choose_chinese tasks.
    const distractorPool = [
      "老师", "学生", "学校", "书", "朋友",
      "猫", "狗", "鱼", "鸟", "花", "树",
      "苹果", "香蕉", "面包", "米饭", "牛奶", "水",
      "红色", "蓝色", "绿色", "黄色", "白色", "黑色",
      "大", "小", "高", "快", "慢",
      "爸爸", "妈妈", "姐姐", "哥哥", "同学",
      "家", "公园", "商店", "医院",
      "桌子", "椅子", "门", "窗",
      "唱歌", "跳舞", "跑步", "游泳",
      "昨天", "今天", "明天", "早上", "晚上",
    ].filter((w) => w !== correctTranslation);
    // Fisher-Yates shuffle then take 5 distractors
    for (let i = distractorPool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [distractorPool[i], distractorPool[j]] = [distractorPool[j], distractorPool[i]];
    }
    const options = [correctTranslation, ...distractorPool.slice(0, 5)];
    // Shuffle the final 6 so the correct answer isn't always option #1
    for (let i = options.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [options[i], options[j]] = [options[j], options[i]];
    }
    return options;
  }, []);
  const [mistakePracticeTranslations, setMistakePracticeTranslations] = useState<Record<string, string>>({});
  const [sentenceWordMeanings, setSentenceWordMeanings] = useState<Record<number, string>>({});
  const [reviewTaskWordTranslation, setReviewTaskWordTranslation] = useState("");
  const [pendingMistakePracticeWords, setPendingMistakePracticeWords] = useState<string[]>([]);
  const [deferredDynamicWords, setDeferredDynamicWords] = useState<string[]>([]);
  const [hasFinalizedCurrentItem, setHasFinalizedCurrentItem] = useState(false);
  const [startedAt, setStartedAt] = useState<number>(() => Date.now());
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [feedbackType, setFeedbackType] = useState<"success" | "error" | "info">("info");
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const selectedChoiceRef = useRef<string | null>(null);
  const [choiceResult, setChoiceResult] = useState<"correct" | "incorrect" | null>(null);
  const choiceResultRef = useRef<"correct" | "incorrect" | null>(null);
  choiceResultRef.current = choiceResult;
  const [debugLog, setDebugLog] = useState<string[]>([]);
  const [childHint, setChildHint] = useState<ChildFriendlyHintData | null>(null);
  function setFeedback(message: string | null, type: "success" | "error" | "info" = "info") {
    setFeedbackMessage(message);
    if (message) {
      setFeedbackType(type);
    }
  }

  const [previewCountdownSeconds, setPreviewCountdownSeconds] = useState(0);
  const [celebrationTrigger, setCelebrationTrigger] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);
  const [activeStudySeconds, setActiveStudySeconds] = useState(0);
  const [isStudyPaused, setIsStudyPaused] = useState(false);
  const [isDictationMode, setIsDictationMode] = useState(false);
  // Focus mode defaults OFF — user must click the "聚焦模式" button to
  // enable it. Previously this defaulted ON for struggling learners (and
  // OFF in 'learn' mode), but the forced-on behaviour surprised users
  // who expected to see the full new-course queue first. Now the child
  // gets the full content every time and can opt into the focused
  // 7-word × 3-mode subset when they want concentrated practice.
  const [isFocusMode, setIsFocusMode] = useState(false);
  // Reset focus mode to OFF when the user navigates between study modes
  // via the switchStudyMode buttons. The lazy initializer above only runs
  // once on mount — without this effect, switching from one mode to
  // another would carry over the previous mode's focus-mode toggle.
  // We intentionally reset to OFF (not preserve the previous value) so
  // the child always starts each mode with a full queue.
  useEffect(() => {
    setIsFocusMode(false);
  }, [studyMode]);
  // AI review recommendations from the "推荐复习" button on the home page
  const isAiReview = searchParams.get("ai") === "1";
  const [aiRecommendedWords, setAiRecommendedWords] = useState<string[]>([]);
  const [aiReasoning, setAiReasoning] = useState("");
  useEffect(() => {
    if (!isAiReview) return;
    const token = getAccessToken();
    if (!token) return;
    getReviewAdvice(token).then((advice) => {
      if (advice?.recommended_words?.length > 0) {
        setAiRecommendedWords(advice.recommended_words);
        setAiReasoning(advice.reasoning || "");
      }
    }).catch(() => {});
  }, [isAiReview]);
  const [celebrationSummary, setCelebrationSummary] = useState<CelebrationSummary | null>(null);
  // Immersive (沉浸) mode defaults OFF — user must click the "沉浸模式"
  // button to enable it. Previously this defaulted ON, but the forced
  // fullscreen view hid the page chrome (course info, progress
  // indicators, exit button) which made it harder for the parent to
  // peek at progress or for the child to back out without a keyboard
  // shortcut. Now the child gets the bordered "card" view by default
  // and can opt into the full-bleed fullscreen view when they want
  // distraction-free practice.
  const [isStudyFullscreen, setIsStudyFullscreen] = useState(false);
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const mistakePracticeInputRef = useRef<HTMLInputElement | null>(null);
  // Refs for meaning-quiz state, synced in useEffect — avoids stale
  // closure in the window keydown handler.
  const mistakeMeaningQuizOptionsRef = useRef<string[]>([]);
  const mistakeMeaningQuizSelectedRef = useRef<string | null>(null);
  // Sync at render time (not useEffect) — same race as choiceOptionsRef
  mistakeMeaningQuizOptionsRef.current = mistakeMeaningQuizOptions;
  mistakeMeaningQuizSelectedRef.current = mistakeMeaningQuizSelected;
  const activeStudyMsRef = useRef(0);
  const pendingStudyMsRef = useRef(0);
  const courseRunStartedActiveMsRef = useRef(0);
  const courseRunCorrectWordKeysRef = useRef<Set<string>>(new Set());
  const lastStudyTickAtRef = useRef(Date.now());
  const lastStudyActivityAtRef = useRef(Date.now());
  const isStudyPausedRef = useRef(false);
  const isDictationModeRef = useRef(false);
  const isFlushingStudyTimeRef = useRef(false);
  const voiceSequenceRef = useRef(0);
  const modelSettingsRef = useRef<ModelSettings>(defaultModelSettings);
  const answerStateRef = useRef<AnswerState>("typing");
  const isCompletingSentenceRef = useRef(false);
  const pendingMistakePracticeWordsRef = useRef<string[]>([]);
  const pendingMistakePracticeTranslationsRef = useRef<Record<string, string>>({});
  const spaceCooldownRef = useRef(0);
  const queuedReviewItemIdsRef = useRef<Set<string>>(new Set());
  const choiceOptionsRef = useRef<string[]>([]);
  const currentIndexRef = useRef(0);
  const currentItemRef = useRef<LearningItem | null>(null);
  const celebrationSummaryRef = useRef<CelebrationSummary | null>(null);
  const wordPreviewTimeoutRef = useRef<number | null>(null);
  const mistakePracticePreviewTimeoutRef = useRef<number | null>(null);
  const previewCountdownIntervalRef = useRef<number | null>(null);
  const respellWordIndexesRef = useRef<number[]>([]);
  const encodingStageRef = useRef<EncodingStage | null>(null);
  const encodingStartTimeRef = useRef(0);
  const encodingStageStartTimeRef = useRef(0);
  const encodingStageTimeoutRef = useRef<number | null>(null);
  const wordMeaningReviewTimeoutRef = useRef<number | null>(null);

  const [encodingStage, setEncodingStage] = useState<EncodingStage | null>(null);
  const [encodingWord, setEncodingWord] = useState("");
  const [, setEncodingChineseText] = useState("");

  const [currentStreak, setCurrentStreak] = useState(0);
  const [correctWordCount, setCorrectWordCount] = useState(0);
  const [perfectSentenceCount, setPerfectSentenceCount] = useState(0);
  const [wordAnimations, setWordAnimations] = useState<Record<number, { type: "correct" | "incorrect"; key: number }>>({});
  const [streakCelebrateKey, setStreakCelebrateKey] = useState(0);
  const [milestoneCelebrateKey, setMilestoneCelebrateKey] = useState(0);
  const [starAnimKey, setStarAnimKey] = useState(0);
  const feedbackSetAtRef = useRef<number>(0);
  const wordAnimKeyRef = useRef(0);


  // Focus mode rotation tracking
  const focusCorrectCountRef = useRef<Map<string, number>>(new Map());
  const [focusRotatedMessage, setFocusRotatedMessage] = useState<string | null>(null);



  // P2-2: Milestone tracking
  const [milestoneMessage, setMilestoneMessage] = useState<string | null>(null);
  const totalMasteredRef = useRef(0);

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
  const isChoiceReviewTask = currentItem?.review_task_type === "listen_choose_chinese" || currentItem?.review_task_type === "english_to_chinese" || currentItem?.review_task_type === "match_translation";
  const isListeningChoiceReviewTask = currentItem?.review_task_type === "listen_choose_chinese";
  const isSingleWordReviewTask = Boolean(currentItem?.review_task_type && currentItem.review_task_type !== "cloze_sentence");
  const isSpellingReviewTask = Boolean(currentItem?.review_task_type && SPELLING_REVIEW_TASK_TYPES.has(currentItem.review_task_type));
  const isSentenceCloze = currentItem?.review_task_type === "cloze_sentence";
  const currentFocusedReviewWord = dynamicReviewWords[0] ?? (isSingleWordReviewTask || currentItem?.item_type === "word" ? currentWords[0] : "");
  const shouldUseSingleWordPrompt = Boolean(!isChoiceReviewTask && (isSingleWordReviewTask || currentItem?.item_type === "word"));
  const isFocusedWordReview = Boolean(shouldUseSingleWordPrompt && currentFocusedReviewWord);
  const choiceReviewOptions = useMemo(() => {
    if (!currentItem || !isChoiceReviewTask) {
      return [];
    }
    const backendAnswer = hasChineseText(currentItem.review_answer) ? currentItem.review_answer : "";
    const fallbackAnswer = hasChineseText(reviewTaskWordTranslation) ? reviewTaskWordTranslation : "";
    const correctAnswer = backendAnswer || fallbackAnswer;
    const fallbackChoices = (currentItem.review_choices && currentItem.review_choices.length > 0 ? currentItem.review_choices : [])
      .filter((choice) => hasChineseText(choice));
    const choicesWithAnswer = correctAnswer && !fallbackChoices.some((choice) => isCorrectChoiceAnswer(choice, correctAnswer))
      ? [correctAnswer, ...fallbackChoices]
      : fallbackChoices;
    // Prefer backend-provided choices (4+ items) over frontend 3-item fallback
    if (choicesWithAnswer.length >= 4) {
      return rotateChoices(choicesWithAnswer, `${currentItem.id}-${currentFocusedReviewWord}`);
    }
    const translatedChoices = correctAnswer ? [correctAnswer, "老师", "学生", "学校", "书", "朋友"].filter((choice, index, values) => values.indexOf(choice) === index) : [];
    return rotateChoices(translatedChoices.length > 0 ? translatedChoices : choicesWithAnswer, `${currentItem.id}-${currentFocusedReviewWord}`);
  }, [currentFocusedReviewWord, currentItem, isChoiceReviewTask, reviewTaskWordTranslation]);
  // Sync the ref at RENDER time (not in useEffect) so the keydown
  // handler sees the current options on the VERY FIRST keypress.
  // The useEffect approach ran AFTER render — the first keypress
  // after an item change saw an empty array and was silently ignored.
  choiceOptionsRef.current = choiceReviewOptions;
  // Mirror selectedChoice into a ref so the keydown handler can read
  // the latest value without waiting for React to flush the state
  // update. setSelectedChoice() is async — without this ref, the
  // space handler sees the OLD value (null) and returns early.
  selectedChoiceRef.current = selectedChoice;

  const reviewTaskInstruction = buildReviewTaskInstruction(currentItem?.review_task_type, currentFocusedReviewWord);
  const reviewTaskModeLabel = getReviewTaskModeLabel(currentItem?.review_task_type);
  const progressPercent = items.length > 0 ? ((currentIndex + 1) / items.length) * 100 : 0;
  const packageId = packageIdFromUrl || resolvedPackageId;
  const nextCourse = useMemo<NextCourseTarget | null>(() => {
    if (!packageId || packageCourses.length === 0) {
      return null;
    }

    const currentCourseIndex = packageCourses.findIndex((course) => course.id === courseId);
    if (currentCourseIndex < 0) {
      return null;
    }

    const nextCourseIndex = (currentCourseIndex + 1) % packageCourses.length;
    const targetCourse = packageCourses[nextCourseIndex];
    // Check if next course has a prerequisite — lock status will be refined via API call
    const hasPrerequisite = Boolean(targetCourse.prerequisite_course_id);
    return {
      id: targetCourse.id,
      name: targetCourse.name,
      packageId,
      isLocked: hasPrerequisite,
    };
  }, [courseId, packageCourses, packageId]);

  useEffect(() => {
    setModelSettings(getModelSettings());
    void loadPersistedModelSettings().then(setModelSettings);
  }, []);

  useEffect(() => {
    modelSettingsRef.current = modelSettings;
  }, [modelSettings]);

  useEffect(() => {
    isDictationModeRef.current = isDictationMode;
  }, [isDictationMode]);

  useEffect(() => {
    currentIndexRef.current = currentIndex;
    currentItemRef.current = currentItem;
    celebrationSummaryRef.current = celebrationSummary;
  }, [celebrationSummary, currentIndex, currentItem]);

  useEffect(() => {
    answerStateRef.current = answerState;
  }, [answerState]);

  useEffect(() => {
    if (feedbackMessage) {
      feedbackSetAtRef.current = Date.now();
    }
  }, [feedbackMessage]);

  useEffect(() => {
    if (correctWordCount > 0 && correctWordCount % 5 === 0) {
      setMilestoneCelebrateKey((k) => k + 1);
    }
  }, [correctWordCount]);

  useEffect(() => {
    if (currentStreak > 0 && [5, 10, 15].includes(currentStreak)) {
      setStreakCelebrateKey((k) => k + 1);
      playLevelUpSound();
    }
  }, [currentStreak]);

  const updateAnswerState = useCallback(function updateAnswerState(nextState: AnswerState) {
    answerStateRef.current = nextState;
    setAnswerState(nextState);
  }, []);

  function clearWordMeaningReview() {
    if (wordMeaningReviewTimeoutRef.current !== null) {
      window.clearTimeout(wordMeaningReviewTimeoutRef.current);
      wordMeaningReviewTimeoutRef.current = null;
    }
    setSentenceWordMeanings({});
  }

  const clearWordPreview = useCallback(function clearWordPreview() {
    if (wordPreviewTimeoutRef.current !== null) {
      window.clearTimeout(wordPreviewTimeoutRef.current);
      wordPreviewTimeoutRef.current = null;
    }
    if (previewCountdownIntervalRef.current !== null) {
      window.clearInterval(previewCountdownIntervalRef.current);
      previewCountdownIntervalRef.current = null;
    }
    setPreviewCountdownSeconds(0);
    setPreviewWordIndex(null);
  }, []);

  const clearMistakePracticePreview = useCallback(function clearMistakePracticePreview() {
    if (mistakePracticePreviewTimeoutRef.current !== null) {
      window.clearTimeout(mistakePracticePreviewTimeoutRef.current);
      mistakePracticePreviewTimeoutRef.current = null;
    }
    setPreviewMistakePracticeWord(false);
  }, []);

  const showWordPreview = useCallback(function showWordPreview(index: number, expectedWord: string, chineseMeaning = "") {
    clearWordPreview();
    setPreviewWordIndex(index);
    setWordAnswers((current) => {
      const nextAnswers = [...current];
      nextAnswers[index] = "";
      return nextAnswers;
    });

    // Start countdown bar
    const previewSeconds = Math.round(WORD_PREVIEW_MS / 1000);
    setPreviewCountdownSeconds(previewSeconds);
    if (previewCountdownIntervalRef.current !== null) {
      window.clearInterval(previewCountdownIntervalRef.current);
    }
    const startTime = Date.now();
    previewCountdownIntervalRef.current = window.setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, previewSeconds - Math.floor(elapsed / 1000));
      setPreviewCountdownSeconds(remaining);
      if (remaining <= 0) {
        if (previewCountdownIntervalRef.current !== null) {
          window.clearInterval(previewCountdownIntervalRef.current);
          previewCountdownIntervalRef.current = null;
        }
      }
    }, 200);

    // Play slow audio of the word
    const settings = modelSettingsRef.current;
    const speechText = buildFocusedWordSpeechText(expectedWord);
    if (speechText) {
      voiceSequenceRef.current += 1;
      stopAudioPlayback();
      const previewSequenceId = voiceSequenceRef.current;
      void speakWithKokoro(
        speechText,
        settings.ttsEnglishVoice,
        "en-US",
        settings,
        SLOW_ENGLISH_TTS_OPTIONS,
        () => voiceSequenceRef.current === previewSequenceId,
      );
    }

    setFeedback(chineseMeaning
      ? `记住它！还剩 ${previewSeconds} 秒：${expectedWord}。中文意思：${chineseMeaning}`
      : `记住它！还剩 ${previewSeconds} 秒：${expectedWord}`);
    wordPreviewTimeoutRef.current = window.setTimeout(() => {
      wordPreviewTimeoutRef.current = null;
      if (previewCountdownIntervalRef.current !== null) {
        window.clearInterval(previewCountdownIntervalRef.current);
        previewCountdownIntervalRef.current = null;
      }
      setPreviewCountdownSeconds(0);
      setPreviewWordIndex(null);
      setWordStatuses((current) => {
        const nextStatuses = [...current];
        if (nextStatuses[index] === "incorrect") {
          nextStatuses[index] = "idle";
        }
        return nextStatuses;
      });
      setFeedback(chineseMeaning ? `现在请凭记忆重新拼写这个单词。中文意思：${chineseMeaning}` : "现在请凭记忆重新拼写这个单词。");
      window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
    }, WORD_PREVIEW_MS);
  }, [clearWordPreview]);

  const showMistakePracticePreview = useCallback(function showMistakePracticePreview(expectedWord: string) {
    clearMistakePracticePreview();
    setPreviewMistakePracticeWord(true);
    setMistakePracticeAnswer("");
    setFeedback(`看清这个单词 ${Math.round(WORD_PREVIEW_MS / 1000)} 秒：${expectedWord}。它会自动隐藏，然后请凭记忆完整拼写。`);
    mistakePracticePreviewTimeoutRef.current = window.setTimeout(() => {
      mistakePracticePreviewTimeoutRef.current = null;
      setPreviewMistakePracticeWord(false);
      setMistakePracticeStatus("idle");
      setFeedback("现在请凭记忆重新拼写这个错词。");
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
    }, WORD_PREVIEW_MS);
  }, [clearMistakePracticePreview]);

  const clearEncodingTimeout = useCallback(function clearEncodingTimeout() {
    if (encodingStageTimeoutRef.current !== null) {
      window.clearTimeout(encodingStageTimeoutRef.current);
      encodingStageTimeoutRef.current = null;
    }
  }, []);

  const startVoiceSequence = useCallback(function startVoiceSequence(): number {
    voiceSequenceRef.current += 1;
    stopAudioPlayback();
    return voiceSequenceRef.current;
  }, []);

  const isCurrentVoiceSequence = useCallback(function isCurrentVoiceSequence(sequenceId: number): boolean {
    return voiceSequenceRef.current === sequenceId;
  }, []);

  const waitInVoiceSequence = useCallback(async function waitInVoiceSequence(sequenceId: number, ms: number): Promise<boolean> {
    await wait(ms);
    return isCurrentVoiceSequence(sequenceId);
  }, [isCurrentVoiceSequence]);

  const speakInVoiceSequence = useCallback(async function speakInVoiceSequence(
    sequenceId: number,
    text: string,
    voice: string,
    language: string,
    settings: ModelSettings,
    options: TtsSynthesisOptions = {},
  ): Promise<boolean> {
    if (!isCurrentVoiceSequence(sequenceId)) {
      return false;
    }
    await speakWithKokoro(text, voice, language, settings, options, () => isCurrentVoiceSequence(sequenceId));
    return isCurrentVoiceSequence(sequenceId);
  }, [isCurrentVoiceSequence]);

  const speakClearEnglishSentence = useCallback(async function speakClearEnglishSentence(
    sequenceId: number,
    englishText: string,
    settings: ModelSettings,
    includeNaturalRead = true,
  ): Promise<boolean> {
    const cleanText = englishText.trim();
    if (!cleanText) {
      return false;
    }

    if (includeNaturalRead) {
      if (!(await speakInVoiceSequence(sequenceId, cleanText, settings.ttsEnglishVoice, "en-US", settings, NATURAL_ENGLISH_TTS_OPTIONS))) {
        return false;
      }
      if (!(await waitInVoiceSequence(sequenceId, 900))) {
        return false;
      }
    }

    const chunkedText = buildChunkedEnglishSpeechText(cleanText);
    if (!(await speakInVoiceSequence(sequenceId, chunkedText, settings.ttsEnglishVoice, "en-US", settings, SLOW_ENGLISH_TTS_OPTIONS))) {
      return false;
    }
    if (!(await waitInVoiceSequence(sequenceId, 900))) {
      return false;
    }

    const wordByWordText = buildWordByWordEnglishSpeechText(cleanText);
    if (wordByWordText && wordByWordText !== chunkedText) {
      return speakInVoiceSequence(sequenceId, wordByWordText, settings.ttsEnglishVoice, "en-US", settings, WORD_ENGLISH_TTS_OPTIONS);
    }

    return true;
  }, [speakInVoiceSequence, waitInVoiceSequence]);

  const speakFocusedEnglishWord = useCallback(async function speakFocusedEnglishWord(
    sequenceId: number,
    word: string,
    settings: ModelSettings,
  ): Promise<boolean> {
    const speechText = buildFocusedWordSpeechText(word);
    if (!speechText) {
      return false;
    }
    return speakInVoiceSequence(sequenceId, speechText, settings.ttsEnglishVoice, "en-US", settings, WORD_ENGLISH_TTS_OPTIONS);
  }, [speakInVoiceSequence]);

  const speakClearEnglish = useCallback(async function speakClearEnglish(
    sequenceId: number,
    englishText: string,
    settings: ModelSettings,
    includeNaturalRead = true,
  ): Promise<boolean> {
    return tokenizeEnglish(englishText).length <= 1
      ? speakFocusedEnglishWord(sequenceId, englishText, settings)
      : speakClearEnglishSentence(sequenceId, englishText, settings, includeNaturalRead);
  }, [speakClearEnglishSentence, speakFocusedEnglishWord]);

  const advanceEncodingStage = useCallback(async function advanceEncodingStage(stage: EncodingStage, word: string, chineseText: string) {
    const settings = modelSettingsRef.current;
    const sequenceId = startVoiceSequence();
    let nextStage: EncodingStage | null = null;
    if (stage === "meaning_intro") { nextStage = "listen"; }
    else if (stage === "listen") { nextStage = "chunk_trace"; }
    else if (stage === "chunk_trace") { nextStage = "whole_recall"; }
    else if (stage === "whole_recall") { nextStage = null; }

    encodingStageStartTimeRef.current = Date.now();
    encodingStageRef.current = nextStage;
    setEncodingStage(nextStage);
    clearEncodingTimeout();

    if (!nextStage) {
      encodingStartTimeRef.current = 0;
      setEncodingWord("");
      setEncodingChineseText("");
      updateAnswerState("sentence-complete");
      setFeedback("记忆编码完成！点击「下一题」按钮继续。", "success");
      return;
    }

    if (nextStage === "listen") {
      const syllables = (currentItem?.syllables && currentItem.syllables.length > 0)
        ? currentItem.syllables
        : splitIntoSyllables(word);
      setFeedback("仔细听，这个单词分为 " + syllables.length + " 个音节：" + syllables.join(" — "));
      if (!isDictationModeRef.current) {
        const wordByWord = buildWordByWordEnglishSpeechText(word);
        await speakInVoiceSequence(sequenceId, wordByWord, settings.ttsEnglishVoice, "en-US", settings, SLOW_ENGLISH_TTS_OPTIONS);
      }
      encodingStageTimeoutRef.current = window.setTimeout(function () {
        void advanceEncodingStage("listen", word, chineseText);
      }, 3000);
      return;
    }

    if (nextStage === "chunk_trace") {
      const graphemeMap = currentItem?.grapheme_phoneme_map ?? null;
      if (graphemeMap) {
        const hintParts: string[] = [];
        const entries = Object.entries(graphemeMap);
        for (let i = 0; i < entries.length; i++) {
          hintParts.push(entries[i][0] + "=" + entries[i][1]);
        }
        setFeedback("点击每个颜色块听发音。拼读提示：" + hintParts.join("，"));
      } else {
        setFeedback("点击每个颜色块听发音，也可以按字母逐音朗读。");
      }
      encodingStageTimeoutRef.current = window.setTimeout(function () {
        void advanceEncodingStage("chunk_trace", word, chineseText);
      }, 6000);
      return;
    }

    if (nextStage === "whole_recall") {
      const previewMs = getAdaptivePreviewDuration(word);
      const previewSeconds = Math.round(previewMs / 1000);
      setFeedback("记住它！还剩 " + previewSeconds + " 秒：" + word);
      setPreviewCountdownSeconds(previewSeconds);
      if (previewCountdownIntervalRef.current !== null) {
        window.clearInterval(previewCountdownIntervalRef.current);
      }
      const intervalStart = Date.now();
      previewCountdownIntervalRef.current = window.setInterval(function () {
        const elapsed = Date.now() - intervalStart;
        const remaining = Math.max(0, previewSeconds - Math.floor(elapsed / 1000));
        setPreviewCountdownSeconds(remaining);
        if (remaining <= 0) {
          if (previewCountdownIntervalRef.current !== null) {
            window.clearInterval(previewCountdownIntervalRef.current);
            previewCountdownIntervalRef.current = null;
          }
        }
      }, 200);
      const speechText = buildFocusedWordSpeechText(word);
      if (speechText && !isDictationModeRef.current) {
        void speakInVoiceSequence(sequenceId, speechText, settings.ttsEnglishVoice, "en-US", settings, SLOW_ENGLISH_TTS_OPTIONS);
      }
      encodingStageTimeoutRef.current = window.setTimeout(function () {
        encodingStageTimeoutRef.current = null;
        if (previewCountdownIntervalRef.current !== null) {
          window.clearInterval(previewCountdownIntervalRef.current);
          previewCountdownIntervalRef.current = null;
        }
        setPreviewCountdownSeconds(0);
        setFeedback("现在请凭记忆拼写这个单词。");
        setWordAnswers(function (current) {
          const nextAnswers = current.slice();
          nextAnswers[0] = "";
          return nextAnswers;
        });
        window.setTimeout(function () { const ref = inputRefs.current[0]; if (ref) { ref.focus(); } }, 0);
      }, previewMs);
      return;
    }

  }, [clearEncodingTimeout, currentItem, speakClearEnglish, speakInVoiceSequence, startVoiceSequence, updateAnswerState, waitInVoiceSequence]);

  const startEncodingFn = useCallback(async function startEncoding(word: string, chineseText: string) {
    const now = Date.now();
    encodingStartTimeRef.current = now;
    encodingStageStartTimeRef.current = now;
    setEncodingStage("meaning_intro");
    setEncodingWord(word);
    setEncodingChineseText(chineseText);
    setPreviewCountdownSeconds(0);
    clearEncodingTimeout();
    clearWordPreview();
    encodingStageRef.current = "meaning_intro";

    setFeedback("先听中文意思和英文发音，不要看拼写。中文：" + chineseText);

    const settings = modelSettingsRef.current;
    const sequenceId = startVoiceSequence();

    if (!isDictationModeRef.current) {
      await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings);
      if (!(await waitInVoiceSequence(sequenceId, 800))) return;
      const speechText = buildFocusedWordSpeechText(word);
      if (speechText) {
        await speakInVoiceSequence(sequenceId, speechText, settings.ttsEnglishVoice, "en-US", settings, SLOW_ENGLISH_TTS_OPTIONS);
        if (!(await waitInVoiceSequence(sequenceId, 600))) return;
        await speakInVoiceSequence(sequenceId, speechText, settings.ttsEnglishVoice, "en-US", settings, SLOW_ENGLISH_TTS_OPTIONS);
      }
    }

    encodingStageTimeoutRef.current = window.setTimeout(function () {
      void advanceEncodingStage("meaning_intro", word, chineseText);
    }, 1500);
  }, [advanceEncodingStage, clearEncodingTimeout, clearWordPreview, speakInVoiceSequence, startVoiceSequence, waitInVoiceSequence]);

  useEffect(() => {
    return () => {
      if (wordPreviewTimeoutRef.current !== null) {
        window.clearTimeout(wordPreviewTimeoutRef.current);
      }
      if (mistakePracticePreviewTimeoutRef.current !== null) {
        window.clearTimeout(mistakePracticePreviewTimeoutRef.current);
      }
      if (previewCountdownIntervalRef.current !== null) {
        window.clearInterval(previewCountdownIntervalRef.current);
      }
      if (encodingStageTimeoutRef.current !== null) {
        window.clearTimeout(encodingStageTimeoutRef.current);
      }
    };
  }, []);

  const setPendingMistakePractice = useCallback(function setPendingMistakePractice(words: string[], translations: Record<string, string>) {
    pendingMistakePracticeWordsRef.current = words;
    pendingMistakePracticeTranslationsRef.current = translations;
    setPendingMistakePracticeWords(words);
  }, []);

  const getRequiredWordIndexes = useCallback(function getRequiredWordIndexes(): number[] {
    if (respellWordIndexesRef.current.length > 0) return respellWordIndexesRef.current;
    return isDynamicFillBlankItem ? dynamicReviewWordIndexes : currentWords.map((_, index) => index);
  }, [currentWords, dynamicReviewWordIndexes, isDynamicFillBlankItem]);

  function areRequiredWordAnswersComplete(nextAnswers: string[], nextStatuses = wordStatuses): boolean {
    return getRequiredWordIndexes().every((wordIndex) => (
      nextStatuses[wordIndex] === "skipped"
      || normalizeTypedWord(nextAnswers[wordIndex] ?? "") === normalizeTypedWord(currentWords[wordIndex] ?? "")
    ));
  }

  const playCurrentItemIntro = useCallback(async function playCurrentItemIntro(item: LearningItem, sequenceId: number) {
    const settings = modelSettingsRef.current;
    let chineseText = item.chinese_text;
    let englishText = item.english_text;
    const focusedWords = getDynamicReviewWords(item);
    const isChoiceIntroTask = item.review_task_type === "listen_choose_chinese" || item.review_task_type === "english_to_chinese" || item.review_task_type === "match_translation";
    const isListenSpellTask = item.review_task_type === "listen_spell";
    const shouldUseSingleWordIntro = Boolean(!isChoiceIntroTask && ((item.review_task_type && item.review_task_type !== "cloze_sentence") || item.item_type === "word"));
    const focusedWord = (isChoiceIntroTask || shouldUseSingleWordIntro) ? focusedWords[0] ?? tokenizeEnglish(item.english_text)[0] ?? "" : "";
    if (isListenSpellTask && focusedWord) {
      // Focus mode items have pre-cached Chinese meanings in chinese_text
      const hasCachedMeaning = item.focus_words && item.focus_words.length > 0 && hasChineseText(item.chinese_text);
      if (hasCachedMeaning) {
        chineseText = item.chinese_text;
        setReviewTaskWordTranslation(item.chinese_text);
      } else {
        chineseText = "听英文发音后拼写";
        setReviewTaskWordTranslation("");
      }
      englishText = focusedWord;
    } else if (isChoiceIntroTask && focusedWord) {
      chineseText = item.review_task_type === "listen_choose_chinese" ? "听英文发音，选择中文意思" : "请选择这个英文单词的中文意思";
      englishText = focusedWord;
      try {
        const accessToken = getAccessToken();
        if (accessToken) {
          const response = await translateLearningText(focusedWord, accessToken, modelSettingsRef.current);
          setReviewTaskWordTranslation(response.chinese_text);
        }
      } catch {
        setReviewTaskWordTranslation("");
      }
    } else if (shouldUseSingleWordIntro && focusedWord) {
      // Focus mode items have pre-cached Chinese meanings in chinese_text
      const hasCachedMeaning = item.focus_words && item.focus_words.length > 0 && hasChineseText(item.chinese_text);
      if (hasCachedMeaning) {
        chineseText = item.chinese_text;
        setReviewTaskWordTranslation(item.chinese_text);
        englishText = focusedWord;
      } else {
      try {
        const accessToken = getAccessToken();
        if (accessToken) {
          const response = await translateLearningText(focusedWord, accessToken, modelSettingsRef.current);
          chineseText = response.chinese_text;
          setReviewTaskWordTranslation(response.chinese_text);
          englishText = focusedWord;
        } else {
          chineseText = "";
          englishText = focusedWord;
        }
      } catch {
        chineseText = "";
        englishText = focusedWord;
      }
      }
    }

    if (isDictationModeRef.current) {
      return;
    }

    // P1-6: Auto-skip items without a valid Chinese translation.
    // No child should ever see a word without Chinese context.
    if (shouldUseSingleWordIntro && focusedWord && !hasChineseText(chineseText)) {
      handleNextItem({ completedCurrentItem: true });
      return;
    }

    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    if (item.review_task_type === "listen_choose_chinese" || item.review_task_type === "listen_spell") {
      // Focus mode: speak Chinese meaning first, then English
      const isFocusListenSpell = item.review_task_type === "listen_spell" && item.focus_words && item.focus_words.length > 0 && hasChineseText(chineseText);
      if (isFocusListenSpell) {
        if (!(await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings))) {
          return;
        }
        if (!(await waitInVoiceSequence(sequenceId, 600))) {
          return;
        }
      }
      await speakClearEnglish(sequenceId, englishText, settings, false);
      return;
    }
    if (!(await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    await speakClearEnglish(sequenceId, englishText, settings, true);
  }, [speakClearEnglish, speakInVoiceSequence, waitInVoiceSequence]);

  const playChineseThenEnglish = useCallback(async function playChineseThenEnglish(chineseText: string, englishText: string, sequenceId = startVoiceSequence()) {
    if (isDictationModeRef.current) {
      return;
    }
    const settings = modelSettingsRef.current;
    if (!(await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    await speakClearEnglish(sequenceId, englishText, settings, true);
  }, [speakClearEnglish, speakInVoiceSequence, startVoiceSequence, waitInVoiceSequence]);

  const playEnglishThenChinese = useCallback(async function playEnglishThenChinese(englishText: string, chineseText: string, sequenceId = startVoiceSequence()) {
    if (isDictationModeRef.current) {
      return;
    }
    const settings = modelSettingsRef.current;
    if (!(await speakClearEnglish(sequenceId, englishText, settings, false))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 700))) {
      return;
    }
    if (chineseText.trim()) {
      await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings);
    }
  }, [speakClearEnglish, speakInVoiceSequence, startVoiceSequence, waitInVoiceSequence]);

  const focusInputAtEnd = useCallback(function focusInputAtEnd(input: HTMLInputElement | null) {
    if (!input) {
      return;
    }
    if (input.disabled || input.readOnly || document.hidden) {
      return;
    }
    input.focus();
    const cursorPosition = input.value.length;
    input.setSelectionRange(cursorPosition, cursorPosition);
  }, []);

  const canKeepStudyInputFocused = useCallback(function canKeepStudyInputFocused() {
    if (!currentItem || isChoiceReviewTask || celebrationSummary) {
      return false;
    }
    if (answerStateRef.current === "mistake-word-practice") {
      return !previewMistakePracticeWord;
    }
    if (answerStateRef.current !== "typing" || isCompletingSentenceRef.current) {
      return false;
    }
    if (previewWordIndex !== null) {
      return false;
    }
    return true;
  }, [celebrationSummary, currentItem, isChoiceReviewTask, previewMistakePracticeWord, previewWordIndex]);

  const focusCurrentUnfinishedWord = useCallback(function focusCurrentUnfinishedWord() {
    if (!canKeepStudyInputFocused()) {
      return;
    }
    if (answerStateRef.current === "mistake-word-practice") {
      focusInputAtEnd(mistakePracticeInputRef.current);
      return;
    }
    if (answerStateRef.current !== "typing") {
      return;
    }

    const requiredIndexes = getRequiredWordIndexes();
    const firstUnfinished = requiredIndexes.find((index) => {
      const inputValue = inputRefs.current[index]?.value ?? wordAnswers[index] ?? "";
      return normalizeTypedWord(inputValue) !== normalizeTypedWord(currentWords[index] ?? "");
    });
    const targetIndex = firstUnfinished ?? activeWordIndex;
    focusInputAtEnd(inputRefs.current[targetIndex] ?? null);
  }, [activeWordIndex, canKeepStudyInputFocused, currentWords, focusInputAtEnd, getRequiredWordIndexes, wordAnswers]);

  const scheduleStudyFocusReturn = useCallback(function scheduleStudyFocusReturn() {
    window.setTimeout(focusCurrentUnfinishedWord, 0);
    window.setTimeout(focusCurrentUnfinishedWord, 80);
    window.setTimeout(focusCurrentUnfinishedWord, 250);
  }, [focusCurrentUnfinishedWord]);

  useEffect(() => {
    if (!isSpellingReviewTask || answerState !== "typing") {
      return;
    }
    const targetIndex = dynamicReviewWordIndexes[0] ?? 0;
    setActiveWordIndex(targetIndex);
    const timers = [0, 120, 360, 900].map((delay) => window.setTimeout(() => {
      focusInputAtEnd(inputRefs.current[targetIndex] ?? null);
    }, delay));
    return () => {
      timers.forEach((timerId) => window.clearTimeout(timerId));
    };
  }, [answerState, currentItem?.id, dynamicReviewWordIndexes, focusInputAtEnd, isSpellingReviewTask]);

  function keepStudyInputFocus(event: MouseEvent<HTMLElement>) {
    event.preventDefault();
    scheduleStudyFocusReturn();
  }

  useEffect(() => {
    if (!currentItem || isChoiceReviewTask) {
      return;
    }

    const isCurrentStudyInput = (element: Element | null) => {
      if (!element) {
        return false;
      }
      if (element === mistakePracticeInputRef.current) {
        return true;
      }
      return inputRefs.current.some((input) => input === element);
    };

    const returnFocusIfNeeded = () => {
      if (!canKeepStudyInputFocused()) {
        return;
      }
      if (!isCurrentStudyInput(document.activeElement)) {
        scheduleStudyFocusReturn();
      }
    };

    const handleVisibilityChange = () => {
      if (!document.hidden) {
        scheduleStudyFocusReturn();
      }
    };

    document.addEventListener("focusout", returnFocusIfNeeded, true);
    window.addEventListener("pointerup", returnFocusIfNeeded, true);
    window.addEventListener("keyup", returnFocusIfNeeded, true);
    window.addEventListener("focus", returnFocusIfNeeded);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    const intervalId = window.setInterval(returnFocusIfNeeded, 1200);
    scheduleStudyFocusReturn();

    return () => {
      document.removeEventListener("focusout", returnFocusIfNeeded, true);
      window.removeEventListener("pointerup", returnFocusIfNeeded, true);
      window.removeEventListener("keyup", returnFocusIfNeeded, true);
      window.removeEventListener("focus", returnFocusIfNeeded);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.clearInterval(intervalId);
    };
  }, [canKeepStudyInputFocused, currentItem, isChoiceReviewTask, scheduleStudyFocusReturn]);

  const handleSpeakEnglish = useCallback(function handleSpeakEnglish() {
    const textToSpeak = answerStateRef.current === "mistake-word-practice" ? currentMistakePracticeWord : currentItem?.english_text;
    if (!textToSpeak) {
      return;
    }
    scheduleStudyFocusReturn();
    const sequenceId = startVoiceSequence();
    const settings = modelSettingsRef.current;
    const focusedTranslation = answerStateRef.current === "mistake-word-practice"
      ? currentMistakePracticeTranslation
      : isFocusedWordReview
        ? reviewTaskWordTranslation
        : "";
    const speechPromise = focusedTranslation
      ? playEnglishThenChinese(textToSpeak, focusedTranslation, sequenceId)
      : speakClearEnglish(sequenceId, textToSpeak, settings, false);
    void speechPromise.finally(() => {
      scheduleStudyFocusReturn();
    });
  }, [currentItem, currentMistakePracticeTranslation, currentMistakePracticeWord, isFocusedWordReview, playEnglishThenChinese, reviewTaskWordTranslation, scheduleStudyFocusReturn, speakClearEnglish, startVoiceSequence]);

  function runStudyButtonAction(action: () => void | Promise<unknown>) {
    const result = action();
    scheduleStudyFocusReturn();
    if (result && typeof (result as Promise<unknown>).finally === "function") {
      void (result as Promise<unknown>).finally(() => {
        scheduleStudyFocusReturn();
      });
    }
  }

  function toggleStudyFullscreen() {
    setIsStudyFullscreen((current) => !current);
  }

  function toggleDictationMode() {
    setIsDictationMode((current) => !current);
  }

  function switchStudyMode(targetMode: StudyMode) {
    if (targetMode === studyMode) {
      return;
    }
    const params = new URLSearchParams(searchParams.toString());
    if (targetMode === "mix") {
      params.delete("mode");
    } else {
      params.set("mode", targetMode);
    }
    if (targetMode === "review") {
      // Global review — drop course-specific params so backend returns reviews from all courses.
      params.delete("course_id");
      params.delete("package_id");
      params.delete("course_name");
    } else if (targetMode === "learn") {
      // Pure new-content learning — make sure a course is selected.
      if (!courseId) {
        router.push("/learning");
        return;
      }
    }
    const queryString = params.toString();
    router.push(queryString ? `${pathname}?${queryString}` : pathname);
  }

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
    courseRunStartedActiveMsRef.current = 0;
    courseRunCorrectWordKeysRef.current = new Set();
    lastStudyTickAtRef.current = Date.now();
    lastStudyActivityAtRef.current = Date.now();
    isStudyPausedRef.current = false;
    setActiveStudySeconds(0);
    setIsStudyPaused(false);
    setCelebrationSummary(null);
  }, [courseId]);

  useEffect(() => {
    function markStudyActivity() {
      const now = Date.now();
      lastStudyActivityAtRef.current = now;
      if (isStudyPausedRef.current) {
        isStudyPausedRef.current = false;
        setIsStudyPaused(false);
      }
    }

    function tickStudyTime() {
      const now = Date.now();
      const elapsedMs = Math.max(0, now - lastStudyTickAtRef.current);
      lastStudyTickAtRef.current = now;
      if (now - lastStudyActivityAtRef.current > STUDY_IDLE_TIMEOUT_MS) {
        if (!isStudyPausedRef.current) {
          isStudyPausedRef.current = true;
          setIsStudyPaused(true);
        }
        return;
      }

      if (isStudyPausedRef.current) {
        isStudyPausedRef.current = false;
        setIsStudyPaused(false);
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

    window.addEventListener("keydown", markStudyActivity);
    window.addEventListener("pointerdown", markStudyActivity, true);
    window.addEventListener("touchstart", markStudyActivity, true);
    window.addEventListener("pagehide", flushWhenLeaving);
    document.addEventListener("visibilitychange", flushWhenLeaving);
    const intervalId = window.setInterval(tickStudyTime, 1000);

    return () => {
      window.removeEventListener("keydown", markStudyActivity);
      window.removeEventListener("pointerdown", markStudyActivity, true);
      window.removeEventListener("touchstart", markStudyActivity, true);
      window.removeEventListener("pagehide", flushWhenLeaving);
      document.removeEventListener("visibilitychange", flushWhenLeaving);
      window.clearInterval(intervalId);
      void flushStudyTime(true);
    };
  }, [flushStudyTime]);

  useEffect(() => {
    setWordAnswers(currentWords.map(() => ""));
    setWordStatuses(currentWords.map(() => "idle"));
    setWordErrorCounts(currentWords.map(() => 0));
    setWordConsecutiveCorrect(currentWords.map(() => 0));
    clearWordMeaningReview();
    clearWordPreview();
    clearMistakePracticePreview();
    setMistakenWords([]);
    setMistakePracticeWords([]);
    setMistakePracticeIndex(0);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeTranslations({});
    setReviewTaskWordTranslation("");
    setPendingMistakePractice([], {});
    setCurrentStreak(0);
    setWordAnimations({});
    setHasFinalizedCurrentItem(false);
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    updateAnswerState("typing");
    isCompletingSentenceRef.current = false;
    respellWordIndexesRef.current = [];
    setFeedback(null);
    setSelectedChoice(null);
    setChoiceResult(null);
    setEncodingStage(null);
    encodingStageRef.current = null;
    // P1-2: Multi-modal encoding — trigger for new words AND weak review words.
    // Previously only triggered for non-review-task word items (almost never).
    // Now also triggers for word review items that are single-word and new/weak.
    const isNewWord = Boolean(currentItem && !currentItem.review_task_type && currentItem.item_type === "word" && currentWords[0]);
    // Only trigger encoding for hidden_recall tasks
    // where the child needs to memorize the word before spelling it.
    // listen_spell, chinese_to_english, missing_letter should NOT show the word first.
    const ENCODING_REVIEW_TYPES = new Set(["hidden_recall"]);
    const isSpellingReview = Boolean(
      currentItem && currentItem.review_task_type && currentItem.item_type === "word"
      && currentWords.length === 1 && currentWords[0]
      && ENCODING_REVIEW_TYPES.has(currentItem.review_task_type)
      && !isChoiceReviewTask
      && (currentItem.difficulty_level >= 4 || currentItem.source?.includes("复习"))
    );
    if (isNewWord || isSpellingReview) {
      const encWord = currentWords[0];
      const encChinese = currentItem.chinese_text;
      void startEncodingFn(encWord, encChinese);
    } else {
      window.setTimeout(function () { const ref = inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]; if (ref) { ref.focus(); } }, 0);
      const seqId = startVoiceSequence();
      if (currentItem?.chinese_text && currentItem.english_text) {
        void playCurrentItemIntro(currentItem, seqId);
      }
      if (currentItem?.review_task_type === "hidden_recall" && currentWords[0]) {
        window.setTimeout(function () { showWordPreview(0, currentWords[0]); }, 1200);
      }
    }
  }, [clearMistakePracticePreview, clearWordPreview, currentItem, currentWords, dynamicReviewWordIndexes, playCurrentItemIntro, setPendingMistakePractice, showWordPreview, startVoiceSequence, updateAnswerState, startEncodingFn]);

  useEffect(() => {
    if (answerState === "mistake-word-practice") {
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
    }
  }, [answerState, mistakePracticeIndex]);

  useEffect(() => {
    // Race-condition guard: when the user rapidly switches studyMode /
    // courseId / isFocusMode, multiple async loads can be in flight. Without
    // this guard, a stale response from the previous request can overwrite
    // the items array and currentIndex from the latest request. We use a
    // monotonically increasing load-id and discard any response that doesn't
    // match the latest load at the time the awaited promises resolve.
    let loadId = 0;
    let cancelled = false;

    async function loadStudyItems(currentLoadId: number) {
      const accessToken = getAccessToken();
      if (!accessToken) {
        setErrorMessage("请先登录后再学习");
        setIsLoading(false);
        return;
      }
      if (studyMode !== "review" && !courseId) {
        setErrorMessage("缺少课程信息，请从课程目录选择课程后开始学习");
        setIsLoading(false);
        return;
      }

      try {
        let mergedItems: LearningItem[] = [];
        let dueReviewItems: LearningItem[] = [];
        let nextItems: LearningItem[] = [];

        if (studyMode === "review") {
          // Pure review: all due review items across all courses, no course-specific filtering.
          dueReviewItems = await listDueReviewItems(
            accessToken,
            undefined,
            INITIAL_REVIEW_QUEUE_LIMIT,
            false,
            INITIAL_REVIEW_QUEUE_LIMIT,
            isFocusMode,
          ).catch(() => [] as LearningItem[]);
          mergedItems = dueReviewItems;
        } else if (studyMode === "learn") {
          // Learn mode: the backend now supports include_choices=true,
          // which prepends an english_to_chinese choice item before each
          // word-type item. The distractors come from the user's own
          // word database (WordTranslation ORDER BY random()), NOT from
          // a fixed pool. No frontend choice generation needed.
          // Fetch course items with include_choices=true — the backend
          // generates english_to_chinese items with DB-random distractors
          // prepended before each word-type item. Filter out sentence and
          // phrase items: learn mode focuses on individual words, not
          // full sentences. The parent chose this course to learn new
          // words, not to re-read sentences they already know.
          const choiceParams = new URLSearchParams({ course_id: courseId, include_choices: "true" });
          const response = await fetchWithAuth(
            `${getApiBaseUrl()}/learning/items?${choiceParams.toString()}`,
            { cache: "no-store" },
            accessToken,
          );
          if (!response.ok) {
            throw new Error(await parseApiError(response));
          }
          const allItems = (await response.json()) as LearningItem[];
          // Keep all items: word choices (english_to_chinese), word spellings,
          // sentences, and phrases. The backend interleaves them naturally —
          // choice→word→choice→word→...→sentence→next sentence's words.
          // The flow: pick Chinese meaning → spell English → pick Chinese for
          // next word → spell → ... → when all words in a sentence are done,
          // the full sentence spelling appears as the final consolidation step.
          mergedItems = allItems;
        } else {
          // Default mixed behavior: review + new content interleaved.
          nextItems = await listLearningItems(accessToken, courseId).catch(() => [] as LearningItem[]);
          dueReviewItems = await listDueReviewItems(accessToken, courseId, INITIAL_REVIEW_QUEUE_LIMIT, false, INITIAL_REVIEW_QUEUE_LIMIT, isFocusMode).catch(() => [] as LearningItem[]);
          mergedItems = mergeLearningQueues(dueReviewItems, nextItems);
        }

        // Discard the response if the user has triggered a newer load or the
        // effect has been cleaned up. This prevents a slow in-flight request
        // from clobbering a fast follow-up's results.
        if (cancelled || currentLoadId !== loadId) {
          return;
        }

        queuedReviewItemIdsRef.current = new Set(dueReviewItems.map((item) => item.id));
        const savedIndex = Number(window.localStorage.getItem(getStudyProgressKey(courseId || "global")) ?? "0");
        // Restore saved progress across all modes. The previous version
        // forced safeIndex = 0 in review mode, which combined with the
        // localStorage key mismatch (read used "global", write used "")
        // caused review-mode users to always restart from item 0 — never
        // resuming where they left off. Now read and write share the same
        // key, and the same bounds check applies in every mode.
        const safeIndex = dueReviewItems.length === 0
          && Number.isInteger(savedIndex)
          && savedIndex >= 0
          && savedIndex < mergedItems.length
            ? savedIndex
            : 0;
        setItems(mergedItems);
        setCurrentIndex(safeIndex);
        if (studyMode === "review") {
          setFeedback(mergedItems.length > 0 ? `开始复习 ${mergedItems.length} 条到期单词。` : "目前没有需要复习的单词，太棒了！");
        } else if (studyMode === "learn") {
          setFeedback(mergedItems.length > 0 ? `本课共有 ${mergedItems.length} 条新内容待学习。` : "这门课还没有新内容可学。");
        } else {
          setFeedback(dueReviewItems.length > 0 ? `先复习 ${dueReviewItems.length} 条历史错词/到期内容，再进入当前课程。` : null);
        }
      } catch (error) {
        if (cancelled || currentLoadId !== loadId) {
          return;
        }
        setErrorMessage(error instanceof Error ? error.message : "读取学习内容失败");
      } finally {
        if (!cancelled && currentLoadId === loadId) {
          setIsLoading(false);
        }
      }
    }

    const currentLoadId = ++loadId;
    void loadStudyItems(currentLoadId);
    return () => {
      cancelled = true;
    };
  }, [courseId, isFocusMode, studyMode]);

  // Prefetch course audio in background after study items are loaded
  useEffect(() => {
    if (isLoading || items.length === 0 || !courseId) return;
    const accessToken = getAccessToken();
    if (!accessToken) return;
    const settings = modelSettingsRef.current;
    if (settings.ttsProvider !== "volcark") return;
    const speechRate = getTtsSpeechRate(settings);
    void prefetchCourseAudio(courseId, settings.ttsEnglishVoice, "en-US", speechRate, accessToken).catch(() => { /* silent */ });
  }, [isLoading, items.length, courseId]);

  const insertDueReviewItemsAfterCurrent = useCallback(async function insertDueReviewItemsAfterCurrent() {
    if (isFocusMode) return;  // focus mode has fixed item set
    if (studyMode === "learn") return;  // learn mode only shows new content
    const accessToken = getAccessToken();
    const activeItem = currentItemRef.current;
    if (!accessToken || studyMode === "review" || !courseId || !activeItem || celebrationSummaryRef.current) {
      return;
    }

    const dueReviewItems = await listDueReviewItems(accessToken, courseId, REFILL_REVIEW_QUEUE_LIMIT, false, REFILL_REVIEW_QUEUE_LIMIT, isFocusMode).catch(() => [] as LearningItem[]);
    const freshReviewItems = dueReviewItems.filter((item) => !queuedReviewItemIdsRef.current.has(item.id) && item.id !== activeItem.id);
    if (freshReviewItems.length === 0) {
      return;
    }

    freshReviewItems.forEach((item) => queuedReviewItemIdsRef.current.add(item.id));
    setItems((currentItems) => {
      const existingItemIds = new Set(currentItems.map((item) => item.id));
      const insertItems = freshReviewItems.filter((item) => !existingItemIds.has(item.id));
      if (insertItems.length === 0) {
        return currentItems;
      }

      const nextItems = [...currentItems];
      nextItems.splice(Math.min(currentIndexRef.current + 1, nextItems.length), 0, ...insertItems);
      return nextItems;
    });
    setFeedback(`已插入 ${freshReviewItems.length} 条新的到期复习内容，完成当前题后会优先复习。`);
  }, [courseId, studyMode]);

  useEffect(() => {
    if (studyMode === "review" || !courseId || isLoading) {
      return;
    }

    // Only poll once at session start — no silent mid-session injection.
    // Deferred reviews are shown as a count at session end, not forced mid-flow.
    // In focus mode, skip refill entirely — session has a fixed set of items.
    // In learn mode, skip refill entirely — only new content, no silent review injection.
    if (!isFocusMode && studyMode !== "learn") {
      const timeoutId = window.setTimeout(() => {
        void insertDueReviewItemsAfterCurrent();
      }, 15_000);
      const intervalId = window.setInterval(() => {
        void insertDueReviewItemsAfterCurrent();
      }, 900_000);

      return () => {
        window.clearTimeout(timeoutId);
        window.clearInterval(intervalId);
      };
    }
  }, [courseId, insertDueReviewItemsAfterCurrent, isLoading, studyMode]);

  useEffect(() => {
    async function loadPackageCourses() {
      const accessToken = getAccessToken();
      if (!accessToken || !courseId) {
        setPackageCourses([]);
        return;
      }

      try {
        if (packageIdFromUrl) {
          setResolvedPackageId(packageIdFromUrl);
          setPackageCourses(await listCourses(accessToken, packageIdFromUrl));
          return;
        }

        const coursePackages = await listCoursePackages(accessToken);
        for (const coursePackage of coursePackages) {
          const courses = await listCourses(accessToken, coursePackage.id);
          if (courses.some((course) => course.id === courseId)) {
            setResolvedPackageId(coursePackage.id);
            setPackageCourses(courses);
            return;
          }
        }
        setResolvedPackageId("");
        setPackageCourses([]);
      } catch {
        setPackageCourses([]);
      }
    }

    void loadPackageCourses();
  }, [courseId, packageIdFromUrl]);

  function resetAnswer() {
    setWordAnswers(currentWords.map(() => ""));
    setWordStatuses(currentWords.map(() => "idle"));
    setWordErrorCounts(currentWords.map(() => 0));
    clearWordMeaningReview();
    clearWordPreview();
    clearMistakePracticePreview();
    setMistakenWords([]);
    setMistakePracticeWords([]);
    setMistakePracticeIndex(0);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeTranslations({});
    setPendingMistakePractice([], {});
    setHasFinalizedCurrentItem(false);
    // NOTE: do NOT reset startedAt here. resetAnswer() runs between
    // individual words within a sentence (e.g. when the child finishes
    // one word and the input for the next word takes focus). Resetting
    // startedAt here would make the duration_seconds reported for the
    // second word effectively zero. startedAt is properly reset on item
    // change in the [currentItem] effect above.
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    updateAnswerState("typing");
    isCompletingSentenceRef.current = false;
    setCurrentStreak(0);
    setWordAnimations({});
    setFeedback(null);
    setSelectedChoice(null);
    setChoiceResult(null);
    window.setTimeout(() => inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]?.focus(), 0);
  }

  function markCorrectWord(item: LearningItem, word: string, index: number | string) {
    const normalizedWord = normalizeEnglishKey(word);
    if (!normalizedWord) {
      return;
    }

    // Key on the normalized word alone, NOT on (item.id, index, word).
    // The previous composite key counted the same English word as
    // distinct "correct" entries every time it appeared in a different
    // sentence (e.g. "the" in 5 sentences → 5 "correct words" instead
    // of 1). This inflated the course-completion `correct_word_count`
    // and confused downstream FSRS metrics that aggregate by word.
    // Now: a unique word counts once per course run, regardless of
    // how many sentences contain it. The `item` and `index` params
    // are kept for caller compatibility but intentionally unused.
    void item;
    void index;
    courseRunCorrectWordKeysRef.current.add(normalizedWord);
  }

  const generateAndSpeakCompletionEncouragement = useCallback(async function generateAndSpeakCompletionEncouragement(durationSeconds: number) {
    const accessToken = getAccessToken();
    const fallbackEncouragement = getFallbackEncouragement(courseName);
    let encouragement = fallbackEncouragement;

    if (accessToken) {
      try {
        encouragement = await generateLearningEncouragement(
          {
            course_name: courseName,
            duration_seconds: durationSeconds,
          },
          accessToken,
          modelSettingsRef.current,
        ).then((response) => ({
          chineseText: response.chinese_text,
          englishText: response.english_text,
        }));
      } catch {
        encouragement = fallbackEncouragement;
      }
    }

    setCelebrationSummary((current) => current ? {
      ...current,
      encouragementChineseText: encouragement.chineseText,
      encouragementEnglishText: encouragement.englishText,
      statusMessage: "正在朗读鼓励语...",
    } : current);

    const sequenceId = startVoiceSequence();
    const settings = modelSettingsRef.current;
    await speakInVoiceSequence(sequenceId, encouragement.chineseText, settings.ttsChineseVoice, "zh-CN", settings);
    if (await waitInVoiceSequence(sequenceId, 600)) {
      await speakInVoiceSequence(sequenceId, encouragement.englishText, settings.ttsEnglishVoice, "en-US", settings);
    }

    setCelebrationSummary((current) => current ? {
      ...current,
      canContinue: true,
      statusMessage: "鼓励语朗读完成，可以进入下一课。",
    } : current);
  }, [courseName, speakInVoiceSequence, startVoiceSequence, waitInVoiceSequence]);

  const showCourseCompletion = useCallback(function showCourseCompletion() {
    const durationSeconds = Math.max(1, Math.round((activeStudyMsRef.current - courseRunStartedActiveMsRef.current) / 1000));
    const correctWordCount = courseRunCorrectWordKeysRef.current.size;

    addCourseCompletion(courseId, durationSeconds, correctWordCount);
    const accessToken = getAccessToken();
    if (accessToken) {
      void recordCourseCompletion(accessToken, courseId, durationSeconds, correctWordCount).catch(() => undefined);
    }
    courseRunStartedActiveMsRef.current = activeStudyMsRef.current;
    courseRunCorrectWordKeysRef.current = new Set();
    setCelebrationSummary({
      courseName,
      durationSeconds,
      correctWordCount,
      encouragementChineseText: "正在生成一句鼓励的话...",
      encouragementEnglishText: "",
      canContinue: false,
      statusMessage: "正在生成并准备朗读鼓励语，请稍等。",
    });
    playCelebrationSound();
    void generateAndSpeakCompletionEncouragement(durationSeconds);
  }, [courseId, courseName, generateAndSpeakCompletionEncouragement]);

  const handleNextItem = useCallback(function handleNextItem(options: { completedCurrentItem?: boolean } = {}) {
    if (items.length === 0) {
      return;
    }

    startVoiceSequence();
    setCurrentIndex((index) => {
      const nextIndex = index + 1;
      // Focus mode: when all items done (focus + course), rotate and reload
      if (isFocusMode && nextIndex >= items.length) {
        updateAnswerState("sentence-complete");
        setFeedback("🎯 全部完成！正在准备下一轮...", "success");
        // Rotate all items out
        const token = getAccessToken();
        const rotatedIds = new Set<string>();
        for (const item of items) {
          if (item.id && !rotatedIds.has(item.id)) {
            rotatedIds.add(item.id);
            if (token) {
              void rotateFocusWord(token, item.id).catch(() => {});
            }
          }
        }
        window.setTimeout(() => {
          window.location.reload();
        }, 2000);
        return index;
      }
      // Normal mode: wrap around to beginning.
      // Use the same key derivation as the read site (loadStudyItems) so that
      // progress saved in review mode (courseId === "") is keyed under
      // "global" and is actually restored on reload. Previously the write
      // used getStudyProgressKey("") and the read used
      // getStudyProgressKey("global") — the two never matched, so review
      // mode silently lost progress between sessions.
      const wrappedIndex = nextIndex % items.length;
      window.localStorage.setItem(
        getStudyProgressKey(courseId || "global"),
        String(wrappedIndex),
      );
      if (options.completedCurrentItem && wrappedIndex === 0) {
        window.setTimeout(showCourseCompletion, 0);
      }
      return wrappedIndex;
    });
  }, [courseId, items.length, showCourseCompletion, startVoiceSequence]);

  const isGeneratedItem = useCallback(function isGeneratedItem(item: LearningItem): boolean {
    return item.id.startsWith("generated-") || Boolean(item.review_task_id);
  }, []);

  const getSourceLearningItemId = useCallback(function getSourceLearningItemId(item: LearningItem): string | null {
    return item.source_item_id ?? (item.id.startsWith("generated-") ? null : item.id);
  }, []);

  function recordWordMemoryReview(expectedWord: string, score: number, reviewMode: string, responseText = "", errorType?: string) {
    const accessToken = getAccessToken();
    if (!accessToken || !currentItem) {
      return;
    }
    const learningItemId = getSourceLearningItemId(currentItem);
    if (!learningItemId) {
      return;
    }
    const currentEncodingStage = encodingStageRef.current;
    const encodingDurationMs = encodingStartTimeRef.current > 0 ? Date.now() - encodingStartTimeRef.current : 0;
    void logWordReview(
      {
        learning_item_id: learningItemId,
        review_task_id: currentItem.review_task_id,
        word: expectedWord,
        score,
        review_mode: reviewMode,
        response_text: responseText,
        error_type: errorType,
        // Real elapsed time, not 0. The backend uses this for the study-time
        // summary; sending 0 here made the dashboard's "累计学习时长" (total
        // study time) underreport dramatically.
        duration_seconds: Math.max(1, Math.round((Date.now() - startedAt) / 1000)),
        encoding_stage: currentEncodingStage ?? undefined,
        encoding_duration_ms: encodingDurationMs,
      },
      accessToken,
    ).catch(() => undefined);
  }

  function recordSuccessfulWordSpelling(expectedWord: string, typedWord: string, errorCount: number, usedPreview: boolean, isStandalonePractice: boolean) {
    if (usedPreview) {
      recordWordMemoryReview(expectedWord, 3, "word-preview", typedWord);
      return;
    }
    if (errorCount > 0) {
      recordWordMemoryReview(expectedWord, 4, "word-hinted", typedWord);
      return;
    }
    if (!isStandalonePractice) {
      recordWordMemoryReview(expectedWord, 3, "word-context", typedWord);
      return;
    }
    recordWordMemoryReview(expectedWord, 5, "word-recall", typedWord);
  }

  function getUniqueMistakenWords() {
    return Array.from(new Set(mistakenWords.map(normalizeEnglishKey).filter(Boolean)));
  }

  const translateMistakePracticeWords = useCallback(async function translateMistakePracticeWords(words: string[]) {
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
  }, [modelSettings]);

  async function finalizeCurrentSentenceReview() {
    if (!currentItem || hasFinalizedCurrentItem) {
      return;
    }

    setHasFinalizedCurrentItem(true);
    const accessToken = getAccessToken();
    const uniqueMistakenWords = getUniqueMistakenWords();
    if (accessToken && !currentItem.review_task_id && !isGeneratedItem(currentItem)) {
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
        syllables: null,
        grapheme_phoneme_map: null,
        difficulty_level: Math.min(Math.max(currentItem.difficulty_level, 1), 5),
        sort_order: 0,
        unit_label: null,
        source: `AI 动态复习：${uniqueMistakenWords.join(", ")}`,
        source_item_id: currentItem.id,
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

  function getBasicWordMeaning(word: string): string {
    return BASIC_WORD_MEANINGS[normalizeEnglishKey(word)] ?? "";
  }

  async function fetchCachedWordTranslations(words: string[]): Promise<Record<string, string>> {
    const accessToken = getAccessToken();
    if (!accessToken) {
      return {};
    }
    try {
      return await Promise.race([
        getWordTranslations(words, accessToken, modelSettingsRef.current, currentItemRef.current?.course_id),
        new Promise<Record<string, string>>((resolve) => {
          window.setTimeout(() => resolve({}), WORD_TRANSLATION_TIMEOUT_MS);
        }),
      ]);
    } catch {
      return {};
    }
  }

  async function translateSingleWordMeaning(word: string): Promise<string> {
    const accessToken = getAccessToken();
    const fallbackMeaning = getBasicWordMeaning(word);
    if (!accessToken) {
      return fallbackMeaning;
    }

    try {
      const translatedText = await Promise.race([
        getWordTranslations([word], accessToken, modelSettingsRef.current, currentItemRef.current?.course_id).then((translations) => translations[normalizeEnglishKey(word)]?.trim() ?? ""),
        new Promise<string>((resolve) => {
          window.setTimeout(() => resolve(""), WORD_TRANSLATION_TIMEOUT_MS);
        }),
      ]);
      return translatedText || fallbackMeaning;
    } catch {
      return fallbackMeaning;
    }
  }

  async function buildSentenceWordMeaningMap(words: string[]): Promise<Record<number, string>> {
    const normalizedToTranslation: Record<string, string> = {};
    const uniqueWords = Array.from(new Set(words.map(normalizeEnglishKey).filter(Boolean)));

    Object.assign(normalizedToTranslation, await fetchCachedWordTranslations(uniqueWords));
    uniqueWords.forEach((word) => {
      if (!normalizedToTranslation[word]) {
        normalizedToTranslation[word] = getBasicWordMeaning(word);
      }
    });

    const meanings: Record<number, string> = {};
    words.forEach((word, index) => {
      meanings[index] = normalizedToTranslation[normalizeEnglishKey(word)] || "暂未获取释义";
    });
    return meanings;
  }

  async function showWordMeaningsBeforeCompletion() {
    if (!currentItem || isCompletingSentenceRef.current || answerStateRef.current !== "typing") {
      return;
    }

    const itemId = currentItem.id;
    const words = [...currentWords];
    const loadingMeanings: Record<number, string> = {};
    words.forEach((_word, index) => {
      loadingMeanings[index] = "释义准备中";
    });

    inputRefs.current.forEach((input) => input?.blur());
    clearWordPreview();
    setChildHint(null);
    setSentenceWordMeanings(loadingMeanings);
    updateAnswerState("word-meaning-review");
    setFeedback("拼写完成！看一看每个单词的中文意思，5 秒后继续。");

    const meanings = await buildSentenceWordMeaningMap(words);
    if (currentItemRef.current?.id !== itemId || (answerStateRef.current as AnswerState) !== "word-meaning-review") {
      return;
    }

    setSentenceWordMeanings(meanings);
    if (wordMeaningReviewTimeoutRef.current !== null) {
      window.clearTimeout(wordMeaningReviewTimeoutRef.current);
    }
    wordMeaningReviewTimeoutRef.current = window.setTimeout(() => {
      wordMeaningReviewTimeoutRef.current = null;
      setSentenceWordMeanings({});
      void completeCurrentSentence({ fromWordMeaningReview: true });
    }, WORD_MEANING_REVIEW_MS);
  }

  async function completeCurrentSentence(options: { fromWordMeaningReview?: boolean } = {}) {
    if (!currentItem || isCompletingSentenceRef.current || (answerStateRef.current !== "typing" && !options.fromWordMeaningReview)) {
      return;
    }

    isCompletingSentenceRef.current = true;
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
      if (respellWordIndexesRef.current.length > 0) {
        respellWordIndexesRef.current = [];
        updateAnswerState("sentence-complete");
        isCompletingSentenceRef.current = false;
        setFeedback("错词重拼完成。点击「下一句」按钮继续。", "success");
        await playCurrentReviewCompletionAudio();
        return;
      }
      setPendingMistakePractice(uniqueMistakenWords, {});
      updateAnswerState("sentence-complete");
      isCompletingSentenceRef.current = false;
      setFeedback(
        dynamicSentenceFeedback
          ? `${dynamicSentenceFeedback} 本句有错词，点击「进入错词复习」按钮。`
          : "本句有错词，点击「进入错词复习」按钮。",
        "error",
      );
      await playCurrentReviewCompletionAudio();
      return;
    }

    updateAnswerState("sentence-complete");
    isCompletingSentenceRef.current = false;
    if (respellWordIndexesRef.current.length === 0) {
      setPerfectSentenceCount((c) => c + 1);
      setStarAnimKey((k) => k + 1);
    }
    if (respellWordIndexesRef.current.length > 0) {
      respellWordIndexesRef.current = [];
      setFeedback("错词重拼完全正确。点击「下一句」按钮继续。", "success");
    } else {
      setFeedback(dynamicSentenceFeedback ? `${dynamicSentenceFeedback} 点击「下一句」按钮继续。` : "整句拼写正确。点击「下一句」按钮继续。", "success");
    }
    await playCurrentReviewCompletionAudio();
  }

  async function playCurrentReviewCompletionAudio() {
    if (!currentItem) {
      return;
    }
    if (isFocusedWordReview && currentFocusedReviewWord) {
      await playChineseThenEnglish(reviewTaskWordTranslation || "", currentFocusedReviewWord);
      return;
    }
    await playChineseThenEnglish(currentItem.chinese_text, currentItem.english_text);
  }

  function updateWordAnswer(index: number, value: string) {
    setChildHint(null);
    if (previewWordIndex === index) {
      clearWordPreview();
    }
    setWordAnswers((current) => {
      const nextAnswers = [...current];
      nextAnswers[index] = value.replace(/\s/g, "");
      return nextAnswers;
    });
    if (Date.now() - feedbackSetAtRef.current > 1500) {
      setFeedback(null);
    }
    if (wordStatuses[index] === "incorrect" || wordStatuses[index] === "skipped") {
      setWordStatuses((current) => {
        const nextStatuses = [...current];
        nextStatuses[index] = "idle";
        return nextStatuses;
      });
    }
  }

  function resetIncorrectWord(index: number) {
    if (previewWordIndex === index) {
      clearWordPreview();
    }
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
    setFeedback("请重新输入这个单词。", "error");
    window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
  }

  async function translateWordForHint(expectedWord: string): Promise<string> {
    return translateSingleWordMeaning(expectedWord);
  }

  async function handleFirstLetterMistake(expectedWord: string) {
    const translatedWord = await translateWordForHint(expectedWord);
    if (isDictationModeRef.current) {
      setFeedback(translatedWord ? `首字母不对，请参考中文意思重试。中文：${translatedWord}` : "首字母不对，请重试。", "error");
      return;
    }
    setFeedback(translatedWord ? `首字母不对，先听中文和英文读音。中文：${translatedWord}` : "首字母不对，先听英文读音再试一次。", "error");
    await playChineseThenEnglish(translatedWord, expectedWord);
  }

  async function handleSpellingMistake(index: number, expectedWord: string, typedWord: string, errorCount: number, errorType: string) {
    const cachedSyllables = currentItem?.syllables;
    const hint = buildChildFriendlyHint(expectedWord, errorType, getMatchedPrefixLength(expectedWord, typedWord), cachedSyllables);
    const translatedWord = await translateWordForHint(expectedWord);
    const hintText = translatedWord ? `${hint.text} 中文意思：${translatedWord}` : hint.text;
    setChildHint({ ...hint, text: hintText });
    setFeedback(hintText, "error");
    if (translatedWord) {
      await playChineseThenEnglish(translatedWord, expectedWord);
    }
    window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
  }

  function checkWord(index: number) {
    if (answerStateRef.current !== "typing" || isCompletingSentenceRef.current) {
      return;
    }
    if (previewWordIndex === index) {
      setFeedback("先看清单词，等它自动隐藏后再凭记忆输入。");
      return;
    }
    if (encodingStageRef.current === "whole_recall" && previewCountdownSeconds > 0) {
      setFeedback("先看清单词，等它自动隐藏后再凭记忆输入。");
      return;
    }
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

    // Ignore empty submissions — don't count them as mistakes
    const currentRawAnswer = inputRefs.current[index]?.value ?? wordAnswers[index] ?? "";
    const typedValue = normalizeTypedWord(currentRawAnswer);
    if (!typedValue) {
      window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
      return;
    }

    const isCorrect = typedValue === normalizeTypedWord(expectedWord);
    const nextStatusesForAttempt = [...wordStatuses];
    nextStatusesForAttempt[index] = isCorrect ? "correct" : "incorrect";
    setWordStatuses(nextStatusesForAttempt);

    if (!isCorrect) {
      playIncorrectTap();
      setCurrentStreak(0);
      // Focus mode: reset consecutive correct counter on wrong answer
      if (isFocusMode && currentItem) {
        const wordKey = normalizeEnglishKey(expectedWord);
        focusCorrectCountRef.current.set(wordKey, 0);
      }
      wordAnimKeyRef.current += 1;
      setWordAnimations((current) => ({ ...current, [index]: { type: "incorrect", key: wordAnimKeyRef.current } }));
      setTimeout(() => {
        setWordAnimations((current) => {
          const next = { ...current };
          delete next[index];
          return next;
        });
      }, 400);
      const actualWord = currentRawAnswer;
      const errorType = classifySpellingError(expectedWord, actualWord);
      const normalizedExpectedWord = normalizeEnglishKey(expectedWord);
      const nextErrorCount = (wordErrorCounts[index] ?? 0) + 1;
      setWordErrorCounts((current) => {
        const nextCounts = [...current];
        nextCounts[index] = nextErrorCount;
        return nextCounts;
      });
      setWordConsecutiveCorrect((current) => {
        const next = [...current];
        next[index] = 0;
        return next;
      });
      setMistakenWords((current) => (current.includes(normalizedExpectedWord) ? current : [...current, normalizedExpectedWord]));
      const accessToken = getAccessToken();
      const learningItemId = currentItem ? getSourceLearningItemId(currentItem) : null;
      if (accessToken && learningItemId) {
        void logWordMistake(learningItemId, expectedWord, actualWord, accessToken, errorType).catch(() => undefined);
      }
      if (nextErrorCount === 3 || (errorType === "unknown" && nextErrorCount >= 2)) {
        void translateWordForHint(expectedWord).then((translatedWord) => {
          if (translatedWord) {
            void playChineseThenEnglish(translatedWord, expectedWord);
          }
          showWordPreview(index, expectedWord, translatedWord);
        });
        return;
      }
      if (getFirstLetter(typedValue) !== getFirstLetter(expectedWord)) {
        setFeedback("首字母不对，正在准备中文和英文读音...", "error");
        void handleFirstLetterMistake(expectedWord);
      } else {
        void handleSpellingMistake(index, expectedWord, typedValue, nextErrorCount, errorType);
      }
      return;
    }

    const prevErrorCount = wordErrorCounts[index] ?? 0;

    if (prevErrorCount >= 3) {
      const nextConsecutive = (wordConsecutiveCorrect[index] ?? 0) + 1;
      setWordConsecutiveCorrect((current) => {
        const next = [...current];
        next[index] = nextConsecutive;
        return next;
      });

      if (nextConsecutive < DIFFICULT_WORD_CONFIRMATION_COUNT) {
        playCorrectDing();
        setFeedback(`拼对了 ${nextConsecutive} / ${DIFFICULT_WORD_CONFIRMATION_COUNT} 次，再拼一遍这个单词。`, "success");
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
        window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
        return;
      }

      // Show celebration for previously-difficult words
      setFeedback("这个单词已经连续拼对 3 次，太棒了！", "success");
      setCelebrationTrigger((prev) => prev + 1);

      setWordConsecutiveCorrect((current) => {
        const next = [...current];
        next[index] = 0;
        return next;
      });
    }

    if (currentItem) {
      markCorrectWord(currentItem, expectedWord, index);
    }

    playCorrectDing();
    setCurrentStreak((s) => s + 1);



    setCorrectWordCount((c) => c + 1);
    wordAnimKeyRef.current += 1;
    setWordAnimations((current) => ({ ...current, [index]: { type: "correct", key: wordAnimKeyRef.current } }));
    setTimeout(() => {
      setWordAnimations((current) => {
        const next = { ...current };
        delete next[index];
        return next;
      });
    }, 400);

    recordSuccessfulWordSpelling(expectedWord, currentRawAnswer, prevErrorCount, prevErrorCount >= 3, currentItem?.item_type === "word");

    // Focus mode rotation: track consecutive correct, auto-rotate after 3
    if (isFocusMode && currentItem) {
      const wordKey = normalizeEnglishKey(expectedWord);
      const prevCount = focusCorrectCountRef.current.get(wordKey) || 0;
      const newCount = prevCount + 1;
      focusCorrectCountRef.current.set(wordKey, newCount);
      if (newCount >= 3 && currentItem.id) {
        const token = getAccessToken();
        if (token) {
          void rotateFocusWord(token, currentItem.id).then(() => {
            setFocusRotatedMessage(`${expectedWord} 已掌握！下一批`);
            setTimeout(() => setFocusRotatedMessage(null), 3000);
          }).catch(() => {});
        }
        focusCorrectCountRef.current.delete(wordKey);
      }
    }

    // P2-2: Milestone celebration on round numbers
    const milestones = [5, 10, 25, 50, 75, 100, 150, 200];
    const newTotal = correctWordCount + 1;
    if (milestones.includes(newTotal)) {
      setMilestoneMessage(`🎉 太棒了！今天已经正确拼写 ${newTotal} 个词！`);
      playLevelUpSound();
      setTimeout(() => setMilestoneMessage(null), 4000);
    }

    // After encoding whole_recall success, complete encoding
    if (encodingStageRef.current === "whole_recall") {
      setWordErrorCounts((current) => {
        const nextCounts = [...current];
        nextCounts[index] = 0;
        return nextCounts;
      });
      clearEncodingTimeout();
      void advanceEncodingStage("whole_recall", expectedWord, currentItem?.chinese_text ?? "");
      return;
    }

    setWordErrorCounts((current) => {
      const nextCounts = [...current];
      nextCounts[index] = 0;
      return nextCounts;
    });
    const nextAnswers = [...wordAnswers];
    nextAnswers[index] = currentRawAnswer;
    if (areRequiredWordAnswersComplete(nextAnswers, nextStatusesForAttempt)) {
      setWordStatuses((current) => {
        const nextStatuses = [...current];
        getRequiredWordIndexes().forEach((wordIndex) => {
          if (nextStatuses[wordIndex] !== "skipped") {
            nextStatuses[wordIndex] = "correct";
          }
        });
        return nextStatuses;
      });
      clearWordPreview();
      void showWordMeaningsBeforeCompletion();
      return;
    }
    const nextIndex = isDynamicFillBlankItem ? dynamicReviewWordIndexes.find((wordIndex) => wordIndex > index) ?? currentWords.length : index + 1;
    if (nextIndex < currentWords.length) {
      setActiveWordIndex(nextIndex);
      window.setTimeout(() => inputRefs.current[nextIndex]?.focus(), 0);
      return;
    }

    void showWordMeaningsBeforeCompletion();
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
    if (previewMistakePracticeWord) {
      setFeedback("先看清单词，等它自动隐藏后再凭记忆输入。");
      return;
    }

    // Ignore empty submissions — don't count them as errors
    const normalizedAnswer = normalizeTypedWord(mistakePracticeAnswer);
    if (!normalizedAnswer) {
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
      return;
    }

    const isCorrect = normalizedAnswer === normalizeTypedWord(expectedWord);
    if (!isCorrect) {
      // Reset progress — the child must get 3 consecutive correct.
      setMistakePracticeConsecutiveCorrect(0);
      const nextErrorCount = mistakePracticeErrorCount + 1;
      const errorType = classifySpellingError(expectedWord, mistakePracticeAnswer);
      setMistakePracticeErrorCount(nextErrorCount);
      setMistakePracticeStatus("incorrect");
      setMistakePracticeAnswer("");
      const accessToken = getAccessToken();
      const learningItemId = currentItem ? getSourceLearningItemId(currentItem) : null;
      if (accessToken && learningItemId) {
        void logWordMistake(learningItemId, expectedWord, mistakePracticeAnswer, accessToken, errorType).catch(() => undefined);
      }
      if (nextErrorCount === 3 || (errorType === "unknown" && nextErrorCount >= 2)) {
        await playChineseThenEnglish(currentMistakePracticeTranslation, expectedWord);
        showMistakePracticePreview(expectedWord);
        return;
      }

      if (getFirstLetter(normalizedAnswer) !== getFirstLetter(expectedWord)) {
        setFeedback(`首字母不对，先听中文和英文读音。已连续错误 ${nextErrorCount} 次。`, "error");
        await playChineseThenEnglish(currentMistakePracticeTranslation, expectedWord);
        return;
      }

      const practiceHint = buildChildFriendlyHint(expectedWord, errorType, getMatchedPrefixLength(expectedWord, normalizedAnswer), currentItem?.syllables);
      setChildHint(practiceHint);
      setFeedback(`${practiceHint.text} 已连续错误 ${nextErrorCount} 次。`, "error");
      return;
    }

    // --- Correct spelling ---
    recordSuccessfulWordSpelling(expectedWord, mistakePracticeAnswer, mistakePracticeErrorCount, false, true);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setChildHint(null);

    const nextConsecutive = mistakePracticeConsecutiveCorrect + 1;
    if (nextConsecutive < MISTAKE_PRACTICE_REQUIRED_CONSECUTIVE) {
      // Still need more repetitions — show progress and keep practicing
      setMistakePracticeConsecutiveCorrect(nextConsecutive);
      setFeedback(
        `✓ ${expectedWord} 正确！还需再正确拼写 ${MISTAKE_PRACTICE_REQUIRED_CONSECUTIVE - nextConsecutive} 次。`,
        "success",
      );
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
      return;
    }

    // All MISTAKE_PRACTICE_REQUIRED_CONSECUTIVE repetitions complete.
    // Reset the consecutive counter and transition to the meaning quiz.
    setMistakePracticeConsecutiveCorrect(0);
    recordSuccessfulWordSpelling(expectedWord, mistakePracticeAnswer, mistakePracticeErrorCount, true, true);

    // Build the meaning-quiz options using the child-friendly distractor pool
    const correctMeaning = currentMistakePracticeTranslation || expectedWord;
    const quizOptions = buildMeaningQuizOptions(correctMeaning);
    setMistakeMeaningQuizOptions(quizOptions);
    setMistakeMeaningQuizCorrect(correctMeaning);
    setMistakeMeaningQuizSelected(null);
    updateAnswerState("mistake-meaning-quiz");
    setFeedback(
      `很好！选择 ${expectedWord} 的中文意思：`,
      "success",
    );
    window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
  }

  /** Handle the meaning-quiz multiple-choice selection. */
  function handleMistakeMeaningChoice(selectedOption: string) {
    setMistakeMeaningQuizSelected(selectedOption);
    const isCorrect = selectedOption === mistakeMeaningQuizCorrect;
    const expectedWord = mistakePracticeWords[mistakePracticeIndex] || "";

    if (!isCorrect) {
      // Wrong meaning — go back to spelling. Reset consecutive counter
      // so the child must re-earn the 3 correct spellings before the
      // meaning quiz is shown again.
      setMistakePracticeConsecutiveCorrect(0);
      setMistakePracticeErrorCount(0);
      setMistakePracticeAnswer("");
      setMistakePracticeStatus("idle");
      updateAnswerState("mistake-word-practice");
      setFeedback(
        `中文意思不对哦。${expectedWord} 的正确意思是「${mistakeMeaningQuizCorrect}」。请重新拼写 ${expectedWord}。`,
        "error",
      );
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
      return;
    }

    // Correct meaning — advance to the next word.
    const nextIndex = mistakePracticeIndex + 1;
    if (currentItem) {
      markCorrectWord(currentItem, expectedWord, `mistake-${normalizeEnglishKey(expectedWord)}`);
    }
    if (nextIndex < mistakePracticeWords.length) {
      const nextWord = mistakePracticeWords[nextIndex];
      setMistakePracticeIndex(nextIndex);
      setMistakePracticeAnswer("");
      setMistakePracticeStatus("idle");
      setMistakePracticeErrorCount(0);
      setMistakePracticeConsecutiveCorrect(0);
      clearMistakePracticePreview();
      updateAnswerState("mistake-word-practice");
      setFeedback("正确！继续拼写下一个错词。", "success");
      const nextTranslation = mistakePracticeTranslations[nextWord];
      if (nextTranslation) {
        void playChineseThenEnglish(nextTranslation, nextWord);
      }
      return;
    }

    // All mistake words practiced — back to sentence context for final
    // respell, then advance to the next sentence.
    setMistakePracticeWords([]);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeConsecutiveCorrect(0);
    clearMistakePracticePreview();
    // Targeted respell: only previously-mistaken words are re-tested
    const mistakenWordSet = new Set(mistakePracticeWords.map(normalizeEnglishKey));
    const respellIndexes: number[] = [];
    currentWords.forEach((word, idx) => {
      if (mistakenWordSet.has(normalizeEnglishKey(word))) {
        respellIndexes.push(idx);
      }
    });
    respellWordIndexesRef.current = respellIndexes;

    // Pre-fill non-mistaken words, blank mistaken ones
    setWordAnswers(currentWords.map((word, idx) =>
      respellIndexes.includes(idx) ? "" : word
    ));
    setWordStatuses(currentWords.map(() => "idle"));
    setWordErrorCounts(currentWords.map(() => 0));
    setWordConsecutiveCorrect(currentWords.map(() => 0));
    clearWordPreview();
    setMistakenWords([]);
    setPendingMistakePractice([], {});
    updateAnswerState("typing");
    isCompletingSentenceRef.current = false;
    const firstRespellIdx = respellIndexes[0] ?? 0;
    setActiveWordIndex(firstRespellIdx);
    setFeedback("错词复习完成。请在黄色高亮的单词框中拼写刚才错过的词。", "success");
    const respellSequenceId = startVoiceSequence();
    if (currentItem?.chinese_text && currentItem.english_text) {
      void playCurrentItemIntro(currentItem, respellSequenceId);
    }
    window.setTimeout(() => inputRefs.current[firstRespellIdx]?.focus(), 0);
  }

  const confirmChoiceSelection = useCallback(async function confirmChoiceSelection() {
    // Read from ref (latest) NOT state (may be stale). Without the
    // ref, the space-key handler reads the old null value because
    // setSelectedChoice() is async and React hasn't flushed yet.
    const choice = selectedChoiceRef.current;
    if (!currentItem || !isChoiceReviewTask || !choice) {
      return;
    }
    const correctAnswer = hasChineseText(currentItem.review_answer) ? currentItem.review_answer : reviewTaskWordTranslation;
    const isCorrect = isCorrectChoiceAnswer(choice, correctAnswer);
    const accessToken = getAccessToken();
    const learningItemId = getSourceLearningItemId(currentItem);
    const word = currentWords[0] ?? "";

    // Ensure selectedChoice state reflects the actual choice for visual
    // feedback (choice button highlighting). When called from the
    // number-key handler with an explicitChoice, selectedChoice state
    // hasn't been set yet — we set it here before the result render.
    

    // Visual feedback: show correct/incorrect on the choice button
    setChoiceResult(isCorrect ? "correct" : "incorrect");

    if (accessToken && learningItemId && word) {
      try {
        await logWordReview(
          {
            learning_item_id: learningItemId,
            review_task_id: currentItem.review_task_id,
            word,
            score: isCorrect ? 4 : 1,
            review_mode: `word-${currentItem.review_task_type}`,
            response_text: choice,
            error_type: isCorrect ? undefined : "meaning",
            // Real elapsed time, not 0. See the comment in
            // recordWordMemoryReview above.
            duration_seconds: Math.max(1, Math.round((Date.now() - startedAt) / 1000)),
          },
          accessToken,
        );
      } catch {
        // Choice task telemetry should not stop the learning flow.
      }
    }
    if (!isCorrect) {
      setFeedback("这个选项不对，已加入错词专项复习。", "error");
      const correctMeaning = (hasChineseText(reviewTaskWordTranslation) ? reviewTaskWordTranslation : "") || (hasChineseText(currentItem.review_answer) ? currentItem.review_answer : "");
      if (word && correctMeaning) {
        void playEnglishThenChinese(word, correctMeaning);
      }
      // Clear incorrect highlight after a delay so user can see it, then allow re-selection
      window.setTimeout(() => {
        setSelectedChoice(null);
        setChoiceResult(null);
      }, 800);
      return;
    }
    const correctMeaning = (hasChineseText(reviewTaskWordTranslation) ? reviewTaskWordTranslation : "") || (hasChineseText(currentItem.review_answer) ? currentItem.review_answer : "") || choice;
    // Play Chinese meaning audio FIRST, then advance
    if (word && correctMeaning) {
      await playEnglishThenChinese(word, correctMeaning);
    }
    setFeedback("选择正确！", "success");
    // Brief pause so user can see the green highlight, then advance
    await new Promise((resolve) => window.setTimeout(resolve, 600));
    setSelectedChoice(null);
    setChoiceResult(null);
    updateAnswerState("sentence-complete");
    setFeedback("选择正确！请点击「下一句」按钮继续。", "success");
  }, [currentItem, currentWords, getSourceLearningItemId, isChoiceReviewTask, playEnglishThenChinese, reviewTaskWordTranslation, selectedChoice, updateAnswerState]);

  function handleMistakePracticeKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      void checkMistakePracticeWord();
    }
  }

  const beginPendingMistakePractice = useCallback(async function beginPendingMistakePractice() {
    const words = pendingMistakePracticeWordsRef.current;
    if (words.length === 0) {
      return false;
    }

    let practiceTranslations = pendingMistakePracticeTranslationsRef.current;
    const hasMissingTranslation = words.some((word) => !practiceTranslations[word]);
    if (hasMissingTranslation) {
      practiceTranslations = { ...practiceTranslations, ...(await translateMistakePracticeWords(words)) };
    }

    setPendingMistakePractice([], {});
    setMistakePracticeWords(words);
    setMistakePracticeTranslations(practiceTranslations);
    setMistakePracticeIndex(0);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeConsecutiveCorrect(0);
    setMistakeMeaningQuizOptions([]);
    setMistakeMeaningQuizSelected(null);
    setMistakeMeaningQuizCorrect("");
    clearMistakePracticePreview();
    updateAnswerState("mistake-word-practice");
    setFeedback("现在进入错词单独拼写（每词需连续正确 3 次后完成中文意思选择）。");
    const firstTranslation = practiceTranslations[words[0]];
    if (firstTranslation) {
      await playChineseThenEnglish(firstTranslation, words[0]);
    }
    return true;
  }, [clearMistakePracticePreview, playChineseThenEnglish, setPendingMistakePractice, translateMistakePracticeWords, updateAnswerState]);

  useEffect(() => {
	    function handleWindowKeyDown(event: globalThis.KeyboardEvent) {
	      const isSpace = event.key === " ";
	      const isEnter = event.key === "Enter";
			      // Detect number keys from BOTH main keyboard AND numeric keypad
			      const digitMatch = /^(Digit|Numpad)([1-6])$/.exec(event.code);
			      const isDigit = digitMatch !== null || (event.key >= "1" && event.key <= "6");
			      const digitIdx = digitMatch ? parseInt(digitMatch[2], 10) - 1 : (parseInt(event.key, 10) || 0) - 1;
		      if (!isSpace && !isEnter && !isDigit) {
	        return;
	      }

	      // Read latest state from refs to avoid stale-closure bugs
	      const currentState = answerStateRef.current;
	      const item = currentItemRef.current;
	      const isChoiceTask = item?.review_task_type === "listen_choose_chinese" || item?.review_task_type === "english_to_chinese" || item?.review_task_type === "match_translation";
	      const choiceItems = choiceOptionsRef.current;

	      // Debug log - record what key was pressed and what state we're in
	      setDebugLog((prev) => [...prev.slice(-5), `key="${event.key}" code="${event.code}" isDigit=${isDigit} isSpace=${isSpace} state=${currentState} isChoiceTask=${isChoiceTask} options=${choiceItems?.length ?? 0} sel=${selectedChoiceRef.current ?? "-"} res=${choiceResultRef.current ?? "-"}`]);

		      // Number keys 1-6: select choice (choice review tasks in typing state)
		      if (isDigit && isChoiceTask && currentState === "typing" && choiceResult === null) {
		        event.preventDefault();
		        // digitIdx computed from event.code above
		        const choiceItems = choiceOptionsRef.current;
		        if (choiceItems && digitIdx >= 0 && digitIdx < choiceItems.length) {
		          setSelectedChoice(choiceItems[digitIdx]);
		          setFeedback(`已选择第 ${digitIdx + 1} 项，按空格键确认`, "info");
		        }
		        return;
		      }



		      // Number keys 1-6: select meaning quiz option in mistake practice
		      if (isDigit && currentState === "mistake-meaning-quiz") {
		        event.preventDefault();
		        // digitIdx computed from event.code above
		        if (digitIdx >= 0 && digitIdx < mistakeMeaningQuizOptionsRef.current.length && mistakeMeaningQuizSelectedRef.current === null) {
		          handleMistakeMeaningChoice(mistakeMeaningQuizOptionsRef.current[digitIdx]);
		        }
		        return;
		      }

	      // Space: confirm choice selection
	      if (isSpace && isChoiceTask && currentState === "typing") {
	        event.preventDefault();
	        const now = Date.now();
	        if (now - spaceCooldownRef.current < 400) return;
	        spaceCooldownRef.current = now;
	        void confirmChoiceSelection();
	        return;
	      }

	      // Space in typing/mistake-practice: let per-input handlers process it
	      if (isSpace && (currentState === "typing" || currentState === "mistake-word-practice")) {
	        return;
	      }

	      // Ignore keys when focus is inside an input/textarea
	      // Exception: Enter in sentence-complete state is allowed
	      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
	        if (!(isEnter && currentState === "sentence-complete")) {
	          return;
	        }
	      }

	      event.preventDefault();

	      // Cooldown lock: ignore rapid repeated presses within 400ms
	      const now = Date.now();
	      if (now - spaceCooldownRef.current < 400) {
	        return;
	      }
	      spaceCooldownRef.current = now;

	      if (currentState === "sentence-complete") {
	        if (event.repeat) {
	          return;
	        }
	        if (pendingMistakePracticeWordsRef.current.length > 0) {
	          void beginPendingMistakePractice();
	          return;
	        }
	        // Enter key: equivalent to clicking "下一句" button
	        if (isEnter) {
	          answerStateRef.current = "typing";
	          handleNextItem({ completedCurrentItem: true });
	          return;
	        }
	        // Space: do NOT auto-advance — press Enter or click button
	        return;
	      }
	    }

    window.addEventListener("keydown", handleWindowKeyDown);
    return () => window.removeEventListener("keydown", handleWindowKeyDown);
  }, [handleNextItem, beginPendingMistakePractice, confirmChoiceSelection]);

  const wordInputSizeClass = isStudyFullscreen
    ? "study-word-input study-word-input--immersive h-24 ipad:h-28 ipad-lg:h-32"
    : "study-word-input h-20 ipad:h-24";
  const wordDisplaySizeClass = isStudyFullscreen
    ? "h-24 text-5xl ipad:h-28 ipad:text-6xl ipad-lg:h-32 ipad-lg:text-7xl"
    : "h-20 text-4xl ipad:h-20 ipad:text-4xl ipad-lg:h-24 ipad-lg:text-5xl";
  const actionButtonClass = isStudyFullscreen
    ? "min-w-28 ipad:min-w-32 ipad:px-5 ipad:text-base ipad-lg:min-w-36 ipad-lg:text-lg"
    : "min-w-32 ipad:min-w-32 ipad:text-sm ipad-lg:min-w-36 ipad-lg:text-base";
  const displayChinesePrompt = isStudyFullscreen && answerState === "mistake-word-practice"
    ? currentMistakePracticeTranslation || currentItem?.chinese_text || ""
    : isFocusedWordReview && currentItem?.source?.startsWith("聚焦 ") && hasChineseText(currentItem?.chinese_text)
      ? currentItem.chinese_text  // focus mode: pre-cached meaning from backend
    : isChoiceReviewTask
      ? "请选择这个英文单词的中文意思"
    : currentItem?.review_task_type === "listen_spell" && !hasChineseText(currentItem?.chinese_text)
      ? "听英文发音后拼写"
    : isFocusedWordReview
      ? (hasChineseText(currentItem?.chinese_text) ? currentItem.chinese_text : reviewTaskWordTranslation) || ""
      : currentItem?.chinese_text || "";

  return (
    <>
      <style>{`
        @keyframes check-pop {
          0% { transform: scale(0); opacity: 0; }
          60% { transform: scale(1.2); opacity: 1; }
          100% { transform: scale(1.0); opacity: 1; }
        }
        @keyframes incorrect-spin {
          0% { transform: rotate(0deg); opacity: 0; }
          100% { transform: rotate(360deg); opacity: 1; }
        }
        @keyframes confetti-burst {
          0% { transform: translate(0, 0) scale(1); opacity: 1; }
          100% { transform: translate(calc(var(--tx) * 1px), calc(var(--ty) * 1px)) scale(0); opacity: 0; }
        }
        @keyframes star-join {
          0% { transform: scale(0) rotate(-180deg); opacity: 0; }
          60% { transform: scale(1.3) rotate(15deg); opacity: 1; }
          100% { transform: scale(1) rotate(0deg); opacity: 1; }
        }
        @keyframes fire-pulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.15); }
        }
        @keyframes streak-flash {
          0% { transform: scale(1); }
          50% { transform: scale(1.4); }
          100% { transform: scale(1); }
        }
        @keyframes scale-fade {
          0% { transform: scale(0.5); opacity: 0; }
          30% { transform: scale(1.1); opacity: 1; }
          60% { transform: scale(1); opacity: 1; }
          100% { transform: scale(0.8); opacity: 0; }
        }
      `}</style>
      <MiniCelebrationConfetti key={`streak-${streakCelebrateKey}`} triggerKey={streakCelebrateKey} />
      <MiniCelebrationConfetti key={`milestone-${milestoneCelebrateKey}`} triggerKey={milestoneCelebrateKey} speakMessage="太厉害了！" />
      {(() => {
        const modeConfig: Record<StudyMode, { label: string; description: string; color: string; bg: string; border: string }> = {
          review: {
            label: "📚 单词复习",
            description: "只显示所有课程的到期复习单词",
            color: "text-violet-700",
            bg: "bg-violet-50",
            border: "border-violet-200",
          },
          learn: {
            label: "📝 新句子学",
            description: "只显示本课的新内容（句子/短语/单词）",
            color: "text-amber-700",
            bg: "bg-amber-50",
            border: "border-amber-200",
          },
          mix: {
            label: "🔄 混合模式",
            description: "复习 + 新内容一起",
            color: "text-slate-700",
            bg: "bg-slate-50",
            border: "border-slate-200",
          },
        };
        const current = modeConfig[studyMode];
        const otherModes = (["review", "learn", "mix"] as StudyMode[]).filter((m) => m !== studyMode);
        return (
          <div className={`mx-4 mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border ${current.border} ${current.bg} px-4 py-2.5 ipad:mx-6 ipad:mt-4 ipad:px-5 ipad:py-3`}>
            <div className="flex items-center gap-3">
              <span className={`text-sm font-bold ipad:text-base ${current.color}`}>{current.label}</span>
              <span className="text-xs text-slate-600 ipad:text-sm">{current.description}</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500 ipad:text-sm">切换：</span>
              {otherModes.map((m) => {
                const cfg = modeConfig[m];
                return (
                  <Button
                    key={m}
                    className="h-7 px-2.5 text-xs ipad:h-8 ipad:px-3 ipad:text-sm"
                    onClick={() => switchStudyMode(m)}
                    onMouseDown={keepStudyInputFocus}
                    type="button"
                    variant="outline"
                  >
                    {cfg.label}
                  </Button>
                );
              })}
            </div>
          </div>
        );
      })()}
      <main className={isStudyFullscreen ? "flex min-h-[100dvh] flex-col overflow-y-auto bg-white text-slate-950" : "flex min-h-[100dvh] flex-col overflow-y-auto bg-slate-50 text-slate-950"}>
      {celebrationSummary ? <CelebrationModal nextCourse={nextCourse} summary={celebrationSummary} /> : null}
      {focusRotatedMessage ? (
        <div className="fixed left-1/2 top-6 z-40 -translate-x-1/2 animate-bounce rounded-full bg-emerald-500 px-6 py-3 text-base font-bold text-white shadow-lg ipad:px-8 ipad:py-4 ipad:text-lg">
          ✅ {focusRotatedMessage}
        </div>
      ) : null}
      {milestoneMessage ? (
        <div className="fixed left-1/2 top-6 z-40 -translate-x-1/2 animate-bounce rounded-full bg-amber-400 px-6 py-3 text-base font-bold text-white shadow-lg ipad:px-8 ipad:py-4 ipad:text-lg">
          {milestoneMessage}
        </div>
      ) : null}
      {isStudyFullscreen ? (
        <>
          <div className="fixed left-4 top-4 z-30 flex items-center gap-2 ipad:left-6 ipad:top-6">
            {perfectSentenceCount > 0 ? (
              <div className="flex items-center gap-1 rounded-full bg-white/90 px-3 py-1.5 text-sm font-bold text-amber-500 shadow-sm backdrop-blur ipad:px-4 ipad:text-base">
                <span key={starAnimKey} style={{ animation: starAnimKey > 0 ? "star-join 0.6s ease-out" : "none" }}>⭐</span>
                <span>{perfectSentenceCount}</span>
              </div>
            ) : null}
          </div>
          <div className="fixed right-4 top-4 z-30 flex items-center gap-2 ipad:right-6 ipad:top-6">
            {currentStreak > 0 ? (
              <div className="flex items-center gap-1 rounded-full bg-white/90 px-3 py-1.5 text-sm font-bold text-orange-500 shadow-sm backdrop-blur ipad:px-4 ipad:text-base" style={{ animation: currentStreak >= 5 ? "fire-pulse 0.6s ease-in-out infinite" : "none" }}>
                <span key={currentStreak} style={{ animation: currentStreak > 0 ? "streak-flash 0.3s ease-out" : "none" }}>🔥</span>
                <span>{currentStreak}</span>
              </div>
            ) : null}
            <Button className={`h-8 px-2.5 text-xs shadow-sm backdrop-blur ipad:h-9 ipad:px-3 ipad:text-sm ${isDictationMode ? "border-violet-500 bg-violet-50 text-violet-700" : "bg-white/90"}`} onClick={toggleDictationMode} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
              {isDictationMode ? "✎ 默写中" : "默写"}
            </Button>
            <Button className="h-8 px-2.5 text-xs shadow-sm backdrop-blur bg-white/90 ipad:h-9 ipad:px-3 ipad:text-sm" onClick={toggleStudyFullscreen} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
              退出
            </Button>
          </div>
        </>
      ) : null}
      {!isStudyFullscreen ? (
        <header className="shrink-0 border-b bg-white/95 px-4 py-2.5 shadow-sm ipad:px-5 ipad:py-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className={`rounded-md border px-3 py-2 text-left transition-colors ${isStudyPaused ? "border-amber-300 bg-amber-50" : "bg-slate-50"}`}>
                <p className="text-xs text-muted-foreground">{isStudyPaused ? "⏸ 已暂停" : "学习时长"}</p>
                <p className={`font-mono text-lg font-semibold ipad:text-xl ${isStudyPaused ? "text-amber-700" : "text-slate-900"}`}>{formatStudyDuration(activeStudySeconds)}</p>
              </div>
              <Link className="text-sm font-medium text-primary hover:underline ipad:text-base" href="/learning">
                返回开始学习
              </Link>
              <div>
                <p className="text-lg font-semibold ipad:text-lg">课程学习</p>
                <p className="text-sm text-muted-foreground ipad:text-sm">{items.length > 0 ? `第 ${currentIndex + 1} 题 / 共 ${items.length} 题` : "拼写练习"}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-3 text-sm font-semibold text-slate-600 ipad:text-base">
              <span>{device.isIPad ? "⌨ 空格判定单词 · 完成后点击按钮继续" : "单词输入完成按空格判定，完成后点击按钮继续"}</span>
              <Button className={`h-9 px-3 text-sm ipad:h-9 ipad:px-3 ipad:text-sm ${isDictationMode ? "border-violet-500 bg-violet-50 text-violet-700" : ""}`} onClick={toggleDictationMode} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
                {isDictationMode ? "✎ 默写中" : "默写模式"}
              </Button>
              <Button className="h-9 px-3 text-sm ipad:h-9 ipad:px-3 ipad:text-sm" onClick={toggleStudyFullscreen} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
                沉浸模式
              </Button>
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted ipad:h-2">
            <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${progressPercent}%` }} />
          </div>
        </header>
      ) : null}

      <section className={isStudyFullscreen ? "flex min-h-0 flex-1 items-center justify-center overflow-visible px-4 py-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] ipad:px-8 ipad:py-6 ipad-lg:px-10 ipad-lg:py-8" : "flex min-h-0 flex-1 items-start justify-center overflow-visible px-4 py-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] ipad:px-5 ipad:py-4 ipad-lg:px-6 ipad-lg:py-5"}>
        {isLoading ? <p className="text-lg font-bold text-muted-foreground ipad:text-xl">正在加载学习内容...</p> : null}
        {!isLoading && errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ipad:px-6 ipad:py-4 ipad:text-base">{errorMessage}</p> : null}
        {!isLoading && !errorMessage && !currentItem ? <p className="text-lg font-bold text-muted-foreground ipad:text-xl">当前课程还没有导入内容。</p> : null}

        {!isLoading && !errorMessage && currentItem ? (
          <div className={isStudyFullscreen ? "w-full max-w-7xl text-center" : "w-full max-w-6xl rounded-lg border border-slate-200 bg-white px-5 py-5 text-center shadow-sm ipad:px-6 ipad:py-5 ipad-lg:px-8 ipad-lg:py-6"}>
            {!isStudyFullscreen ? (
              <div className="flex justify-center gap-3 text-sm font-medium text-slate-500 ipad:text-sm">
              <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">{itemTypeLabels[currentItem.item_type]}</span>
              <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1">{currentIndex + 1} / {items.length}</span>
              </div>
            ) : null}

            <div className={isStudyFullscreen ? "space-y-10 ipad:space-y-12 ipad-lg:space-y-14" : "mt-5 space-y-5 ipad:mt-5 ipad:space-y-4 ipad-lg:mt-6 ipad-lg:space-y-5"}>
              <p className={isStudyFullscreen ? "text-5xl font-semibold leading-tight text-slate-900 ipad:text-6xl ipad-lg:text-7xl" : "text-4xl font-semibold leading-tight text-slate-900 ipad:text-4xl ipad-lg:text-5xl"}>{displayChinesePrompt}</p>
              {currentItem?.phonetic && !encodingStage ? (
                <p className="text-sm font-medium text-violet-600 ipad:text-base">
                  音标: {currentItem.phonetic}
                </p>
              ) : null}
              {encodingStage && encodingStage !== "whole_recall" && answerState === "typing" ? (
                <div className="mx-auto flex max-w-3xl flex-col items-center gap-4">
                  {encodingStage === "listen" ? (
                    <div className="flex flex-col items-center gap-4">
                      <p className="text-4xl font-bold tracking-widest text-slate-900 ipad:text-5xl ipad-lg:text-6xl">{encodingWord}</p>
                      <HintDisplay
                        word={encodingWord}
                        chunks={(currentItem?.syllables && currentItem.syllables.length > 0) ? currentItem.syllables : splitIntoSyllables(encodingWord)}
                        matchedPrefixLength={0}
                        onPlayChunk={function (i) { playSyllableAudio(encodingWord, i); }}
                        onPlayPhonics={function (chunkIndex: number) { playPhonicsAudio(encodingWord, chunkIndex, currentItem?.grapheme_phoneme_map as Record<string, string> | null | undefined); }}
                      />
                      <div className="mt-2 text-base text-violet-600 font-medium ipad:text-lg">
                        {currentItem?.phonetic ? ("音标: " + currentItem.phonetic) : null}
                      </div>
                    </div>
                  ) : encodingStage === "chunk_trace" ? (
                    <div className="flex flex-col items-center gap-4">
                      <p className="text-4xl font-bold tracking-widest text-slate-900 ipad:text-5xl ipad-lg:text-6xl">{encodingWord}</p>
                      <HintDisplay
                        word={encodingWord}
                        chunks={(currentItem?.syllables && currentItem.syllables.length > 0) ? currentItem.syllables : splitIntoSyllables(encodingWord)}
                        matchedPrefixLength={0}
                        onPlayChunk={function (i) { playSyllableAudio(encodingWord, i); }}
                        onPlayPhonics={function (chunkIndex: number) { playPhonicsAudio(encodingWord, chunkIndex, currentItem?.grapheme_phoneme_map as Record<string, string> | null | undefined); }}
                      />
                      <p className="text-sm text-slate-500">点击颜色块或喇叭按钮听发音</p>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {encodingStage === "whole_recall" && previewCountdownSeconds > 0 ? (
                <div className="mx-auto rounded-md bg-amber-100 px-6 py-3 text-4xl font-bold text-amber-950 ipad:text-5xl ipad-lg:text-6xl">{encodingWord}</div>
              ) : null}
              {reviewTaskModeLabel || reviewTaskInstruction ? (
                <div className="mx-auto max-w-2xl space-y-2">
                  {reviewTaskModeLabel ? <p className="text-base font-bold text-emerald-700 ipad:text-lg">{reviewTaskModeLabel}</p> : null}
                  {reviewTaskInstruction ? <p className="text-xl font-bold text-slate-700 ipad:text-2xl">{reviewTaskInstruction}</p> : null}
                </div>
              ) : null}
              {encodingStage && encodingStage !== "whole_recall" && answerState === "typing" ? null : isChoiceReviewTask ? (
                <div className={isStudyFullscreen ? "mx-auto flex max-w-4xl flex-col items-center gap-6 ipad:gap-8" : "mx-auto flex max-w-xl flex-col items-center gap-4 rounded-lg border bg-slate-50 px-5 py-5 ipad:max-w-xl ipad:gap-4 ipad:px-6 ipad:py-5 ipad-lg:max-w-2xl ipad-lg:gap-5 ipad-lg:px-7 ipad-lg:py-6"}>
                  <p className="text-4xl font-bold text-slate-900 ipad:text-5xl ipad-lg:text-6xl">{isListeningChoiceReviewTask ? "听读音，选中文" : currentWords[0]}</p>
                  <p className="text-sm font-medium text-slate-500 ipad:text-base">按数字键 1-6 选择，空格键确认</p>
                  <div className="grid w-full max-w-xl grid-cols-2 gap-3">
                    {(choiceReviewOptions.length > 0 ? choiceReviewOptions : []).map((choice, index) => {
                      const isSelected = selectedChoice === choice;
                      const hasResult = choiceResult !== null;
                      const isChosenResult = hasResult && isSelected;
                      const showCorrect = isChosenResult && choiceResult === "correct";
                      const showIncorrect = isChosenResult && choiceResult === "incorrect";
                      return (
                        <Button
                          className={`justify-center px-4 py-5 text-base font-bold ipad:text-lg transition-colors ${
                            showCorrect ? "border-2 border-emerald-500 bg-emerald-100 text-emerald-800 shadow-sm" : ""
                          } ${
                            showIncorrect ? "border-2 border-red-500 bg-red-100 text-red-800 shadow-sm" : ""
                          } ${
                            !hasResult && isSelected ? "border-2 border-emerald-500 bg-emerald-50 text-emerald-700 shadow-sm" : ""
                          }`}
                          disabled={hasResult || answerState !== "typing" }
                          key={`${choice}-${index}`}
                          onClick={() => { if (answerState === "typing" && !hasResult) { setSelectedChoice(choice); setFeedback(null); } }}
                          type="button"
                          variant={showCorrect || (!hasResult && isSelected) ? "default" : "outline"}
                        >
                          {showCorrect ? "✓ " : showIncorrect ? "✗ " : isSelected ? "✓ " : ""}{index + 1}. {choice}
                        </Button>
                      );
                    })}
                  </div>
                </div>
              ) : answerState === "mistake-word-practice" ? (
                <div className={isStudyFullscreen ? "mx-auto flex max-w-4xl flex-col items-center gap-6 ipad:gap-8" : "mx-auto flex max-w-xl flex-col items-center gap-4 rounded-lg border bg-slate-50 px-5 py-5 ipad:max-w-xl ipad:gap-4 ipad:px-6 ipad:py-5 ipad-lg:max-w-2xl ipad-lg:gap-5 ipad-lg:px-7 ipad-lg:py-6"}>
                  {!isStudyFullscreen ? (
                    <div className="space-y-1 ipad:space-y-2">
                      <p className="text-base font-bold text-muted-foreground ipad:text-base ipad-lg:text-lg">错词单独拼写 {mistakePracticeIndex + 1} / {mistakePracticeWords.length}</p>
                      <p className="text-sm font-bold text-muted-foreground ipad:text-sm ipad-lg:text-base">根据中文拼写正确英文（需连续正确 {MISTAKE_PRACTICE_REQUIRED_CONSECUTIVE} 次）</p>
                      {mistakePracticeConsecutiveCorrect > 0 ? (
                        <p className="text-sm font-bold text-emerald-600">✅ 已连续正确 {mistakePracticeConsecutiveCorrect}/{MISTAKE_PRACTICE_REQUIRED_CONSECUTIVE} 次</p>
                      ) : null}
                      <p className="text-2xl font-bold text-slate-900 ipad:text-2xl ipad-lg:text-3xl">{currentMistakePracticeTranslation || "中文提示准备中"}</p>
                    </div>
                  ) : null}
                  <input
                    ref={mistakePracticeInputRef}
                    aria-label="错词单独拼写"
                    autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false}
                    className={`${wordInputSizeClass} w-full max-w-md border-0 border-b-4 bg-transparent px-1 text-center font-semibold leading-none outline-none transition ipad:max-w-lg ipad-lg:max-w-xl ${mistakePracticeStatus === "incorrect" ? "border-red-500 text-red-700" : "border-emerald-500 text-emerald-700"}`}
                    disabled={previewMistakePracticeWord}
                    placeholder={previewMistakePracticeWord ? "先看后拼" : undefined}
                    value={mistakePracticeAnswer}
                    onChange={(event) => {
                      if (previewMistakePracticeWord) {
                        clearMistakePracticePreview();
                      }
                      setChildHint(null);
                      setMistakePracticeAnswer(event.target.value.replace(/\s/g, ""));
                      setMistakePracticeStatus("idle");
                      setFeedback(null);
                    }}
                    onKeyDown={handleMistakePracticeKeyDown}
                  />
                  {previewMistakePracticeWord ? (
                    <p className="rounded-md bg-amber-100 px-4 py-2 text-3xl font-bold text-amber-950 ipad:text-4xl ipad-lg:text-5xl">{currentMistakePracticeWord}</p>
                  ) : null}
                  {!isStudyFullscreen ? <p className="text-base font-bold text-muted-foreground ipad:text-base ipad-lg:text-lg">
                    {device.isIPad ? "⌨ 空格/回车判定 · 不显示完整答案" : "按空格或回车判定，多次错误会逐级提示，但不直接显示完整答案。"}
                  </p> : null}
                </div>
              ) : answerState === "mistake-meaning-quiz" ? (
                <div className="mx-auto flex max-w-md flex-col items-center gap-4">
                  <p className="text-sm font-medium text-slate-500 ipad:text-base">按数字键 1-6 选择中文意思</p>
                  <div className="grid w-full max-w-md grid-cols-2 gap-3">
                    {mistakeMeaningQuizOptions.map((option, index) => {
                      const isSelected = option === mistakeMeaningQuizSelected;
                      const showCorrect = mistakeMeaningQuizSelected !== null && option === mistakeMeaningQuizCorrect;
                      const isWrongSelected = isSelected && option !== mistakeMeaningQuizCorrect;
                      return (
                        <Button
                          key={option}
                          type="button"
                          disabled={mistakeMeaningQuizSelected !== null}
                          className={`justify-center px-3 py-3 text-sm font-bold ipad:text-base transition-colors ${
                            showCorrect ? "border-2 border-emerald-500 bg-emerald-100 text-emerald-800 shadow-sm" : ""
                          } ${
                            isWrongSelected ? "border-2 border-red-500 bg-red-100 text-red-800 shadow-sm" : ""
                          }`}
                          variant={showCorrect ? "default" : "outline"}
                          onClick={() => handleMistakeMeaningChoice(option)}
                        >
                          {showCorrect ? "✓ " : isWrongSelected ? "✗ " : ""}{index + 1}. {option}
                        </Button>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className={isStudyFullscreen ? "flex flex-wrap items-end justify-center gap-x-5 gap-y-7 ipad:gap-x-7 ipad:gap-y-8 ipad-lg:gap-x-8 ipad-lg:gap-y-10" : "flex flex-wrap items-end justify-center gap-x-4 gap-y-5 ipad:gap-x-4 ipad:gap-y-5 ipad-lg:gap-x-5 ipad-lg:gap-y-6"}>
                  {currentWords.map((word, index) => {
                    const normalizedWord = normalizeEnglishKey(word);
                    const status = wordStatuses[index] ?? "idle";
                    const isDynamicBlank = isDynamicFillBlankItem && dynamicReviewWordIndexes.includes(index);
                    const isRespellRound = respellWordIndexesRef.current.length > 0;
                    const isRespellTarget = isRespellRound && respellWordIndexesRef.current.includes(index);
                    const isRespellNonTarget = isRespellRound && !respellWordIndexesRef.current.includes(index);
                    const shouldShowInput = (!isDynamicFillBlankItem && !isRespellNonTarget) || isDynamicBlank;
                    return (
                      <WordInput
                        key={`${word}-${index}`}
                        word={word}
                        index={index}
                        status={status}
                        value={wordAnswers[index] ?? ""}
                        isActive={activeWordIndex === index && answerState === "typing" && shouldShowInput}
                        isDynamicBlank={isDynamicBlank && isSentenceCloze}
                        isRespellNonTarget={isRespellNonTarget}
                        isRespellTarget={isRespellTarget}
                        shouldShowInput={shouldShowInput}
                        isPreview={previewWordIndex === index}
                        answerState={answerState}
                        wordInputSizeClass={wordInputSizeClass}
                        wordDisplaySizeClass={wordDisplaySizeClass}
                        isStudyFullscreen={isStudyFullscreen}
                        itemTypeLabel={commonPartLabels[normalizedWord] ?? itemTypeLabels[currentItem.item_type]}
                        chineseText={currentItem.chinese_text}
                        meaningText={sentenceWordMeanings[index]}
                        animationType={wordAnimations[index]?.type}
                        inputRef={(element) => {
                          inputRefs.current[index] = element;
                        }}
                        onUpdateWordAnswer={updateWordAnswer}
                        onFocusWord={(idx) => setActiveWordIndex(idx)}
                        onWordKeyDown={handleWordKeyDown}
                      />
                    );
                  })}
                </div>
              )}
              {previewCountdownSeconds > 0 ? (
                <div className="mx-auto mb-2 w-full max-w-md">
                  <div className="mb-1 text-center text-sm font-bold text-amber-700 ipad:text-base ipad-lg:text-lg">
                    记住它！还剩 {previewCountdownSeconds} 秒
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-amber-100">
                    <div className="h-full rounded-full bg-amber-500 transition-all duration-200 ease-linear" style={{ width: `${(previewCountdownSeconds / Math.round(WORD_PREVIEW_MS / 1000)) * 100}%` }} />
                  </div>
                </div>
              ) : null}
              {childHint ? ( <div className="flex flex-col items-center gap-2"> <p className={isStudyFullscreen ? "text-xl font-bold text-red-600 ipad:text-2xl ipad-lg:text-3xl" : "text-base font-bold text-red-600 ipad:text-base ipad-lg:text-lg"}>{childHint.text}</p> <HintDisplay word={childHint.word} chunks={childHint.chunks} matchedPrefixLength={childHint.matchedPrefixLength} onPlayChunk={(i) => playSyllableAudio(childHint.word, i, currentItem?.syllables, (blob) => { void playAudioBlob(blob); })} onPlayPhonics={(i) => { void playPhonicsAudio(childHint.word, i, currentItem?.grapheme_phoneme_map, currentItem?.syllables, (blob) => { void playAudioBlob(blob); }); }} graphemePhonemeMap={currentItem?.grapheme_phoneme_map} cachedSyllables={currentItem?.syllables} /> </div> ) : feedbackMessage ? <p key={celebrationTrigger} className={`${isStudyFullscreen ? `text-xl font-bold ipad:text-2xl ipad-lg:text-3xl ${feedbackType === "error" ? "text-red-600" : feedbackType === "success" ? "text-emerald-700" : "text-slate-600"}` : `text-base font-bold ipad:text-base ipad-lg:text-lg ${feedbackType === "error" ? "text-red-600" : feedbackType === "success" ? "text-emerald-600" : "text-slate-600"}`} ${celebrationTrigger > 0 ? "celebration-burst" : ""}`}>{feedbackMessage}</p> : null}
              <div className={isStudyFullscreen ? "mx-auto flex w-full max-w-5xl flex-wrap items-center justify-center gap-4 px-0 py-0 ipad:gap-5 ipad-lg:gap-6" : "mx-auto flex w-full max-w-3xl flex-col items-center gap-4 rounded-lg border border-slate-200 bg-slate-50/90 px-4 py-4 ipad:max-w-3xl ipad:flex-col ipad:justify-center ipad:gap-4 ipad:px-5 ipad-lg:max-w-4xl ipad-lg:flex-row ipad-lg:gap-6 ipad-lg:px-6"}>
                <div className="flex flex-wrap justify-center gap-3 ipad:gap-3 ipad-lg:gap-4">
                  {answerState === "mistake-word-practice" ? (
                    <>
                      <Button className={actionButtonClass} onClick={() => runStudyButtonAction(checkMistakePracticeWord)} onMouseDown={keepStudyInputFocus} type="button">判定错词</Button>
                      <Button className={actionButtonClass} onClick={handleSpeakEnglish} onMouseDown={keepStudyInputFocus} type="button" variant="secondary">朗读英文</Button>
                    </>
                  ) : (
                    <>
                      <Button className={actionButtonClass} disabled={answerState !== "typing" || isChoiceReviewTask} onClick={() => runStudyButtonAction(() => checkWord(activeWordIndex))} onMouseDown={keepStudyInputFocus} type="button">
                        {(isDynamicFillBlankItem && isSentenceCloze) ? "判定填空" : "判定单词"}
                      </Button>
                      <Button className={actionButtonClass} onClick={handleSpeakEnglish} onMouseDown={keepStudyInputFocus} type="button" variant="secondary">朗读英文</Button>
                    </>
                  )}
                </div>
                {!isStudyFullscreen ? <div className="h-px w-full bg-slate-200 ipad:h-px ipad:w-full ipad-lg:h-9 ipad-lg:w-px" /> : null}
                <div className="flex flex-wrap justify-center gap-3 ipad:gap-3 ipad-lg:gap-4">
                  {answerState === "sentence-complete" ? (
                    <Button
                      className={actionButtonClass}
                      onMouseDown={keepStudyInputFocus}
                      onClick={() => {
                        runStudyButtonAction(() => {
                          if (pendingMistakePracticeWordsRef.current.length > 0) {
                            return beginPendingMistakePractice();
                          }
                          answerStateRef.current = "typing";
                          handleNextItem({ completedCurrentItem: true });
                        });
                      }}
                      type="button"
                    >
                      {pendingMistakePracticeWords.length > 0 ? "进入错词复习" : "下一句"}
                    </Button>
                  ) : null}
                  <Button className={actionButtonClass} onClick={() => runStudyButtonAction(resetAnswer)} onMouseDown={keepStudyInputFocus} type="button" variant="secondary">
                    再来一次
                  </Button>
                  <Button className={actionButtonClass} onClick={() => runStudyButtonAction(() => handleNextItem())} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
                    跳过
                  </Button>
                  {isStudyFullscreen ? (
                    <>
                      <Button className={`${actionButtonClass} ${isDictationMode ? "border-violet-500 bg-violet-50 text-violet-700" : ""}`} onClick={toggleDictationMode} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
                        {isDictationMode ? "✎ 默写中" : "默写模式"}
                      </Button>
                      <Button className={actionButtonClass} onClick={toggleStudyFullscreen} onMouseDown={keepStudyInputFocus} type="button" variant="outline">
                        退出沉浸
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </section>
      {/* Debug overlay - shows key press diagnostics */}
      <div className="fixed bottom-0 left-0 right-0 z-50 max-h-32 overflow-y-auto bg-black/90 p-2 text-xs font-mono text-green-400">
        {debugLog.length === 0 ? <p>等待按键... (按 1-6 或空格测试)</p> : null}
        {debugLog.map((entry, i) => (
          <p key={i}>{entry}</p>
        ))}
      </div>
    </main>
    <style>{`
      @keyframes celebration-star-burst {
        0% { transform: scale(0); opacity: 0; }
        50% { transform: scale(1.3); opacity: 1; }
        100% { transform: scale(1); opacity: 1; }
      }
      .celebration-burst {
        animation: celebration-star-burst 400ms ease-out;
        display: inline-block;
        position: relative;
      }
      .celebration-burst::before {
        content: "\\2736";
        position: absolute;
        left: -2rem;
        top: 50%;
        transform: translateY(-50%);
        animation: celebration-star-burst 400ms ease-out;
        font-size: 2rem;
        color: #f59e0b;
      }
    `}</style>
    </>
  );
}

export default function StudyPage() {
  return (
    <Suspense fallback={<main className="flex min-h-screen items-center justify-center bg-white text-sm text-muted-foreground">正在加载学习内容...</main>}>
      <StudyContent />
    </Suspense>
  );
}
