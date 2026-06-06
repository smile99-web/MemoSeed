# MemoSeed

MemoSeed 是一个**面向中小学生的英语长期记忆操作系统**——不是大而全的英语学习平台，而是一个专注单词、短语和简单句长期记忆的训练系统。

核心算法基于 **FSRS v4（Free Spaced Repetition Scheduler）** + 儿童参数校准 + SM-2 动态难度修正 + AI 每日调度，实现从"学了就忘"到"真正记住"的闭环。

## 许可证

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0），详见 [LICENSE](LICENSE)。

## 技术栈

| 层级 | 技术 |
|---|---|
| **前端** | Next.js 15 (App Router), React 19, TypeScript (strict), TailwindCSS, shadcn/ui-compatible |
| **后端** | FastAPI, Python 3.12, SQLAlchemy 2.x, Alembic, Pydantic v2 |
| **数据库** | PostgreSQL 16, Prisma schema |
| **认证** | JWT access + refresh token, bcrypt, HMAC token hashing |
| **AI/LLM** | Ollama（本地）、DeepSeek、Qwen（OpenAI-compatible Chat Completions） |
| **TTS** | CosyVoice 2.0（本地 Docker）、Kokoro（本地 OpenAPI）、火山引擎 TTS（云端）、浏览器 speechSynthesis 降级 |
| **部署** | Docker Compose（开发 / 生产双 Profile） |
| **测试/规范** | Playwright (e2e), ESLint/Prettier (前端), Ruff/mypy (后端) |

## 项目结构

```text
MemoSeed/
├── frontend/
│   ├── src/app/               # Next.js App Router 页面
│   │   ├── page.tsx           # 首页（登录态判断、模型设置、学习入口）
│   │   ├── login/             # 登录
│   │   ├── register/          # 注册
│   │   ├── dashboard/         # 学习数据看板
│   │   ├── learning/          # 课程选择
│   │   ├── learning/study/    # 交互式学习页（核心）
│   │   └── learning/import/   # 课程管理/导入
│   ├── src/components/        # AppShell, DeviceProvider, PhonicsAudio, ui/
│   └── src/lib/               # API 客户端、认证、学习工具函数
├── backend/
│   ├── app/api/v1/            # REST API 路由
│   │   ├── auth/              # 注册/登录/刷新/登出
│   │   ├── users/             # 用户管理
│   │   ├── courses/           # 课程包与课程 CRUD、导出导入、缓存重建
│   │   ├── learning/          # 学习项管理、导入、翻译、错词、动态句
│   │   ├── memory/            # 看板、FSRS 拟合、记忆状态、调度、学时
│   │   ├── review/            # 复习队列与日志
│   │   ├── reports/           # 日报、计划、留存曲线、错误分析、导出导入
│   │   ├── settings/          # 用户模型设置（LLM/TTS 配置）
│   │   └── tts/               # 语音合成、缓存、预取、自然拼读
│   ├── app/core/              # 配置、安全（JWT/密码）
│   ├── app/db/                # 数据库引擎与会话
│   ├── app/models/            # SQLAlchemy 2.x ORM 模型（16 张表）
│   ├── app/schemas/           # Pydantic 请求/响应 schema
│   ├── app/services/          # 业务逻辑层
│   └── alembic/               # 数据库迁移
├── database/
│   ├── init/                  # 初始化 SQL 脚本
│   └── prisma/                # Prisma schema
├── docker/                    # Dockerfile（frontend + backend）
├── docs/
├── prompts/                   # AI Prompt 模板
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
└── PROJECT_RULES.md
```

## 已实现功能

### 用户认证
- 注册、登录、JWT access + refresh 双 token 体系
- refresh token 轮换（旧 token 刷新后立即失效）
- 登出撤销 token
- 用户信息查询

### 课程管理
- 层级结构：课程包 → 课程 → 学习项（单词/短语/句子）
- 课程包与课程 CRUD
- 课程包导出/导入（JSON 格式，支持跨用户迁移）
- 课程前置条件锁定（基于前置课程掌握率阈值）
- 课程进度追踪（已掌握/巩固中/教学中/困难词数）
- 课程缓存重建管道（流式 SSE）：翻译 → TTS 预生成 → 语音缓存
- 缓存状态看板（翻译/英文音频/中文音频覆盖率）

### 交互式学习页（核心）
- **多模态编码阶段**：释义初识 → 听力输入 → 拼读追踪（自然拼读）→ 整词回忆 → 语境运用
- **逐词输入句子学习**：句子按词拆分，逐格填入
- **9 种微复习任务**：中译英、听音拼写、听音选义、英译中、翻译匹配、缺字母、完形填空、隐词回忆、单词回想
- **错词即时回练**：错词预览 → 重新输入 → 再次验证
- **AI 动态复习句**：基于错词生成包含上下文的复习句子
- **TTS 语音辅助**：单词发音、中文释义朗读、慢速逐词拆读
- **自适应计时**：>10 秒无操作自动暂停，恢复后继续
- **课程完成庆祝**：AI 生成的中英双语鼓励语 + 动画
- **进度恢复**：localStorage 保存每个课程的学习位置
- **听写模式**、全屏模式切换

