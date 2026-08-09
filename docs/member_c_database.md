# 成员 C（数据库层）交付说明 — FastAPI + 同步 SQLAlchemy 2.x

> 📦 **本 PR 实际交付范围（只有 9 个文件，无越界）**：
> - `backend/db/`           → ORM 模型、Repository、最小独立 DatabaseConfig、初始化入口（init_db.py）
> - `backend/schemas/`      → 统一响应结构、错误码、业务异常（零 Flask 依赖）
> - `backend/validation/`   → 文案合规校验：`validate_copy`（标题≤20、正文非空、描述非空、标签 3~5 去重补 `#`）
> - `backend/tests/`        → 数据库相关测试（默认临时 SQLite；真实 MySQL 需要双开关）
> - `schema/migration/`     → 建库建表初始化 SQL（MySQL 幂等）
> - `docs/member_c_database.md`（本文件）
> - 根目录 `.gitignore`（仅追加：`.env / .env.* / !.env.example`）
>
> ⚠️ **依赖说明（按团队清单执行）**：SQLAlchemy、PyMySQL、pydantic、pydantic-settings **只写在本文档（§9 集成指引）**，集成时由 B+C 一起追加到团队共享的 `pyproject.toml`。本 PR **不新增/不覆盖**团队共享的 `pyproject.toml`、`requirements.txt`、或任何启动入口。

---

## 0. 架构总览（给成员 B 快速上手）

```
[B: FastAPI 路由层（成员 B 负责，C 不修改）]
        │
        │ Depends(get_db) → sqlalchemy.orm.Session
        ▼
[C: Repository（纯同步 SQLAlchemy 2.x，C 负责）]
 ├─ create_pending(db, *, generation_id=..., image_path=..., user_input=...)
 │     要求：generation_id 必须由 B 传入；C 绝不生成任何 UUID / gen_* / task_*
 │     内部映射：外部 generation_id ↔ 数据库列 task_id
 │
 ├─ mark_success(db, *, generation_id, title, content, tags, image_description)
 │     内部强制跑 validate_copy()；不合规 → 抛 VALIDATION_ERROR → rollback，不写脏数据
 │
 ├─ mark_failed(db, *, generation_id, error_code, error_message)
 │     只存：error_code（固定枚举）+ 安全固定文案（禁止写第三方异常原文）
 │
 ├─ get_record(db, *, generation_id)
 └─ list_records(db, *, status=None, limit=100)
        │
        ▼
[GenerationRecord(Base, DeclarativeBase)]
   ↳ 列：task_id(=B 的 generation_id) / image_description(image_summary) / content(body) / tags(带 # 前缀)
        │
        ▼
[独立 DatabaseConfig（backend.db.config，Pydantic v2，SecretStr password + repr=False）]
   - 绝不覆盖 B 本地 backend.core.config.Settings（C 完全不碰 backend.core/）
   - 用法 1（推荐）：init_database_global(database_url=B_settings.DATABASE_URL)  显式注入
   - 用法 2：init_database(B_settings)，只要 B_settings 有 DATABASE_URL 属性即可
   - 用法 3：init_database()  自动用独立 DatabaseConfig 单例（C 自测用）
        │
        ▼
mask_database_url(url) → parse 不确定一律固定占位符 <invalid-database-url>，绝不保留原结构或原 URL
```

> **为什么 C 不写 backend.core.config？** 因为 B 的真实 Settings 目前尚未发布到公共仓库，C 无法看到 B 必需字段（如 siliconflow_api_key / vision_model_name / ocr_model_name / cors_origins / resolved_upload_dir / max_image_size_mb / max_image_pixels / model_max_image_edge）。若 C 强行写同名文件，会静默覆盖或破坏 B 的本地配置。正确做法：由 B 在其真实分支把 `DATABASE_URL / MYSQL_HOST / MYSQL_PORT / MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD` 字段追加到 `backend.core.config.Settings`，然后按 **用法 1 / 用法 2** 注入即可，无需改 C 任何一行。

