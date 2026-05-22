"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { KeyboardEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useDevice } from "@/hooks/use-device";
import { getAccessToken, getAuthUser } from "@/lib/auth";
import { addCourseCompletion, formatCourseDuration } from "@/lib/course-progress";
import { Course, listCoursePackages, listCourses } from "@/lib/courses";
import { generateDynamicSentence, generateLearningEncouragement, LearningItem, listDueReviewItems, listLearningItems, logWordMistake, translateLearningText } from "@/lib/learning";
import { recordCourseCompletion, recordStudyTime, scheduleMemoryReview } from "@/lib/memory";
import { ModelSettings, defaultModelSettings, getModelSettings, loadPersistedModelSettings } from "@/lib/model-settings";
import { playAudioBlob, synthesizeKokoroSpeech, synthesizeVolcengineSpeech, TtsSynthesisOptions } from "@/lib/tts";

const itemTypeLabels: Record<LearningItem["item_type"], string> = {
  word: "单词",
  phrase: "短语",
  sentence: "句子",
};

const annotationColors = ["border-primary", "border-emerald-500", "border-sky-500", "border-violet-500", "border-amber-500"] as const;
const STUDY_IDLE_TIMEOUT_MS = 20_000;
const STUDY_TIME_FLUSH_SECONDS = 10;
const DUE_REVIEW_POLL_MS = 60_000;
const MANUAL_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: -20,
  speed: 0.8,
};
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

interface CelebrationSummary {
  courseName: string;
  durationSeconds: number;
  correctWordCount: number;
  encouragementChineseText: string;
  encouragementEnglishText: string;
  canContinue: boolean;
  statusMessage: string;
}

interface NextCourseTarget {
  id: string;
  name: string;
  packageId: string;
}

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

