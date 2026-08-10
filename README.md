# XHS AI Content Generator

基于 Vue 3、FastAPI、PaddleOCR-VL 和 Qwen3-VL 的单图小红书文案生成全栈应用。页面接收一张图片和可选的商品名、目标人群、语气要求，并展示图片概述、标题、正文与标签。

## 已实现功能

- `POST /api/v1/generations`，使用 `multipart/form-data` 上传单张图片。
- `GET /api/v1/generations`，读取最近成功生成的本地历史记录。
- 首屏官网前台包含产品介绍、功能亮点、使用流程、FAQ 和工作台入口。
- 前端可在生成工作台与历史记录页之间切换，支持历史加载、空状态、错误重试、刷新和单条复制。
- 支持 JPG、JPEG、PNG、WebP，以及单帧 HEIC/HEIF；HEIC/HEIF 会在服务端安全转换为 JPEG，
  并拒绝空文件、伪装格式、损坏图片、超限文件和异常尺寸。
- 自动处理 EXIF 方向、透明通道、颜色模式和大图等比例缩放。
- PaddleOCR-VL 尝试识别包装文字；OCR 失败或超时时自动降级为 Qwen3-VL 直接识图。
- Qwen3-VL 生成固定结构的 `image_summary`、`title`、`body`、`tags`。
- 输出 JSON 解析、有限重试、最长 20 字标题归一化和 3–5 个标签校验。
- 对未经图片证实的护肤功效、适用人群、使用感和明显品类冲突进行保守拦截。
- 统一处理模型失败、超时、输出无效和服务器内部错误，不向客户端暴露 Key 或堆栈。
- 成功和失败后都会清理上传及预处理临时文件。

成员 C 的数据库核心已经通过 B 的异步适配器接入应用生命周期。MySQL 持久化默认
关闭，未启用时继续使用 No-op；显式启用后若启动检查失败，应用会终止启动，不会假装
写库成功。B+C 持久化链路已在本机 MySQL 9.6 专用测试库完成真实验收。

## 环境要求

- Python 3.11 或更高版本
- 可访问硅基流动 API
- 有权使用以下模型的硅基流动 API Key：
  - `Qwen/Qwen3-VL-8B-Instruct`
  - `PaddlePaddle/PaddleOCR-VL-1.5`

## 本地安装

```bash
git clone https://github.com/drkiyira-dev/XHS-ai-content-generator.git
cd XHS-ai-content-generator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## 配置环境变量

复制模板并填写真实 Key：

```bash
cp .env.example .env
```

最少需要设置：

```dotenv
SILICONFLOW_API_KEY=在此填写真实Key
```

`.env.example` 当前提供以下配置：

| 变量 | 用途 | 模板值 |
| --- | --- | --- |
| `SILICONFLOW_API_KEY` | 硅基流动 API Key，仅保存在本地 `.env` | 空，必须填写 |
| `SILICONFLOW_BASE_URL` | 硅基流动 OpenAI 兼容接口地址 | `https://api.siliconflow.cn/v1` |
| `VISION_MODEL_NAME` | 图片理解与文案生成模型 | `Qwen/Qwen3-VL-8B-Instruct` |
| `OCR_MODEL_NAME` | 包装文字 OCR 模型 | `PaddlePaddle/PaddleOCR-VL-1.5` |
| `MODEL_TIMEOUT_SECONDS` | OCR 与 Qwen 共用的总截止时间 | `60` |
| `OCR_TIMEOUT_SECONDS` | OCR 阶段最多占用时间 | `15` |
| `CORS_ALLOW_ORIGINS` | 允许访问后端的前端来源，逗号分隔 | 本地 Vite 地址 |
| `UPLOAD_DIR` | 临时上传目录 | `uploads` |
| `MAX_IMAGE_SIZE_MB` | 单张图片最大体积 | `10` |
| `MAX_IMAGE_PIXELS` | 解码后最大总像素数 | `50000000` |
| `MODEL_MAX_IMAGE_EDGE` | 送入模型前的最长边 | `3584` |
| `DATABASE_ENABLED` | 是否显式启用 MySQL 持久化 | `false` |
| `DATABASE_URL` | 受保护的 `mysql+pymysql` 连接地址 | 空 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | MySQL 建连超时 | `5` |
| `DATABASE_READ_TIMEOUT_SECONDS` | MySQL 读取超时 | `30` |
| `DATABASE_WRITE_TIMEOUT_SECONDS` | MySQL 写入超时 | `30` |
| `DATABASE_POOL_SIZE` | 常驻连接池上限 | `5` |
| `DATABASE_MAX_OVERFLOW` | 连接池临时额外连接上限 | `5` |
| `DATABASE_POOL_TIMEOUT_SECONDS` | 等待连接池的最长时间 | `5` |
| `DATABASE_POOL_RECYCLE_SECONDS` | 连接回收周期 | `1800` |
| `DATABASE_TLS_CA` | 远程 MySQL 的 CA 证书路径 | 空 |

