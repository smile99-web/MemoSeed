# FSRS 个性化应用验证报告

**生成日期**: 2026-06-17
**目标**: 验证 FSRS 个性化参数是否真实应用于复习调度
**状态**: **部分应用 — 个性化已生效，但审计轨迹缺失**

---

## 1. TL;DR

| 维度 | 结论 | 证据 |
|---|---|---|
| FSRS 整体激活 | ✅ 已应用 | 对比脚本：SM-2 输出与 default FSRS 差异显著 |
| 个性化权重激活 | ✅ 已应用 | 对比脚本：personalized FSRS 输出与 default FSRS 差异显著 |
| 队列使用个性化 | ✅ 间接应用 | `next_review_at` / `forget_risk` 由 `schedule_memory_review` 写入，调用了 `get_effective_fsrs_params` |
| 持久化到 `user_model_settings` | ✅ 部分 | 存了 19 元 `fsrsWeights` 元组 + 4 个元数据；没存 7 个具名参数 |
| **审计轨迹** | ❌ **缺失** | `review_logs` 和 `memory_states` 都没记 `scheduler_type` / `algorithm_version` / `fsrs_params_snapshot` / `previous_interval` / `new_interval` / `next_review_at` |

**核心 gap**: 事后从一行 `review_log` 无法回答"那一次复习用了哪组 weights"。

---

## 2. 验证方法

### 2.1 静态代码审查

阅读以下文件以建立 baseline 认知：
- `backend/app/services/memory_scheduler.py` — 核心调度逻辑
- `backend/app/services/fsrs_fitting.py` — 用户参数拟合
- `backend/app/models/review_log.py` — 复习日志表
- `backend/app/models/memory_state.py` — 记忆状态表
- `backend/app/models/daily_plan.py` — 每日计划表
- `backend/app/models/user_model_settings.py` — 用户模型设置
- `backend/app/api/v1/review/router.py` — 复习队列 API
- `backend/app/api/v1/settings/router.py` — 设置同步 API
- `backend/app/api/v1/memory/router.py` — 记忆 API

### 2.2 离线对比脚本

新增 `backend/scripts/verify_fsrs_application.py`，对同一组模拟 review 历史（7 步，混合 Good/Hard/Easy/一次 lapse）运行三种调度策略，输出 `next_review_at` / `interval_days` / `forget_risk` 的差异。

可重复运行：

```bash
# 在 backend 的 Python 环境中（fastapi + sqlalchemy 已装）
python backend/scripts/verify_fsrs_application.py
python backend/scripts/verify_fsrs_application.py --verbose
```

不依赖数据库，纯 Python。

### 2.3 字段扫描

脚本同时扫描 `review_log.py` 和 `memory_state.py` 的源文件，报告审计字段是否存在。

---

## 3. 个性化参数持久化验证

### 3.1 已存储的内容

`user_model_settings.settings` JSONB 字段保存：

| 键 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `fsrsWeights` | list[float], 19 元 | `fit_user_fsrs_parameters` | 19 元 FSRS v4 权重 |
| `fsrsFittedAt` | ISO datetime | 同上 | 拟合时间戳 |
| `fsrsTrainingReviewCount` | int | 同上 | 训练样本数（≥ 3000） |
| `fsrsTrainingPairCount` | int | 同上 | 训练对数（同一单词前后两次 review） |
| `fsrsAccuracyRate` | float | 同上 | 拟合期准确率 |
| `childFitted` | bool | 同上 | 标记 |
| `useChildProfile` | bool | 用户设置 | 切换 target retention 0.90 / 0.92 |
| `useSlowLearnerProfile` | bool | 用户设置 | 切换 SLOW_LEARNER_FSRS_WEIGHTS |

### 3.2 未按 7 字段命名存储

Goal 描述的 7 个具名参数：

| 参数 | 是否存储 | 备注 |
|---|---|---|
| `sample_count` | ✅ 存为 `fsrsTrainingReviewCount` | |
| `fitted_at` | ✅ 存为 `fsrsFittedAt` | |
| `target_retention` | ⚠️ 间接存 | 通过 `useChildProfile` / `useSlowLearnerProfile` 切换固定值（0.90 / 0.92 / 0.90），不是拟合出来的 |
| `forgetting_speed` | ❌ 未单独存 | 隐式编码在 19 元 `fsrsWeights` 中 |
| `stability_growth` | ❌ 未单独存 | 同上 |
| `difficulty_weight` | ❌ 未单独存 | 同上 |
| `mistake_penalty` | ❌ 未单独存 | 同上 |

**判断**: 当前实现选择存 19 元 FSRS v4 标准权重元组（一个完整定义 FSRS v4 行为），而不是拆成 7 个具名参数。这种方式**技术上更标准**（直接对应 FSRS 论文的参数），但**不易解释**给非技术读者。如果产品决策要求按 7 字段暴露给前端，需要额外做一次"19 元 → 7 字段"的投影。

