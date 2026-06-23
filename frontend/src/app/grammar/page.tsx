"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  GrammarLevelDistribution,
  GrammarQuestion,
  GrammarQuestionSet,
  GrammarQuestionType,
  generateGrammarQuestions,
  getGrammarLevels,
} from "@/lib/grammar";

type LoadState =
  | { kind: "idle" }
  | { kind: "loading-levels" }
  | { kind: "loading-questions"; level: number }
  | { kind: "ready"; level: number; set: GrammarQuestionSet }
  | { kind: "error"; level: number | null; message: string };

/**
 * 语法练习页
 * ----------
 * 用户先选难度(1-10),点击"开始练习"调 LLM 生成 10 道题。
 * 答题流程:
 *  - 选择题:点击选项 → 立即高亮对/错 + 解释
 *  - 填空题:输入文本 → 点击"提交" → 高亮对/错 + 解释
 *  答完一道后底部出现"下一题"按钮,最后一道完成后显示"再来一组"。
 *
 * 进度跟踪仅在客户端 (sessionStorage),不上报后端 — 语法是轻量 LLM
 * 练习,不计入 FSRS 复习调度。
 */
export default function GrammarPage() {
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [selectedLevel, setSelectedLevel] = useState<number>(3);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const [fillText, setFillText] = useState("");
  const [submitted, setSubmitted] = useState(false);

  // Load the levels info on mount so we can render the difficulty band
  // descriptions without hard-coding them.
  const [levelsInfo, setLevelsInfo] = useState<{
    min: number;
    max: number;
    distribution: GrammarLevelDistribution[];
  } | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const info = await getGrammarLevels();
        if (!cancelled) {
          setLevelsInfo(info);
        }
      } catch (err) {
        // Non-fatal: the page can still render with hard-coded fallback
        // band names.
        if (!cancelled) {
          setLevelsInfo({ min: 1, max: 10, distribution: [] });
        }
        console.warn("Failed to load grammar levels", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const distributionFor = useCallback(
    (level: number): GrammarLevelDistribution | null => {
      if (!levelsInfo) return null;
      return levelsInfo.distribution.find((d) => d.level === level) ?? null;
    },
    [levelsInfo],
  );

  const startPractice = useCallback(async (level: number) => {
    setState({ kind: "loading-questions", level });
    setCurrentIndex(0);
    setSelectedChoice(null);
    setFillText("");
    setSubmitted(false);
    try {
      const set = await generateGrammarQuestions(level);
      setState({ kind: "ready", level, set });
    } catch (err) {
      const message = err instanceof Error ? err.message : "出题失败";
      setState({ kind: "error", level, message });
    }
  }, []);

  const currentQuestion = useMemo<GrammarQuestion | null>(() => {
    if (state.kind !== "ready") return null;
    return state.set.questions[currentIndex] ?? null;
  }, [state, currentIndex]);

  const isCorrect = useMemo<boolean | null>(() => {
    if (!submitted || !currentQuestion) return null;
    if (currentQuestion.type === "choice") {
      return selectedChoice === currentQuestion.answer;
    }
    // fill-in-blank: case-insensitive trim match. Whitespace differences
    // are common when the child types fast.
    return fillText.trim().toLowerCase() === currentQuestion.answer.trim().toLowerCase();
  }, [submitted, currentQuestion, selectedChoice, fillText]);

  const advance = useCallback(() => {
    if (state.kind !== "ready") return;
    if (currentIndex + 1 >= state.set.questions.length) {
      // End of set — stay on the last question but flip submitted so the
      // CTA switches to "再来一组".
      return;
    }
    setCurrentIndex((i) => i + 1);
    setSelectedChoice(null);
    setFillText("");
    setSubmitted(false);
  }, [state, currentIndex]);

  const restartWithLevel = useCallback(
    (level: number) => {
      void startPractice(level);
    },
    [startPractice],
  );

  // ---------- Render ----------

  return (
    <main className="mx-auto flex min-h-[100dvh] max-w-4xl flex-col gap-6 p-4 ipad:p-8">
      <header className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.3em] text-primary">Grammar</p>
          <h1 className="mt-1 text-2xl font-bold ipad:text-3xl">英语语法练习</h1>
          <p className="mt-1 text-sm text-muted-foreground ipad:text-base">
            选择难度(1-10),AI 老师为你出 10 道题,从易到难逐步挑战。
          </p>
        </div>
        <Button asChild variant="outline" className="shrink-0">
          <Link href="/">返回首页</Link>
        </Button>
      </header>

      {state.kind === "idle" || state.kind === "loading-levels" || state.kind === "error" && state.level === null ? (
        <LevelPicker
          selectedLevel={selectedLevel}
          onSelect={setSelectedLevel}
          onStart={startPractice}
          distributionFor={distributionFor}
          levelsInfo={levelsInfo}
          loading={state.kind === "loading-levels"}
        />
      ) : null}

      {state.kind === "loading-questions" ? (
        <Card>
          <CardContent className="flex items-center justify-center gap-3 py-12">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground ipad:text-base">
              正在为你出 {state.level} 级语法题(10 道),AI 老师大约需要 5-10 秒...
            </p>
          </CardContent>
        </Card>
      ) : null}

      {state.kind === "error" && state.level !== null ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-red-600">出题失败</CardTitle>
            <CardDescription>{state.message}</CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Button onClick={() => void startPractice(state.level!)}>重试</Button>
            <Button variant="outline" onClick={() => setState({ kind: "idle" })}>
              返回难度选择
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {state.kind === "ready" && currentQuestion ? (
        <QuestionCard
          question={currentQuestion}
          index={currentIndex}
          total={state.set.questions.length}
          level={state.level}
          selectedChoice={selectedChoice}
          onSelectChoice={(opt) => setSelectedChoice(opt)}
          fillText={fillText}
          onChangeFillText={setFillText}
          submitted={submitted}
          onSubmit={() => setSubmitted(true)}
          isCorrect={isCorrect}
          onAdvance={advance}
          onRestart={() => restartWithLevel(state.level)}
          isLast={currentIndex + 1 >= state.set.questions.length}
        />
      ) : null}
    </main>
  );
}

// --- Sub-components ---

function LevelPicker({
  selectedLevel,
  onSelect,
  onStart,
  distributionFor,
  levelsInfo,
  loading,
}: {
  selectedLevel: number;
  onSelect: (level: number) => void;
  onStart: (level: number) => void;
  distributionFor: (level: number) => GrammarLevelDistribution | null;
  levelsInfo: { min: number; max: number; distribution: GrammarLevelDistribution[] } | null;
  loading: boolean;
}) {
  const min = levelsInfo?.min ?? 1;
  const max = levelsInfo?.max ?? 10;
  const levels = Array.from({ length: max - min + 1 }, (_, i) => min + i);

  const selectedDist = distributionFor(selectedLevel);
  const selectedBand = bandLabel(selectedLevel);

  return (
    <Card>
      <CardHeader>
        <CardTitle>选择难度</CardTitle>
        <CardDescription>
          1-5 级全是选择题(打基础);6-10 级会加入填空题(挑战语法应用)。
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="grid grid-cols-5 gap-2 md:grid-cols-10">
          {levels.map((level) => {
            const dist = distributionFor(level);
            const isSelected = level === selectedLevel;
            return (
              <button
                key={level}
                type="button"
                onClick={() => onSelect(level)}
                className={`flex flex-col items-center justify-center gap-0.5 rounded-lg border-2 px-2 py-3 text-center transition-colors ${
                  isSelected
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-slate-200 bg-white hover:border-primary/50"
                }`}
              >
                <span className="text-lg font-bold ipad:text-xl">{level}</span>
                {dist ? (
                  <span className="text-[10px] text-muted-foreground ipad:text-xs">
                    {dist.choice}选{dist.fill_in_blank > 0 ? `+${dist.fill_in_blank}填` : ""}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
        <div className="rounded-lg border bg-slate-50 p-3 text-sm ipad:text-base">
          <p className="font-medium text-slate-700">{selectedBand}</p>
          {selectedDist ? (
            <p className="mt-1 text-slate-600">
              本难度将出 {selectedDist.choice} 道选择题
              {selectedDist.fill_in_blank > 0
                ? ` + ${selectedDist.fill_in_blank} 道填空题`
                : ""}
              ,共 10 题。
            </p>
          ) : null}
        </div>
        <Button
          size="lg"
          className="w-full ipad:text-lg"
          onClick={() => onStart(selectedLevel)}
          disabled={loading}
        >
          {loading ? "加载中..." : `开始 Level ${selectedLevel} 练习(10 题)`}
        </Button>
      </CardContent>
    </Card>
  );
}

function QuestionCard({
  question,
  index,
  total,
  level,
  selectedChoice,
  onSelectChoice,
  fillText,
  onChangeFillText,
  submitted,
  onSubmit,
  isCorrect,
  onAdvance,
  onRestart,
  isLast,
}: {
  question: GrammarQuestion;
  index: number;
  total: number;
  level: number;
  selectedChoice: string | null;
  onSelectChoice: (opt: string) => void;
  fillText: string;
  onChangeFillText: (text: string) => void;
  submitted: boolean;
  onSubmit: () => void;
  isCorrect: boolean | null;
  onAdvance: () => void;
  onRestart: () => void;
  isLast: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between text-sm text-muted-foreground ipad:text-base">
          <span>第 {index + 1} / {total} 题 · Level {level}</span>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-slate-600">
            {question.type === "choice" ? "选择题" : "填空题"}
          </span>
        </div>
        <CardTitle className="mt-2 text-lg leading-relaxed ipad:text-2xl">
          {question.prompt}
        </CardTitle>
        {question.translation ? (
          <CardDescription className="mt-2 italic">
            {question.translation}
          </CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {question.type === "choice" && question.options ? (
          <ChoiceList
            options={question.options}
            selectedOption={selectedChoice}
            correctAnswer={question.answer}
            submitted={submitted}
            onSelect={onSelectChoice}
          />
        ) : null}
        {question.type === "fill_in_blank" ? (
          <FillInput
            value={fillText}
            onChange={onChangeFillText}
            submitted={submitted}
            correctAnswer={question.answer}
            disabled={submitted}
          />
        ) : null}

        {submitted && isCorrect !== null ? (
          <ResultBanner
            isCorrect={isCorrect}
            correctAnswer={question.answer}
            explanation={question.explanation}
          />
        ) : null}

        <div className="flex items-center justify-end gap-2 pt-2">
          {!submitted && question.type === "fill_in_blank" ? (
            <Button onClick={onSubmit} disabled={fillText.trim().length === 0}>
              提交
            </Button>
          ) : null}
          {submitted && !isLast ? (
            <Button onClick={onAdvance}>下一题 →</Button>
          ) : null}
          {submitted && isLast ? (
            <Button onClick={onRestart}>再来一组</Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ChoiceList({
  options,
  selectedOption,
  correctAnswer,
  submitted,
  onSelect,
}: {
  options: string[];
  selectedOption: string | null;
  correctAnswer: string;
  submitted: boolean;
  onSelect: (opt: string) => void;
}) {
  return (
    <div className="grid gap-2">
      {options.map((opt) => {
        const isSelected = opt === selectedOption;
        const isCorrect = opt === correctAnswer;
        const isWrongSelected = submitted && isSelected && !isCorrect;
        const showCorrectHighlight = submitted && isCorrect && !isSelected;
        let cls = "border-slate-200 bg-white hover:border-primary/50";
        if (isSelected && !submitted) {
          cls = "border-primary bg-primary/10";
        }
        if (submitted && isCorrect) {
          cls = "border-emerald-500 bg-emerald-50 text-emerald-700";
        }
        if (isWrongSelected) {
          cls = "border-red-500 bg-red-50 text-red-700";
        }
        if (showCorrectHighlight) {
          // Subtle green border so the child sees the right answer
          cls = "border-emerald-300 bg-emerald-50/50 text-emerald-700";
        }
        return (
          <button
            key={opt}
            type="button"
            onClick={() => !submitted && onSelect(opt)}
            disabled={submitted}
            className={`flex items-center gap-3 rounded-lg border-2 px-4 py-3 text-left text-sm transition-colors ipad:px-5 ipad:py-4 ipad:text-base ${cls}`}
          >
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-bold ${
                isSelected
                  ? "border-primary bg-primary text-white"
                  : "border-slate-300 bg-white text-slate-500"
              }`}
            >
              {options.indexOf(opt) + 1}
            </span>
            <span className="flex-1">{opt}</span>
            {submitted && isCorrect ? <span>✅</span> : null}
            {isWrongSelected ? <span>❌</span> : null}
          </button>
        );
      })}
    </div>
  );
}

function FillInput({
  value,
  onChange,
  submitted,
  correctAnswer,
  disabled,
}: {
  value: string;
  onChange: (text: string) => void;
  submitted: boolean;
  correctAnswer: string;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-col gap-2">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder="在此输入答案..."
        className="w-full rounded-lg border-2 border-slate-200 bg-white px-4 py-3 text-base focus:border-primary focus:outline-none disabled:bg-slate-50 disabled:text-slate-500 ipad:px-5 ipad:py-4 ipad:text-lg"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !submitted && value.trim().length > 0) {
            e.preventDefault();
            // The parent owns the submit click — dispatch a synthetic click
            // on the submit button by submitting through the form.
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      {submitted ? (
        <p className="text-sm text-slate-500">
          正确答案: <span className="font-semibold text-slate-700">{correctAnswer}</span>
        </p>
      ) : null}
    </div>
  );
}

function ResultBanner({
  isCorrect,
  correctAnswer,
  explanation,
}: {
  isCorrect: boolean;
  correctAnswer: string;
  explanation: string;
}) {
  return (
    <div
      className={`rounded-lg border-2 p-3 text-sm ipad:p-4 ipad:text-base ${
        isCorrect
          ? "border-emerald-300 bg-emerald-50 text-emerald-800"
          : "border-red-300 bg-red-50 text-red-800"
      }`}
    >
      <p className="font-bold">
        {isCorrect ? "🎉 答对了!" : "😅 答错了"}
        {!isCorrect ? (
          <>
            {" "}
            正确答案: <span className="font-mono">{correctAnswer}</span>
          </>
        ) : null}
      </p>
      <p className="mt-1 leading-relaxed">{explanation}</p>
    </div>
  );
}

function bandLabel(level: number): string {
  if (level <= 2) return "🟢 入门 (Level 1-2):最简单语法,适合刚接触英语的孩子";
  if (level <= 4) return "🟡 初级 (Level 3-4):基础时态和句型";
  if (level === 5) return "🟠 初级+ (Level 5):加入情态动词等更复杂的语法点";
  if (level <= 7) return "🔴 中级 (Level 6-7):完成时、过去时、简单从句";
  if (level <= 9) return "🟣 中高级 (Level 8-9):被动语态、条件句、强调句";
  return "⚫ 高级 (Level 10):长难句、虚拟语气、倒装句";
}