> **关于 B 的 async 路由同步 Session 调用建议**：本 PR 已彻底删除异步 SQLAlchemy 栈（避免 SQLite 同步驱动不兼容、aiomysql 缺依赖、Depends 报错等）。若 B 的路由是 `async def ...`，请把同步 Repository 调用放进线程池：
> ```python
> from starlette.concurrency import run_in_threadpool
> await run_in_threadpool(create_pending, db, generation_id=..., image_path=...)
> # 或者 await asyncio.to_thread(create_pending, db, generation_id=...)
> ```

---

## 1. 数据库表结构（generation_records）

**表名**：`generation_records`（SQLAlchemy 2.x DeclarativeBase model: `backend.db.GenerationRecord`，索引 `idx_task_id / idx_status / idx_created_at` 在 `__table_args__` 中声明）

| 字段                | 类型         | 索引/约束            | 说明                                                                 |
| ------------------- | ------------ | -------------------- | -------------------------------------------------------------------- |
| `id`                | INT          | PK AUTO_INCREMENT    | 内部自增主键（仅供 C 内部 ORM 使用，不对外暴露）                    |
| `task_id`（对外列名 `generation_id`） | VARCHAR(64)  | UNIQUE / idx_task_id | **由 B 传入的 generation_id**，由 B 自己生成与决定格式；C 绝不改动、绝不重新生成。建议 B 用 `str(uuid.uuid4())`（36 字符），或团队约定的其他字符串格式（≤64）。 |
| `status`            | VARCHAR(16)  | idx_status           | `pending` / `success` / `failed`                                     |
| `image_path`        | VARCHAR(512) |                      | 上传图片在本地/对象存储的路径                                       |
| `image_description` | TEXT         |                      | 识图结果 / 图片描述；对外别名 `image_summary`                       |
| `user_input`        | TEXT         |                      | 用户可选输入：风格偏好、关键词                                       |
| `title`             | VARCHAR(100) |                      | 生成标题；`validate_copy` 强制 ≤20 字                                 |
| `content`           | TEXT         |                      | 生成正文；对外别名 `body`；`validate_copy` 强制非空                 |
| `tags`              | JSON         |                      | 标签数组：3~5 个；`validate_copy` / `normalize_tags` 去重并补 `#` 前缀 |
| `error_code`        | VARCHAR(64)  |                      | 失败时：固定错误码枚举（见 `backend.schemas.ErrorCode`）            |
| `error_message`     | TEXT         |                      | 失败时：安全固定文案（禁止写入第三方异常原文 / str(e) / repr(last_error)） |
| `created_at`        | DATETIME     | idx_created_at       | 创建时间 UTC（`server_default=func.now()` + 本地 `default=utcnow`） |
| `updated_at`        | DATETIME     |                      | 最后更新时间 UTC（`onupdate=utcnow`）                                |

- DDL 源码（幂等，MySQL 原生）：`schema/migration/001_init.sql`
- ORM 源码：`backend/db/__init__.py` 中 `class GenerationRecord` 与 `Base.metadata.create_all(...)`
- 对外字典：`GenerationRecord.to_dict()` 只返回 `generation_id`（**不再同时返回 `task_id` 别名**，避免 B 误用两套 ID）

---

## 2. 初始化与迁移

数据库初始化 **幂等**（可重复执行，不破坏已有数据）。两种方式任选其一：

### 方式 A（后端开发者）：Python 入口（自动 MySQL 建库 + create_all）

⚠️ 前提：团队共享 `pyproject.toml` 已追加 SQLAlchemy / PyMySQL / pydantic-settings 依赖（见 §9）。

```bash
python backend/db/init_db.py
```

预期输出示例（**密码已脱敏，永远不会输出明文**）：

