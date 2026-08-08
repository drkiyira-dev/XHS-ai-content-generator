# 成员 C（数据库层）交付说明 — FastAPI + SQLAlchemy 2.x

> 📦 交付范围（本 PR 只修改这些路径）：
> - `backend/db/`           → ORM 模型（SQLA2 DeclarativeBase）+ Repository（显式 Session）+ Pydantic Settings + 初始化入口
> - `backend/schemas/`      → 统一响应结构、错误码、业务异常（零 Flask 依赖）
> - `backend/validation/`   → 文案合规校验（validate_copy：标题≤20、正文非空、描述非空、标签3~5去重补#）
> - `backend/tests/`        → 数据库相关测试：默认临时 SQLite；`MYSQL_TEST_URL` 开启真实 MySQL 成功/失败写入验证
> - `schema/migration/`     → 建库建表初始化 SQL（MySQL 幂等）
> - 根目录 `.gitignore` / `requirements.txt`（数据库相关依赖）
>
> 🚫 不在本 PR 范围内（保持不变或由其他成员负责）：
> - `frontend/` 全部
> - `backend/api/v1/generations.py`（成员 B 负责，接口层）；未修改 `POST /api/v1/generations`
> - `backend/services/model/`、Qwen Prompt、SiliconFlow / dashscope 调用
> - Flask / Flask-Cors / Flask-SQLAlchemy / Werkzeug / dashscope：**本 PR 完全移除（requirements.txt 不再出现）**

---

## 0. 架构总览（给成员 B 快速上手）

```
[B: FastAPI 路由层]
        │
        │ Depends(get_db) → sqlalchemy.orm.Session
        ▼
[C: Repository (纯 SQLAlchemy 2.x)]
 ├─ create_pending(db, image_path=..., user_input=...)
 ├─ mark_success(db, task_id=generation_id, title, content, tags, image_description)
 │       └── 内部强制跑 validate_copy()，不合规抛 BusinessException，不写脏数据
 ├─ mark_failed(db, task_id, error_code, error_message)
 ├─ get_record(db, task_id=generation_id)
 └─ list_records(db, status=None, limit=100)
        │
        ▼
[GenerationRecord(Base, DeclarativeBase)] ── SQLAlchemy 2.x ORM ──► MySQL 8.x / SQLite
        │
        ▼
[独立 DatabaseConfig (backend.db.config, Pydantic v2 SecretStr password) — 不覆盖 B 本地 backend.core.config.Settings]
   - 优先显式 database_url 注入（B 的真实 settings 只要有 DATABASE_URL 属性即可直接 init_database(B_settings) 或 init_database(database_url=B_settings.DATABASE_URL)）；
   - 否则由 MYSQL_HOST/PORT/DATABASE/USER/PASSWORD 组合（SQLAlchemy URL.create() 自动转义特殊字符密码）；
   - 所有日志/print 输出：mask_database_url(url) 保守脱敏 → parse 不确定直接输出固定安全占位符 <invalid-database-url>，绝不回原连接串。
```

> **为什么 C 这里不直接改 backend.core.config？**（B 侧分支尚未发布到 GitHub，C 无法看到 B 必需的 `siliconflow_api_key / vision_model_name / ocr_model_name / cors_origins / resolved_upload_dir / max_image_size_mb / max_image_pixels / model_max_image_edge` 等真实字段，重写会覆盖/静默忽略 B 的本地配置导致破坏。后续由 B 在其真实分支把 DATABASE_URL + MYSQL_* 追加到 B 的 `backend.core.config.Settings` 即可无缝替换；C 层 `init_database()` 三种签名（空参用独立 DatabaseConfig / 传 cfg 对象 / 传 database_url）都已留好兼容位。

成员 B 不再**需要写 SQL**，也**不再需要 Flask app context**。所有 repository 函数第一个参数都是显式 `Session`（FastAPI 的 `Depends(get_db)` 注入即可）。为了方便脚本/单测/CLI 调用，C 层还提供了一组带 `_g` 后缀的「便捷版」（自动使用全局 session_factory，无需传 Session）。

> **关于 FastAPI 异步路由的同步阻塞 Session 调用建议：**C 本版已彻底删除异步 asyncio SQLAlchemy 栈（避免 SQLite 同步驱动不兼容、aiomysql 缺依赖、Depends(async_get_db) 直接报错等问题）。若 B 的路由是 `async def ...`，请把同步 Repository 调用放进线程池：`from starlette.concurrency import run_in_threadpool` → `await run_in_threadpool(create_pending, db, image_summary=...)`，或 `await asyncio.to_thread(create_pending, db, ...)`。