安全要求：不得提交真实 `.env`、API Key、上传图片、日志或模型原始响应。项目的 `.gitignore` 已忽略这些内容。

## MySQL 持久化（默认关闭）

数据库开关关闭时，应用不会创建 Engine、连接 MySQL 或执行 SQL，Swagger 行为与此前
一致。启用前必须先准备：

- 已存在的专用数据库；
- 有限权限的非 `root` 用户；
- 已执行 [migrations/001_generation_records.sql](migrations/001_generation_records.sql)
  的 `generation_records` 表；
- 远程数据库还必须准备 CA 文件并配置 `DATABASE_TLS_CA`。

迁移文件不会创建、选择或删除数据库，只会在操作者已经明确选中的数据库中创建项目表。
应用启动本身也不会自动建库、建表或修改 schema。

准备完成后，在本地 `.env` 中填写，不要把密码发送到聊天或提交 Git：

```dotenv
DATABASE_ENABLED=true
DATABASE_URL=mysql+pymysql://xhs_app:在此填写密码@127.0.0.1:3306/xhs_ai_test
```

只允许 `mysql+pymysql`，并拒绝空密码、`root` 用户、URL query 参数以及缺少 CA 的远程
地址。启动时会进行一次只读连通性和表存在性检查；检查失败会安全终止启动，不会静默
退回 No-op。Engine 固定隐藏 SQL 参数，并设置连接、读写和连接池超时。

本机专用测试库已经验证：API 成功写入、模型失败状态写入、重复 ID 回滚、同一 pending
记录的并发终态竞争、Engine 释放后的重新连接与数据读取，以及本轮测试记录的精确清理。
应用用户仅拥有该测试库的 `SELECT`、`INSERT`、`UPDATE`、`DELETE` 权限。

## 启动后端

激活虚拟环境后运行：

```bash
python -m uvicorn backend.main:app --reload
```

默认地址：

- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- 健康检查：`GET http://127.0.0.1:8000/api/health`
- 生成接口：`POST http://127.0.0.1:8000/api/v1/generations`
- 历史接口：`GET http://127.0.0.1:8000/api/v1/generations?limit=20`

健康检查只确认 FastAPI 进程能够响应，不会调用 OCR、Qwen 或查询数据库，也不会返回
模型、数据库或环境变量详情。若显式启用了 MySQL，数据库启动检查仍会在应用进入可用状态
前独立执行，失败时应用会终止启动。

## 后端 Docker 镜像（增值功能）

仓库根目录提供后端专用的多阶段 `Dockerfile`。镜像使用 Python 3.12 Debian slim，
最终阶段固定以 UID/GID `10001:10001` 的非 root 用户运行，只复制后端代码与运行依赖。
构建上下文采用白名单，不会把 `.env`、Git 历史、前端依赖、测试数据、上传图片或日志
发送给 Docker 构建器。

构建镜像：

```bash
docker build --tag xhs-ai-backend:local .
```

本段只容器化后端，不自动启动或修改 MySQL。先复制一份不会提交的容器配置，并确保其中
至少填写真实 `SILICONFLOW_API_KEY`，同时保持 `DATABASE_ENABLED=false`：

```bash
cp .env.example .env.docker
```

使用只读根文件系统和专用 tmpfs 启动；上传原图、预处理图以及 Python 临时文件只存在于
内存中，并会在容器停止后消失：

```bash
docker run --rm \
  --name xhs-ai-backend \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777 \
  --tmpfs /run/xhs/uploads:rw,noexec,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700 \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 128 \
  --publish 127.0.0.1:8000:8000 \
  --env-file .env.docker \
  --env UPLOAD_DIR=/run/xhs/uploads \
  --env DATABASE_ENABLED=false \
  xhs-ai-backend:local
```

浏览器继续访问 <http://127.0.0.1:8000/docs>；可用下面的命令单独验证容器内置健康检查：

```bash
docker inspect --format '{{.State.Health.Status}}' xhs-ai-backend
```

不要使用 Docker `ARG`、Dockerfile `ENV` 或镜像层传入真实 API Key；示例通过未跟踪的
`.env.docker` 在运行时注入。镜像没有声明保存上传文件的 `VOLUME`，避免匿名卷残留图片。
容器内部必须监听 `0.0.0.0` 才能接受端口映射，但宿主机只发布到 `127.0.0.1`，因为
当前历史接口仍是无登录鉴权的本地单用户功能。