```
[OK] 数据库 xhs_db 已就绪
[OK] 表 generation_records 已就绪（DATABASE_URL=mysql+pymysql://root:***@127.0.0.1:3306/xhs_db?charset=utf8mb4）
[DONE] 数据库初始化完成，可重复执行。
```

逻辑：若 `DATABASE_URL.startswith("mysql")`，先用 PyMySQL 跑 `CREATE DATABASE IF NOT EXISTS ... utf8mb4`（数据库名先通过 **严格白名单** 校验，不合格直接抛 DATABASE_ERROR，禁止拼入 SQL），再 `Base.metadata.create_all(engine)` 建表；SQLite 直接 `create_all`。

### 方式 B（DBA / 纯 SQL 执行）：

```bash
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p < schema/migration/001_init.sql
```

---

## 2.6 安全 & 保守策略总览（C 侧强制启用，不可关闭）

| 策略 | 生效范围 | 失败时行为 |
|------|----------|------------|
| **URL 脱敏：`mask_database_url(url)`** | 所有日志 / print / 异常消息 / 测试脚本输出 | parse 不确定（如端口非数字、密码段位置歧义、SA parse 异常、渲染后仍等于原 URL 且非 SQLite）一律返回固定安全占位符 `<invalid-database-url>`，**绝不保留原连接串结构，绝不回原 URL 或原密码片段**。SQLite 路径（无密码段）可正常显示，便于调试。 |
| **MySQL 自测双开关**：`MYSQL_TEST_URL + MYSQL_TEST_CONFIRM=YES_I_KNOW_IT_IS_A_TEST_DB` | `backend/tests/run_selfcheck.py` 的真实 MySQL 模式 | 在**任何 connect / CREATE DATABASE / CREATE TABLE / 写入测试记录 / DROP DATABASE 之前**先跑：① 双开关存在且值正确；② URL parse 合法；③ 库名严格测试白名单。任一失败 → 直接 `SystemExit`，零副作用。 |
| **严格测试库名白名单** | 上面的 MySQL 双开关逻辑 + `_create_mysql_database_if_missing()` 初始化入口 | 标识符强制 `^[A-Za-z][A-Za-z0-9_]{0,63}$`；任何包含子串 `prod / production / online / master / live / release / staging / uat / pre / preprod` 一律拒绝；仅允许：精确 `xhs_test`、前缀 `xhs_test_ / test_`、后缀 `*_test`（且后缀 head 段不得再含 `test`，避免 `production_test / test_prod`）。 |
| **SecretStr + repr=False** | `DatabaseConfig.MYSQL_PASSWORD`、`DatabaseConfig.DATABASE_URL` | `repr(cfg) / str(cfg) / print(cfg)` 绝对不会输出明文 URL 或明文密码；只有内部 `get_secret_value()` 能取。 |
| **Rollback 优先 + 异常再包装** | 所有 Repository 函数（create / mark_success / mark_failed / get_record / list_records）+ `new_session()` 上下文 + `get_db()` Depends | 任何非 `BusinessException` 的 DB 异常先 `session.rollback()`，再统一转成 `DATABASE_ERROR`，**只保留 `type(e).__name__`**，不泄漏原始 SQL、原始 URL、驱动堆栈、密码。 |
| **错误落库仅存安全固定文案** | `mark_failed(... error_message=...)` 的调用契约 + 自测断言 | 禁止传入 `str(exception)` 或 `repr(last_error)` 或任何含密钥/SQL 的原文。只接受固定错误码 + 人工可读安全固定文案（如「图片处理失败」「限流，请稍后重试」等）。 |
| **DROP DATABASE 默认关闭** | `run_selfcheck.py` 的 MySQL 清理逻辑 | 只有同时满足：双开关都通过 + `MYSQL_TEST_CLEANUP=1` + 库名再次通过白名单 + 字符集再校验，才会执行 `DROP DATABASE IF EXISTS ...`。默认不清理。 |

---

## 3. 配置变量（C 侧真实使用，不覆盖 B）