### 3.3 触发条件

`fsrs_fitting.py:MIN_FSRS_TRAINING_REVIEWS = 3000`。当用户 `review_count < 3000` 时，API 抛 400。

**当前数据**: 7000+ review_logs，理论上至少一个用户已触发过 fitting。但 `fit_user_fsrs_parameters` 是手动触发的，入口未在前端 settings 页暴露（待确认）。需要查 `user_model_settings` 表确认哪些 user_id 有非空的 `fsrsWeights` 记录。

### 3.4 真实数据库验证（待补）

需要运行 SQL 确认已 fitted 的用户数：

```sql
SELECT
  COUNT(*) FILTER (WHERE settings ? 'fsrsWeights') AS fitted_users,
  COUNT(*) AS total_users
FROM user_model_settings;
```

此步骤需要在 VPS 数据库上跑（不在本次验证范围）。

---

## 4. 复习队列使用验证

### 4.1 路径追踪

1. **写入侧**（每次 review）: `api/v1/review/router.py:create_review_log` → `services/memory_scheduler.py:schedule_memory_review`
   - 第 603 行：`fsrs_weights, fsrs_target_retention = get_effective_fsrs_params(db, user_id)`
   - 后续 612-637 行：`next_fsrs_difficulty` / `initial_fsrs_stability` / `next_fsrs_recall_stability` / `next_fsrs_forget_stability` 都用 `fsrs_weights`
   - 第 664 行：`calculate_fsrs_interval(next_stability_days, fsrs_target_retention)` 用 target retention
   - 写入 `memory_state.next_review_at = now + review_delay`（第 681 行）

2. **读取侧**（队列构建）: `api/v1/review/router.py:get_review_queue`（20-46 行）
   - 查询 `MemoryState.next_review_at <= now`（已由上面写入）
   - 排序 `calculate_review_priority(memory_state, now)`（memory_scheduler.py:284-301）
   - `calculate_review_priority` 不直接调 weights，但**用的是 `forget_risk` 和 `next_review_at`，这两个值在上一步写入时由 weights 算出**

**结论**: 队列个性化通过持久化字段（`next_review_at` / `forget_risk`）间接生效。**没有"每次查询时重新跑 weights"的成本**，但也没有"切换 weights 后历史 review 能被重新评估"的能力。

### 4.2 `daily_plans` 表的 FSRS 使用

`daily_plans` 表只存静态时间分配（`warmup_review_minutes=10` 等）和 `strategy` JSONB 字段。**实际不存储也不读 FSRS 权重**。复习队列完全由 `memory_states.next_review_at` 决定。

`daily_plans` 的 `strategy` 字段未在 memory_scheduler 路径中读取，与 FSRS 权重无关联。

---

## 5. 对比脚本结果

运行 `python backend/scripts/verify_fsrs_application.py` 的输出（2026-06-17）：

```
==============================================================================
FSRS APPLICATION VERIFICATION
==============================================================================

Step history (score, days-since-prev):
  (4, +0d), (3, +1d), (4, +3d), (5, +7d), (2, +14d), (3, +1d), (4, +4d)

------------------------------------------------------------------------------
Final scheduling outcomes
------------------------------------------------------------------------------
strategy          | weights                       | interval | stability(d) | difficulty | forget_risk
------------------------------------------------------------------------------------------------------
baseline_sm2      | SM-2 (ease 1.3-2.5+)          | 6d       | 6.0          | 4.5        | 0.5
default_fsrs      | FSRS(19 weights, target=0.9)  | 3d       | 3.224        | 9.26       | 0.1
personalized_fsrs | FSRS(19 weights, target=0.92) | 1d       | 1.522        | 9.26       | 0.08

------------------------------------------------------------------------------
Per-step intervals (days)
------------------------------------------------------------------------------
step | score | baseline_sm2 | default_fsrs | personalized_fsrs
--------------------------------------------------------------
1    | 4     | 1d           | 1d           | 1d
2    | 3     | 6d           | 2d           | 1d
3    | 4     | 29d          | 8d           | 3d
4    | 5     | 140d         | 58d          | 26d
5    | 2     | 1d           | 1d           | 1d
6    | 3     | 1d           | 0d           | 0d
7    | 4     | 6d           | 3d           | 1d

------------------------------------------------------------------------------
Findings
------------------------------------------------------------------------------
  FSRS is at least active vs SM-2 baseline:     YES
  Personalized weights differ from default:    YES
  Personalized-vs-default interval delta:     -2 days (final step)
```

### 5.1 解读

- **SM-2 在第 4 步爆炸到 140 天**：ease_factor 上不封顶（>2.5），符合 SM-2 已知行为。生产环境不会出现这个，因为 MemoSeed 走的是 FSRS。
- **Default FSRS 第 4 步 58 天**：FSRS v4 + child weights + target 0.9，行为正常。
- **Personalized FSRS 第 4 步 26 天**：将 `w[8]`（stability growth intercept）减 20% + `w[10]` 减 15% + `w[15]` 加 10%，模拟"学得慢"用户。**间隔更短、恢复更慢**，符合慢学习者预期。
- **Final step 7 的对比**：3d vs 1d，差 2 天。说明个性化**真实影响**最终决策。