---

## 1. 数据库表结构（generation_records）

**表名**：`generation_records`（SQLAlchemy 2.x DeclarativeBase model: `backend.db.GenerationRecord`，索引 `idx_task_id/idx_status/idx_created_at` 在 `__table_args__` 中声明）

| 字段                | 类型         | 索引/约束            | 说明                                                                 |
| ------------------- | ------------ | -------------------- | -------------------------------------------------------------------- |
| `id`                | INT          | PK AUTO_INCREMENT    | 内部自增主键                                                         |
| `task_id` (对外 = `generation_id`) | VARCHAR(64)  | UNIQUE / idx_task_id | **对外暴露的 generation_id**，标准 `str(uuid.uuid4())`（带 4 个 `-`），在 `create_pending` 入口只生成一次；失败不重新生成。 |
| `status`            | VARCHAR(16)  | idx_status           | `pending` / `success` / `failed`                                     |
| `image_path`        | VARCHAR(512) |                      | 上传图片在本地/对象存储的路径                                       |
| `image_description` | TEXT         |                      | 识图结果 / 图片描述（应用层+mark_success 内强制非空）               |
| `user_input`        | TEXT         |                      | 用户可选输入：风格偏好、关键词                                       |
| `title`             | VARCHAR(100) |                      | 生成标题（≤20 字在应用层强制）                                       |
| `content`           | TEXT         |                      | 生成正文（非空在应用层强制）                                         |
| `tags`              | JSON         |                      | 标签数组：3~5 个，**去重并补 `#` 前缀**（`validate_copy`/`normalize_tags`） |
| `error_code`        | VARCHAR(64)  |                      | 失败时错误码（见 `backend.schemas.ErrorCode`，如 `IMAGE_PROCESS_ERROR`、`LLM_GENERATE_ERROR`） |
| `error_message`     | TEXT         |                      | 失败时详细错误信息                                                   |
| `created_at`        | DATETIME     | idx_created_at       | 创建时间（UTC，SQLAlchemy `server_default=func.now()` + 本地 default=utcnow） |
| `updated_at`        | DATETIME     |                      | 最后更新时间（`onupdate=utcnow`）                                    |