**代码中不存在任何明文密码 / API Key / sk-* 真实密钥**。所有配置来自环境变量（或本地 `.env`，根目录 `.gitignore` 已阻止 `.env` 入库）。

### 3.1 数据库必填字段（建议 B 追加到真实 Settings）

| 变量名            | 说明                                                                 | 默认 / 示例                                                           |
| ----------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `DATABASE_URL`    | 首选；完整 SQLAlchemy 连接串                                        | `mysql+pymysql://u:p@127.0.0.1:3306/xhs_db?charset=utf8mb4` / `sqlite:///./xhs.db` |
| `MYSQL_HOST`      | **当 `DATABASE_URL` 未设置时**，与下列字段自动拼接 URL              | `127.0.0.1`                                                           |
| `MYSQL_PORT`      | 同上                                                                 | `3306`                                                                |
| `MYSQL_DATABASE`  | 同上                                                                 | `xhs_db`                                                              |
| `MYSQL_USER`      | 同上                                                                 | `root`                                                                |
| `MYSQL_PASSWORD`  | 同上（真实值仅写本地 `.env`；所有输出一律 mask 成 `***`）            | 空字符串                                                              |

### 3.2 数据库测试额外变量（仅测试/CI 用，不算 Settings 必填）

| 变量名                | 说明                                                                                                                                      |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `MYSQL_TEST_URL`      | 设置：run_selfcheck.py 尝试真实 MySQL；但必须同时满足下面第二开关才会真的连接。默认**不设置**，走临时 SQLite。                            |
| `MYSQL_TEST_CONFIRM`  | **必须设置为精确字符串** `YES_I_KNOW_IT_IS_A_TEST_DB`，作为第二重确认。缺失或不一致：测试脚本在任何连接之前直接 SystemExit。               |
| `MYSQL_TEST_CLEANUP`  | 仅当上面两个变量都已通过，且值等于 `1`：测试结束自动 `DROP DATABASE`。默认 `0`（不删，方便人工 SELECT 验收真实写入）。                    |

独立 `DatabaseConfig` 源码：`backend/db/config.py`（Pydantic v2 BaseSettings，SecretStr password + repr=False 防泄漏）

保守脱敏工具函数：`backend/db/config.py → mask_database_url(url)`（parse 不确定直接占位符，绝不回原 URL）

---

## 4. Repository 调用示例

### 4.0 B 的 FastAPI 启动时一次性初始化（几行代码）

```python
# backend/main.py（成员 B 的 FastAPI 入口，C 不修改此文件）
from fastapi import FastAPI
from backend.db import init_database_global

# 推荐：显式注入，零依赖 C 的 config 类
init_database_global(database_url=backend.core.config.settings.DATABASE_URL)

app = FastAPI(title="XHS Content Generator", version="0.3.0")
# 之后所有路由：def xxx(db: Session = Depends(get_db)) 即可
```

### 4.1 create_pending（必须传入 B 生成的 generation_id）

```python
from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from backend.db import get_db, create_pending
import uuid

router = APIRouter(prefix="/api/v1/generations", tags=["generations"])

@router.post("")
def create_task(
    image: UploadFile = File(...),
    user_input: str = Form(""),
    db: Session = Depends(get_db),
):
    # B 层自己做：image 落盘到 UPLOAD_DIR，拿到 image_path
    image_path = f"/uploads/{image.filename}"

    # ⚠️ generation_id 必须由 B 一次性生成，传给 C；C 不生成、不改、不重生成。
    generation_id = str(uuid.uuid4())

    rec = create_pending(
        db,
        generation_id=generation_id,
        image_path=image_path,
        user_input=user_input,
        # 可选：如果此时已有识图结果可传 image_summary=...
    )
    assert rec.to_dict()["generation_id"] == generation_id, "C 不得改 generation_id"
    return {"generation_id": generation_id, "status": "pending"}
```

### 4.2 validate_copy（validate_generation_result）