`127.0.0.1` 在容器中指向容器自身，不是宿主机 MySQL。本段因此明确保持数据库关闭；
数据库容器编排、专用网络、迁移执行和持久卷属于后续 Docker Compose 段，不能用临时
主机映射绕过现有 MySQL/TLS 安全检查。

## API 请求

请求类型：`multipart/form-data`

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `image` | 是 | 单张 JPG、JPEG、PNG、WebP 或单帧 HEIC/HEIF 图片，模板限制为 10MB；HEIC/HEIF 会转换为 JPEG 后送入模型 |
| `product_name` | 否 | 用户猜测的候选商品名；与图片冲突时以图片为准 |
| `target_audience` | 否 | 调整表达角度，不能作为产品适用性的事实证据 |
| `tone` | 否 | 调整文案风格，不能作为产品属性的事实证据 |

curl 示例：

```bash
curl -X POST 'http://127.0.0.1:8000/api/v1/generations' \
  -H 'accept: application/json' \
  -F 'image=@/absolute/path/example.jpg;type=image/jpeg' \
  -F 'product_name=玫瑰果卸妆油' \
  -F 'target_audience=大学生' \
  -F 'tone=轻松自然'
```

三个可选字段可以全部省略。

## 成功响应

成功状态码：`200 OK`

```json
{
  "generation_id": "550e8400-e29b-41d4-a716-446655440000",
  "image_summary": "浅粉色瓶身搭配浅色标签，标签可见品牌、品类和容量信息。",
  "title": "玫瑰果卸妆油开箱",
  "body": "浅粉色瓶身搭配简约标签，包装上可见 CLEANSING OIL、ROSE HIP 和 100 ML。",
  "tags": ["#卸妆油", "#玫瑰果", "#粉色包装", "#护肤分享"],
  "created_at": "2026-08-07T07:39:32.921950Z"
}
```

`generation_id` 和 `created_at` 每次请求都会变化。

## 历史记录接口（本地单用户增值功能）

前端顶部的“历史记录”入口会在用户主动进入时读取最近成功结果，不会触发图片上传、
OCR 或 Qwen 调用。页面展示图片摘要、标题、正文、标签和本地格式化时间，并允许复制
单条文案；不会展示本地图片路径、用户输入、失败原因或数据库内部字段。

```http
GET /api/v1/generations?limit=20
```

- 只返回 `success` 记录，按创建时间从新到旧排列。
- `limit` 默认为 `20`，允许范围为 `1` 到 `50`。
- 响应沿用生成结果的 `generation_id`、`image_summary`、`title`、`body`、
  `tags`、`created_at` 字段，并额外返回 `count`。
- 不返回本地图片路径、用户输入、失败原因或数据库内部字段。
- 响应带有 `Cache-Control: no-store`，避免浏览器或代理缓存生成文案。
- `DATABASE_ENABLED=false` 时 No-op 持久化不会保存数据，因此历史固定为空。

示例：

```json
{
  "items": [
    {
      "generation_id": "550e8400-e29b-41d4-a716-446655440000",
      "image_summary": "浅粉色瓶身搭配浅色标签。",
      "title": "玫瑰果卸妆油开箱",
      "body": "包装上可见 CLEANSING OIL、ROSE HIP 和 100 ML。",
      "tags": ["#卸妆油", "#玫瑰果", "#护肤分享"],
      "created_at": "2026-08-07T07:39:32.921950Z"
    }
  ],
  "count": 1
}
```

该接口当前只面向 README 默认的 `127.0.0.1` 本地单用户演示。它没有登录鉴权或
用户数据隔离，不得直接绑定 `0.0.0.0` 暴露到局域网或公网；若后续需要多用户部署，
必须先增加身份认证和记录所有权过滤。

## 错误响应

所有应用错误使用同一结构：

```json
{
  "error": {
    "code": "MODEL_OUTPUT_INVALID",
    "message": "模型返回的内容格式无效，请重试。",
    "retryable": true
  }
}
```

