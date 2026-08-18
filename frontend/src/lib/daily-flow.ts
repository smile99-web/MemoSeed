// 今日学习流程（daily flow）：复习30词 → 新词20个 → 句子30句 → 每日一测
// 四阶段的定义与当日进度持久化。
//
// 2026-08-11 重构（version 2）：计时制 → 计数制。点击「开始今日学习」把四个
// 阶段加入当日任务序列，当天不重置；每次进入都从断点继续，直到全部完成；
// 第二天按日期 key 自动从初始态重新开始，内容由后端按记忆算法重新安排
// （复习=到期词，新词/句子=中考英语课程包内未学内容）。
// 纯前端实现：进度存 localStorage，按本地日期分 key。
export interface DailyFlowPhase {
  key: "review" | "learn" | "sentence" | "test";
  label: string;
  icon: string;
  /** 对应 study 页的 mode 参数（"mix" = 无 mode 参数）。 */
  mode: "review" | "learn" | "mix" | "test";
  /** 阶段完成配额（词/句数）；test 阶段无配额——以成绩单出现为完成信号。 */
  quota?: number;
  quotaUnit?: string;
  /** 内容来源说明（展示用）。 */
  note?: string;
}
export const DAILY_FLOW_PHASES: readonly DailyFlowPhase[] = [
  { key: "review",   label: "复习单词", icon: "📚", mode: "review", quota: 30, quotaUnit: "词" },
  { key: "learn",    label: "新单词",   icon: "🌱", mode: "learn",  quota: 8,  quotaUnit: "词", note: "中考英语课程包" },
  { key: "sentence", label: "练句子",   icon: "📝", mode: "mix",    quota: 30, quotaUnit: "句", note: "中考英语课程包" },
  { key: "test",     label: "每日一测", icon: "✅", mode: "test" },
];
export interface DailyFlowState {
  version: 2;
  /** 当前阶段下标（进入 DAILY_FLOW_PHASES）。 */
  phaseIndex: number;
  /** 当前阶段在之前的页面会话中已累计的有效学习时长（毫秒）。 */
  phaseElapsedMs: number;
  done: boolean;
  /** 各阶段完成时结算的总用时（毫秒），用于完成页展示。 */
  phaseDurationsMs: number[];
  /** 各阶段已完成数量（词/句），完成页展示；当前阶段实时值取 completedKeys.length。 */
  phaseCounts: number[];
  /** 当前阶段已完成的去重 key（词阶段=归一化单词，句阶段=句子 item id）。
   *  换阶段时清空——配额判断只看当前阶段。 */
  completedKeys: string[];
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
    version: 2,
    phaseIndex: 0,
    phaseElapsedMs: 0,
    done: false,
    phaseDurationsMs: DAILY_FLOW_PHASES.map(() => 0),
    phaseCounts: DAILY_FLOW_PHASES.map(() => 0),
    completedKeys: [],
  };
}
/** 读取当日流程进度；key 不存在 / 日期不对（key 本身就含日期）/ 版本不符 / 数据损坏 → 初始态。 */
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
    // v1（计时制）与 v2 结构不兼容，直接重新开始——当日进度损失可接受。
    if (!parsed || parsed.version !== 2 || typeof parsed.phaseIndex !== "number") {
      return createDailyFlowState();
    }
    const phaseIndex = Math.min(
      DAILY_FLOW_PHASES.length - 1,
      Math.max(0, Math.floor(parsed.phaseIndex)),
    );
    const durations = Array.isArray(parsed.phaseDurationsMs) ? parsed.phaseDurationsMs : [];
    const counts = Array.isArray(parsed.phaseCounts) ? parsed.phaseCounts : [];
    return {
      version: 2,
      phaseIndex,
      phaseElapsedMs: typeof parsed.phaseElapsedMs === "number" ? Math.max(0, parsed.phaseElapsedMs) : 0,
      done: parsed.done === true,
      phaseDurationsMs: DAILY_FLOW_PHASES.map((_phase, index) => {
        const value = durations[index];
        return typeof value === "number" && value > 0 ? value : 0;
      }),
      phaseCounts: DAILY_FLOW_PHASES.map((_phase, index) => {
        const value = counts[index];
        return typeof value === "number" && value > 0 ? Math.floor(value) : 0;
      }),
      completedKeys: Array.isArray(parsed.completedKeys)
        ? parsed.completedKeys.filter((key): key is string => typeof key === "string" && key.length > 0)
        : [],
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