```python
from backend.validation import validate_copy, validate_generation_result
from backend.schemas import BusinessException, ErrorCode

# 写法一：逐项传
try:
    desc, title, content, tags = validate_copy(
        image_description=llm_output["image_description"],
        title=llm_output["title"],
        content=llm_output["content"],
        tags=llm_output["tags"],
    )
except BusinessException as e:
    assert e.code == ErrorCode.VALIDATION_ERROR
    errors_dict = e.data  # 如 {"title": "标题超过 20 字限制"}
    # 交给 mark_failed 写库，不要直接抛给前端

# 写法二：整包传（对 LLM 返回 dict 做一次规范化；同时支持 image_summary / body 别名）
norm = validate_generation_result(llm_output)
# norm = {"image_description", "image_summary", "title", "content", "body", "tags"}
```

### 4.3 mark_success（必须复用同一个 generation_id）

```python
from backend.db import mark_success, mark_failed
from backend.validation import validate_generation_result
from backend.schemas import BusinessException, ErrorCode

def handle_llm_done(db: Session, generation_id: str, llm_output: dict):
    """B 层异步完成时调用：先校验、再写 success。generation_id 必须与 pending 相同。"""
    try:
        norm = validate_generation_result(llm_output)
        # 即便 B 层做过校验，mark_success 内部也会强制再校验一次（双重保险）
        rec = mark_success(
            db,
            generation_id=generation_id,
            title=norm["title"],
            content=norm["content"],
            tags=norm["tags"],
            image_description=norm["image_description"],
        )
        return rec.to_dict()  # generation_id 与输入完全相等
    except BusinessException as e:
        # 校验失败：按失败入库，保证所有任务一定有终态；复用同一个 generation_id
        mark_failed(
            db,
            generation_id=generation_id,
            error_code=e.code,
            error_message=e.message,  # 仅安全固定文案
        )
        raise
```

### 4.4 mark_failed（同一 generation_id；禁止写入异常原文）

```python
from backend.db import mark_failed
from backend.schemas import ErrorCode

# 例 1：伪扩展名 / 坏图
mark_failed(
    db,
    generation_id=generation_id,   # 与 pending 完全相同
    error_code=ErrorCode.IMAGE_PROCESS_ERROR,
    error_message="图片损坏或格式不支持",   # ✅ 安全固定文案
)

# 例 2：模型限流 / 超时
mark_failed(
    db,
    generation_id=generation_id,
    error_code=ErrorCode.LLM_GENERATE_ERROR,
    error_message="模型限流，请稍后重试",   # ✅ 安全固定文案（禁止写 repr(last_error) 或 str(exception)）
)

# 例 3：参数错（文件过大）
mark_failed(
    db,
    generation_id=generation_id,
    error_code=ErrorCode.PARAMS_ERROR,
    error_message="文件超过 10MB",
)
```

### 4.5 B 侧查询接口（对外统一 generation_id）

```python
from backend.db import get_record, list_records

@router.get("/{generation_id}")
def get_status(generation_id: str, db: Session = Depends(get_db)):
    rec = get_record(db, generation_id=generation_id)
    if rec is None:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND)
    return rec.to_dict()

@router.get("")
def list_all(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    return [r.to_dict() for r in list_records(db, status=status, limit=limit)]
```

---

## 5. 状态保存总览

| 场景                                    | 写入函数                   | 最终 status | 关键字段保存                                                                 |
| --------------------------------------- | -------------------------- | ----------- | ---------------------------------------------------------------------------- |
| 用户刚提交请求                          | `create_pending(db, ...)`  | pending     | `generation_id`(=task_id 唯一) / image_path / user_input / created_at / updated_at |
| LLM 通过 `validate_copy` 校验           | `mark_success(db, ...)`    | success     | title(≤20) / content(非空) / tags(JSON 3~5 且均带 `#` 前缀) / image_description(非空) / updated_at；error_code+error_message 清为 NULL |
| 任何失败分支（坏图/模型异常/校验不通过）| `mark_failed(db, ...)`     | failed      | error_code + 安全固定 error_message / updated_at（可选覆盖 image_description）|