function speakWithBrowser(text: string, language: string, playbackRate = 0.85): Promise<void> {
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

async function speakWithKokoro(text: string, voice: string, language: string, settings: ModelSettings, options: TtsSynthesisOptions = {}) {
  if (!text.trim()) {
    return;
  }

  try {
    const audioBlob = settings.ttsProvider === "volcark" ? await speakWithVolcengine(text, voice, language, settings, options) : await speakWithKokoroApi(text, voice, settings, options);
    await playAudioBlob(audioBlob);
  } catch (error) {
    console.warn("课程学习 TTS 播放失败，已回退到浏览器语音。", error);
    await speakWithBrowser(text, language, options.speed ?? 0.85);
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

function getFallbackEncouragement(courseName: string): { chineseText: string; englishText: string } {
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

function getDynamicReviewWords(item: LearningItem | null): string[] {
  if (!item?.source?.startsWith("AI 动态复习：")) {
    return [];
  }
  return item.source
    .replace("AI 动态复习：", "")
    .split(",")
    .map(normalizeEnglishKey)
    .filter(Boolean);
}

function mergeLearningQueues(reviewItems: LearningItem[], courseItems: LearningItem[]): LearningItem[] {
  const seenItemIds = new Set<string>();
  return [...reviewItems, ...courseItems].filter((item) => {
    if (seenItemIds.has(item.id)) {
      return false;
    }
    seenItemIds.add(item.id);
    return true;
  });
}

async function speakWithKokoroApi(text: string, voice: string, settings: ModelSettings, options: TtsSynthesisOptions = {}): Promise<Blob> {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error("Login required for Kokoro TTS");
  }

  return synthesizeKokoroSpeech(text, voice, settings, accessToken, options);
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

function CelebrationConfetti({ runKey }: { runKey: number }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) {
      return;
    }

    const dpr = window.devicePixelRatio || 1;
    const resizeCanvas = () => {
      canvas.width = Math.floor(canvas.clientWidth * dpr);
      canvas.height = Math.floor(canvas.clientHeight * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resizeCanvas();

    const colors = ["#10b981", "#0ea5e9", "#f59e0b", "#ef4444", "#8b5cf6"];
    const pieces = Array.from({ length: 150 }, () => ({
      x: Math.random() * canvas.clientWidth,
      y: -20 - Math.random() * canvas.clientHeight * 0.6,
      size: 6 + Math.random() * 7,
      color: colors[Math.floor(Math.random() * colors.length)],
      rotation: Math.random() * Math.PI,
      rotationSpeed: -0.18 + Math.random() * 0.36,
      speedX: -2.5 + Math.random() * 5,
      speedY: 2 + Math.random() * 4,
    }));

    let animationFrame = 0;
    const startedAt = performance.now();
    const drawFrame = (time: number) => {
      context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      pieces.forEach((piece) => {
        piece.x += piece.speedX;
        piece.y += piece.speedY;
        piece.rotation += piece.rotationSpeed;
        if (piece.y > canvas.clientHeight + 30) {
          piece.y = -20;
          piece.x = Math.random() * canvas.clientWidth;
        }

        context.save();
        context.translate(piece.x, piece.y);
        context.rotate(piece.rotation);
        context.fillStyle = piece.color;
        context.fillRect(-piece.size / 2, -piece.size / 2, piece.size, piece.size * 0.55);
        context.restore();
      });

      if (time - startedAt < 5200) {
        animationFrame = window.requestAnimationFrame(drawFrame);
      }
    };

    animationFrame = window.requestAnimationFrame(drawFrame);
    window.addEventListener("resize", resizeCanvas);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", resizeCanvas);
    };
  }, [runKey]);

  return <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />;
}

function CelebrationModal({ nextCourse, summary }: { nextCourse: NextCourseTarget | null; summary: CelebrationSummary }) {
  const nextCourseHref = nextCourse
    ? `/learning/study?course_id=${nextCourse.id}&package_id=${nextCourse.packageId}&course_name=${encodeURIComponent(nextCourse.name)}`
    : "/learning";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-slate-950/60 px-6">
      <CelebrationConfetti runKey={summary.durationSeconds + summary.correctWordCount} />
      <div className="relative z-10 w-full max-w-md rounded-lg bg-white p-7 text-center shadow-2xl ipad:max-w-lg ipad:p-10">
        <p className="text-sm font-semibold text-primary ipad:text-base">学习完成</p>
        <h2 className="mt-2 text-3xl font-bold tracking-tight ipad:text-4xl">{summary.courseName}学习完成</h2>
        <div className="mt-6">
          <div className="rounded-lg border bg-slate-50 px-4 py-4 ipad:px-6 ipad:py-5">
            <p className="text-xs text-muted-foreground ipad:text-sm">本次用时</p>
            <p className="mt-1 text-xl font-bold text-slate-900 ipad:text-3xl">{formatCourseDuration(summary.durationSeconds)}</p>
          </div>
        </div>
        <div className="mt-5 rounded-lg border bg-emerald-50 px-4 py-4 text-left ipad:mt-6 ipad:px-6 ipad:py-5">
          <p className="text-sm font-semibold text-emerald-700 ipad:text-base">鼓励语</p>
          <p className="mt-2 text-base font-bold text-slate-900 ipad:text-lg">{summary.encouragementChineseText}</p>
          <p className="mt-1 text-sm font-medium text-slate-700 ipad:text-base">{summary.encouragementEnglishText}</p>
          <p className="mt-3 text-xs text-muted-foreground ipad:text-sm">{summary.statusMessage}</p>
        </div>
        <div className="mt-6 flex flex-wrap justify-center gap-3 ipad:gap-4">
          {summary.canContinue ? (
            <Button asChild className="ipad:text-lg ipad:px-6 ipad:py-3">
              <Link href={nextCourseHref}>{nextCourse ? "学习下一课" : "返回开始学习"}</Link>
            </Button>
          ) : (
            <Button disabled type="button" className="ipad:text-lg ipad:px-6 ipad:py-3">{nextCourse ? "朗读后进入下一课" : "朗读后返回"}</Button>
          )}
          <Button asChild variant="secondary" className="ipad:text-lg ipad:px-6 ipad:py-3">
            <Link href="/learning">返回开始学习</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}

function StudyContent() {
  const searchParams = useSearchParams();
  const courseId = searchParams.get("course_id") ?? "";
  const packageIdFromUrl = searchParams.get("package_id") ?? "";
  const courseName = searchParams.get("course_name") ?? "本课";
  const device = useDevice();
  const [items, setItems] = useState<LearningItem[]>([]);
  const [packageCourses, setPackageCourses] = useState<Course[]>([]);
  const [resolvedPackageId, setResolvedPackageId] = useState("");
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
  const [pendingMistakePracticeWords, setPendingMistakePracticeWords] = useState<string[]>([]);
  const [deferredDynamicWords, setDeferredDynamicWords] = useState<string[]>([]);
  const [hasFinalizedCurrentItem, setHasFinalizedCurrentItem] = useState(false);
  const [startedAt, setStartedAt] = useState<number>(() => Date.now());
  const [isTranslatingWords, setIsTranslatingWords] = useState(false);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);
  const [activeStudySeconds, setActiveStudySeconds] = useState(0);
  const [celebrationSummary, setCelebrationSummary] = useState<CelebrationSummary | null>(null);
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const mistakePracticeInputRef = useRef<HTMLInputElement | null>(null);
  const activeStudyMsRef = useRef(0);
  const pendingStudyMsRef = useRef(0);
  const courseRunStartedActiveMsRef = useRef(0);
  const courseRunCorrectWordKeysRef = useRef<Set<string>>(new Set());
  const lastStudyTickAtRef = useRef(Date.now());
  const lastKeyboardAtRef = useRef(Date.now());
  const isFlushingStudyTimeRef = useRef(false);
  const voiceSequenceRef = useRef(0);
  const modelSettingsRef = useRef<ModelSettings>(defaultModelSettings);
  const answerStateRef = useRef<AnswerState>("typing");
  const isTranslatingWordsRef = useRef(false);
  const isCompletingSentenceRef = useRef(false);
  const areWordTranslationsReadyRef = useRef(false);
  const pendingMistakePracticeWordsRef = useRef<string[]>([]);
  const pendingMistakePracticeTranslationsRef = useRef<Record<string, string>>({});
  const spaceCooldownRef = useRef(0);
  const queuedReviewItemIdsRef = useRef<Set<string>>(new Set());
  const currentIndexRef = useRef(0);
  const currentItemRef = useRef<LearningItem | null>(null);
  const celebrationSummaryRef = useRef<CelebrationSummary | null>(null);

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
    return {
      id: targetCourse.id,
      name: targetCourse.name,
      packageId,
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
    currentIndexRef.current = currentIndex;
    currentItemRef.current = currentItem;
    celebrationSummaryRef.current = celebrationSummary;
  }, [celebrationSummary, currentIndex, currentItem]);

  useEffect(() => {
    answerStateRef.current = answerState;
  }, [answerState]);

  useEffect(() => {
    isTranslatingWordsRef.current = isTranslatingWords;
  }, [isTranslatingWords]);

  const updateAnswerState = useCallback(function updateAnswerState(nextState: AnswerState) {
    answerStateRef.current = nextState;
    setAnswerState(nextState);
  }, []);

  const setPendingMistakePractice = useCallback(function setPendingMistakePractice(words: string[], translations: Record<string, string>) {
    pendingMistakePracticeWordsRef.current = words;
    pendingMistakePracticeTranslationsRef.current = translations;
    setPendingMistakePracticeWords(words);
  }, []);

  const getRequiredWordIndexes = useCallback(function getRequiredWordIndexes(): number[] {
    return isDynamicFillBlankItem ? dynamicReviewWordIndexes : currentWords.map((_, index) => index);
  }, [currentWords, dynamicReviewWordIndexes, isDynamicFillBlankItem]);

  function areRequiredWordAnswersCorrect(nextAnswers: string[]): boolean {
    return getRequiredWordIndexes().every((wordIndex) => normalizeTypedWord(nextAnswers[wordIndex] ?? "") === normalizeTypedWord(currentWords[wordIndex] ?? ""));
  }

  const buildMistakePracticeTranslations = useCallback(function buildMistakePracticeTranslations(words: string[], translations: string[]): Record<string, string> {
    const translationByWord: Record<string, string> = {};
    words.forEach((word) => {
      const normalizedWord = normalizeEnglishKey(word);
      const translatedIndex = currentWords.findIndex((currentWord) => normalizeEnglishKey(currentWord) === normalizedWord);
      translationByWord[word] = translatedIndex >= 0 ? translations[translatedIndex] || "" : "";
    });
    return translationByWord;
  }, [currentWords]);

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
    await speakWithKokoro(text, voice, language, settings, options);
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

  const focusInputAtEnd = useCallback(function focusInputAtEnd(input: HTMLInputElement | null) {
    if (!input) {
      return;
    }
    input.focus();
    const cursorPosition = input.value.length;
    input.setSelectionRange(cursorPosition, cursorPosition);
  }, []);

  const focusCurrentUnfinishedWord = useCallback(function focusCurrentUnfinishedWord() {
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
  }, [activeWordIndex, currentWords, focusInputAtEnd, getRequiredWordIndexes, wordAnswers]);

  const handleSpeakEnglish = useCallback(function handleSpeakEnglish() {
    if (!currentItem?.english_text) {
      return;
    }
    focusCurrentUnfinishedWord();
    const sequenceId = startVoiceSequence();
    const settings = modelSettingsRef.current;
    void speakInVoiceSequence(sequenceId, currentItem.english_text, settings.ttsEnglishVoice, "en-US", settings, MANUAL_ENGLISH_TTS_OPTIONS).finally(() => {
      window.setTimeout(focusCurrentUnfinishedWord, 0);
    });
  }, [currentItem, focusCurrentUnfinishedWord, speakInVoiceSequence, startVoiceSequence]);

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
    lastKeyboardAtRef.current = Date.now();
    setActiveStudySeconds(0);
    setCelebrationSummary(null);
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
    setPendingMistakePractice([], {});
    setHasFinalizedCurrentItem(false);
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    updateAnswerState("typing");
    setIsTranslatingWords(false);
    isTranslatingWordsRef.current = false;
    isCompletingSentenceRef.current = false;
    areWordTranslationsReadyRef.current = false;
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]?.focus(), 0);
    const sequenceId = startVoiceSequence();
    if (currentItem?.chinese_text && currentItem.english_text) {
      void playCurrentItemIntro(currentItem, sequenceId);
    }
  }, [currentItem, currentWords, dynamicReviewWordIndexes, playCurrentItemIntro, setPendingMistakePractice, startVoiceSequence, updateAnswerState]);

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
        const [dueReviewItems, nextItems] = await Promise.all([
          listDueReviewItems(accessToken, courseId).catch(() => [] as LearningItem[]),
          listLearningItems(accessToken, courseId),
        ]);
        queuedReviewItemIdsRef.current = new Set(dueReviewItems.map((item) => item.id));
        const mergedItems = mergeLearningQueues(dueReviewItems, nextItems);
        const savedIndex = Number(window.localStorage.getItem(getStudyProgressKey(courseId)) ?? "0");
        const safeIndex = dueReviewItems.length === 0 && Number.isInteger(savedIndex) && savedIndex >= 0 && savedIndex < mergedItems.length ? savedIndex : 0;
        setItems(mergedItems);
        setCurrentIndex(safeIndex);
        setFeedbackMessage(dueReviewItems.length > 0 ? `先复习 ${dueReviewItems.length} 条历史错词/到期内容，再进入当前课程。` : null);
      } catch (error) {
        setErrorMessage(error instanceof Error ? error.message : "读取学习内容失败");
      } finally {
        setIsLoading(false);
      }
    }

    void loadStudyItems();
  }, [courseId]);

  const insertDueReviewItemsAfterCurrent = useCallback(async function insertDueReviewItemsAfterCurrent() {
    const accessToken = getAccessToken();
    const activeItem = currentItemRef.current;
    if (!accessToken || !courseId || !activeItem || celebrationSummaryRef.current) {
      return;
    }

    const dueReviewItems = await listDueReviewItems(accessToken, courseId, 6).catch(() => [] as LearningItem[]);
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
    setFeedbackMessage(`已插入 ${freshReviewItems.length} 条新的到期复习内容，完成当前题后会优先复习。`);
  }, [courseId]);

  useEffect(() => {
    if (!courseId || isLoading) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      void insertDueReviewItemsAfterCurrent();
    }, 15_000);
    const intervalId = window.setInterval(() => {
      void insertDueReviewItemsAfterCurrent();
    }, DUE_REVIEW_POLL_MS);

    return () => {
      window.clearTimeout(timeoutId);
      window.clearInterval(intervalId);
    };
  }, [courseId, insertDueReviewItemsAfterCurrent, isLoading]);

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
    setWordTranslations(currentWords.map(() => ""));
    setMistakenWords([]);
    setMistakePracticeWords([]);
    setMistakePracticeIndex(0);
    setMistakePracticeAnswer("");
    setMistakePracticeStatus("idle");
    setMistakePracticeErrorCount(0);
    setMistakePracticeTranslations({});
    setPendingMistakePractice([], {});
    setHasFinalizedCurrentItem(false);
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    updateAnswerState("typing");
    setIsTranslatingWords(false);
    isTranslatingWordsRef.current = false;
    isCompletingSentenceRef.current = false;
    areWordTranslationsReadyRef.current = false;
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]?.focus(), 0);
  }

  function markCorrectWord(item: LearningItem, word: string, index: number | string) {
    const normalizedWord = normalizeEnglishKey(word);
    if (!normalizedWord) {
      return;
    }

    courseRunCorrectWordKeysRef.current.add(`${item.id}:${index}:${normalizedWord}`);
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

    setCurrentIndex((index) => {
      const nextIndex = (index + 1) % items.length;
      window.localStorage.setItem(getStudyProgressKey(courseId), String(nextIndex));
      if (options.completedCurrentItem && nextIndex === 0) {
        window.setTimeout(showCourseCompletion, 0);
      }
      return nextIndex;
    });
  }, [courseId, items.length, showCourseCompletion]);

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
    if (!currentItem || isCompletingSentenceRef.current || answerStateRef.current !== "typing") {
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
      setPendingMistakePractice(uniqueMistakenWords, {});
      updateAnswerState("sentence-complete");
      isCompletingSentenceRef.current = false;
      setFeedbackMessage(
        dynamicSentenceFeedback
          ? `${dynamicSentenceFeedback} 本句有错词，先按空格查看每个单词的中文意思，再进入错词复习。`
          : "本句有错词。先按空格查看每个单词的中文意思，再进入错词复习。",
      );
      await playChineseThenEnglish(currentItem.chinese_text, currentItem.english_text);
      return;
    }

    updateAnswerState("sentence-complete");
    isCompletingSentenceRef.current = false;
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
    if (answerStateRef.current !== "typing" || isCompletingSentenceRef.current) {
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
    setWordStatuses((current) => {
      const nextStatuses = [...current];
      nextStatuses[index] = isCorrect ? "correct" : "incorrect";
      return nextStatuses;
    });

    if (!isCorrect) {
      const actualWord = currentRawAnswer;
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

    if (currentItem) {
      markCorrectWord(currentItem, expectedWord, index);
    }
    const nextAnswers = [...wordAnswers];
    nextAnswers[index] = currentRawAnswer;
    if (areRequiredWordAnswersCorrect(nextAnswers)) {
      setWordStatuses((current) => {
        const nextStatuses = [...current];
        getRequiredWordIndexes().forEach((wordIndex) => {
          nextStatuses[wordIndex] = "correct";
        });
        return nextStatuses;
      });
      void completeCurrentSentence();
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

    // Ignore empty submissions — don't count them as errors
    const normalizedAnswer = normalizeTypedWord(mistakePracticeAnswer);
    if (!normalizedAnswer) {
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
      return;
    }

    const isCorrect = normalizedAnswer === normalizeTypedWord(expectedWord);
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
    if (currentItem) {
      markCorrectWord(currentItem, expectedWord, `mistake-${normalizeEnglishKey(expectedWord)}`);
    }
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
    handleNextItem({ completedCurrentItem: true });
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
    if (!currentItem || isTranslatingWordsRef.current || answerStateRef.current !== "sentence-complete") {
      return;
    }

    const accessToken = getAccessToken();
    if (!accessToken) {
      setFeedbackMessage("请先登录后再调用 LLM 翻译单词。");
      return;
    }

    areWordTranslationsReadyRef.current = false;
    isTranslatingWordsRef.current = true;
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
    const pendingWords = pendingMistakePracticeWordsRef.current;
    if (pendingWords.length > 0) {
      pendingMistakePracticeTranslationsRef.current = buildMistakePracticeTranslations(pendingWords, translations);
    }
    updateAnswerState("word-translations-shown");
    setFeedbackMessage(pendingWords.length > 0 ? "已显示逐词中文。再次按空格进入错词复习。" : "已显示逐词中文。再次按空格进入下一句。");
    setIsTranslatingWords(false);
    isTranslatingWordsRef.current = false;
    // Use setTimeout (macrotask) instead of rAF so React has flushed
    // the wordTranslations render before space can advance to mistake practice.
    window.setTimeout(() => {
      areWordTranslationsReadyRef.current = true;
    }, 0);
  }, [buildMistakePracticeTranslations, currentItem, currentWords, modelSettings, updateAnswerState]);

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
    updateAnswerState("mistake-word-practice");
    setFeedbackMessage("现在进入错词单独拼写。");
    const firstTranslation = practiceTranslations[words[0]];
    if (firstTranslation) {
      await playChineseThenEnglish(firstTranslation, words[0]);
    }
    return true;
  }, [playChineseThenEnglish, setPendingMistakePractice, translateMistakePracticeWords, updateAnswerState]);

  function handleWordTranslationsContinue() {
    if (isTranslatingWordsRef.current || !areWordTranslationsReadyRef.current) {
      return;
    }
    areWordTranslationsReadyRef.current = false;
    if (pendingMistakePracticeWordsRef.current.length > 0) {
      void beginPendingMistakePractice();
      return;
    }
    answerStateRef.current = "typing";
    handleNextItem({ completedCurrentItem: true });
  }

  useEffect(() => {
    function handleWindowKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== " ") {
        return;
      }

      // Read latest state from refs to avoid stale-closure bugs
      const currentState = answerStateRef.current;
      if (currentState === "typing" || currentState === "mistake-word-practice") {
        return;
      }
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
        return;
      }

      event.preventDefault();

      // Cooldown lock: ignore rapid repeated spaces within 400ms
      const now = Date.now();
      if (now - spaceCooldownRef.current < 400) {
        return;
      }
      spaceCooldownRef.current = now;

      if (currentState === "sentence-complete") {
        if (event.repeat || isTranslatingWordsRef.current) {
          return;
        }
        void revealWordTranslations();
        return;
      }
      if (currentState === "word-translations-shown") {
        if (event.repeat || isTranslatingWordsRef.current || !areWordTranslationsReadyRef.current) {
          return;
        }
        areWordTranslationsReadyRef.current = false;
        if (pendingMistakePracticeWordsRef.current.length > 0) {
          void beginPendingMistakePractice();
          return;
        }
        answerStateRef.current = "typing";
        handleNextItem({ completedCurrentItem: true });
      }
    }

    window.addEventListener("keydown", handleWindowKeyDown);
    return () => window.removeEventListener("keydown", handleWindowKeyDown);
  }, [revealWordTranslations, handleNextItem, beginPendingMistakePractice]);

  return (
    <main className="flex min-h-screen flex-col bg-white">
      {celebrationSummary ? <CelebrationModal nextCourse={nextCourse} summary={celebrationSummary} /> : null}
      <header className="border-b bg-white px-5 py-4 ipad:px-7 ipad:py-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="rounded-md border bg-slate-50 px-3 py-2 text-left">
              <p className="text-xs text-muted-foreground">学习时长</p>
              <p className="font-mono text-lg font-semibold text-slate-900 ipad:text-2xl">{formatStudyDuration(activeStudySeconds)}</p>
            </div>
            <Link className="text-sm font-medium text-primary hover:underline ipad:text-base" href="/learning">
              返回开始学习
            </Link>
            <div>
              <p className="text-lg font-semibold ipad:text-xl">课程学习</p>
              <p className="text-sm text-muted-foreground ipad:text-base">{items.length > 0 ? `第 ${currentIndex + 1} 题 / 共 ${items.length} 题` : "拼写练习"}</p>
            </div>
          </div>
          <div className="text-base font-semibold text-slate-600 ipad:text-lg">
            {device.isIPad ? "⌨ 空格判定单词 · 整句完成后空格查看逐词中文" : "单词输入完成按空格判定，整句完成后按空格查看逐词中文"}
          </div>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted ipad:h-2.5">
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progressPercent}%` }} />
        </div>
      </header>

      <section className="flex flex-1 items-center justify-center px-6 py-12 ipad:px-10 ipad:py-16">
        {isLoading ? <p className="text-lg font-bold text-muted-foreground ipad:text-xl">正在加载学习内容...</p> : null}
        {!isLoading && errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 ipad:px-6 ipad:py-4 ipad:text-base">{errorMessage}</p> : null}
        {!isLoading && !errorMessage && !currentItem ? <p className="text-lg font-bold text-muted-foreground ipad:text-xl">当前课程还没有导入内容。</p> : null}

        {!isLoading && !errorMessage && currentItem ? (
          <div className="w-full max-w-5xl space-y-10 text-center ipad:space-y-14">
            <div className="flex justify-center gap-3 text-sm text-muted-foreground ipad:text-base">
              <span className="rounded-full border px-3 py-1 ipad:px-4 ipad:py-1.5">{itemTypeLabels[currentItem.item_type]}</span>
              <span className="rounded-full border px-3 py-1 ipad:px-4 ipad:py-1.5">{currentIndex + 1} / {items.length}</span>
            </div>

            <div className="space-y-10 ipad:space-y-14">
              <p className="text-4xl font-semibold tracking-tight text-slate-900 ipad:text-5xl ipad:leading-tight">{currentItem.chinese_text}</p>
              {answerState === "mistake-word-practice" ? (
                <div className="mx-auto flex max-w-xl flex-col items-center gap-5 rounded-lg border bg-slate-50 px-6 py-7 ipad:max-w-2xl ipad:gap-7 ipad:px-10 ipad:py-10">
                  <div className="space-y-2 ipad:space-y-3">
                    <p className="text-base font-bold text-muted-foreground ipad:text-lg">错词单独拼写 {mistakePracticeIndex + 1} / {mistakePracticeWords.length}</p>
                    <p className="text-sm font-bold text-muted-foreground ipad:text-base">根据中文拼写正确英文</p>
                    <p className="text-3xl font-bold text-slate-900 ipad:text-4xl">{currentMistakePracticeTranslation || "中文提示准备中"}</p>
                  </div>
                  <input
                    ref={mistakePracticeInputRef}
                    aria-label="错词单独拼写"
                    autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false}
                    className={`h-16 w-full max-w-sm border-0 border-b-4 bg-transparent px-1 text-center text-4xl outline-none transition ipad:h-20 ipad:max-w-md ipad:text-5xl ${mistakePracticeStatus === "incorrect" ? "border-red-500 text-red-700" : "border-primary"}`}
                    value={mistakePracticeAnswer}
                    onChange={(event) => {
                      setMistakePracticeAnswer(event.target.value.replace(/\s/g, ""));
                      setMistakePracticeStatus("idle");
                      setFeedbackMessage(null);
                    }}
                    onKeyDown={handleMistakePracticeKeyDown}
                  />
                  <p className="text-base font-bold text-muted-foreground ipad:text-lg">
                    {device.isIPad ? "⌨ 空格/回车判定 · 连续 3 次错误后进入下一句" : "按空格或回车判定，连续 3 次错误后先进入下一句。"}
                  </p>
                </div>
              ) : (
                <div className="flex flex-wrap items-end justify-center gap-x-4 gap-y-6 ipad:gap-x-6 ipad:gap-y-8">
                  {currentWords.map((word, index) => {
                    const normalizedWord = normalizeEnglishKey(word);
                    const status = wordStatuses[index] ?? "idle";
                    const isDynamicBlank = isDynamicFillBlankItem && dynamicReviewWordIndexes.includes(index);
                    const shouldShowInput = !isDynamicFillBlankItem || isDynamicBlank;
                    const isActive = activeWordIndex === index && answerState === "typing" && shouldShowInput;
                    return (
                      <div className="flex flex-col items-center gap-2 ipad:gap-3" key={`${word}-${index}`} style={{ minWidth: getWordInputWidth(word) }}>
                        {answerState !== "typing" && shouldShowInput ? (
                          <span className="rounded-full border px-3 py-0.5 text-xs text-muted-foreground ipad:px-4 ipad:text-sm">
                            {commonPartLabels[normalizedWord] ?? itemTypeLabels[currentItem.item_type]}
                          </span>
                        ) : null}
                        {shouldShowInput ? (
                          <input
                            ref={(element) => {
                              inputRefs.current[index] = element;
                            }}
                            aria-label={isDynamicBlank ? `填空：${currentItem.chinese_text}` : `第 ${index + 1} 个单词`}
                            autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false}
                            className={`h-16 border-0 border-b-4 bg-transparent px-1 text-center text-4xl outline-none transition ipad:h-20 ipad:text-5xl ${status === "correct" ? "border-emerald-500 text-emerald-700" : ""} ${status === "incorrect" ? "border-red-500 text-red-700" : ""} ${status === "idle" ? annotationColors[index % annotationColors.length] : ""} ${isActive ? "bg-primary/5" : ""}`}
                            disabled={answerState !== "typing"}
                            placeholder={isDynamicBlank ? "____" : undefined}
                            style={{ width: getWordInputWidth(word) }}
                            value={wordAnswers[index] ?? ""}
                            onChange={(event) => updateWordAnswer(index, event.target.value)}
                            onFocus={() => setActiveWordIndex(index)}
                            onKeyDown={(event) => handleWordKeyDown(event, index)}
                          />
                        ) : (
                          <span className="flex h-16 items-center text-4xl font-semibold text-slate-900 ipad:h-20 ipad:text-5xl">{word}</span>
                        )}
                        {answerState !== "typing" && shouldShowInput ? <span className="text-2xl font-medium text-slate-900 ipad:text-3xl">{word}</span> : null}
                        {answerState === "word-translations-shown" ? (
                          <span className="max-w-44 rounded-md bg-secondary px-3 py-1.5 text-lg font-bold text-slate-800 ipad:max-w-56 ipad:px-4 ipad:py-2 ipad:text-xl">
                            {wordTranslations[index] || "翻译失败"}
                          </span>
                        ) : null}
                        {status === "incorrect" ? <span className="text-base font-bold text-red-600 ipad:text-lg">正确：{word}</span> : null}
                      </div>
                    );
                  })}
                </div>
              )}
              {feedbackMessage ? <p className={answerState !== "typing" ? "text-lg font-bold text-emerald-600 ipad:text-xl" : "text-lg font-bold text-red-600 ipad:text-xl"}>{feedbackMessage}</p> : null}
              <div className="flex flex-wrap justify-center gap-3 ipad:gap-4">
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

            <div className="flex flex-wrap justify-center gap-3 ipad:gap-4">
              {answerState === "word-translations-shown" ? (
                <Button className="ipad:text-lg ipad:px-6 ipad:py-3" onClick={handleWordTranslationsContinue} type="button">
                  {pendingMistakePracticeWords.length > 0 ? "进入错词复习" : "下一句"}
                </Button>
              ) : null}
              <Button className="ipad:text-lg ipad:px-6 ipad:py-3" onClick={resetAnswer} type="button" variant="secondary">
                再来一次
              </Button>
              <Button className="ipad:text-lg ipad:px-6 ipad:py-3" onClick={() => handleNextItem()} type="button" variant="outline">
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