### 记忆算法（FSRS v4 + SM-2）
- 完整 FSRS v4 实现，19 个可调权重
- **儿童参数校准**：初稳定度比成人降低 50%
- **"慢学者"模式**：可进一步缩短复习间隔
- **用户个性化拟合**：基于 ≥150 条复习记录自动拟合 FSRS 权重
- SM-2 兼容评分（0-5 分）、ease factor、间隔计算
- 学习项类型感知调度（句子比单词衰减更快）
- **词级记忆追踪**：细粒度错误类型分类（首字母/释义/中间/顺序/尾字母/漏字母/多字母/未知）
- 遗忘风险、记忆强度基于 FSRS 稳定度和流逝时间实时计算

### 学习看板
- 记忆概览：已掌握/学习中/薄弱词数、正确率、平均记忆强度/遗忘风险
- FSRS 参数展示与拟合建议
- **留存曲线**：按复习后天数分组的内联 SVG 柱状图 + 正确率折线
- **复习预测**：今日剩余、明日预计、未来 7 天趋势及负荷等级标签
- **错误类型分布**：本周 vs 上周对比 + 趋势箭头
- **学习连续**：当前连续天数、最长连续、总学习天数
- **学时统计**：今日/本周/本月/今年/总计
- **单词下钻**：任意单词的完整复习与错误历史
- 数据导出/导入（全部业务表 JSON 格式，支持 ID 重映射和去重）

### AI 集成（LLM）
- 英译中（单词级 + 句子级）
- AI 生成动态复习句（基于错词）
- AI 日报生成（正确率、错误率、积压量、策略建议 JSON）
- AI 课程完成鼓励语生成
- 多 Provider 切换：Ollama（本地）、DeepSeek（云端）、Qwen（云端）
- 首页模型连通性测试（本地/网络 LLM、本地/网络 TTS）

### TTS 语音合成
- 三个 TTS Provider：CosyVoice 2.0（本地 Docker）、Kokoro（本地 OpenAPI）、火山引擎（云端 seed-tts-2.0）
- 浏览器 speechSynthesis 降级
- 多种中英文音色可选（含性别区分）
- 语音资源缓存系统（数据库追踪 + 文件缓存）
- 课程级音频预取
- **自然拼读卡片**：音素级语音合成
- TTS 用量日志

### 日报与计划
- 每日学习报告（含 AI 生成分析）
- 今日学习计划（复习预热 → 新学 → 句子训练 → 错词强化，各组时间分配）
- 次日 AI 策略建议

## 数据库

PostgreSQL 16，共 16 张业务表。容器首次启动自动执行 `database/init/` 下的初始化脚本：

| 脚本 | 内容 |
|---|---|
| `001_schema.sql` | 核心表：users, refresh_tokens, course_packages, courses, learning_items, memory_states, review_logs, mistake_logs, word_memory_states, word_review_tasks |
| `002_courses.sql` | 预置课程数据 |
| `003_user_model_settings.sql` | 用户模型设置表 |
| `004_study_time_logs.sql` | 学习时间日志 |
| `005_word_memory_tables.sql` | 词级记忆与复习任务 |
| `006_daily_plans.sql` | 每日学习计划 |
| `007_ai_daily_reports.sql` | AI 日报 |
| `008_speech_assets.sql` | TTS 语音资源缓存 |
| `009_course_completion_logs.sql` | 课程完成记录 |

Alembic 可用于后续迁移：

```bash
cd backend
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## 环境准备

复制环境变量模板：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

至少修改以下配置：

- `JWT_SECRET_KEY` — 必填，JWT 签名密钥
- `POSTGRES_PASSWORD` — 数据库密码
- 如使用云端大模型，填写 `AI_API_KEY`
- 如使用火山引擎 TTS，填写 `VOLCENGINE_TTS_API_KEY`

真实密钥应只写入本地 `.env` 或登录后在模型设置界面保存，不要提交到 Git。

## Docker 启动

### 开发环境（热更新，挂载本地代码）

```bash
docker compose up --build
```

服务地址：

| 服务 | 地址 |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000/api/v1 |
| Health Check | http://localhost:8000/health |
| OpenAPI 文档 | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |

### 启用 TTS Profile

CosyVoice 2.0 本地 TTS（需要额外资源）：

```bash
docker compose --profile tts up --build
```

全部服务（含 CosyVoice）：

```bash
docker compose --profile full up --build
```

### 局域网访问

前端默认通过同源 `/api/v1` 代理访问后端，手机或其他设备使用：

```text
http://<你的局域网IP>:3000
```

无需额外修改 API 地址。

### 生产镜像验证

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

生产镜像复用同一端口和数据库卷。停止：

```bash
docker compose down
```

清空数据库卷：

```bash
docker compose down -v
```

## 本地运行（不使用 Docker）

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Windows PowerShell：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

常用命令：

```bash
npm run lint
npm run typecheck
npm run format:check
npm run build
```

### Prisma（可选）

```bash
cd database
npx prisma generate --schema prisma/schema.prisma
```

## API 概览

### Auth / Users

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register` | 注册 |
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/refresh` | 刷新 token |
| POST | `/api/v1/auth/logout` | 登出 |
| GET | `/api/v1/auth/me` | 当前用户信息 |
| GET | `/api/v1/users/{user_id}` | 用户查询 |

### Courses

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/courses/packages` | 课程包列表 |
| POST | `/api/v1/courses/packages` | 创建课程包 |
| DELETE | `/api/v1/courses/packages/{id}` | 删除课程包 |
| POST | `/api/v1/courses/packages/export` | 导出课程包 |
| POST | `/api/v1/courses/packages/import` | 导入课程包 |
| GET | `/api/v1/courses/courses` | 课程列表 |
| POST | `/api/v1/courses/courses` | 创建课程 |
| DELETE | `/api/v1/courses/courses/{id}` | 删除课程 |
| GET | `/api/v1/courses/{id}/lock-status` | 前置锁定状态 |
| GET | `/api/v1/courses/{id}/progress` | 课程进度 |
| POST | `/api/v1/courses/{id}/rebuild-cache` | 重建缓存（SSE 流） |
| GET | `/api/v1/courses/{id}/cache-status` | 缓存状态 |

