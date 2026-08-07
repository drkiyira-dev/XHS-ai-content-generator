# 成员 C（数据库层）交付说明

> 📦 交付范围（本 PR 只修改这些路径）：
> - `backend/db/`           → ORM 模型 + repository + 配置 + 初始化入口
> - `backend/schemas/`      → 统一响应结构、错误码、业务异常
> - `backend/validation/`   → 文案合规校验（validate_copy）
> - `backend/tests/`        → 数据库相关测试
> - `schema/migration/`     → 建库建表初始化 SQL
> - 根目录 `.gitignore` / `requirements.txt` / `README.md` 中与数据库相关的说明
>
> 🚫 不在本 PR 范围内（保持不变或由其他成员负责）：
> - `frontend/` 全部
> - `backend/api/v1/generations.py` （成员 B 负责，接口层）
> - `backend/services/model/` 、Qwen Prompt、SiliconFlow API 调用

---

## 1. 数据库表结构（generation_records）

**表名**：`generation_records`（Flask-SQLAlchemy model: `backend.db.GenerationRecord`）

| 字段               | 类型         | 索引/约束            | 说明                                                           |
| ------------------ | ------------ | -------------------- | -------------------------------------------------------------- |
| `id`               | INT          | PK AUTO_INCREMENT    | 内部自增主键                                                   |
| `task_id`          | VARCHAR(64)  | UNIQUE / idx_task_id | **对外暴露的 generation_id**，字符串，形如 `gen_<hex24>`，成员 B 以此追踪 |
| `status`           | VARCHAR(16)  | idx_status           | `pending` / `success` / `failed`                               |
| `image_path`       | VARCHAR(512) |                      | 上传图片在本地/对象存储的路径                                 |
| `image_description`| TEXT         |                      | 识图结果 / 图片描述（非空在应用层强制）                       |
| `user_input`       | TEXT         |                      | 用户可选输入：风格偏好、关键词                               |
| `title`            | VARCHAR(100) |                      | 生成标题（≤20 字在应用层强制）                                |
| `content`          | TEXT         |                      | 生成正文（非空在应用层强制）                                  |
| `tags`             | JSON         |                      | 标签数组：3~5 个，去重并补 `#` 前缀                           |
| `error_code`       | VARCHAR(64)  |                      | 失败时错误码（见 `backend/schemas.ErrorCode`）                |
| `error_message`    | TEXT         |                      | 失败时详细错误信息                                            |
| `created_at`       | DATETIME     | idx_created_at       | 创建时间（默认 UTC now）                                      |
| `updated_at`       | DATETIME     |                      | 最后更新时间（on update UTC now）                             |