### B 联调验收 SQL（真实库执行，复制即查）

```sql
-- 5.1 三态数量
SELECT status, COUNT(*) AS cnt FROM generation_records GROUP BY status;

-- 5.2 所有 success 任务二次合规性检查（C 层必须 100% 通过）
SELECT
  task_id                          AS generation_id,
  LENGTH(title)                    AS title_chars,   -- ≤20
  CHAR_LENGTH(content) > 0         AS has_content,   -- =1
  image_description IS NOT NULL
    AND CHAR_LENGTH(image_description) > 0          AS has_desc,      -- =1
  JSON_LENGTH(tags)                AS tag_cnt,       -- 3~5
  (SELECT COUNT(1) FROM JSON_TABLE(tags, '$[*]' COLUMNS (t VARCHAR(64) PATH '$')) jt WHERE t NOT LIKE '#%')
                                   AS tag_missing_hash_cnt  -- =0
FROM generation_records WHERE status = 'success';

-- 5.3 失败任务按错误码统计
SELECT error_code, COUNT(*) AS cnt
FROM generation_records WHERE status = 'failed' GROUP BY error_code;

-- 5.4 按 generation_id 追踪（B 的对外 ID = 数据库 task_id）
SELECT
  task_id AS generation_id, status, image_path, user_input, image_description,
  title, content, tags, error_code, error_message, created_at, updated_at
FROM generation_records WHERE task_id = 'gen-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx';
```

---

## 6. generation_id 字段规范（本 PR 最终版本）

| 项 | 值 |
|----|----|
| DB 实际列名 | `task_id`（原因：保留 SQL 常用命名；不影响 B） |
| Repository 对外入参名 / to_dict() 出参名 | **统一 `generation_id`**（见 `create_pending / mark_success / mark_failed / get_record` 的签名） |
| to_dict() 是否同时返回 `task_id` 别名 | **否**；避免 ID 混用 |
| 谁生成 | **仅 B，一次性生成并传入 C** |
| C 是否会重生成、重写、或追加前缀 gen_/task_ | **否**；任何形式都不允许 |
| MySQL 类型 / 约束 | `VARCHAR(64) NOT NULL UNIQUE`；二级索引 `idx_task_id` |
| 推荐 B 的格式 | `str(uuid.uuid4())`（标准 36 字符，5 段 4 个 `-`）；`VARCHAR(64)` 为未来扩展预留 |
| 唯一性保障 | B 侧生成 + DB 层 UNIQUE 约束双重兜底 |

---

## 7. 运行数据库测试

C 层提供两套测试，都不依赖 Flask / SiliconFlow / B 的 FastAPI 文件：

### 7.1 `run_selfcheck.py`（一键 5 步验收：默认 SQLite；切 MySQL 只需环境变量）

```bash
# 默认（临时 SQLite，不需 MySQL daemon）：5 步全部通过即成功
python backend/tests/run_selfcheck.py
# 预期最后一行：[ALL PASSED] 数据库核心能力 + 校验规则 + 字段映射全部通过。
```

**5 步对应团队清单的验收点：**
1. create_pending 落库：要求必须传 generation_id；C 内部不改 ID；status=pending
2. validate_copy 4 种子情况（标题超 20 / 空描述 / 空正文 / 标签去重后<3）全部抛 `VALIDATION_ERROR`；mark_success 内部再次校验，不合规不写脏数据
3. mark_success：image_summary/body 别名 → DB image_description/content；同一 generation_id
4. mark_failed：同一 generation_id；只保存 error_code + 安全固定文案，禁止异常原文
5. 关闭所有会话 + dispose engine + 重新 init_database → 两条记录仍在（重启持久化，按 generation_id 查询）

