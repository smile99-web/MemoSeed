# MemoSeed Project Rules

## 产品原则

MemoSeed 是儿童英语长期记忆学习系统，定位为英语记忆操作系统，而不是大而全英语学习平台。

核心内容仅围绕：

1. 单词
2. 常用短语
3. 简单句子

核心训练方式：

1. 主动回忆
2. 拼写训练
3. 错题强化
4. 间隔复习
5. AI 动态调整

## 架构原则

1. 前后端分离。
2. 后端提供 RESTful API。
3. 所有配置必须通过环境变量注入。
4. 数据库只使用 PostgreSQL，不允许使用 SQLite。
5. 所有主键使用 UUID。
6. 数据库字段使用 snake_case。
7. 代码模块必须解耦，禁止跨层直接耦合。
8. 第一阶段不实现复杂业务逻辑，只保留可运行骨架。

## 前端规则

1. 使用 Next.js 15 App Router。
2. 使用 TypeScript strict mode。
3. 禁止使用 `any`。
4. 使用 TailwindCSS。
5. UI 组件保持 shadcn/ui-compatible 结构。
6. API 调用集中放在 `frontend/src/lib/`。
7. 页面组件不得直接硬编码后端业务规则。
8. 不允许 mock 数据冒充真实接口数据。

## 后端规则

1. 使用 FastAPI。
2. 使用 Python 3.12。
3. 使用 SQLAlchemy 2.x typed ORM。
4. 使用 Alembic 管理迁移。
5. JWT 相关逻辑放在 `app/core/security.py`。
6. 配置读取集中放在 `app/core/config.py`。
7. 数据库连接集中放在 `app/db/session.py`。
8. API 按业务域拆分到 `app/api/v1/`。
9. Pydantic schema 放在 `app/schemas/`。
10. SQLAlchemy model 放在 `app/models/`。

## 数据库规则

1. 表名使用复数 snake_case。
2. 字段名使用 snake_case。
3. 时间字段使用 `TIMESTAMPTZ`。
4. 所有关联必须定义外键。
5. 高频查询字段必须建立索引。
6. JSON 数据使用 `JSONB`。
7. 学习内容类型限定为 `word`、`phrase`、`sentence`。
8. 评分限定为 0-5。

## 记忆算法边界

后续实现 SM-2 + 动态难度修正时必须满足：

1. `score >= 3` 进入长期间隔。
2. `score < 3` 回到短周期复习。
3. 错题当天必须再次出现。
4. 句子的衰减速度快于单词。
5. 更新字段包括 `interval_days`、`ease_factor`、`memory_strength`、`forget_risk`、`next_review_at`。

## AI 调度边界

后续 AI 每日分析输入包括：

1. 正确率
2. 拼写错误率
3. 句子错误率
4. 学习时长
5. 复习积压

输出必须是结构化策略，不允许只返回自然语言建议。

## 代码质量

1. 不添加未使用依赖。
2. 不添加未完成的抽象层。
3. 不写伪代码。
4. 不保留无意义注释。
5. 不提交 `.env`。
6. 不绕过 lint、typecheck 或 migration 错误。
7. 新功能必须保证 Docker 环境可启动。