SQL 源码：[schema/migration/001_init.sql](file:///Users/zza/Documents/trae_projects/xhs/schema/migration/001_init.sql)
ORM 源码：[backend/db/__init__.py](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L35-L83)

---

## 2. 初始化和迁移命令

数据库初始化 **幂等**（可重复执行）。两种方式任选其一：

**方式 A（推荐，后端开发者）：**
```bash
# 依赖：pip install -r requirements.txt
python backend/db/init_db.py
```
输出：
```
[OK] 数据库 xhs_db 已就绪
[OK] 表 generation_records 已就绪（DATABASE_URL=...）
[DONE] 数据库初始化完成，可重复执行。
```

**方式 B（DBA / 纯 SQL 执行）：**
```bash
mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p"$MYSQL_PASSWORD" \
  < schema/migration/001_init.sql
```

> 迁移策略：目前是最小 MVP 的单表（001_init）。后续新增迁移文件命名 `schema/migration/NNN_<verb>.sql`，并在 `backend/db/init_db.py` 保持幂等调用，避免破坏已有表。

---

## 3. 所需环境变量名称

**所有敏感配置都通过环境变量或 `.env.example`（占位）加载，代码中不存在明文密钥。**

**本 PR 实际采用的变量（优先级从上到下）：**

| 变量名            | 说明                                                         | 默认值 / 示例                                                   |
| ----------------- | ------------------------------------------------------------ | --------------------------------------------------------------- |
| `DATABASE_URL`    | 首选；完整 SQLAlchemy 连接串                                 | `mysql+pymysql://user:pwd@127.0.0.1:3306/xhs_db?charset=utf8mb4` |
| `MYSQL_HOST`      | 当 `DATABASE_URL` **未设置**时生效，拼接 MySQL 连接串        | `127.0.0.1`                                                     |
| `MYSQL_PORT`      | 同上                                                         | `3306`                                                          |
| `MYSQL_DATABASE`  | 同上                                                         | `xhs_db`                                                        |
| `MYSQL_USER`      | 同上                                                         | `root`                                                          |
| `MYSQL_PASSWORD`  | 同上（**不入库**，只写本地 `.env`，`.gitignore` 已过滤）     | 空字符串（本地开发无密码）                                      |

另外，虽然本 PR 不直接使用，但为对齐团队变量，`.env.example` 也预留：
- `DASHSCOPE_API_KEY`、`MODEL_NAME`、`UPLOAD_DIR`、`FLASK_ENV`、`FLASK_PORT`

源码：[backend/db/config.py](file:///Users/zza/Documents/trae_projects/xhs/backend/db/config.py#L13-L38)

---

## 4. create_pending / validate_copy / mark_success / mark_failed 调用示例

> 成员 B 在 `backend/api/v1/generations.py` 内按下面示例 import 即可；**不需要写任何 SQL**。

```python
from backend.db import (
    create_pending,
    mark_success,
    mark_failed,
    get_record,
    TASK_STATUS_PENDING, TASK_STATUS_SUCCESS, TASK_STATUS_FAILED,
)
from backend.validation import validate_copy, validate_generation_result
from backend.schemas import (
    ErrorCode, BusinessException, success_response, error_response,
)


# ---------------- 4.1 create_pending：创建 pending 任务 ----------------
def create_pending_demo():
    # 上传图片落盘后，把路径 + 用户输入传给 create_pending
    record = create_pending(
        image_path="/uploads/scenery_01.jpg",
        user_input="海边 度假风 拍照姿势",
        image_description=None,   # 还没跑识图可以留空，后面 mark_* 时再补
    )
    generation_id = record.task_id   # ← 这就是 generation_id，返回给前端轮询
    return generation_id


# ---------------- 4.2 validate_copy：校验 + 规范化 LLM 返回 ----------------
def validate_llm_output_demo(raw: dict):
    """raw 是 LLM 返回的 dict：
        {"image_description": "...", "title": "...",
         "content": "...", "tags": ["a","b","c","d"]}
    不合规则抛 BusinessException(VALIDATION_ERROR)，不会进入写库步骤。
    """
    # 写法 1：逐项传
    desc, title, content, tags = validate_copy(
        raw["image_description"],
        raw["title"],
        raw["content"],
        raw["tags"],
    )
    # 写法 2：整包传
    norm = validate_generation_result(raw)
    # norm = {"image_description", "title", "content", "tags"}
    return norm


# ---------------- 4.3 mark_success：写成功状态（内部强制跑 validate_copy） ----------------
def mark_success_demo(generation_id: str, norm: dict):
    """不合规则抛异常（不写库），不会把脏数据交给前端。"""
    try:
        record = mark_success(
            task_id=generation_id,
            title=norm["title"],
            content=norm["content"],
            tags=norm["tags"],
            image_description=norm["image_description"],
        )
        return record.to_dict()
    except BusinessException as e:
        # 校验不通过 → 直接 mark_failed 或抛给上层统一处理
        mark_failed(generation_id, error_code=e.code, error_message=str(e.message))
        raise


# ---------------- 4.4 mark_failed：写失败状态 + 错误码 ----------------
def mark_failed_demo(generation_id: str):
    # 错误码使用 ErrorCode.*，例如：
    mark_failed(
        task_id=generation_id,
        error_code=ErrorCode.IMAGE_PROCESS_ERROR,
        error_message="图片损坏无法识别",
        image_description="",   # 可选
    )
```

源码：
- create_pending/mark_success/mark_failed → [backend/db/__init__.py](file:///Users/zza/Documents/trae_projects/xhs/backend/db/__init__.py#L111-L201)
- validate_copy → [backend/validation/__init__.py](file:///Users/zza/Documents/trae_projects/xhs/backend/validation/__init__.py#L39-L80)

---

## 5. 成功与失败状态如何保存

| 情况 | 写入方式 | 最终状态 | 关键字段 |
| --- | --- | --- | --- |
| 用户刚提交生成请求 | `create_pending()` | `pending` | image_path, user_input, created_at, updated_at |
| LLM 返回合规，通过 `validate_copy` | `mark_success()` | `success` | title(≤20字), content(非空), tags(JSON 3~5个且都带`#`), image_description(非空), updated_at |
| LLM 返回不合规 / 图片损坏 / 模型超时 / 其他 | `mark_failed(error_code, error_message)` | `failed` | error_code, error_message, updated_at |

**如何查询（SQL 验收）：**
```sql
-- 成功/失败计数
SELECT status, COUNT(*) FROM generation_records GROUP BY status;

-- 成功任务合规性检查（C 的校验保证了不会出现脏数据，这里仅用于二次验收）
SELECT task_id generation_id,
       LENGTH(title) title_chars,
       CHAR_LENGTH(content) content_chars,
       JSON_LENGTH(tags) tag_cnt
FROM generation_records WHERE status='success';

-- 失败任务错误码分布
SELECT error_code, COUNT(*) FROM generation_records
WHERE status='failed' GROUP BY error_code;
```

---

## 6. generation_id 的字段类型

- **代码中实际字段名**：`task_id`（在 `GenerationRecord.task_id` / `to_dict()["generation_id"]` 与 `to_dict()["task_id"]` **均提供**，成员 B 任选）
- **MySQL 列类型**：`VARCHAR(64) NOT NULL UNIQUE`（有二级索引，按 generation_id 查找是索引访问）
- **生成规则**：`gen_` 前缀 + 24 位十六进制（`uuid4().hex[:24]`），例如 `gen_356d0dbc912d44cf84682a8c`
- **长度**：固定长度 4 + 24 = `28` 字符；`VARCHAR(64)` 预留扩展
- **全局唯一性**：UUID4 空间足够，可认为分布式不冲突；外加 DB UNIQUE 约束兜底

---

## 7. 如何运行数据库测试

本 PR 提供 **两份** 数据库测试，**都不依赖真实 MySQL**（默认用临时 SQLite）。
若要跑 MySQL，只需把 `DATABASE_URL` 改成真实连接串即可，代码无需修改。

```bash
# 7.1 脚本化自测（最稳，推荐 CI / 预提交）
python backend/tests/run_selfcheck.py
```

预期输出（脱敏示例）：
```
[1/5] create_pending...
    OK generation_id = gen_356d0dbc912d44cf84682a8c
[2/5] validate_copy 不合规 -> 抛异常，mark_success 内部已拦截
    OK 超长标题被拦截，code= VALIDATION_ERROR
    OK 空描述被拦截
    OK 空正文被拦截
    OK 标签不足(去重后<3)被拦截，code= VALIDATION_ERROR
[3/5] mark_success 合规成功，自动规范化
    OK tags= ['#夏日', '#穿搭', '#OOTD', '#每日']
[4/5] mark_failed 带错误码落库
    OK error_code/error_message 已保存
[5/5] 服务重启后记录仍存在
    OK
[ALL PASSED] 数据库 4 核心能力 + 5 校验规则全部通过。
```

```bash
# 7.2 Unittest 风格（可选，方便后面扩展更多用例）
python -m backend.tests.test_database -v
```

---

## 8. 一次真实写入 MySQL 的脱敏证据

> 由于当前机器没有启动 MySQL（`localhost:3306 拒绝连接`），这里用「同样代码写 SQLite + 相同字段内容」来脱敏证明写库行为真实、字段全、服务重启后仍能查到；切到 MySQL 时只需改环境变量，代码路径完全一致。
>
> 你在本机用真实 MySQL 重跑 `backend/tests/run_selfcheck.py` 时，可以把 DATABASE_URL 设置为 MySQL 连接串，然后用下列 SQL 做等价验证：

**证据 A：通过测试脚本 run_selfcheck.py 的成功输出（见 §7）**
- 已实际写入 `pending` 1 条 → `success` 1 条 → `failed` 1 条
- `tag_cnt` = 4（#夏日/#穿搭/#OOTD/#每日）、`title_chars` = 6（≤20）、`image_description` 非空、`user_input` 非空

**证据 B：等价 MySQL 查数（你换成 MySQL 后可直接跑）**
```sql
-- 1) 成功任务合规性
SELECT task_id AS generation_id, status,
       image_path IS NOT NULL has_image_path,
       user_input IS NOT NULL has_user_input,
       image_description IS NOT NULL has_image_desc,
       LENGTH(title) title_chars,
       CHAR_LENGTH(content) > 0 has_content,
       JSON_LENGTH(tags) tag_cnt,
       error_code IS NULL no_err
FROM generation_records
WHERE status='success' ORDER BY created_at DESC LIMIT 1;

-- 2) 失败任务错误码
SELECT task_id, status, error_code, error_message
FROM generation_records WHERE status='failed' ORDER BY created_at DESC LIMIT 1;

-- 3) 重启后仍在（同一 task_id 再次查询应仍返回 success）
SELECT status, title FROM generation_records WHERE task_id='<刚才的 generation_id>';
```

**证据 C：脱敏后的 INSERT 语句（字段齐全，无真实密码/密钥/业务数据）**
```
INSERT INTO generation_records
  (task_id, status, image_path, image_description, user_input,
   title, content, tags, error_code, error_message)
VALUES
  ('gen_xxxxxxxxxxxxxxxxxxxxxxxx', 'success',
   '/uploads/scenery_01.jpg',
   '阳光下的白色连衣裙',
   '海边 度假风',
   '夏日穿搭分享',
   '今天分享三套夏日 look，显瘦又出片！',
   '["#夏日","#穿搭","#OOTD","#每日"]',
   NULL, NULL);
```

---

## 9. 尚未完成的 B+C 联调事项

以下由成员 B 完成，C 已经把调用接口稳定给出（见 §4）：

1. **`POST /api/v1/generations` 中调用 create_pending**：在接收到用户上传图片 + user_input 后，落盘图片得到 image_path → 调 create_pending，把 generation_id 返回给前端。

2. **LLM 回调/异步完成时调用 validate_copy + mark_success**：拿到 LLM 的 (description, title, content, tags) 后先 validate_copy（或整包 validate_generation_result），通过后调 mark_success；**不要绕过校验直接写库**，否则 C 的规则等于没生效。

3. **任何失败分支（识图失败/超时/限流/模型异常）统一调用 mark_failed**：务必带上 ErrorCode 中已有的错误码，保证前后端同一份字典（`ErrorCode.PARAMS_ERROR / VALIDATION_ERROR / IMAGE_PROCESS_ERROR / LLM_GENERATE_ERROR / DATABASE_ERROR / INTERNAL_ERROR`）。

4. **查询接口（GET /api/v1/generations/<id>）：** 用 `backend.db.get_record(task_id=...)` 拿到结果，调用 `.to_dict()` 返回给前端即可（dict 中同时含 `generation_id` 和 `task_id` 字段，B 决定对外输出哪个；建议用 `generation_id`）。

5. **环境变量对齐**：B 在 `backend/api/v1/generations.py` 里不要自己拼 MySQL 连接串，统一使用 `backend.db.config.Config` 加载的 `MYSQL_*` 或 `DATABASE_URL`，避免双份配置。

6. **启动顺序**：`python backend/db/init_db.py` 必须在启动服务前执行（或服务启动代码里先调 `backend.db.init_database(app)`），否则第一次启动可能出现「no such table: generation_records」。

> 🚫 本 PR **未修改** `POST /api/v1/generations` 路径、未新增任何生成接口、未引入 Qwen/SiliconFlow 调用代码，完全符合约束。

---

## 快速对接：一句话总结给成员 B

```python
# 你只需要 import 下面这些东西
from backend.db import create_pending, mark_success, mark_failed, get_record
from backend.validation import validate_copy, validate_generation_result
from backend.schemas import ErrorCode, BusinessException
# 其余的写库、索引、校验、规范化 全部交给 C 层处理。
```
