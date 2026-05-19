# MemoSeed

MemoSeed 是一个“基于艾宾浩斯记忆曲线 + AI 动态调度”的儿童英语长期记忆学习系统。

产品定位不是大而全英语平台，而是英语记忆操作系统。第一阶段仅包含完整项目骨架，不实现复杂业务逻辑。

## 许可证

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0）协议，详见 [LICENSE](LICENSE)。

## 技术栈

- Frontend: Next.js 15, TypeScript, TailwindCSS, shadcn/ui-compatible components
- Backend: FastAPI, Python 3.12, SQLAlchemy, Alembic, JWT
- Database: PostgreSQL 16
- ORM / Schema: SQLAlchemy + Prisma schema
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
│   │   ├── users/
│   │   ├── learning/
│   │   ├── review/
│   │   ├── memory/
│   │   └── reports/
│   ├── app/core/
│   ├── app/db/
│   ├── app/models/
│   ├── app/schemas/
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
- 后续接入大模型时填写 `AI_API_KEY`

## Docker 启动

```bash
docker compose up --build
```

服务地址：

- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Backend Health: http://localhost:8000/health
- OpenAPI: http://localhost:8000/docs
- PostgreSQL: localhost:5432

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

## 数据库迁移

PostgreSQL 容器启动时会自动执行：

```text
database/init/001_schema.sql
```

Alembic 已初始化，可用于后续业务迁移：

```bash
cd backend
alembic revision --autogenerate -m "create initial schema"
alembic upgrade head
```

## Prisma

Prisma schema 位于：

```text
database/prisma/schema.prisma
```

如果前端或工具链需要 Prisma Client，可在后续阶段安装 Prisma CLI 并生成 client：

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

常用命令：

```bash
npm run lint
npm run typecheck
npm run format:check
npm run build
```

## API 模块

当前已生成基础 RESTful API 结构：

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/users/me`
- `GET /api/v1/users/{user_id}`
- `GET /api/v1/learning/items`
- `POST /api/v1/learning/items`
- `GET /api/v1/learning/items/{item_id}`
- `GET /api/v1/review/queue`
- `POST /api/v1/review/logs`
- `GET /api/v1/memory/states/{learning_item_id}`
- `POST /api/v1/memory/schedule`
- `GET /api/v1/reports/daily`
- `GET /api/v1/reports/plans/today`

## 第一阶段范围

已完成：

- 完整目录结构
- Docker Compose
- FastAPI 基础后端
- SQLAlchemy 模型
- Alembic 初始化
- PostgreSQL 初始化 SQL
- Prisma schema
- Next.js 15 前端骨架
- Tailwind/shadcn/ui-compatible 基础配置
- 环境变量模板
- 开发规范文档

未实现：

- 真实用户注册/登录落库
- SM-2 记忆算法
- AI 动态调度
- 学习内容管理业务逻辑
- 大模型 API 调用