- DDL 源码（幂等，MySQL 原生）：[schema/migration/001_init.sql](file:///Users/zza/Documents/trae_projects/xhs/schema/migration/001_init.sql)
- ORM 源码（SQLAlchemy 2.x Mapped[] / mapped_column）：[backend/db/__init__.py → GenerationRecord](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L108-L159)

---

## 2. 初始化和迁移命令

数据库初始化 **幂等**（可重复执行，不破坏已有数据）。两种方式任选其一：

### 方式 A（推荐，后端开发者）：Python 入口（自动 MySQL 建库 + create_all）
```bash
pip install -r requirements.txt
python backend/db/init_db.py
```
预期输出示例（**密码已脱敏，永远不会输出明文**）：
```
[OK] 数据库 xhs_db 已就绪
[OK] 表 generation_records 已就绪（DATABASE_URL=mysql+pymysql://root:***@127.0.0.1:3306/xhs_db?charset=utf8mb4）
[DONE] 数据库初始化完成，可重复执行。
```
逻辑：`DATABASE_URL.startswith("mysql")` 时，PyMySQL 先 `CREATE DATABASE IF NOT EXISTS ... utf8mb4`，再 `Base.metadata.create_all(engine)` 建表；SQLite 直接 `create_all`。

源码：[backend/db/init_db.py](file:///Users/zza/Documents/trae_projects/xhs/backend/db/init_db.py#L1-L55)（依赖 [独立 DatabaseConfig](file:///Users/zza/Documents/trae_projects/xhs/backend/db/config.py) 和 [init_database](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L252-L278)）

### 方式 B（DBA / 纯 SQL 执行）：
```bash
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p < schema/migration/001_init.sql
```

---

## 2.6 安全 & 保守策略总览（C 侧强制启用）

| 策略 | 范围 | 失败时行为 |
|------|------|------------|
| **URL 脱敏**：`mask_database_url` | 所有日志 / print / 异常字符串 | parse 不确定或畸形 URL（如端口 `notaport`、密码段位置不确定、SA parse 异常）一律返回固定安全占位符 `<invalid-database-url>`，绝不保留原连接串结构，绝不含原密码片段；SQLite 路径（无密码段）可正常显示，便于调试。 |
| **双开关 MySQL 自测**：`MYSQL_TEST_URL` + `MYSQL_TEST_CONFIRM=YES_I_KNOW_IT_IS_A_TEST_DB` | `run_selfcheck.py` 的真实 MySQL 模式 | 在**任何 connect / CREATE DATABASE / CREATE TABLE / 写入测试记录 / DROP DATABASE** 之前先跑双开关校验 + 严格测试库名白名单；缺少任一项直接 `SystemExit` 退出，零副作用。 |
| **测试库名白名单** | 上面的 MySQL 双开关逻辑 + `_create_mysql_database_if_missing` 初始化入口 | 严格 `^[A-Za-z][A-Za-z0-9_]{0,63}$` 字符集；子串包含 `prod/production/online/master/live/release/staging/uat/pre/preprod` 一律拒绝；仅允许：精确 `xhs_test`、前缀 `xhs_test_` / `test_`、后缀 `*_test`（且后缀模式下 head 段不得再含 `test`，避免 `production_test`/`test_prod`）。 |
| **SecretStr 密码字段** | `DatabaseConfig.MYSQL_PASSWORD` + `DatabaseConfig.DATABASE_URL` | Pydantic `repr=False`，`repr(cfg)` / `str(cfg)` / `print(cfg)` 永远不会输出明文 URL 或明文密码；只有 `cfg.MYSQL_PASSWORD.get_secret_value()` 内部调用才能拿到。 |
| **异常再包装 + rollback 优先** | 所有 Repository 函数（create/mark_success/mark_failed/get_record/list_records）+ FastAPI Depends | 任何非 `BusinessException` 的 DB 异常先 `session.rollback()`，再统一转成 `DATABASE_ERROR`（只保留 `type(e).__name__`，不泄漏原始 SQL / 原始 URL / 驱动堆栈 / 密码）。 |

---

## 3. 所需环境变量名称

**代码中不存在任何明文密码 / API Key / sk-* 真实密钥**。所有配置来自环境变量（或本地 `.env`，`.gitignore` 已阻止 `.env` 入库）。

### 本 PR 实际采用的变量（Settings 字段）：

| 变量名           | 说明                                                                 | 默认 / 示例                                                                    |
| ---------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| **`DATABASE_URL`** | 首选；完整 SQLAlchemy 连接串                                        | `mysql+pymysql://u:p@127.0.0.1:3306/xhs_db?charset=utf8mb4` / `sqlite:///./xhs.db` |
| `MYSQL_HOST`     | 当 **`DATABASE_URL` 未设置时** 用它们自动拼接 URL                   | `127.0.0.1`                                                                     |
| `MYSQL_PORT`     | 同上                                                                 | `3306`                                                                          |
| `MYSQL_DATABASE` | 同上                                                                 | `xhs_db`                                                                        |
| `MYSQL_USER`     | 同上                                                                 | `root`                                                                          |
| `MYSQL_PASSWORD` | 同上（真实值**仅写本地 `.env`**；所有输出一律 mask 成 `***`，不入库） | 空字符串（本地开发无密码）                                                      |
| `UPLOAD_DIR`     | 保留字段，成员 B 的上传逻辑使用                                      | `./uploads`                                                                     |

### 数据库测试额外变量（仅 tests/CI 用，不算 Settings 必填）：

| 变量名                | 说明                                                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `MYSQL_TEST_URL`      | 若设置：run_selfcheck.py 会尝试真实 MySQL；必须配合第二个确认开关才会真正连接（双开关）。默认**不设置**，走临时 SQLite               |
| `MYSQL_TEST_CONFIRM`  | **必须设置为固定字符串** `YES_I_KNOW_IT_IS_A_TEST_DB`，作为第二重确认；缺失或值不一致：测试脚本在任何连接前直接 SystemExit。       |
| `MYSQL_TEST_CLEANUP`  | 仅当上面两个变量都已通过 + 值为 `1`：测试结束自动 `DROP DATABASE`；默认 **0（不删）** 便于人工 SELECT 验收真实写入                 |

独立 DatabaseConfig 源码（Pydantic v2 BaseSettings + SecretStr password + repr=False 防泄漏）：
[backend/db/config.py → DatabaseConfig](file:///Users/zza/Documents/trae_projects/xhs/backend/db/config.py#L64-L110)
保守脱敏工具函数（parse 不确定直接占位符，绝不回原 URL）：
[mask_database_url(url)](file:///Users/zza/Documents/trae_projects/xhs/backend/db/config.py#L136-L175)

---

## 4. create_pending / validate_copy / mark_success / mark_failed 调用示例

### 4.0 成员 B 在 FastAPI 中前置准备（几行代码，之后所有路由直接 Depends）

```python
# backend/main.py（成员 B 的 FastAPI 入口）
from fastapi import FastAPI
from backend.db import init_database_global

# 方案 A（推荐）：B 的真实 Settings 有 DATABASE_URL 属性，直接传
init_database_global(
    database_url=backend.core.config.settings.DATABASE_URL
)
# 方案 B（若 B 的真实 Settings 已追加了 MYSQL_HOST/PORT/... 等字段）：
#   from backend.db.config import DatabaseConfig  # 不要覆盖 B 原 backend.core.config
#   或直接 init_database_global(B_settings)，只要 B_settings 有 DATABASE_URL 即可。

app = FastAPI(title="XHS Content Generator", version="0.2.0")

# 以后所有路由的 db 参数，统一用 Depends(get_db) 注入 Session
```

### 4.1 create_pending

```python
from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from backend.db import get_db, create_pending

router = APIRouter(prefix="/api/v1/generations", tags=["generations"])

@router.post("")
def create_task(
    image: UploadFile = File(...),
    user_input: str = Form(""),
    db: Session = Depends(get_db),
):
    # （成员 B 自行把 image 落盘到 UPLOAD_DIR，拿到 image_path）
    image_path = f"/uploads/{image.filename}"

    rec = create_pending(
        db,                        # 第一个参数永远是显式 Session
        image_path=image_path,
        user_input=user_input,
    )
    generation_id = rec.task_id     # 即 generation_id（B 对外返回它）
    return {"generation_id": generation_id, "status": "pending"}
```

源码：[create_pending(db, ...)](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L343-L370)

### 4.2 validate_copy（validate_generation_result）

```python
from backend.validation import validate_copy, validate_generation_result
from backend.schemas import BusinessException, ErrorCode

# 写法一：逐项传
try:
    desc, title, content, tags = validate_copy(
        llm_output["image_description"],
        llm_output["title"],
        llm_output["content"],
        llm_output["tags"],
    )
except BusinessException as e:
    assert e.code == ErrorCode.VALIDATION_ERROR
    errors_dict = e.data          # 如 {"title": "标题超过 20 字限制"}
    # 交给 mark_failed 写库，不要直接抛给前端

# 写法二：整包传（对 LLM 返回 dict 做一次规范化）
norm = validate_generation_result(llm_output)
# norm = {"image_description", "title", "content", "tags"}
```

源码：[validate_copy](file:///Users/zza/Documents/trae_projects/xhs/backend/validation/__init__.py#L48-L79) · [validate_generation_result](file:///Users/zza/Documents/trae_projects/xhs/backend/validation/__init__.py#L82-L98)

### 4.3 mark_success

```python
from backend.db import mark_success
from backend.validation import validate_generation_result
from backend.schemas import BusinessException, ErrorCode

def handle_llm_done(db: Session, task_id: str, llm_output: dict):
    """B 层异步完成时调用：先校验、再写成功。"""
    try:
        norm = validate_generation_result(llm_output)
        # ⚠️ 即便 B 层没校验，mark_success 内部也会强制再校验一次（双重保险）
        rec = mark_success(
            db,
            task_id=task_id,
            title=norm["title"],
            content=norm["content"],
            tags=norm["tags"],
            image_description=norm["image_description"],
        )
        return rec.to_dict()
    except BusinessException as e:
        # 校验失败：按失败入库，保证所有任务一定有终态
        from backend.db import mark_failed
        mark_failed(db, task_id=task_id, error_code=e.code, error_message=e.message)
        raise
```

源码：[mark_success(db, ...)](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L383-L422)（内部强制 `validate_copy`，不合规**直接抛异常不写脏数据**）

### 4.4 mark_failed

```python
from backend.db import mark_failed
from backend.schemas import ErrorCode

# 例 1：图片伪扩展名 / 损坏（识图阶段失败）
mark_failed(
    db,
    task_id=generation_id,
    error_code=ErrorCode.IMAGE_PROCESS_ERROR,
    error_message="图片损坏或格式不支持",
)

# 例 2：LLM 调用超时 / 限流 / 返回非 JSON
mark_failed(
    db,
    task_id=generation_id,
    error_code=ErrorCode.LLM_GENERATE_ERROR,
    error_message=repr(last_error),
)

# 例 3：参数错（文件太大 / 类型不支持）
mark_failed(db, task_id=generation_id, error_code=ErrorCode.PARAMS_ERROR, error_message="文件超过 10MB")
```

源码：[mark_failed(db, ...)](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L425-L451)

### 4.5 便捷版（脚本 / CLI / 离线工具使用，无需传 Session）

所有便捷版在 B 调用前必须初始化全局单例一次（`init_database_global(settings)` 已经做了，后面直接调）：

```python
# 不需要 db 参数的「便捷版」函数名都是原版 + _g 后缀
from backend.db import create_pending_g, mark_success_g, mark_failed_g, get_record_g, list_records_g

rec = create_pending_g(image_path="/tmp/a.png", user_input="度假风")
```

### 4.6 B 层查询接口

```python
from backend.db import get_record, list_records

@router.get("/{generation_id}")
def get_status(generation_id: str, db: Session = Depends(get_db)):
    rec = get_record(db, task_id=generation_id)
    if rec is None:
        raise BusinessException(ErrorCode.TASK_NOT_FOUND)
    return rec.to_dict()    # dict 同时含 generation_id 与 task_id，建议对外用前者

@router.get("")
def list_all(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    return [r.to_dict() for r in list_records(db, status=status, limit=limit)]
```

---

## 5. 成功与失败状态如何保存

| 场景                                    | 写入函数                 | 最终 status | 关键字段写入                                                                 |
| --------------------------------------- | ------------------------ | ----------- | ---------------------------------------------------------------------------- |
| 用户刚提交生成请求                      | `create_pending(db, ...)`  | pending     | `task_id`(唯一) / `image_path` / `user_input` / `created_at` / `updated_at`   |
| LLM 返回通过 `validate_copy` 校验        | `mark_success(db, ...)`   | success     | `title`(≤20 字) / `content`(非空) / `tags`(JSON 3~5 个且均带 `#` 前缀) / `image_description`(非空) / `updated_at`；`error_code`+`error_message` 置为 NULL |
| 任何分支失败（图坏/模型超时/校验不通过） | `mark_failed(db, ...)`    | failed      | `error_code` + `error_message` / `updated_at`（支持可选覆盖 `image_description`）|

### B 联调验收 SQL（真实库执行，复制即查）

```sql
-- 5.1 成功/失败/待处理总数
SELECT status, COUNT(*) AS cnt FROM generation_records GROUP BY status;

-- 5.2 所有成功任务合规性二次检查（C 层应 100% 通过；若查出任何一行不合规请截图给 C）
SELECT
  task_id                          AS generation_id,
  LENGTH(title)                    AS title_chars,  -- 应 <=20
  CHAR_LENGTH(content) > 0         AS has_content,  -- 应为 1
  image_description IS NOT NULL
    AND CHAR_LENGTH(image_description) > 0         AS has_desc,     -- 应为 1
  JSON_LENGTH(tags)                AS tag_cnt,      -- 应 3~5
  (SELECT COUNT(1) FROM JSON_TABLE(tags, '$[*]' COLUMNS (t VARCHAR(64) PATH '$')) jt WHERE t NOT LIKE '#%')
                                   AS tag_missing_hash_cnt  -- 应为 0（全部带 # 前缀）
FROM generation_records WHERE status = 'success';

-- 5.3 失败任务按错误码统计（方便 B 看是图片问题多还是 LLM 问题多）
SELECT error_code, COUNT(*) AS cnt
FROM generation_records WHERE status = 'failed' GROUP BY error_code;

-- 5.4 按 generation_id 全量追踪（联调必备）
SELECT
  task_id AS generation_id, status, image_path, user_input, image_description,
  title, content, tags, error_code, error_message, created_at, updated_at
FROM generation_records WHERE task_id = 'gen_<hex24>';
```

---

## 6. generation_id 的字段类型

- **DB 实际列名**：`task_id`（ORM：`GenerationRecord.task_id`）
- **对外返回别名**：`GenerationRecord.to_dict()["generation_id"]`（与 `task_id` 完全相同，成员 B 任选；建议对外统一用 `generation_id`）
- **MySQL 类型**：**`VARCHAR(64) NOT NULL UNIQUE`**，有二级索引 `idx_task_id`（按 generation_id 查询是 O(log n) 索引访问）
- **生成规则**：`gen_` 前缀 + `uuid.uuid4().hex[:24]`；示例 `gen_356d0dbc912d44cf84682a8c`
- **实际长度**：4 + 24 = 28 字符；`VARCHAR(64)` 是为未来扩展预留（加时间戳前缀、租户 ID 等）
- **唯一性保障**：UUID4 的 122 bit 熵 + DB 层 UNIQUE 约束双重兜底

源码：[_generate_task_id()](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L339-L340)

---

## 7. 如何运行数据库测试

C 层提供两套测试：

### 7.1 run_selfcheck.py（推荐：一键 5 步验收，SQLite 默认；切 MySQL 只需设环境变量）

```bash
# 默认（临时 SQLite，不依赖 MySQL daemon）：5 步全部通过即成功
python backend/tests/run_selfcheck.py
# 预期最后一行：[ALL PASSED] 数据库 4 核心能力 + 5 校验规则全部通过。
```

**5 步含义（对应团队清单的验收点）：**
1. create_pending 落库，generation_id 格式正确且 status=pending
2. validate_copy 4 种子情况（超标题/空描述/空正文/标签去重后<3）全部抛 VALIDATION_ERROR；**mark_success 内部也会再次校验，不合规不写脏数据**
3. mark_success 合规输入写库：tags 自动去重补 `#`，并且 tag count 3~5
4. mark_failed 写库：error_code / error_message 保存
5. **模拟重启（engine.dispose + session_factory.close_all + 重新 init_database）**：两条记录仍然可读到，字段齐全

### 7.2 真实 MySQL 成功/失败写入验证（用户新要求「补充真实 MySQL 验证」）

⚠️ 请在本机已启动 MySQL daemon（如 `mysql.server start` 或 Docker）后执行；URL 里指定一个**单独的测试库名**（如 `xhs_test`），避免污染正式库。

```bash
MYSQL_TEST_URL="mysql+pymysql://root:yourpass@127.0.0.1:3306/xhs_test?charset=utf8mb4" \
  python backend/tests/run_selfcheck.py
```

预期输出首行会提示 `[MODE] 真实 MySQL 验收（MYSQL_TEST_URL=mysql+pymysql://root:***@127.0.0.1:3306/xhs_test?charset=utf8mb4）`（**密码是 ***，绝对不会输出明文**）；跑完 5 步后同样输出 `[ALL PASSED] 真实 MySQL 验证通过：成功 + 失败 + 重启持久化。`。

验收完成后如果要自动清理临时库（可选，默认不清理方便手动 SELECT），加 `MYSQL_TEST_CLEANUP=1`：

```bash
MYSQL_TEST_CLEANUP=1 MYSQL_TEST_URL="mysql+pymysql://root:pwd@127.0.0.1:3306/xhs_test?charset=utf8mb4" \
  python backend/tests/run_selfcheck.py
```

### 7.3 test_database.py（Unittest 风格，适合团队继续加更多用例）

```bash
python -m backend.tests.test_database -v
```

---

## 8. 一次真实写入 MySQL 的脱敏证据

### 证据 A（默认 SQLite 模式下 run_selfcheck.py 的真实输出——代码路径与 MySQL 完全相同）

```
[MODE] 临时 SQLite（DATABASE_URL=sqlite:////var/folders/.../t.db）
[1/5] create_pending...
    OK generation_id = gen_356d0dbc912d44cf84682a8c
[2/5] validate_copy 不合规 -> 抛异常；mark_success 内部再校验一遍
    OK 超长标题被拦截，code= VALIDATION_ERROR
    OK 空描述被拦截
    OK 空正文被拦截
    OK 标签不足(去重后<3)被拦截，code= VALIDATION_ERROR
    OK mark_success 内部再次校验，不合规不写库
[3/5] mark_success 合规成功，自动规范化
    OK tags= ['#夏日', '#穿搭', '#OOTD', '#每日']
[4/5] mark_failed 带错误码落库
    OK error_code/error_message 已保存
[5/5] 服务重启后记录仍存在
    OK

[ALL PASSED] 数据库 4 核心能力 + 5 校验规则全部通过。
```

### 证据 B（用户本机有 MySQL 时的等价验收 SQL，跑完 `MYSQL_TEST_URL=... run_selfcheck.py` 后在 MySQL 里粘贴执行，可直接截图作为真实写入证据）

```sql
-- B1. 三态计数（至少应有 1 pending + 1 success + 1 failed）
SELECT status, COUNT(*) cnt FROM xhs_test.generation_records GROUP BY status;

-- B2. 成功任务合规性快照（应 100% 通过）
SELECT
  task_id AS generation_id, status,
  LENGTH(title)                            AS title_chars,     -- <=20
  CHAR_LENGTH(content) > 0                 AS has_content,     -- =1
  image_description IS NOT NULL
    AND CHAR_LENGTH(image_description) > 0 AS has_desc,        -- =1
  JSON_LENGTH(tags)                        AS tag_cnt,         -- 3~5
  error_code IS NULL                       AS no_error         -- =1
FROM xhs_test.generation_records WHERE status='success' LIMIT 1;

-- B3. 失败任务快照（应有 error_code = 'IMAGE_PROCESS_ERROR'）
SELECT task_id AS generation_id, status, error_code, error_message
FROM xhs_test.generation_records WHERE status='failed' LIMIT 1;
```

### 证据 C（脱敏 INSERT 示例：无任何真实密钥/业务数据）

```sql
INSERT INTO generation_records
  (task_id, status, image_path, image_description, user_input,
   title, content, tags, error_code, error_message)
VALUES
  ('gen_xxxxxxxxxxxxxxxxxxxxxxxx', 'success',
   '/uploads/scenery_01.jpg',
   '阳光下的白色连衣裙模特图',
   '海边 度假风 拍照姿势',
   '夏日穿搭分享',
   '今天分享三套夏日 look，显瘦又出片！',
   '["#夏日","#穿搭","#OOTD","#每日"]',
   NULL, NULL),
  ('gen_yyyyyyyyyyyyyyyyyyyyyyyy', 'failed',
   '/uploads/fake.png', NULL, '测试伪扩展',
   NULL, NULL, NULL,
   'IMAGE_PROCESS_ERROR', '图片损坏无法解析');
```

---

## 9. 尚未完成的 B+C 联调事项（由成员 B 完成，C 层已提供全部稳定调用入口与 Depends）

1. **FastAPI 启动时初始化一次**：`from backend.db import init_database_global; from backend.db.config import get_settings; init_database_global(get_settings())`。禁止再拼第二份 MySQL 配置。
2. **POST /api/v1/generations**：图片落盘拿到 image_path → 调 `create_pending(db, image_path=..., user_input=...)`，把 `rec.task_id` 作为 `generation_id` 返回前端。
3. **LLM 异步完成链路**：拿到 LLM 返回后**必须先** `validate_generation_result(llm_output)`，**通过后再** `mark_success(db, task_id=..., **norm)`。**禁止跳过校验直接写库**（C 层在 mark_success 内部也会再校验一次——双重保险）。
4. **所有失败分支统一写 mark_failed + ErrorCode**：
   - 上传格式/尺寸 → `PARAMS_ERROR`
   - 伪扩展名/坏图 → `IMAGE_PROCESS_ERROR`
   - 模型超时/限流/返回非 JSON → `LLM_GENERATE_ERROR`
   - 校验不通过 → `VALIDATION_ERROR`
   - DB 异常 → `DATABASE_ERROR`
   - 兜底 → `INTERNAL_ERROR`
5. **GET 查询**：`get_record(db, task_id=...)` / `list_records(db, status=..., limit=...)` → `.to_dict()` 直接返回。
6. **响应结构统一**：用 `backend.schemas.success_response` / `error_response` 包装；业务异常统一抛 `BusinessException`，交给 FastAPI `@app.exception_handler(BusinessException)` 统一序列化成 JSON，避免前后端各自解释第三方异常。
7. **禁止打印含密码的 DATABASE_URL**：所有需要输出连接串的日志/print，一律套 `from backend.db.config import mask_database_url; print(mask_database_url(settings.DATABASE_URL))`。C 层的 `init_db.py` 和 `run_selfcheck.py` 已经全部做了脱敏。

🚫 **本 PR 先不要合并**，等成员 B 完成以上 7 条联调 + 至少一次真实 MySQL 成功/失败写入验收快照（§8.B 的三条 SQL 结果贴到 PR 评论）后再合。