| HTTP 状态 | 错误码 | 说明 | 可重试 |
| --- | --- | --- | --- |
| 400 | `IMAGE_REQUIRED` | 未上传图片或文件为空 | 否 |
| 400 | `FORM_FIELD_TOO_LARGE` | 可选文本字段超过表单解析上限 | 否 |
| 413 | `IMAGE_TOO_LARGE` | 图片超过当前配置的体积限制 | 否 |
| 415 | `UNSUPPORTED_IMAGE_TYPE` | 格式不支持，或 MIME、魔数、真实格式不一致 | 否 |
| 422 | `IMAGE_DECODE_FAILED` | 图片损坏或无法解码 | 否 |
| 422 | `INVALID_IMAGE_DIMENSIONS` | 图片尺寸无效或总像素超限 | 否 |
| 502 | `MODEL_FAILED` | 模型服务、权限、限流或上游响应失败 | 视上游状态而定 |
| 502 | `MODEL_OUTPUT_INVALID` | 模型输出无法归一化，或未通过事实安全校验 | 是 |
| 504 | `MODEL_TIMEOUT` | OCR 与 Qwen 的总处理时间超限 | 是 |
| 500 | `DATABASE_ERROR` | 生成结果暂时无法保存 | 是 |
| 500 | `INTERNAL_ERROR` | 未分类的服务器内部错误 | 否 |

B 侧的 `DATABASE_ERROR` 与成员 C 的适配器已经接通。真实 MySQL 测试确认成功和失败
记录使用同一个 `generation_id`，重复 ID 会安全返回 `DATABASE_ERROR` 且不覆盖原记录。

## 处理流程

```text
上传图片
  → 文件类型、魔数、体积、解码和尺寸校验
  → EXIF、透明通道、RGB 与缩放预处理（HEIC/HEIF 统一转换为 JPEG）
  → create_pending（MySQL 未显式启用时为 No-op）
  → PaddleOCR-VL（失败时安全降级）
  → Qwen3-VL 图片理解与文案生成
  → JSON 解析、字段归一化与有限重试
  → 事实安全校验
  → mark_success / mark_failed（MySQL 未显式启用时为 No-op）
  → 返回固定 API 响应
```

正常情况下会调用一次 OCR 和一次 Qwen。只有 Qwen 首次输出需要修复时，才会使用唯一一次 Qwen 重试。

## 运行测试

```bash
python -m pytest
```

当前测试覆盖：

- API 请求字段、二进制上传和 CORS。
- 历史记录排序、数量限制、时区、损坏记录拒绝和数据库错误脱敏。
- 配置验证及敏感信息隐藏。
- 图片格式、魔数、体积、解码、尺寸和多文件拒绝。
- EXIF 方向、透明图片、大图缩放、UUID 临时文件和清理。
- OCR 降级、Qwen 请求、总超时、取消、有限重试和错误映射。
- JSON/schema 归一化、长标题处理和事实安全校验。

真实 API 测试会产生模型调用费用；自动化测试默认使用 Mock，不会调用硅基流动。

真实 MySQL 测试默认跳过，避免 CI 或普通本地测试误写数据库。它只允许连接文档约定的
`xhs_app@127.0.0.1/xhs_ai_test`，并且需要显式确认变量：

```bash
XHS_MYSQL_LIVE_TEST_CONFIRM=YES_USE_XHS_AI_TEST \
  python -m pytest tests/test_mysql_live.py
```

该测试使用三个随机 UUID，验证完成后只删除这三个 ID，不删除数据库、表或其他记录。

## GitHub Actions 自动检查

仓库的 `CI` workflow 会在以下情况自动运行：

- 向 `develop` 或 `main` 提交 Pull Request。
- `develop` 或 `main` 收到新的提交。
- 在 GitHub Actions 页面手动触发。

CI 包含三个互相独立的任务：

- Python 3.12：安装后端开发依赖并运行全部 `pytest`。
- Node.js 24：使用 `npm ci` 按锁文件安装前端依赖并执行生产构建。
- Docker：构建后端镜像，以非 root、只读文件系统、无网络和无 Linux capabilities 的
  容器执行 `/api/health` 冒烟检查；只使用无效占位 Key，不连接模型或 MySQL。

该 workflow 只有仓库内容读取权限，不使用硅基流动 Key、不连接 MySQL、
不登录镜像仓库、不推送镜像、不部署应用，也不会自动修改或合并 PR。重复推送同一分支
时，较旧的运行会被取消。

## 团队整合状态与后续工作

当前 `main` 已通过 PR 完成 A、B、C 三部分的整合：

- 成员 A 的 Vue 页面默认调用真实 FastAPI，包含上传、Loading、结果展示、复制和重试状态。
- 成员 B 的多模态生成、事实安全、历史查询及 HEIC/HEIF 后端转换已经接入。
- 成员 C 的数据库职责已通过异步 SQLAlchemy 适配器进入同一生成生命周期。
- 前端允许上传 HEIC/HEIF；浏览器无法原生预览时显示文件占位说明，后端只接受单帧文件并执行最终安全校验与 JPEG 转换。
- 本地单用户历史记录页面已接通安全的只读历史 API。

后续增值功能仍通过独立功能分支和 Pull Request 进入 `main`；禁止直接推送 `main`。