### 7.2 真实 MySQL 验收（双开关；默认不清理）

> ⚠️ **重要：真实 MySQL **尚未在成员 C 的环境中验证完成**（C 侧没有本地 MySQL daemon）。请在 B 的真实 MySQL 环境中按以下步骤执行，然后把脱敏输出贴到 PR 评论。

运行命令（URL 中的用户名和密码用 B 的真实值；密码绝不会在输出中出现）：

```bash
MYSQL_TEST_URL="mysql+pymysql://youruser:yoursecretpass@127.0.0.1:3306/xhs_test?charset=utf8mb4" \
MYSQL_TEST_CONFIRM="YES_I_KNOW_IT_IS_A_TEST_DB" \
  python backend/tests/run_selfcheck.py
```

> 如果要自动清理测试库（默认不清理，默认安全），额外加 `MYSQL_TEST_CLEANUP=1`。

### 7.3 Unittest（推荐 CI 常驻）

```bash
python -m unittest backend.tests.test_database -v
```

覆盖点：
- pending / success / failed 三个阶段都复用**同一个** generation_id
- 标题 >20 / 正文空 / 描述空 / 标签少于 3 或多于 5 → 拒绝成功写入
- 查询异常（mock execute 抛 RuntimeError）→ 必须 rollback；异常再包装只含 `RuntimeError` 类型名，不含 str(e) 内容
- commit 异常（mock session.commit 抛自定义 `_Boom`）→ 必须 rollback；最终记录仍是 pending；异常消息不含 TopSecret/raw_sql 等敏感片段
- 畸形 URL `mysql+pymysql://user:TOP/SECRET@db.local:notaport/xhs` → mask 直接 `<invalid-database-url>`；0 残留 TOP/SECRET/notaport/db.local/xhs
- 非测试库在 connect 之前被拒绝：`_is_safe_test_db` 拒绝 production/test_prod/production_test/xhs_db_prod；`_mysql_dual_switch_or_exit` 在缺少第二确认开关时对任何 URL 都 `SystemExit`（含合法的 xhs_test）

---

## 8. 脱敏验收证据（当前 C 仅有 SQLite；MySQL 待 B 补）

### 证据 A：SQLite 默认 run_selfcheck.py 真实输出（本机执行，非伪造）

```text
[MODE] 临时 SQLite（DATABASE_URL=sqlite:////<临时目录>/xhs_dbtest_<随机>/t.db）
[1/5] create_pending（必须使用 B 传入的 generation_id，不自行生成）...
    OK generation_id = gen-demo0001-1234-5678-9abc-def012345678（B 传入字符串，未被修改或重新生成）
[2/5] validate_copy/validate_generation_result + mark_success 内部校验不写脏数据
    OK 超长标题拦截，code= VALIDATION_ERROR
    OK 空描述拦截
    OK 空正文拦截
    OK 标签不足（去重后<3）拦截
    OK validate_generation_result: image_summary 空拦截
    OK mark_success 内部再次校验，不合规不写库
[3/5] mark_success（image_summary/body 字段别名 → DB image_description/content；同一 generation_id）
    OK tags = ['#夏日', '#穿搭', '#OOTD', '#每日']
[4/5] mark_failed 带错误码落库（同一 generation_id）
    OK error_code + 固定错误文案已保存；generation_id 与 pending 完全相同
[5/5] 服务重启后记录仍存在（按 generation_id 查询）
    OK

[ALL PASSED] 数据库核心能力 + 校验规则 + 字段映射全部通过。
```

### 证据 B：真实 MySQL 验收粘贴位（待 B 本机执行 §7.2 后粘贴）

请把 §7.2 命令的真实输出复制到下方/PR 评论。**输出自动已脱敏：URL 里的密码会是 `***`；若 URL 畸形则是 `<invalid-database-url>`。**

