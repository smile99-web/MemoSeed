# MemoSeed

MemoSeed 是一个“基于艾宾浩斯记忆曲线 + SM-2 + AI 动态调度”的儿童英语长期记忆学习系统。

产品定位不是大而全英语平台，而是英语记忆操作系统，专注中小学阶段英语基础薄弱学生的单词、短语与简单句长期记忆。

## 许可证

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0）协议，详见 [LICENSE](LICENSE)。

## 技术栈

- Frontend: Next.js 15, TypeScript, TailwindCSS, shadcn/ui-compatible components
- Backend: FastAPI, Python 3.12, SQLAlchemy, Alembic, JWT
- Database: PostgreSQL 16
- ORM / Schema: SQLAlchemy + Prisma schema
- AI: Ollama 本地模型、DeepSeek / OpenAI-compatible Chat Completions
- TTS: 浏览器语音、Kokoro-compatible API、火山引擎 TTS
- Deployment: Docker Compose

## 项目结构

```text
MemoSeed/
├── frontend/
│   ├── src/app/
│   ├── src/components/ui/
│   └── src/lib/
├── backend/
│   ├── app/api/v1/
│   │   ├── auth/
│   │   ├── courses/
│   │   ├── learning/
│   │   ├── memory/
│   │   ├── reports/
│   │   ├── review/
│   │   ├── settings/
│   │   ├── tts/
│   │   └── users/
│   ├── app/core/
│   ├── app/db/
│   ├── app/models/
│   ├── app/schemas/
│   ├── app/services/
│   └── alembic/
├── database/
│   ├── init/
│   └── prisma/
├── docker/
│   ├── backend/
│   └── frontend/
├── docs/
├── prompts/
├── docker-compose.yml
├── .env.example
└── PROJECT_RULES.md
```

## 已实现功能

- 真实用户注册、登录、刷新令牌、退出登录，用户与 refresh token 均落库。
- 课程包、课程、学习内容的创建、查询、删除与导入。
- 单词、短语、句子拼写学习流程，支持本地学习进度恢复。
- SM-2 + 动态难度修正的记忆调度，更新复习间隔、ease factor、记忆强度、遗忘风险和下次复习时间。
- 复习日志、错词日志、错词单独拼写和 AI 动态复习句生成。
- 学习数据看板：已掌握/未掌握单词、复习准确率、复习时间分布、学习时长、最需复习词和最稳定掌握词。
- 大模型翻译与生成：支持 Ollama 本地模型和 DeepSeek / OpenAI-compatible API。
- 用户模型设置保存，包括 LLM、TTS provider、模型、Base URL、API Key 等配置。
- TTS 朗读：支持浏览器 speechSynthesis、Kokoro-compatible API 和火山引擎 TTS。

## 环境准备

复制环境变量文件：

```bash
cp .env.example .env
```

在 Windows PowerShell 中：

```powershell
Copy-Item .env.example .env
```

首次开发时至少修改：

- `JWT_SECRET_KEY`
- `POSTGRES_PASSWORD`
- 如使用在线大模型，填写 `AI_API_KEY`
- 如使用火山引擎 TTS，填写 `VOLCENGINE_TTS_API_KEY`

真实密钥应只写入本地 `.env` 或用户界面保存的个人配置，不要提交到 Git。仓库只提交 `.env.example` 空模板。

## Docker 启动

开发环境（热更新，挂载本地代码）：

```bash
docker compose up --build
```

服务地址：

- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Backend Health: http://localhost:8000/health
- OpenAPI: http://localhost:8000/docs
- PostgreSQL: localhost:5432

局域网设备访问：

- Frontend: `http://你的电脑局域网IP:3000`
- 前端默认通过同源 `/api/v1` 代理访问后端，手机或其他电脑不需要把 API 地址改成自己的 `localhost`。

生产镜像验证（不挂载本地代码，前端使用 Next standalone）：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
```

生产镜像会复用同一组端口和数据库卷；停止命令同样可以追加 `-f docker-compose.prod.yml`。

停止服务：

```bash
docker compose down
```

清空数据库卷：

```bash
docker compose down -v
```

## 后端本地运行

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
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

## 数据库初始化

PostgreSQL 容器启动时会自动执行：

```text
database/init/001_schema.sql
database/init/002_courses.sql
database/init/003_user_model_settings.sql
database/init/004_study_time_logs.sql
```

Alembic 已初始化，可用于后续业务迁移：

```bash
cd backend
alembic revision --autogenerate -m "create schema change"
alembic upgrade head
```

## Prisma

Prisma schema 位于：

```text
database/prisma/schema.prisma
```

如果前端或工具链需要 Prisma Client，可安装 Prisma CLI 并生成 client：

```bash
cd database
npx prisma generate --schema prisma/schema.prisma
```

## 前端本地运行

```bash
cd frontend
npm install
npm run dev
```

局域网设备访问本地前端时，使用：

```text
http://你的电脑局域网IP:3000
```

常用命令：

```bash
npm run lint
npm run typecheck
npm run format:check
npm run build
```

## API 模块

当前主要 API：

### Auth / Users

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/users/me`
- `GET /api/v1/users/{user_id}`

### Courses

- `GET /api/v1/courses/packages`
- `POST /api/v1/courses/packages`
- `DELETE /api/v1/courses/packages/{package_id}`
- `GET /api/v1/courses/courses`
- `POST /api/v1/courses/courses`
- `DELETE /api/v1/courses/courses/{course_id}`

### Learning

- `GET /api/v1/learning/items`
- `POST /api/v1/learning/items`
- `GET /api/v1/learning/items/{item_id}`
- `POST /api/v1/learning/imports`
- `POST /api/v1/learning/translations`
- `POST /api/v1/learning/word-mistakes`
- `POST /api/v1/learning/dynamic-sentences`

### Review / Memory

- `GET /api/v1/review/queue`
- `POST /api/v1/review/logs`
- `GET /api/v1/memory/dashboard`
- `GET /api/v1/memory/states/{learning_item_id}`
- `POST /api/v1/memory/schedule`
- `POST /api/v1/memory/study-time`

### Settings / TTS / Reports

- `GET /api/v1/settings/model`
- `PUT /api/v1/settings/model`
- `POST /api/v1/tts/speech`
- `GET /api/v1/reports/daily`
- `GET /api/v1/reports/plans/today`

## 记忆算法说明

学习项完成后会调用记忆调度服务：

- `score >= 3`：增加 repetition count，更新 ease factor，进入长间隔复习。
- `score < 3`：重置 repetition count，增加 lapse count，回到短周期复习。
- 句子和短语会比单词使用更短的基础复习间隔。
- 每次调度都会更新 `interval_days`、`ease_factor`、`memory_strength`、`forget_risk`、`last_reviewed_at`、`next_review_at`。

## AI 调用说明

- 默认本地模型配置为 Ollama：`http://localhost:11434`，模型 `phi4-mini`。
- 在线模式支持 DeepSeek / OpenAI-compatible `/chat/completions`。
- 导入学习内容缺少中文释义时，会尝试调用当前 LLM 配置补全。
- 学习过程中可调用 LLM 翻译单词，并可基于错词生成动态复习句。
- API Key 可以通过环境变量或登录后的模型设置传入；仓库不会提交真实密钥。
