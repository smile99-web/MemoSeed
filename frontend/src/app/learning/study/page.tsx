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
import { playAudioBlob, stopAudioPlayback, synthesizeCosyVoiceSpeech, synthesizeKokoroSpeech, synthesizeVolcengineSpeech, TtsSynthesisOptions } from "@/lib/tts";

const itemTypeLabels: Record<LearningItem["item_type"], string> = {
  word: "单词",
  phrase: "短语",
  sentence: "句子",
};

const annotationColors = ["border-emerald-500", "border-sky-500", "border-violet-500", "border-amber-500", "border-rose-400"] as const;
const STUDY_IDLE_TIMEOUT_MS = 20_000;
const STUDY_TIME_FLUSH_SECONDS = 10;
const DUE_REVIEW_POLL_MS = 60_000;
const WORD_PREVIEW_MS = 3000;
const NATURAL_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: -12,
};
const SLOW_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: -32,
};
const WORD_ENGLISH_TTS_OPTIONS: TtsSynthesisOptions = {
  speechRate: -38,
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

type AnswerState = "typing" | "mistake-word-practice" | "sentence-complete";
type WordStatus = "idle" | "correct" | "incorrect" | "skipped";
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

function cleanEnglishSpeechWord(value: string): string {
  return value.trim().replace(/^[^a-zA-Z0-9']+|[^a-zA-Z0-9']+$/g, "");
}

function buildChunkedEnglishSpeechText(value: string): string {
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

function buildWordByWordEnglishSpeechText(value: string): string {
  return tokenizeEnglish(value).map(cleanEnglishSpeechWord).filter(Boolean).join(". ");
}

function buildFocusedWordSpeechText(word: string): string {
  const cleanWord = cleanEnglishSpeechWord(word);
  if (!cleanWord) {
    return "";
  }
  return `${cleanWord}. ${cleanWord}.`;
}

function getFirstLetter(value: string): string {
  return normalizeEnglishKey(value).charAt(0);
}

function getMatchedPrefixLength(expectedWord: string, typedWord: string): number {
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

function maskWordByIndexes(word: string, visibleIndexes: Set<number>): string {
  const normalizedWord = normalizeEnglishKey(word);
  return normalizedWord
    .split("")
    .map((letter, index) => (visibleIndexes.has(index) ? letter : "_"))
    .join(" ");
}

function formatChunkLengths(lengths: number[]): string {
  return lengths.join(" + ");
}

function getFrontHintChunkLengths(wordLength: number): number[] {
  if (wordLength <= 3) {
    return [wordLength];
  }

  const firstChunkLength = wordLength <= 6 ? 2 : 3;
  return [firstChunkLength, wordLength - firstChunkLength];
}

function getBackHintChunkLengths(wordLength: number): number[] {
  if (wordLength <= 3) {
    return [wordLength];
  }

  const lastChunkLength = Math.min(3, wordLength - 1);
  const remainingLength = wordLength - lastChunkLength;
  if (remainingLength <= 3) {
    return [remainingLength, lastChunkLength];
  }

  return [Math.ceil(remainingLength / 2), Math.floor(remainingLength / 2), lastChunkLength];
}

function buildFrontChunkHint(word: string): string {
  const normalizedWord = normalizeEnglishKey(word);
  const chunkLengths = getFrontHintChunkLengths(normalizedWord.length);
  const visibleLength = chunkLengths[0] ?? 0;
  const visibleIndexes = new Set<number>();
  for (let index = 0; index < visibleLength; index += 1) {
    visibleIndexes.add(index);
  }

  return `分段提示：按 ${formatChunkLengths(chunkLengths)} 个字母分段，一共 ${normalizedWord.length} 个字母。前面音节：${maskWordByIndexes(word, visibleIndexes)}`;
}

function buildBackChunkHint(word: string): string {
  const normalizedWord = normalizeEnglishKey(word);
  const chunkLengths = getBackHintChunkLengths(normalizedWord.length);
  const visibleLength = chunkLengths[chunkLengths.length - 1] ?? 0;
  const visibleIndexes = new Set<number>();
  for (let index = Math.max(normalizedWord.length - visibleLength, 0); index < normalizedWord.length; index += 1) {
    visibleIndexes.add(index);
  }

  return `分段提示：按 ${formatChunkLengths(chunkLengths)} 个字母分段，一共 ${normalizedWord.length} 个字母。后面音节：${maskWordByIndexes(word, visibleIndexes)}`;
}

function buildProgressiveSpellingHint(expectedWord: string, typedWord: string, errorCount: number): string {
  const normalizedExpected = normalizeEnglishKey(expectedWord);
  const matchedPrefixLength = getMatchedPrefixLength(expectedWord, typedWord);
  const nextLetterIndex = Math.max(matchedPrefixLength + 1, 2);

  if (errorCount <= 1) {
    return `前 ${Math.max(matchedPrefixLength, 1)} 个字母是对的，从第 ${nextLetterIndex} 个字母再想想。`;
  }

  if (errorCount === 2) {
    return `提示：${maskWordByIndexes(expectedWord, new Set([0]))}，一共 ${normalizedExpected.length} 个字母。`;
  }

  if (errorCount === 3) {
    return buildFrontChunkHint(expectedWord);
  }

  if (errorCount === 4) {
    return buildBackChunkHint(expectedWord);
  }

  const nextVisibleIndex = Math.min(Math.max(matchedPrefixLength, 1), Math.max(normalizedExpected.length - 1, 0));
  const visibleIndexes = new Set<number>([0]);
  for (let index = 0; index < Math.min(matchedPrefixLength, normalizedExpected.length); index += 1) {
    visibleIndexes.add(index);
  }
  visibleIndexes.add(nextVisibleIndex);
  const nextLetter = normalizedExpected[nextVisibleIndex] ?? "";
  return `下一步提示：第 ${nextVisibleIndex + 1} 个字母是 ${nextLetter}。${maskWordByIndexes(expectedWord, visibleIndexes)}。先遮住答案，在心里拼一遍再输入。`;
}

function getWordInputWidth(word: string): string {
  const normalizedLength = Math.max(normalizeEnglishKey(word).length, 2);
  return `${Math.min(Math.max(normalizedLength * 3, 8), 24)}rem`;
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
            <Button asChild className="ipad:text-base ipad:px-5 ipad:py-2">
              <Link href={nextCourseHref}>{nextCourse ? "学习下一课" : "返回开始学习"}</Link>
            </Button>
          ) : (
            <Button disabled type="button" className="ipad:text-base ipad:px-5 ipad:py-2">{nextCourse ? "朗读后进入下一课" : "朗读后返回"}</Button>
          )}
          <Button asChild variant="secondary" className="ipad:text-base ipad:px-5 ipad:py-2">
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
  const [mistakePracticeTranslations, setMistakePracticeTranslations] = useState<Record<string, string>>({});
  const [pendingMistakePracticeWords, setPendingMistakePracticeWords] = useState<string[]>([]);
  const [deferredDynamicWords, setDeferredDynamicWords] = useState<string[]>([]);
  const [hasFinalizedCurrentItem, setHasFinalizedCurrentItem] = useState(false);
  const [startedAt, setStartedAt] = useState<number>(() => Date.now());
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);
  const [activeStudySeconds, setActiveStudySeconds] = useState(0);
  const [celebrationSummary, setCelebrationSummary] = useState<CelebrationSummary | null>(null);
  const [isStudyFullscreen, setIsStudyFullscreen] = useState(true);
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
  const isCompletingSentenceRef = useRef(false);
  const pendingMistakePracticeWordsRef = useRef<string[]>([]);
  const pendingMistakePracticeTranslationsRef = useRef<Record<string, string>>({});
  const spaceCooldownRef = useRef(0);
  const queuedReviewItemIdsRef = useRef<Set<string>>(new Set());
  const currentIndexRef = useRef(0);
  const currentItemRef = useRef<LearningItem | null>(null);
  const celebrationSummaryRef = useRef<CelebrationSummary | null>(null);
  const wordPreviewTimeoutRef = useRef<number | null>(null);
  const mistakePracticePreviewTimeoutRef = useRef<number | null>(null);

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

  const updateAnswerState = useCallback(function updateAnswerState(nextState: AnswerState) {
    answerStateRef.current = nextState;
    setAnswerState(nextState);
  }, []);

  const clearWordPreview = useCallback(function clearWordPreview() {
    if (wordPreviewTimeoutRef.current !== null) {
      window.clearTimeout(wordPreviewTimeoutRef.current);
      wordPreviewTimeoutRef.current = null;
    }
    setPreviewWordIndex(null);
  }, []);

  const clearMistakePracticePreview = useCallback(function clearMistakePracticePreview() {
    if (mistakePracticePreviewTimeoutRef.current !== null) {
      window.clearTimeout(mistakePracticePreviewTimeoutRef.current);
      mistakePracticePreviewTimeoutRef.current = null;
    }
    setPreviewMistakePracticeWord(false);
  }, []);

  const showWordPreview = useCallback(function showWordPreview(index: number, expectedWord: string) {
    clearWordPreview();
    setPreviewWordIndex(index);
    setWordAnswers((current) => {
      const nextAnswers = [...current];
      nextAnswers[index] = "";
      return nextAnswers;
    });
    setFeedbackMessage(`看清这个单词 ${Math.round(WORD_PREVIEW_MS / 1000)} 秒：${expectedWord}。它会自动隐藏，然后请凭记忆完整拼写。`);
    wordPreviewTimeoutRef.current = window.setTimeout(() => {
      wordPreviewTimeoutRef.current = null;
      setPreviewWordIndex(null);
      setWordStatuses((current) => {
        const nextStatuses = [...current];
        if (nextStatuses[index] === "incorrect") {
          nextStatuses[index] = "idle";
        }
        return nextStatuses;
      });
      setFeedbackMessage("现在请凭记忆重新拼写这个单词。");
      window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
    }, WORD_PREVIEW_MS);
  }, [clearWordPreview]);

  const showMistakePracticePreview = useCallback(function showMistakePracticePreview(expectedWord: string) {
    clearMistakePracticePreview();
    setPreviewMistakePracticeWord(true);
    setMistakePracticeAnswer("");
    setFeedbackMessage(`看清这个单词 ${Math.round(WORD_PREVIEW_MS / 1000)} 秒：${expectedWord}。它会自动隐藏，然后请凭记忆完整拼写。`);
    mistakePracticePreviewTimeoutRef.current = window.setTimeout(() => {
      mistakePracticePreviewTimeoutRef.current = null;
      setPreviewMistakePracticeWord(false);
      setMistakePracticeStatus("idle");
      setFeedbackMessage("现在请凭记忆重新拼写这个错词。");
      window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
    }, WORD_PREVIEW_MS);
  }, [clearMistakePracticePreview]);

  useEffect(() => {
    return () => {
      if (wordPreviewTimeoutRef.current !== null) {
        window.clearTimeout(wordPreviewTimeoutRef.current);
      }
      if (mistakePracticePreviewTimeoutRef.current !== null) {
        window.clearTimeout(mistakePracticePreviewTimeoutRef.current);
      }
    };
  }, []);

  const setPendingMistakePractice = useCallback(function setPendingMistakePractice(words: string[], translations: Record<string, string>) {
    pendingMistakePracticeWordsRef.current = words;
    pendingMistakePracticeTranslationsRef.current = translations;
    setPendingMistakePracticeWords(words);
  }, []);

  const getRequiredWordIndexes = useCallback(function getRequiredWordIndexes(): number[] {
    return isDynamicFillBlankItem ? dynamicReviewWordIndexes : currentWords.map((_, index) => index);
  }, [currentWords, dynamicReviewWordIndexes, isDynamicFillBlankItem]);

  function areRequiredWordAnswersComplete(nextAnswers: string[], nextStatuses = wordStatuses): boolean {
    return getRequiredWordIndexes().every((wordIndex) => (
      nextStatuses[wordIndex] === "skipped"
      || normalizeTypedWord(nextAnswers[wordIndex] ?? "") === normalizeTypedWord(currentWords[wordIndex] ?? "")
    ));
  }

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
    await speakClearEnglish(sequenceId, item.english_text, settings, true);
  }, [speakClearEnglish, speakInVoiceSequence, waitInVoiceSequence]);

  const playChineseThenEnglish = useCallback(async function playChineseThenEnglish(chineseText: string, englishText: string, sequenceId = startVoiceSequence()) {
    const settings = modelSettingsRef.current;
    if (!(await speakInVoiceSequence(sequenceId, chineseText, settings.ttsChineseVoice, "zh-CN", settings))) {
      return;
    }
    if (!(await waitInVoiceSequence(sequenceId, 1000))) {
      return;
    }
    await speakClearEnglish(sequenceId, englishText, settings, true);
  }, [speakClearEnglish, speakInVoiceSequence, startVoiceSequence, waitInVoiceSequence]);

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
    const textToSpeak = answerStateRef.current === "mistake-word-practice" ? currentMistakePracticeWord : currentItem?.english_text;
    if (!textToSpeak) {
      return;
    }
    focusCurrentUnfinishedWord();
    const sequenceId = startVoiceSequence();
    const settings = modelSettingsRef.current;
    void speakClearEnglish(sequenceId, textToSpeak, settings, false).finally(() => {
      window.setTimeout(focusCurrentUnfinishedWord, 0);
    });
  }, [currentItem, currentMistakePracticeWord, focusCurrentUnfinishedWord, speakClearEnglish, startVoiceSequence]);

  function toggleStudyFullscreen() {
    setIsStudyFullscreen((current) => !current);
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
    setWordErrorCounts(currentWords.map(() => 0));
    setWordConsecutiveCorrect(currentWords.map(() => 0));
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
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    updateAnswerState("typing");
    isCompletingSentenceRef.current = false;
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[dynamicReviewWordIndexes[0] ?? 0]?.focus(), 0);
    const sequenceId = startVoiceSequence();
    if (currentItem?.chinese_text && currentItem.english_text) {
      void playCurrentItemIntro(currentItem, sequenceId);
    }
  }, [clearMistakePracticePreview, clearWordPreview, currentItem, currentWords, dynamicReviewWordIndexes, playCurrentItemIntro, setPendingMistakePractice, startVoiceSequence, updateAnswerState]);

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
    setWordErrorCounts(currentWords.map(() => 0));
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
    setStartedAt(Date.now());
    setActiveWordIndex(dynamicReviewWordIndexes[0] ?? 0);
    updateAnswerState("typing");
    isCompletingSentenceRef.current = false;
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

    startVoiceSequence();
    setCurrentIndex((index) => {
      const nextIndex = (index + 1) % items.length;
      window.localStorage.setItem(getStudyProgressKey(courseId), String(nextIndex));
      if (options.completedCurrentItem && nextIndex === 0) {
        window.setTimeout(showCourseCompletion, 0);
      }
      return nextIndex;
    });
  }, [courseId, items.length, showCourseCompletion, startVoiceSequence]);

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
          ? `${dynamicSentenceFeedback} 本句有错词，按空格进入错词复习。`
          : "本句有错词，按空格进入错词复习。",
      );
      await playChineseThenEnglish(currentItem.chinese_text, currentItem.english_text);
      return;
    }

    updateAnswerState("sentence-complete");
    isCompletingSentenceRef.current = false;
    setFeedbackMessage(dynamicSentenceFeedback ? `${dynamicSentenceFeedback} 按空格进入下一句。` : "整句拼写正确。按空格进入下一句。");
    await playChineseThenEnglish(currentItem.chinese_text, currentItem.english_text);
  }

  function updateWordAnswer(index: number, value: string) {
    if (previewWordIndex === index) {
      clearWordPreview();
    }
    setWordAnswers((current) => {
      const nextAnswers = [...current];
      nextAnswers[index] = value.replace(/\s/g, "");
      return nextAnswers;
    });
    setFeedbackMessage(null);
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
    setFeedbackMessage("请重新输入这个单词。");
    window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
  }

  async function translateWordForHint(expectedWord: string): Promise<string> {
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
    return translatedWord;
  }

  async function handleFirstLetterMistake(expectedWord: string) {
    const translatedWord = await translateWordForHint(expectedWord);
    setFeedbackMessage(translatedWord ? `首字母不对，先听中文和英文读音。中文：${translatedWord}` : "首字母不对，先听英文读音再试一次。");
    await playChineseThenEnglish(translatedWord, expectedWord);
  }

  function handleSpellingMistake(index: number, expectedWord: string, typedWord: string, errorCount: number) {
    setFeedbackMessage(buildProgressiveSpellingHint(expectedWord, typedWord, errorCount));
  }

  function checkWord(index: number) {
    if (answerStateRef.current !== "typing" || isCompletingSentenceRef.current) {
      return;
    }
    if (previewWordIndex === index) {
      setFeedbackMessage("先看清单词，等它自动隐藏后再凭记忆输入。");
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
      const actualWord = currentRawAnswer;
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
      if (accessToken && currentItem && !isGeneratedItem(currentItem)) {
        void logWordMistake(currentItem.id, expectedWord, actualWord, accessToken).catch(() => undefined);
      }
      if (nextErrorCount === 3) {
        void playChineseThenEnglish(currentItem?.chinese_text ?? "", expectedWord);
        showWordPreview(index, expectedWord);
        return;
      }
      if (nextErrorCount >= 8) {
        const nextSkippedStatuses = [...nextStatusesForAttempt];
        nextSkippedStatuses[index] = "skipped";
        setWordStatuses(nextSkippedStatuses);
        clearWordPreview();
        setFeedbackMessage("这个词先加入错词复习，继续完成句子。稍后会用中文、发音和隐藏重拼再练。");
        const nextIndex = isDynamicFillBlankItem ? dynamicReviewWordIndexes.find((wordIndex) => wordIndex > index) ?? currentWords.length : index + 1;
        if (areRequiredWordAnswersComplete(wordAnswers, nextSkippedStatuses)) {
          void completeCurrentSentence();
          return;
        }
        if (nextIndex < currentWords.length) {
          setActiveWordIndex(nextIndex);
          window.setTimeout(() => inputRefs.current[nextIndex]?.focus(), 0);
        }
        return;
      }
      if (getFirstLetter(typedValue) !== getFirstLetter(expectedWord)) {
        setFeedbackMessage("首字母不对，正在准备中文和英文读音...");
        void handleFirstLetterMistake(expectedWord);
      } else {
        handleSpellingMistake(index, expectedWord, typedValue, nextErrorCount);
      }
      return;
    }

    if (currentItem) {
      markCorrectWord(currentItem, expectedWord, index);
    }

    const prevErrorCount = wordErrorCounts[index] ?? 0;

    if (prevErrorCount >= 3) {
      const nextConsecutive = (wordConsecutiveCorrect[index] ?? 0) + 1;
      setWordConsecutiveCorrect((current) => {
        const next = [...current];
        next[index] = nextConsecutive;
        return next;
      });

      if (nextConsecutive < 2) {
        setFeedbackMessage("正确！请再拼写一次确认。");
        if (inputRefs.current[index]) {
          inputRefs.current[index].value = "";
        }
        setWordAnswers((current) => {
          const next = [...current];
          next[index] = "";
          return next;
        });
        setWordStatuses((current) => {
          const next = [...current];
          next[index] = "idle";
          return next;
        });
        window.setTimeout(() => inputRefs.current[index]?.focus(), 0);
        return;
      }

      setWordConsecutiveCorrect((current) => {
        const next = [...current];
        next[index] = 0;
        return next;
      });
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
    if (previewMistakePracticeWord) {
      setFeedbackMessage("先看清单词，等它自动隐藏后再凭记忆输入。");
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
      setMistakePracticeErrorCount(nextErrorCount);
      setMistakePracticeConsecutiveCorrect(0);
      setMistakePracticeStatus("incorrect");
      setMistakePracticeAnswer("");
      if (nextErrorCount === 3) {
        await playChineseThenEnglish(currentMistakePracticeTranslation, expectedWord);
        showMistakePracticePreview(expectedWord);
        return;
      }

      if (nextErrorCount >= 9) {
        clearMistakePracticePreview();
        setDeferredDynamicWords((current) => {
          const normalizedWord = normalizeEnglishKey(expectedWord);
          return normalizedWord && !current.includes(normalizedWord) ? [...current, normalizedWord] : current;
        });
        setFeedbackMessage("这个词已经看过并重新尝试过了，先放入后续复习句。继续下一个，稍后再回来练。");
        const nextIndex = mistakePracticeIndex + 1;
        if (nextIndex < mistakePracticeWords.length) {
          const nextWord = mistakePracticeWords[nextIndex];
          setMistakePracticeIndex(nextIndex);
          setMistakePracticeAnswer("");
          setMistakePracticeStatus("idle");
          setMistakePracticeErrorCount(0);
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
        handleNextItem({ completedCurrentItem: true });
        return;
      }

      if (getFirstLetter(normalizedAnswer) !== getFirstLetter(expectedWord)) {
        setFeedbackMessage(`首字母不对，先听中文和英文读音。已连续错误 ${nextErrorCount} 次。`);
        await playChineseThenEnglish(currentMistakePracticeTranslation, expectedWord);
        return;
      }

      setFeedbackMessage(`${buildProgressiveSpellingHint(expectedWord, normalizedAnswer, nextErrorCount)} 已连续错误 ${nextErrorCount} 次。`);
      return;
    }

    if (mistakePracticeErrorCount >= 3) {
      const nextConsecutive = mistakePracticeConsecutiveCorrect + 1;
      setMistakePracticeConsecutiveCorrect(nextConsecutive);

      if (nextConsecutive < 2) {
        setFeedbackMessage("正确！请再拼写一次确认。");
        setMistakePracticeAnswer("");
        setMistakePracticeStatus("idle");
        window.setTimeout(() => mistakePracticeInputRef.current?.focus(), 0);
        return;
      }

      setMistakePracticeConsecutiveCorrect(0);
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
      setMistakePracticeConsecutiveCorrect(0);
      clearMistakePracticePreview();
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
    setMistakePracticeConsecutiveCorrect(0);
    clearMistakePracticePreview();
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
    clearMistakePracticePreview();
    updateAnswerState("mistake-word-practice");
    setFeedbackMessage("现在进入错词单独拼写。");
    const firstTranslation = practiceTranslations[words[0]];
    if (firstTranslation) {
      await playChineseThenEnglish(firstTranslation, words[0]);
    }
    return true;
  }, [clearMistakePracticePreview, playChineseThenEnglish, setPendingMistakePractice, translateMistakePracticeWords, updateAnswerState]);

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
        if (event.repeat) {
          return;
        }
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
  }, [handleNextItem, beginPendingMistakePractice]);

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
    : currentItem?.chinese_text || "";

  return (
    <main className={isStudyFullscreen ? "flex min-h-[100dvh] flex-col overflow-y-auto bg-white text-slate-950" : "flex min-h-[100dvh] flex-col overflow-y-auto bg-slate-50 text-slate-950"}>
      {celebrationSummary ? <CelebrationModal nextCourse={nextCourse} summary={celebrationSummary} /> : null}
      {!isStudyFullscreen ? (
        <header className="shrink-0 border-b bg-white/95 px-4 py-2.5 shadow-sm ipad:px-5 ipad:py-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="rounded-md border bg-slate-50 px-3 py-2 text-left">
                <p className="text-xs text-muted-foreground">学习时长</p>
                <p className="font-mono text-lg font-semibold text-slate-900 ipad:text-xl">{formatStudyDuration(activeStudySeconds)}</p>
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
              <span>{device.isIPad ? "⌨ 空格判定单词 · 整句完成后空格查看逐词中文" : "单词输入完成按空格判定，整句完成后按空格查看逐词中文"}</span>
              <Button className="h-9 px-3 text-sm ipad:h-9 ipad:px-3 ipad:text-sm" onClick={toggleStudyFullscreen} type="button" variant="outline">
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
              {answerState === "mistake-word-practice" ? (
                <div className={isStudyFullscreen ? "mx-auto flex max-w-4xl flex-col items-center gap-6 ipad:gap-8" : "mx-auto flex max-w-xl flex-col items-center gap-4 rounded-lg border bg-slate-50 px-5 py-5 ipad:max-w-xl ipad:gap-4 ipad:px-6 ipad:py-5 ipad-lg:max-w-2xl ipad-lg:gap-5 ipad-lg:px-7 ipad-lg:py-6"}>
                  {!isStudyFullscreen ? (
                    <div className="space-y-1 ipad:space-y-2">
                      <p className="text-base font-bold text-muted-foreground ipad:text-base ipad-lg:text-lg">错词单独拼写 {mistakePracticeIndex + 1} / {mistakePracticeWords.length}</p>
                      <p className="text-sm font-bold text-muted-foreground ipad:text-sm ipad-lg:text-base">根据中文拼写正确英文</p>
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
                      setMistakePracticeAnswer(event.target.value.replace(/\s/g, ""));
                      setMistakePracticeStatus("idle");
                      setFeedbackMessage(null);
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
              ) : (
                <div className={isStudyFullscreen ? "flex flex-wrap items-end justify-center gap-x-5 gap-y-7 ipad:gap-x-7 ipad:gap-y-8 ipad-lg:gap-x-8 ipad-lg:gap-y-10" : "flex flex-wrap items-end justify-center gap-x-4 gap-y-5 ipad:gap-x-4 ipad:gap-y-5 ipad-lg:gap-x-5 ipad-lg:gap-y-6"}>
                  {currentWords.map((word, index) => {
                    const normalizedWord = normalizeEnglishKey(word);
                    const status = wordStatuses[index] ?? "idle";
                    const isDynamicBlank = isDynamicFillBlankItem && dynamicReviewWordIndexes.includes(index);
                    const shouldShowInput = !isDynamicFillBlankItem || isDynamicBlank;
                    const isActive = activeWordIndex === index && answerState === "typing" && shouldShowInput;
                    return (
                      <div className="flex shrink-0 flex-col items-center gap-2" key={`${word}-${index}`} style={{ minWidth: getWordInputWidth(word) }}>
                        {answerState !== "typing" && shouldShowInput && !isStudyFullscreen ? (
                          <span className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground ipad:px-3 ipad:text-xs">
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
                            className={`${wordInputSizeClass} shrink-0 border-0 border-b-4 bg-transparent px-1 text-center font-semibold leading-none outline-none transition ${status === "correct" ? "border-emerald-500 text-emerald-700" : ""} ${status === "incorrect" ? "border-red-500 text-red-700" : ""} ${status === "skipped" ? "border-amber-500 text-amber-700" : ""} ${status === "idle" ? annotationColors[index % annotationColors.length] : ""} ${isActive ? "bg-emerald-50" : ""}`}
                            disabled={answerState !== "typing" || status === "skipped" || previewWordIndex === index}
                            placeholder={previewWordIndex === index ? "先看后拼" : status === "skipped" ? "稍后练" : isDynamicBlank ? "____" : undefined}
                            style={{ width: getWordInputWidth(word) }}
                            value={wordAnswers[index] ?? ""}
                            onChange={(event) => updateWordAnswer(index, event.target.value)}
                            onFocus={() => setActiveWordIndex(index)}
                            onKeyDown={(event) => handleWordKeyDown(event, index)}
                          />
                        ) : (
                          <span className={`flex items-center font-semibold leading-none text-slate-900 ${wordDisplaySizeClass}`}>{word}</span>
                        )}
                        {previewWordIndex === index ? (
                          <span className="rounded-md bg-amber-100 px-3 py-1 text-3xl font-bold text-amber-950 ipad:text-4xl ipad-lg:text-5xl">{word}</span>
                        ) : null}
                        {answerState !== "typing" && shouldShowInput && !isStudyFullscreen ? <span className="text-2xl font-medium text-slate-900 ipad:text-2xl ipad-lg:text-3xl">{word}</span> : null}
                        {status === "skipped" && !isStudyFullscreen ? <span className="text-base font-bold text-amber-700 ipad:text-base ipad-lg:text-lg">已加入错词复习</span> : null}
                        {status === "incorrect" && !isStudyFullscreen ? <span className="text-base font-bold text-red-600 ipad:text-base ipad-lg:text-lg">请按提示再试一次</span> : null}
                      </div>
                    );
                  })}
                </div>
              )}
              {feedbackMessage ? <p className={isStudyFullscreen ? "text-xl font-bold text-emerald-700 ipad:text-2xl ipad-lg:text-3xl" : answerState !== "typing" ? "text-base font-bold text-emerald-600 ipad:text-base ipad-lg:text-lg" : "text-base font-bold text-red-600 ipad:text-base ipad-lg:text-lg"}>{feedbackMessage}</p> : null}
              <div className={isStudyFullscreen ? "mx-auto flex w-full max-w-5xl flex-wrap items-center justify-center gap-4 px-0 py-0 ipad:gap-5 ipad-lg:gap-6" : "mx-auto flex w-full max-w-3xl flex-col items-center gap-4 rounded-lg border border-slate-200 bg-slate-50/90 px-4 py-4 ipad:max-w-3xl ipad:flex-col ipad:justify-center ipad:gap-4 ipad:px-5 ipad-lg:max-w-4xl ipad-lg:flex-row ipad-lg:gap-6 ipad-lg:px-6"}>
                <div className="flex flex-wrap justify-center gap-3 ipad:gap-3 ipad-lg:gap-4">
                  {answerState === "mistake-word-practice" ? (
                    <>
                      <Button className={actionButtonClass} onClick={() => void checkMistakePracticeWord()} type="button">判定错词</Button>
                      <Button className={actionButtonClass} onClick={handleSpeakEnglish} type="button" variant="secondary">朗读英文</Button>
                    </>
                  ) : (
                    <>
                      <Button className={actionButtonClass} disabled={answerState !== "typing"} onClick={() => checkWord(activeWordIndex)} type="button">
                        {isDynamicFillBlankItem ? "判定填空" : "判定单词"}
                      </Button>
                      <Button className={actionButtonClass} onClick={handleSpeakEnglish} type="button" variant="secondary">朗读英文</Button>
                    </>
                  )}
                </div>
                {!isStudyFullscreen ? <div className="h-px w-full bg-slate-200 ipad:h-px ipad:w-full ipad-lg:h-9 ipad-lg:w-px" /> : null}
                <div className="flex flex-wrap justify-center gap-3 ipad:gap-3 ipad-lg:gap-4">
                  {answerState === "sentence-complete" ? (
                    <Button
                      className={actionButtonClass}
                      onClick={() => {
                        if (pendingMistakePracticeWordsRef.current.length > 0) {
                          void beginPendingMistakePractice();
                          return;
                        }
                        answerStateRef.current = "typing";
                        handleNextItem({ completedCurrentItem: true });
                      }}
                      type="button"
                    >
                      {pendingMistakePracticeWords.length > 0 ? "进入错词复习" : "下一句"}
                    </Button>
                  ) : null}
                  <Button className={actionButtonClass} onClick={resetAnswer} type="button" variant="secondary">
                    再来一次
                  </Button>
                  <Button className={actionButtonClass} onClick={() => handleNextItem()} type="button" variant="outline">
                    跳过
                  </Button>
                  {isStudyFullscreen ? (
                    <Button className={actionButtonClass} onClick={toggleStudyFullscreen} type="button" variant="outline">
                      退出沉浸
                    </Button>
                  ) : null}
                </div>
              </div>
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
