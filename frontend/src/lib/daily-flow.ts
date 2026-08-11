// 今日学习流程（daily flow）：复习 → 新单词 → 句子 → 每日一测 四阶段的定义与
// 当日进度持久化。纯前端实现：进度存 localStorage，按本地日期分 key，
// 新的一天自动从初始态重新开始。

export interface DailyFlowPhase {
  key: string;
  label: string;
  icon: string;
  /** 对应 study 页的 mode 参数（"mix" = 无 mode 参数）。 */
  mode: "review" | "learn" | "mix" | "test";
  minutes: number;
  /** learn 阶段的新词配额：完成 N 个不同新词即可提前进入下一阶段。 */
  wordQuota?: number;
  /** 该阶段是否必须先选择课程（无课程时 study 页显示选课引导卡）。 */
  needsCourse: boolean;
}

export const DAILY_FLOW_PHASES: readonly DailyFlowPhase[] = [
  { key: "review",   label: "复习",     icon: "📚", mode: "review", minutes: 30, needsCourse: false },
  { key: "learn",    label: "新单词",   icon: "🌱", mode: "learn",  minutes: 40, wordQuota: 20, needsCourse: true },
  { key: "sentence", label: "句子",     icon: "📝", mode: "mix",    minutes: 30, needsCourse: true },
  { key: "test",     label: "每日一测", icon: "✅", mode: "test",   minutes: 20, needsCourse: false },
];

export interface DailyFlowState {
  version: 1;
  /** 当前阶段下标（进入 DAILY_FLOW_PHASES）。 */
  phaseIndex: number;
  /** 当前阶段在之前的页面会话中已累计的有效学习时长（毫秒）。 */
  phaseElapsedMs: number;
  /** learn 阶段已完成的不同新词（normalizeEnglishKey 去重后的 key）。 */
  learnWords: string[];
  done: boolean;
  /** 各阶段完成时结算的总用时（毫秒），用于完成页展示。 */
  phaseDurationsMs: number[];
}

/** localStorage key：memoseed-daily-flow-<YYYY-MM-DD>（本地日期）。 */
export function dailyFlowStorageKey(date: Date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `memoseed-daily-flow-${year}-${month}-${day}`;
}

export function createDailyFlowState(): DailyFlowState {
  return {
    version: 1,
    phaseIndex: 0,
    phaseElapsedMs: 0,
    learnWords: [],
    done: false,
    phaseDurationsMs: DAILY_FLOW_PHASES.map(() => 0),
  };
}

/** 读取当日流程进度；key 不存在 / 日期不对（key 本身就含日期）/ 数据损坏 → 初始态。 */
export function loadDailyFlow(): DailyFlowState {
  if (typeof window === "undefined") {
    return createDailyFlowState();
  }
  try {
    const raw = window.localStorage.getItem(dailyFlowStorageKey());
    if (!raw) {
      return createDailyFlowState();
    }
    const parsed = JSON.parse(raw) as Partial<DailyFlowState> | null;
    if (!parsed || parsed.version !== 1 || typeof parsed.phaseIndex !== "number") {
      return createDailyFlowState();
    }
    const phaseIndex = Math.min(
      DAILY_FLOW_PHASES.length - 1,
      Math.max(0, Math.floor(parsed.phaseIndex)),
    );
    const durations = Array.isArray(parsed.phaseDurationsMs) ? parsed.phaseDurationsMs : [];
    return {
      version: 1,
      phaseIndex,
      phaseElapsedMs: typeof parsed.phaseElapsedMs === "number" ? Math.max(0, parsed.phaseElapsedMs) : 0,
      learnWords: Array.isArray(parsed.learnWords)
        ? parsed.learnWords.filter((word): word is string => typeof word === "string" && word.length > 0)
        : [],
      done: parsed.done === true,
      phaseDurationsMs: DAILY_FLOW_PHASES.map((_phase, index) => {
        const value = durations[index];
        return typeof value === "number" && value > 0 ? value : 0;
      }),
    };
  } catch {
    return createDailyFlowState();
  }
}

export function saveDailyFlow(state: DailyFlowState): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(dailyFlowStorageKey(), JSON.stringify(state));
  } catch {
    // 存储满 / 隐私模式下静默失败 —— 流程仍可在本次会话内继续进行。
  }
}