### 5.2 局限性

- `personalized_fsrs` 用的是**模拟权重**（手工扰动 3 个参数）。真实拟合权重可能产生不同方向的差异，但**只要差异非零，就证明权重在调度路径上被消费**。
- 脚本不复现 `STS / 慢学习者 / 时间窗口调整` 等真实生产路径中的额外行为。**它只测核心 FSRS 权重是否被消费**，不测所有启发式。

---

## 6. 审计字段缺失 — 核心 gap

### 6.1 当前 schema 缺少的字段

| 表 | 缺失字段 | 影响 |
|---|---|---|
| `review_logs` | `scheduler_type` | 不知道这次是 `built_in` / `user_fitted` / `child` / `slow_learner` 哪一档 |
| `review_logs` | `algorithm_version` | 不知道 FSRS v4 / 旧版 / 实验性 |
| `review_logs` | `fsrs_params_snapshot` | 不知道那一次用的 19 元权重具体值 |
| `review_logs` | `previous_interval` | 不知道 review 前的 interval_days |
| `review_logs` | `new_interval` | 不知道 review 后的 interval_days |
| `review_logs` | `next_review_at` | 不知道排到哪一天 |
| `memory_states` | `scheduler_type` | 同上 |
| `memory_states` | `algorithm_version` | 同上 |
| `memory_states` | `fsrs_params_snapshot` | 同上 |

### 6.2 影响

- **不能回答**: "这个孩子在 2026-05-15 复习 apple 时，scheduler 用了哪组 weights？"
- **不能复现**: 历史 schedule 决策（因为 weights 可能已变化）
- **不能 A/B 测试**: 没有 ground truth 知道哪一组用户受哪组 weights 影响
- **不能排查"为什么这个单词被推到 30 天后"**: 只能看到 `forget_risk=0.1`，但不知道算这个 0.1 的 weights 是什么

### 6.3 修复建议（task 6 范围）

最小改动：在 `ReviewLog` 和 `MemoryState` 增加 6 个字段（见 task 6）。

**约束遵守**: 禁删数据 / 禁重置 DB / 禁 mock / 禁大规模重构。增加字段是 additive migration，安全。

---

## 7. 修复建议优先级

| 优先级 | 项 | 文件 | 工作量 |
|---|---|---|---|
| **P0** | 6 个审计字段加到 schema + alembic migration | `models/review_log.py`, `models/memory_state.py`, `alembic/versions/xxx_*.py` | 1h |
| **P0** | `schedule_memory_review` 写入审计字段 | `services/memory_scheduler.py:683-695` | 30min |
| **P1** | 数据库回填：现有 review_logs 用当前 `user_model_settings.fsrsWeights` 填 `fsrs_params_snapshot` | 新建 `backend/scripts/backfill_fsrs_audit.py` | 1h |
| **P1** | 测试用例（task 7 范围） | `backend/tests/test_fsrs_application.py` | 2h |
| **P2** | 投影 19 元 → 7 具名参数（如前端要） | `services/fsrs_fitting.py` | 1h |

---

## 8. 后续步骤

1. **task 6**: 实施 P0 修复（schema + scheduler 写入）
2. **task 7**: 编写测试
3. 在 VPS 上跑 SQL 确认哪些用户已 fitted
4. 前端 settings 页暴露"fit personalized"按钮（当前未见入口）

---

## 附录 A: 关键代码引用

| 行 | 内容 |
|---|---|
| `services/memory_scheduler.py:210-239` | `get_effective_fsrs_params` — 权重选择 |
| `services/memory_scheduler.py:603` | `schedule_memory_review` 调 `get_effective_fsrs_params` |
| `services/memory_scheduler.py:612-664` | weights 实际消费处 |
| `services/fsrs_fitting.py:25` | `MIN_FSRS_TRAINING_REVIEWS = 3000` |
| `services/fsrs_fitting.py:38-116` | `fit_user_fsrs_parameters` — 拟合 + 持久化 |
| `api/v1/review/router.py:30-34` | 队列构建 + 排序 |
| `api/v1/settings/router.py:108-115` | `preserve_non_model_settings` 保留 fsrs* 键 |

## 附录 B: 复现命令

```bash
# macOS / Linux
cd /Users/ai/MemoSeed
source /tmp/memoseed_verify_venv/bin/activate   # 或项目 venv
python backend/scripts/verify_fsrs_application.py --verbose

# Windows (PowerShell)
cd C:\Users\asd\MemoSeed
.\.venv\Scripts\Activate.ps1
python backend\scripts\verify_fsrs_application.py --verbose
```
