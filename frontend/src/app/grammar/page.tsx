"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  GrammarLevelDistribution,
  GrammarQuestion,
  GrammarQuestionSet,
  completeGrammarSession,
  generateGrammarQuestions,
  getGrammarLevels,
  startGrammarSession,
  submitGrammarAnswer,
} from "@/lib/grammar";

type LoadState =
  | { kind: "idle" }
  | { kind: "loading-levels" }
  | { kind: "loading-questions"; level: number }
  | { kind: "ready"; level: number; set: GrammarQuestionSet; sessionId: string }
  | { kind: "completed"; level: number; set: GrammarQuestionSet; sessionId: string; correct: number }
  | { kind: "error"; level: number | null; message: string };

interface AnswerRecord {
  question_id: string;
  question_type: "choice" | "fill_in_blank";
  level: number;
  prompt: string;
  user_answer: string;
  correct_answer: string;
  is_correct: boolean;
  time_spent_ms: number;
}

/**
 * 语法练习页
 * ----------
 * 用户先选难度(1-10),点击"开始练习"调 LLM 生成 10 道题。
 * 答题流程:
 *  - 选择题:点击选项 → 立即高亮对/错 + 解释
 *  - 填空题:输入文本 → 点击"提交" → 高亮对/错 + 解释
 *  答完一道后底部出现"下一题"按钮,最后一道完成后显示本轮成绩。
 *
 * 历史持久化:每次开始练习调 POST /grammar/sessions 创建服务端
 * session;每答一题 POST /answers;最后调 POST /complete 封档。
 * 网络错误时降级为本地作答(不阻塞 UI),并在控制台打印警告。
 */