### Learning

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/learning/items` | 学习项列表 |
| POST | `/api/v1/learning/items` | 创建学习项 |
| GET | `/api/v1/learning/items/{id}` | 学习项详情 |
| POST | `/api/v1/learning/imports` | 文件导入（.txt/.xlsx） |
| POST | `/api/v1/learning/translations` | LLM 翻译 |
| POST | `/api/v1/learning/word-mistakes` | 词级错词记录 |
| GET | `/api/v1/learning/word-reviews` | 词级复习任务 |
| POST | `/api/v1/learning/dynamic-sentences` | AI 生成动态复习句 |
| GET | `/api/v1/learning/generated-sentences` | 已生成的复习句 |

### Review / Memory

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/review/queue` | 复习队列 |
| POST | `/api/v1/review/logs` | 提交复习记录 |
| GET | `/api/v1/memory/dashboard` | 记忆看板 |
| GET | `/api/v1/memory/states/{item_id}` | 记忆状态 |
| POST | `/api/v1/memory/schedule` | 记忆调度 |
| POST | `/api/v1/memory/study-time` | 记录学习时间 |
| POST | `/api/v1/memory/fit` | FSRS 参数拟合 |
| GET | `/api/v1/memory/review-forecast` | 复习预测 |
| GET | `/api/v1/memory/retention-curve` | 留存曲线 |

### Reports

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/reports/daily` | 每日报告 |
| GET | `/api/v1/reports/plans/today` | 今日计划 |
| GET | `/api/v1/reports/word-history/{word}` | 单词历史 |
| GET | `/api/v1/reports/error-breakdown` | 错误类型分析 |
| GET | `/api/v1/reports/study-streak` | 学习连续 |
| GET | `/api/v1/reports/export` | 数据导出 |
| POST | `/api/v1/reports/import` | 数据导入 |

### Settings / TTS

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/settings/model` | 查询模型设置 |
| PUT | `/api/v1/settings/model` | 更新模型设置 |
| POST | `/api/v1/tts/speech` | 语音合成 |
| GET | `/api/v1/tts/audio/{cache_key}.mp3` | 获取缓存音频 |
| POST | `/api/v1/tts/prefetch` | 课程音频预取 |
| GET | `/api/v1/tts/phonics-deck` | 自然拼读卡片 |

## 记忆算法说明

### FSRS v4 调度

- 19 个可调参数控制记忆状态转移（初始稳定度、难度、遗忘速度等）
- 评分 3+：增加 repetition count，更新 ease factor，进入长间隔复习
- 评分 < 3：重置 repetition count，增加 lapse count，回到短周期复习
- 句子和短语使用比单词更短的基础复习间隔
- 每次调度更新 `interval_days`、`ease_factor`、`memory_strength`、`forget_risk`、`last_reviewed_at`、`next_review_at`

### 儿童参数校准

- 默认初始稳定度仅为成人参数的 50%，适应儿童遗忘更快的认知特点
- "慢学者"模式进一步缩短所有间隔
- 用户积累 ≥150 条复习记录后，可触发 FSRS 个性化参数拟合，生成专属调度权重

### 词级微复习

- 每个单词独立追踪记忆状态（status, error_type_counts, micro-review stage）
- 9 种复习任务类型，按错误类型自动匹配最需要的练习形式
- 优先级评分驱动出题，确保薄弱点优先得到强化
- 错词日志记录期望答案与实际输入，支持精确的错误类型分类

## AI 调用说明

- 默认本地模型：Ollama `http://localhost:11434`，模型 `phi4-mini`
- 在线模式支持 DeepSeek / Qwen / 任意 OpenAI-compatible `/chat/completions` 端点
- 导入学习项缺少中文释义时，自动调用 LLM 补全翻译
- 学习过程中可调用 LLM 翻译单词，基于错词生成动态复习句
- 每日报告、课程庆祝语均由 LLM 生成
- API Key 可通过环境变量或在登录后的模型设置界面配置，不会提交到 Git