```text
<B 的真实 MySQL 运行日志粘贴处>
示例格式：
[MODE] 真实 MySQL 验收（双开关已打开）
       MYSQL_TEST_URL=mysql+pymysql://youruser:***@127.0.0.1:3306/xhs_test?charset=utf8mb4
[1/5] create_pending...
  OK generation_id = gen-demo0001-1234-5678-9abc-def012345678（B 传入字符串，未被修改或重新生成）
...
[ALL PASSED] 真实 MySQL 验证通过：成功 + 失败 + 重启持久化。
```

---

## 9. B+C 集成指引（C 不再覆盖任何 B 文件）

1. **初始化一次**：B 的 FastAPI `main.py` 里 `init_database_global(database_url=B_settings.DATABASE_URL)`。不要再拼第二份独立配置。
2. **POST /api/v1/generations**：B 层 `str(uuid.uuid4())` 生成 generation_id → image 落盘拿 image_path → `create_pending(db, generation_id=..., image_path=..., user_input=...)` → 返回 `{generation_id, status:"pending"}`。
3. **LLM 异步完成链路**：拿到 LLM 返回后先 `validate_generation_result(llm_output)`，通过后再 `mark_success(db, generation_id=..., **norm)`。禁止跳过校验（C 层 mark_success 内部会再校验一次——双重保险）。
4. **所有失败分支统一 mark_failed**：失败 error_code 用 `backend.schemas.ErrorCode`；`error_message` 写安全固定文案；**禁止写入 `str(exception) / repr(last_error) / 任何第三方异常原文 / 原始 SQL / URL 明文`**。
5. **GET 查询**：`get_record(db, generation_id=...)` 或 `list_records(...)` → `.to_dict()` 直接返回（字段已对齐 B 约定，含 image_summary/body 与 generation_id）。
6. **响应结构统一**：用 `backend.schemas.success_response / error_response` 包装；抛 `BusinessException`，交给 B 的 FastAPI 全局 `@app.exception_handler(BusinessException)` 序列化成 JSON。
7. **禁止打印完整 DATABASE_URL**：所有需要输出连接串的日志/print 一律 `from backend.db.config import mask_database_url` 后再输出。
8. **依赖追加（团队一次性动作）**：在团队共享的 `pyproject.toml` dependencies 中追加：
   - `SQLAlchemy>=2.0.25`
   - `PyMySQL>=1.1`
   - `pydantic>=2.6`
   - `pydantic-settings>=2.2`

---

## 10. PR 实际文件清单 & 规模（按当前 PR 自动统计）

> 实际统计口径：`git diff --stat <target branch> HEAD`，以 B 在 GitHub 上实际合并到的目标分支为 baseline 重新跑一次最准。当前 C 侧估算：

- 允许范围内的文件（仅 9）：
  1. `backend/db/__init__.py`
  2. `backend/db/config.py`
  3. `backend/db/init_db.py`
  4. `backend/schemas/__init__.py`
  5. `backend/validation/__init__.py`
  6. `backend/tests/__init__.py`
  7. `backend/tests/run_selfcheck.py`
  8. `backend/tests/test_database.py`
  9. `schema/migration/001_init.sql`
  10. `docs/member_c_database.md`
  11. `.gitignore`（仅追加 `.env` 相关忽略）

**严禁出现的越界文件（已在本轮全部删除）**：`app/`、`run.py`、`scripts/init_db.py`、`tests/test_e2e.py`、`test_images/`、`migrations/001_init.sql`、C 自建的 `pyproject.toml`、C 自建的 `requirements.txt`、C 自建的 `backend/__init__.py`、任何 Flask/Flask-Cors/Flask-SQLAlchemy/Werkzeug/DashScope 代码。

🚫 **本 PR 暂不合并**，等待成员 B 完成：(a) 真实 MySQL 双开关验收并把脱敏日志贴到 PR 评论；(b) B 真实 `backend.core.config.Settings` 已追加 DB 字段并按 §4.0 注入；(c) 以上 9 条集成指引跑通后再决定是否合并。