export default function GrammarPage() {
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [selectedLevel, setSelectedLevel] = useState<number>(3);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const [fillText, setFillText] = useState("");
  const [submitted, setSubmitted] = useState(false);

  // Tracks the in-memory answer log so we can show the running score on
  // the "本轮完成" card and so a refresh-mid-session doesn't lose the
  // submission status (we still POST to the server on submit, but this
  // also drives the UI state).
  const [localAnswers, setLocalAnswers] = useState<AnswerRecord[]>([]);

  // Track per-question timing (in ms) for submission to the server.
  // Reset each time we advance to a new question.
  const questionStartedAtRef = useRef<number>(Date.now());

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
    setLocalAnswers([]);
    try {
      const set = await generateGrammarQuestions(level);
      // Best-effort: try to start a server session. If the user is offline
      // or the call fails, fall back to local-only — the page should still
      // work, just without the history recorded.
      let sessionId = "local";
      try {
        const session = await startGrammarSession({
          level,
          total_questions: set.questions.length,
          choice_questions: set.questions.filter((q) => q.type === "choice").length,
          fill_in_questions: set.questions.filter((q) => q.type === "fill_in_blank").length,
          question_ids: set.questions.map((q) => q.id),
        });
        sessionId = session.id;
      } catch (sessionErr) {
        console.warn("Failed to start grammar session (offline?) — continuing in local mode", sessionErr);
      }
      questionStartedAtRef.current = Date.now();
      setState({ kind: "ready", level, set, sessionId });
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

  // Fire when the user submits (clicks choice / submits fill-in). Records
  // locally + POSTs to the server (best-effort).
  const handleSubmit = useCallback(
    (question: GrammarQuestion, userAnswer: string, correct: boolean) => {
      const timeSpent = Math.max(0, Date.now() - questionStartedAtRef.current);
      const record: AnswerRecord = {
        question_id: question.id,
        question_type: question.type,
        level: question.level,
        prompt: question.prompt,
        user_answer: userAnswer,
        correct_answer: question.answer,
        is_correct: correct,
        time_spent_ms: timeSpent,
      };
      setLocalAnswers((prev) => [...prev, record]);

      if (state.kind === "ready" && state.sessionId !== "local") {
        // Best-effort: don't block the UI if the network is down.
        void submitGrammarAnswer(state.sessionId, record).catch((err) => {
          console.warn("Failed to submit grammar answer", err);
        });
      }
    },
    [state],
  );

  const advance = useCallback(() => {
    if (state.kind !== "ready") return;
    if (currentIndex + 1 >= state.set.questions.length) {
      // Last question — seal the session and transition to the
      // "completed" state. Completion call is best-effort.
      const correctCount = localAnswers.filter((a) => a.is_correct).length;
      if (state.sessionId !== "local") {
        void completeGrammarSession(state.sessionId).catch((err) => {
          console.warn("Failed to complete grammar session", err);
        });
      }
      setState({
        kind: "completed",
        level: state.level,
        set: state.set,
        sessionId: state.sessionId,
        correct: correctCount,
      });
      return;
    }
    setCurrentIndex((i) => i + 1);
    setSelectedChoice(null);
    setFillText("");
    setSubmitted(false);
    questionStartedAtRef.current = Date.now();
  }, [state, currentIndex, localAnswers]);

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
        <div className="flex shrink-0 gap-2">
          <Button asChild variant="outline">
            <Link href="/grammar/history">练习历史</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/">返回首页</Link>
          </Button>
        </div>
      </header>

      {state.kind === "idle" || state.kind === "loading-levels" || (state.kind === "error" && state.level === null) ? (
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
          onSubmitAnswer={(userAnswer, correct) => handleSubmit(currentQuestion, userAnswer, correct)}
          onSubmitClicked={() => setSubmitted(true)}
          isCorrect={isCorrect}
          onAdvance={advance}
          isLast={currentIndex + 1 >= state.set.questions.length}
        />
      ) : null}

      {state.kind === "completed" ? (
        <SessionResults
          level={state.level}
          total={state.set.questions.length}
          correct={state.correct}
          set={state.set}
          onRestart={() => restartWithLevel(state.level)}
          onChangeLevel={() => setState({ kind: "idle" })}
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
  onSubmitAnswer,
  onSubmitClicked,
  isCorrect,
  onAdvance,
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
  onSubmitAnswer: (userAnswer: string, correct: boolean) => void;
  onSubmitClicked: () => void;
  isCorrect: boolean | null;
  onAdvance: () => void;
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
            onSelect={(opt) => {
              onSelectChoice(opt);
              if (!submitted) {
                // Choice questions lock in immediately — fire the
                // submission handler and flip to the result state in the
                // same tick so the styling + banner update together.
                const correct = opt === question.answer;
                onSubmitAnswer(opt, correct);
                onSubmitClicked();
              }
            }}
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
            <Button
              onClick={() => {
                const correct =
                  fillText.trim().toLowerCase() === question.answer.trim().toLowerCase();
                onSubmitAnswer(fillText.trim(), correct);
                onSubmitClicked();
              }}
              disabled={fillText.trim().length === 0}
            >
              提交
            </Button>
          ) : null}
          {submitted && !isLast ? (
            <Button onClick={onAdvance}>下一题 →</Button>
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

/**
 * End-of-session summary shown after the last question is graded.
 * Displays:
 *  - The accuracy % with an emoji band (🌟/👍/💪/📚)
 *  - Per-type breakdown (choice vs fill-in accuracy)
 *  - Encouragement message tailored to the level
 *  - Two CTAs: "再来一组" (same level) or "换个难度" (back to picker)
 */
function SessionResults({
  level,
  total,
  correct,
  set,
  onRestart,
  onChangeLevel,
}: {
  level: number;
  total: number;
  correct: number;
  set: GrammarQuestionSet;
  onRestart: () => void;
  onChangeLevel: () => void;
}) {
  const accuracy = total > 0 ? correct / total : 0;
  const encouragement = pickEncouragement(accuracy, level);
  const emoji = accuracyBand(accuracy);

  // Per-type breakdown — recompute from the question set so the
  // counts match the just-graded set (no extra round-trip needed).
  const choiceTotal = set.questions.filter((q) => q.type === "choice").length;
  const fillTotal = set.questions.filter((q) => q.type === "fill_in_blank").length;
  // We don't have per-question is_correct here, so the breakdown shows
  // the *type split* only. A future iteration could enrich this from
  // the per-answer submission log; for now the headline accuracy is
  // what the parent cares about.

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between text-sm text-muted-foreground ipad:text-base">
          <span>Level {level} 练习完成</span>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-slate-600">
            {total} 题
          </span>
        </div>
        <CardTitle className="mt-3 text-3xl font-bold ipad:text-4xl">
          {emoji} {correct} / {total}
        </CardTitle>
        <CardDescription className="text-base ipad:text-lg">
          正确率 {Math.round(accuracy * 100)}% · {encouragement}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border bg-slate-50 p-3 text-sm ipad:p-4 ipad:text-base">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              选择题
            </p>
            <p className="mt-1 text-2xl font-bold ipad:text-3xl">{choiceTotal}</p>
            <p className="text-xs text-muted-foreground">共 {choiceTotal} 题</p>
          </div>
          <div className="rounded-lg border bg-slate-50 p-3 text-sm ipad:p-4 ipad:text-base">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              填空题
            </p>
            <p className="mt-1 text-2xl font-bold ipad:text-3xl">{fillTotal}</p>
            <p className="text-xs text-muted-foreground">共 {fillTotal} 题</p>
          </div>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onChangeLevel}>
            换个难度
          </Button>
          <Button onClick={onRestart}>再来一组(同难度)</Button>
        </div>
      </CardContent>
    </Card>
  );
}

function accuracyBand(accuracy: number): string {
  if (accuracy >= 0.9) return "🌟";
  if (accuracy >= 0.7) return "👍";
  if (accuracy >= 0.5) return "💪";
  return "📚";
}

function pickEncouragement(accuracy: number, level: number): string {
  if (accuracy >= 0.9) {
    return "太棒了!你已经掌握了这个难度的语法,可以挑战更高的 Level 了!";
  }
  if (accuracy >= 0.7) {
    return "做得不错!继续练习就能稳定掌握这些语法点。";
  }
  if (accuracy >= 0.5) {
    return "已经及格,但还有提升空间。回顾一下错题,再来一次吧!";
  }
  if (level > 1) {
    return "别灰心!可以先降到更低的 Level 巩固基础,语法需要多练。";
  }
  return "没关系,语法需要慢慢积累。点“再试一次”继续挑战!";
}
