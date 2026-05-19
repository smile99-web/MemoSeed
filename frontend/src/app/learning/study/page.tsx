"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getAccessToken } from "@/lib/auth";
import { LearningItem, listLearningItems, translateLearningText } from "@/lib/learning";
import { ModelSettings, defaultModelSettings, getModelSettings } from "@/lib/model-settings";

const itemTypeLabels: Record<LearningItem["item_type"], string> = {
  word: "单词",
  phrase: "短语",
  sentence: "句子",
};

const annotationColors = ["border-primary", "border-emerald-500", "border-sky-500", "border-violet-500", "border-amber-500"] as const;

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

function getWordInputWidth(word: string): string {
  const normalizedLength = Math.max(normalizeEnglishKey(word).length, 2);
  return `${Math.min(Math.max(normalizedLength * 2.1, 4.5), 18)}rem`;
}

function speakWithBrowser(text: string, language: string) {
  if (!text.trim() || !("speechSynthesis" in window)) {
    return;
  }

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = language;
  utterance.rate = 0.85;
  window.speechSynthesis.speak(utterance);
}

async function speakWithKokoro(text: string, voice: string, language: string, settings: ModelSettings) {
  if (!text.trim()) {
    return;
  }

  try {
    const response = await fetch(`${settings.ttsApiUrl.replace(/\/$/, "")}/v1/audio/speech`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: settings.ttsProvider,
        input: text,
        voice,
        response_format: "mp3",
      }),
    });
    if (!response.ok) {
      throw new Error(`TTS failed: ${response.status}`);
    }

    const audioBlob = await response.blob();
    const audioUrl = URL.createObjectURL(audioBlob);
    const audio = new Audio(audioUrl);
    audio.onended = () => URL.revokeObjectURL(audioUrl);
    await audio.play();
  } catch {
    speakWithBrowser(text, language);
  }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export default function StudyPage() {
  const searchParams = useSearchParams();
  const courseId = searchParams.get("course_id") ?? "";
  const [items, setItems] = useState<LearningItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [wordAnswers, setWordAnswers] = useState<string[]>([]);
  const [activeWordIndex, setActiveWordIndex] = useState(0);
  const [answerState, setAnswerState] = useState<"idle" | "correct">("idle");
  const [wordStatuses, setWordStatuses] = useState<Array<"idle" | "correct" | "incorrect">>([]);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(defaultModelSettings);
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);

  const currentItem = items[currentIndex] ?? null;
  const currentWords = useMemo(() => tokenizeEnglish(currentItem?.english_text ?? ""), [currentItem]);
  const progressPercent = items.length > 0 ? ((currentIndex + 1) / items.length) * 100 : 0;

  useEffect(() => {
    setModelSettings(getModelSettings());
  }, []);

  useEffect(() => {
    setWordAnswers(currentWords.map(() => ""));
    setWordStatuses(currentWords.map(() => "idle"));
    setActiveWordIndex(0);
    setAnswerState("idle");
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[0]?.focus(), 0);
    if (currentItem?.chinese_text) {
      void speakWithKokoro(currentItem.chinese_text, modelSettings.ttsChineseVoice, "zh-CN", modelSettings);
    }
  }, [currentItem, currentWords, modelSettings]);

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
        setItems(nextItems);
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
    setActiveWordIndex(0);
    setAnswerState("idle");
    setFeedbackMessage(null);
    window.setTimeout(() => inputRefs.current[0]?.focus(), 0);
  }

  function handleNextItem() {
    if (items.length === 0) {
      return;
    }

    setCurrentIndex((index) => (index + 1) % items.length);
  }

  async function completeCurrentSentence() {
    if (!currentItem) {
      return;
    }

    setAnswerState("correct");
    setFeedbackMessage("拼写正确，准备进入下一题");
    await speakWithKokoro(currentItem.chinese_text, modelSettings.ttsChineseVoice, "zh-CN", modelSettings);
    await wait(2000);
    await speakWithKokoro(currentItem.english_text, modelSettings.ttsEnglishVoice, "en-US", modelSettings);
    await wait(600);
    handleNextItem();
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
    await speakWithKokoro(expectedWord, modelSettings.ttsEnglishVoice, "en-US", modelSettings);
    if (translatedWord) {
      window.setTimeout(() => {
        void speakWithKokoro(translatedWord, modelSettings.ttsChineseVoice, "zh-CN", modelSettings);
      }, 900);
    }
  }

  function checkWord(index: number) {
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
      setFeedbackMessage(`正在翻译并朗读：${expectedWord}`);
      setWordAnswers((current) => {
        const nextAnswers = [...current];
        nextAnswers[index] = expectedWord;
        return nextAnswers;
      });
      void handleIncorrectWord(expectedWord);
      return;
    }

    const nextIndex = index + 1;
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
      checkWord(index);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      checkWord(index);
    }
  }

  function handleRevealAnswer() {
    setWordAnswers(currentWords);
    setWordStatuses(currentWords.map(() => "correct"));
    setActiveWordIndex(currentWords.length - 1);
    setAnswerState("correct");
    setFeedbackMessage("已显示正确答案");
  }

  return (
    <main className="flex min-h-screen flex-col bg-white">
      <header className="border-b bg-white px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <Link className="text-sm font-medium text-primary hover:underline" href="/learning/import">
              返回课程目录
            </Link>
            <div>
              <p className="text-lg font-semibold">课程学习</p>
              <p className="text-sm text-muted-foreground">{items.length > 0 ? `第 ${currentIndex + 1} 题 / 共 ${items.length} 题` : "拼写练习"}</p>
            </div>
          </div>
          <div className="text-sm text-muted-foreground">每个单词输入完成后按空格判定</div>
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progressPercent}%` }} />
        </div>
      </header>

      <section className="flex flex-1 items-center justify-center px-6 py-12">
        {isLoading ? <p className="text-sm text-muted-foreground">正在加载学习内容...</p> : null}
        {!isLoading && errorMessage ? <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{errorMessage}</p> : null}
        {!isLoading && !errorMessage && !currentItem ? <p className="text-sm text-muted-foreground">当前课程还没有导入内容。</p> : null}

        {!isLoading && !errorMessage && currentItem ? (
          <div className="w-full max-w-5xl space-y-10 text-center">
            <div className="flex justify-center gap-3 text-sm text-muted-foreground">
              <span className="rounded-full border px-3 py-1">{itemTypeLabels[currentItem.item_type]}</span>
              <span className="rounded-full border px-3 py-1">{currentIndex + 1} / {items.length}</span>
            </div>

            <div className="space-y-10">
              <p className="text-4xl font-semibold tracking-tight text-slate-900">{currentItem.chinese_text}</p>
              <div className="flex flex-wrap items-end justify-center gap-x-4 gap-y-6">
                {currentWords.map((word, index) => {
                  const normalizedWord = normalizeEnglishKey(word);
                  const status = wordStatuses[index] ?? "idle";
                  const isActive = activeWordIndex === index && answerState !== "correct";
                  return (
                    <div className="flex flex-col items-center gap-2" key={`${word}-${index}`} style={{ minWidth: getWordInputWidth(word) }}>
                      {answerState === "correct" ? (
                        <span className="rounded-full border px-3 py-0.5 text-xs text-muted-foreground">
                          {commonPartLabels[normalizedWord] ?? itemTypeLabels[currentItem.item_type]}
                        </span>
                      ) : null}
                      <input
                        ref={(element) => {
                          inputRefs.current[index] = element;
                        }}
                        aria-label={`第 ${index + 1} 个单词`}
                        autoComplete="off"
                        className={`h-16 border-0 border-b-4 bg-transparent px-1 text-center text-4xl outline-none transition ${status === "correct" ? "border-emerald-500 text-emerald-700" : ""} ${status === "incorrect" ? "border-red-500 text-red-700" : ""} ${status === "idle" ? annotationColors[index % annotationColors.length] : ""} ${isActive ? "bg-primary/5" : ""}`}
                        disabled={answerState === "correct"}
                        style={{ width: getWordInputWidth(word) }}
                        value={wordAnswers[index] ?? ""}
                        onChange={(event) => updateWordAnswer(index, event.target.value)}
                        onFocus={() => setActiveWordIndex(index)}
                        onKeyDown={(event) => handleWordKeyDown(event, index)}
                      />
                      {answerState === "correct" ? <span className="text-2xl font-medium text-slate-900">{word}</span> : null}
                      {status === "incorrect" ? <span className="text-xs font-medium text-red-600">正确：{word}</span> : null}
                    </div>
                  );
                })}
              </div>
              {feedbackMessage ? <p className={answerState === "correct" ? "text-sm font-medium text-emerald-600" : "text-sm font-medium text-red-600"}>{feedbackMessage}</p> : null}
              <div className="flex flex-wrap justify-center gap-3">
                <Button onClick={() => checkWord(activeWordIndex)} type="button">判定当前单词</Button>
                <Button onClick={handleRevealAnswer} type="button" variant="outline">
                  显示答案
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap justify-center gap-3">
              {answerState === "correct" ? <Button onClick={handleNextItem} type="button">下一题</Button> : null}
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
