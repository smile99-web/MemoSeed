"use client";

import { KeyboardEvent, memo, RefCallback } from "react";

import { getWordInputWidth } from "@/lib/study-utils";

export const annotationColors = ["border-emerald-500", "border-sky-500", "border-violet-500", "border-amber-500", "border-rose-400"] as const;

interface WordInputProps {
  word: string;
  index: number;
  status: "idle" | "correct" | "incorrect" | "skipped";
  value: string;
  isActive: boolean;
  isDynamicBlank: boolean;
  isRespellNonTarget: boolean;
  isRespellTarget: boolean;
  shouldShowInput: boolean;
  isPreview: boolean;
  answerState: "typing" | "mistake-word-practice" | "word-meaning-review" | "sentence-complete";
  wordInputSizeClass: string;
  wordDisplaySizeClass: string;
  isStudyFullscreen: boolean;
  itemTypeLabel: string;
  chineseText: string;
  meaningText?: string;
  animationType: "correct" | "incorrect" | undefined;
  inputRef: RefCallback<HTMLInputElement>;
  onUpdateWordAnswer: (index: number, value: string) => void;
  onFocusWord: (index: number) => void;
  onWordKeyDown: (event: KeyboardEvent<HTMLInputElement>, index: number) => void;
}

const WordInput = memo(function WordInput({
  word,
  index,
  status,
  value,
  isActive,
  isDynamicBlank,
  isRespellNonTarget,
  shouldShowInput,
  isPreview,
  answerState,
  wordInputSizeClass,
  wordDisplaySizeClass,
  isStudyFullscreen,
  itemTypeLabel,
  chineseText,
  meaningText,
  animationType,
  inputRef,
  onUpdateWordAnswer,
  onFocusWord,
  onWordKeyDown,
}: WordInputProps) {
  const isAnswerTyping = answerState === "typing";

  return (
    <div
      className="relative flex shrink-0 flex-col items-center gap-2"
      style={{ minWidth: getWordInputWidth(word) }}
    >
      {/* Part-of-speech label */}
      {!isAnswerTyping && shouldShowInput && !isStudyFullscreen ? (
        <span className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground ipad:px-3 ipad:text-xs">
          {itemTypeLabel}
        </span>
      ) : null}

      {/* Input or display */}
      {shouldShowInput ? (
        <input
          ref={inputRef}
          aria-label={isDynamicBlank ? `填空：${chineseText}` : `第 ${index + 1} 个单词`}
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          className={`${wordInputSizeClass} shrink-0 border-0 border-b-4 bg-transparent px-1 text-center font-semibold leading-none outline-none transition ${
            status === "correct" ? "border-emerald-500 text-emerald-700" : ""
          } ${
            status === "incorrect" ? "border-red-500 text-red-700" : ""
          } ${
            status === "skipped" ? "border-amber-500 text-amber-700" : ""
          } ${
            status === "idle" ? annotationColors[index % annotationColors.length] : ""
          } ${
            isActive ? "bg-emerald-50" : ""
          }`}
          disabled={!isAnswerTyping || status === "skipped" || isPreview}
          placeholder={
            isPreview
              ? "先看后拼"
              : status === "skipped"
                ? "稍后练"
                : isDynamicBlank
                  ? "____"
                  : undefined
          }
          style={{ width: getWordInputWidth(word) }}
          value={value}
          onChange={(event) => onUpdateWordAnswer(index, event.target.value)}
          onFocus={() => onFocusWord(index)}
          onKeyDown={(event) => onWordKeyDown(event, index)}
        />
      ) : (
        <span
          className={`flex items-center font-semibold leading-none text-slate-900 ${wordDisplaySizeClass} ${
            isRespellNonTarget ? "rounded-md bg-yellow-100 px-2 py-1" : ""
          }`}
        >
          {word}
        </span>
      )}

      {/* Preview highlight */}
      {isPreview ? (
        <span className="rounded-md bg-amber-100 px-3 py-1 text-3xl font-bold text-amber-950 ipad:text-4xl ipad-lg:text-5xl">
          {word}
        </span>
      ) : null}

      {/* Correct answer display (non-typing, non-fullscreen) */}
      {!isAnswerTyping && shouldShowInput && !isStudyFullscreen ? (
        <span className="text-2xl font-medium text-slate-900 ipad:text-2xl ipad-lg:text-3xl">
          {word}
        </span>
      ) : null}

      {meaningText ? (
        <span className="max-w-full break-words text-center text-base font-bold text-emerald-700 ipad:text-lg ipad-lg:text-xl">
          {meaningText}
        </span>
      ) : null}

      {/* Skipped status */}
      {status === "skipped" && !isStudyFullscreen ? (
        <span className="text-base font-bold text-amber-700 ipad:text-base ipad-lg:text-lg">
          已加入错词复习
        </span>
      ) : null}

      {/* Error status */}
      {status === "incorrect" && !isStudyFullscreen ? (
        <span className="text-base font-bold text-red-600 ipad:text-base ipad-lg:text-lg">
          请按提示再试一次
        </span>
      ) : null}

      {/* Animation popup */}
      {animationType ? (
        <span
          className="absolute -right-3 -top-3 text-2xl font-bold ipad:text-3xl"
          style={{
            animation: animationType === "correct" ? "check-pop 0.3s ease-out forwards" : "incorrect-spin 0.2s ease-out forwards",
            color: animationType === "correct" ? "#10b981" : "#3b82f6",
          }}
        >
          {animationType === "correct" ? "✓" : "↻"}
        </span>
      ) : null}
    </div>
  );
});

export default WordInput;
