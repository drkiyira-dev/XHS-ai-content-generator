# XHS AI Content Generator

[English](README.md) | **简体中文**

基于 Vue 3、FastAPI、PaddleOCR-VL 和 Qwen3-VL 的单图小红书内容生成全栈应用。页面接收一张图片和可选的主题或名称、目标读者、表达风格，并展示图片理解摘要、标题、正文、话题标签与发布前风险提示。

## 已实现功能

- `POST /api/v1/generations`，使用 `multipart/form-data` 上传单张图片。
- `GET /api/v1/generations`，读取最近成功生成的本地历史记录与受限图片预览状态。
- `GET /api/v1/generations/{generation_id}/image-preview`，按当前账号读取去元数据缩略图。
- `DELETE /api/v1/generations/{generation_id}`，按当前账号软删除一条成功记录。
- 工具首页包含功能介绍、使用流程、FAQ 和创作入口。
- 前端使用独立 URL 提供工具首页、生成工作台与历史记录，支持直达、刷新、前进/后退，以及历史缩略图、载回工作台、复制和确认删除。生成工作台会保留当前稿再生成新版本，并提供会话内的本地编辑与上一版对比；本地编辑不会自动回写历史记录。
- 可选的本地账号模式支持邮箱注册、登录、刷新恢复会话和退出；生成与历史按当前账号隔离。
- 当前结果与历史卡片中的图片理解摘要默认收起，可使用原生折叠控件展开查看。
- 支持 JPG、JPEG、PNG、WebP，以及单帧 HEIC/HEIF；HEIC/HEIF 会在服务端安全转换为 JPEG，
  并拒绝空文件、伪装格式、损坏图片、超限文件和异常尺寸。
- 自动处理 EXIF 方向、透明通道、颜色模式和大图等比例缩放。
- PaddleOCR-VL 尝试识别包装文字；OCR 失败或超时时自动降级为 Qwen3-VL 直接识图。
- Qwen3-VL 生成固定结构的 `image_summary`、`title`、`body`、`tags`。
- 输出 JSON 解析、有限重试、最长 20 字标题归一化和 3–5 个标签校验。
- 对未经图片证实的护肤功效、适用人群、使用感和明显品类冲突进行保守拦截。
- 支持关闭、轻量、丰富三档确定性 Emoji 风格；可从版本化本地目录补充相关标签，
  但不冒充实时热门榜，也不会额外调用模型。
- 使用版本化本地规则提示绝对化承诺、医疗健康、站外导流、诱导互动、虚假背书、
  竞品贬损和泛流量标签等发布风险；提示不等于小红书平台审核或限流预测。
- 生成期间显示与结果结构一致的骨架屏；未经完整结构与事实检查的模型片段不会提前展示。
  当前接口仍返回一次完整 JSON；骨架屏不是 SSE 文案流，避免把可能被后续校验或修复丢弃的片段先展示给用户。
- 统一处理模型失败、超时、输出无效和服务器内部错误，不向客户端暴露 Key 或堆栈。
- 成功和失败后都会清理上传及预处理临时文件。

成员 C 的数据库核心已经通过 B 的异步适配器接入应用生命周期。MySQL 持久化默认
关闭，未启用时继续使用 No-op；显式启用后若启动检查失败，应用会终止启动，不会假装
写库成功。B+C 持久化链路已在本机 MySQL 9.6 专用测试库完成真实验收。

## 环境要求

- Python 3.11 或更高版本
- Node.js 24 与 npm（前端开发和 CI 的验证基线）
- 可访问硅基流动 API
- 可选：支持 `docker compose` 的当前 Docker Desktop 或 Docker Engine（用于一键编排）
- 有权使用以下模型的硅基流动 API Key：
  - `Qwen/Qwen3-VL-8B-Instruct`
  - `PaddlePaddle/PaddleOCR-VL-1.5`

## 本地安装

### 安装后端

```bash
git clone https://github.com/drkiyira-dev/XHS-ai-content-generator.git
cd XHS-ai-content-generator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### 安装前端

```bash
npm --prefix frontend ci
cp frontend/.env.example frontend/.env
```

前端模板默认连接 `http://127.0.0.1:8000` 的真实 FastAPI；只有显式设置
`VITE_USE_MOCK=true` 时才使用浏览器内 Mock 数据。前端配置中不得放入硅基流动 API Key
或数据库密码。Mock 不执行真实识图；它按账号循环三套演示稿，以便验证重新生成和版本对比。

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
| `AUTH_ENABLED` | 是否显式挂载本地演示账号接口 | `false` |
| `AUTH_COOKIE_SECURE` | 是否使用仅 HTTPS 可用的 Secure 会话 Cookie | `false` |

安全要求：不得提交真实 `.env`、API Key、上传图片、日志或模型原始响应。项目的 `.gitignore` 已忽略这些内容。

## MySQL 持久化（默认关闭）

数据库开关关闭时，应用不会创建 Engine、连接 MySQL 或执行 SQL，Swagger 行为与此前
一致。启用前必须先准备：

- 已存在的专用数据库；
- 有限权限的非 `root` 用户；
- 已执行 [migrations/001_generation_records.sql](migrations/001_generation_records.sql)
  的 `generation_records` 表；
- 当前 owner-aware 持久层投入运行前，还需先备份旧历史，再依次显式执行
  [migrations/003_auth_tables.sql](migrations/003_auth_tables.sql) 创建 `users` 与
  `auth_sessions`，并执行
  [migrations/004_generation_ownership.sql](migrations/004_generation_ownership.sql)
  增加可空的 `generation_records.user_id`、外键和查询索引，随后执行
  [migrations/005_generation_previews_and_deletion.sql](migrations/005_generation_previews_and_deletion.sql)
  增加持久化受限预览与软删除字段，最后执行
  [migrations/006_generation_risk_snapshot.sql](migrations/006_generation_risk_snapshot.sql)
  增加可空的生成时风险快照；
- 账号演示时必须同步开启后端 `AUTH_ENABLED` 与前端 `VITE_AUTH_ENABLED`；
- 远程数据库还必须准备 CA 文件并配置 `DATABASE_TLS_CA`。

迁移文件不会创建、选择或删除数据库，只会在操作者已经明确选中的数据库中创建或变更
项目表结构。
运行时 `xhs_app` 账号只有 `SELECT`、`INSERT` 与 `UPDATE`，不能也不应执行 `ALTER`；
003–006 必须在停写并完成备份后，由具有明确 DDL 授权且已选定目标数据库的独立迁移会话执行，
验收结构无误后再恢复使用 `xhs_app` 启动应用。
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

## 账号接口（默认关闭）

账号核心与以下接口已为后续本地演示准备好：

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`

注册使用邮箱和密码，但演示版不发送激活邮件，也不会伪造已验证状态；响应中的
`email_verified` 固定为 `false`。原始会话令牌只写入 `HttpOnly` Cookie，不进入 JSON、
数据库或日志；数据库只保存 SHA-256 摘要。注册、登录和退出还要求固定
`X-XHS-CSRF: 1` 请求头，并启用本地单进程限流。

后端现已准备好账号归属边界：`AUTH_ENABLED=false` 时，生成与历史只访问明确的
`user_id IS NULL` 兼容分区；`AUTH_ENABLED=true` 时，生成记录只能写入当前 Cookie 会话
对应的账号，历史查询也只返回该账号的记录。请求不能通过表单、查询参数或请求头指定
`user_id`，旧的 NULL 记录不会自动归给首个注册用户，也不会出现在任何账号的历史记录中。

前端已经接入注册、登录、会话恢复与退出页面，但后端和前端模板仍默认关闭账号模式。
只有完成受控备份并依次执行 003/004/005/006 后，才可同时设置后端 `AUTH_ENABLED=true` 与前端
`VITE_AUTH_ENABLED=true`。任何尚未迁移的现有 MySQL 都不能直接运行 owner-aware 代码；
启动探针会在缺少可空 `user_id`、预览、软删除或 JSON 风险快照字段时固定失败，避免到首个请求才暴露旧
schema 错误。应用启动不会自动补表、迁移或修改旧数据。

迁移已经完成并准备进行本地账号演示时，分别在两个未跟踪配置文件中同步开启：

```dotenv
# 仓库根目录 .env
AUTH_ENABLED=true
AUTH_COOKIE_SECURE=false

# frontend/.env
VITE_AUTH_ENABLED=true
VITE_API_BASE_URL=http://127.0.0.1:8000
```

本地 HTTP 演示必须继续只监听 `127.0.0.1`。修改开关后需要重启后端与 Vite；不要把上述
本地 Cookie 配置用于局域网或公网。

### 迁移前备份旧历史

仓库提供离线工具 `scripts/backup_generation_history.py`，只读备份账号归属迁移前的
`generation_records` 原有 13 个字段。它不会备份账号表、数据库连接地址或图片文件，
也不会执行 DDL、分配 `user_id` 或修改任何记录。备份写入被 Git 忽略的
`.local-backups/`；每次结果是一个权限为 `0700` 的独立目录，其中数据与校验清单均为
`0600`。工具写完后会重新读取、核对行数与 SHA-256，再以目录原子改名发布。

执行前必须停止后端并保持数据库无写入；一致性快照无法覆盖快照开始后新增的记录。
确认停写后，在项目根目录运行：

```bash
XHS_HISTORY_BACKUP_CONFIRM=YES_BACKUP_XHS_AI_HISTORY \
  .venv/bin/python scripts/backup_generation_history.py
```

工具还会在终端要求逐项输入当前数据库名、快照记录数和 `BACKEND_STOPPED`；标准输入或
输出不是交互式终端时会拒绝运行。它只允许固定的 `.local-backups/` 输出位置，不接受
自定义路径，也不会覆盖已有备份。失败信息不会打印数据库 URL、凭据、SQL 参数或历史内容。
备份成功只代表旧数据已有可校验副本，不代表数据已经属于任何账号。本项目采用“归档”
策略：显式依次执行 003、004、005 与 006 后，旧记录继续保持 `user_id=NULL`，不会猜测归属、
更新或删除；新增的预览与软删除字段也保持 `NULL`；
旧记录的风险快照同样保持 `NULL`，历史接口会明确提示无法还原生成时检查结果，不会用
新版本规则伪装成旧记录当时的结论；
账号模式下它们对所有用户均不可见。真实备份与迁移必须在停止后端的同一维护窗口内另行
明确确认，本说明不会自行连接或改动当前 MySQL。

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

## 启动前端

保持后端运行，并在另一个终端中进入仓库根目录后执行：

```bash
npm --prefix frontend run dev -- --host 127.0.0.1
```

浏览器访问 <http://127.0.0.1:5173>。前端通过 `VITE_API_BASE_URL` 调用后端；默认地址已与
上面的 FastAPI 启动命令匹配。账号模式要求页面与 API 使用同一 hostname 体系，不要把
`localhost` 与 `127.0.0.1` 混用，否则 SameSite Cookie 可能无法发送。提交或交付前可运行
生产构建检查：

- 工具首页：<http://127.0.0.1:5173/>
- 生成工作台：<http://127.0.0.1:5173/app/generate>
- 历史记录：<http://127.0.0.1:5173/app/history>

Vite 开发服务器与 `vite preview` 可直接刷新这些地址。未来若使用其他静态服务器托管
`frontend/dist/`，需要将页面路由回退到 `index.html`，但必须让 `/api/` 继续由后端处理。

```bash
npm --prefix frontend run build
```

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
默认 `AUTH_ENABLED=false` 时仍是 NULL-owner 本地兼容模式；账号模式只应在迁移完成且
前后端开关同步开启后使用。

`127.0.0.1` 在容器中指向容器自身，不是宿主机 MySQL。本段因此明确保持数据库关闭；
如需同时启动后端与数据库，请使用下一节的 Compose 配置，不能用临时主机映射绕过
现有 MySQL/TLS 安全检查。

## Docker Compose 一键编排（增值功能）

仓库根目录的 `compose.yaml` 会启动两个服务：

- 官方 `mysql:8.4.11` 镜像，数据保存在命名卷 `mysql_data`；
- 当前仓库构建出的非 root FastAPI 后端镜像。

该 Compose 配置**只编排后端与 MySQL，不包含前端容器**。启动成功后，仍需按照上面的
“启动前端”步骤在宿主机运行 Vue 开发服务器；因此当前能力应描述为“后端与数据库一键
编排”，不能描述为“一条命令部署完整网站”。

先运行本地初始化脚本。脚本会隐藏输入 API Key、随机生成数据库密码，并创建四个相互
一致的文件；不会把任何 secret 打印到终端：

```bash
python scripts/initialize_compose_secrets.py
```

文件保存在已被 Git 忽略的 `.compose-secrets/`。不要复制、截图、提交或把其中内容发送到
聊天。宿主目录权限为 `0700`，文件为供非 root 容器只读 bind mount 使用的 `0444`；
其他宿主用户无法穿过该目录读取文件。目录非空时脚本会拒绝覆盖，避免意外轮换正在
使用的数据库密码。

检查并启动：

```bash
docker compose config --quiet
docker compose up --build --detach --wait
```

启动完成后访问 <http://127.0.0.1:8000/docs>。前端仍可在宿主机运行，并通过现有
`http://127.0.0.1:8000` 地址调用后端。

安全边界：

- API Key、数据库 URL 和两个数据库密码都使用只读 Compose file secrets，不写入镜像、
  Compose 环境变量或 Git；每个服务只挂载自己需要的 secret。
- 后端与 MySQL 使用 `network_mode: service:mysql` 共享同一个网络命名空间，双方连接的
  是该命名空间内真实的 `127.0.0.1`；MySQL 也只监听该地址。宿主机和其他容器均不发布
  或暴露 `3306`，未使用的 MySQL X Protocol 也被关闭。因此没有把服务名伪装成回环
  地址，也没有放宽非回环 MySQL 必须使用 CA 的既有规则。
- `8000` 只发布到宿主机 `127.0.0.1`。专用 `runtime` bridge 允许后端访问硅基流动，
  但不会向宿主机发布 MySQL。
- 两个服务都禁用新增 Linux capabilities、启用 `no-new-privileges` 和只读根文件系统；
  临时图片只写入后端 tmpfs。

首次使用空的 `mysql_data` 卷时，MySQL 官方入口只创建 `xhs_ai`，随后按顺序执行
`001_generation_records.sql`、`002_create_app_user.sh`、
`003_auth_tables.sql`、`004_generation_ownership.sql`、
`005_generation_previews_and_deletion.sql` 与 `006_generation_risk_snapshot.sql`。第二个脚本直接创建 `xhs_app`，
第一次授权就只有运行时实际需要的 `SELECT`、`INSERT`、`UPDATE`；不存在先授予 `ALL` 再撤销的
中断窗口。第三个迁移准备 `users` 与 `auth_sessions`，不会伪造邮箱激活状态；第四个迁移
为生成记录增加可空的账号外键和历史查询索引，不回填、删除或猜测旧记录归属。FastAPI
自身仍然只做启动检查，不执行 DDL。第五个迁移把去元数据、受限体积的历史预览保存在
MySQL 中，并增加可空软删除时间；旧记录的这些字段保持 `NULL`。现有数据卷必须先显式
执行 005。第六个迁移增加可空 JSON 风险快照，保存生成当时的规则版本与提示；旧记录
不回填。现有数据卷必须继续显式执行 006，才能重启使用新版 ORM，Compose 不会自动补跑迁移。

这些初始化脚本**只会在空数据卷上执行一次**。现有卷不会自动重放迁移；后续 schema
变更必须使用单独、明确审批的迁移流程。若首次初始化中断或失败，应查看固定错误日志并
删除这个尚未投入使用的失败卷后重新初始化；不得继续使用部分初始化的卷。普通停止会
保留历史数据：

```bash
docker compose down
```

下面的命令会永久删除 Compose 数据库和全部生成历史，只能在明确确认不再需要数据时执行：

```bash
docker compose down --volumes
```

不要在保留 `mysql_data` 的同时删除并重新生成 `.compose-secrets/`，否则新密码会与卷内
既有账号不一致。若 secret 丢失，应恢复原文件；只有决定清空全部 Compose 数据时，才先
明确执行 `down --volumes`，再重新初始化 secrets。

## API 请求

请求类型：`multipart/form-data`

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `image` | 是 | 单张 JPG、JPEG、PNG、WebP 或单帧 HEIC/HEIF 图片，模板限制为 10MB；HEIC/HEIF 会转换为 JPEG 后送入模型 |
| `product_name` | 否 | 主题或名称提示；仅用于调整生成方向，与图片冲突时以图片为准 |
| `target_audience` | 否 | 目标读者提示；不能作为适用人群的事实证据 |
| `tone` | 否 | 表达风格提示；不能作为图片事实或产品属性的证据 |
| `emoji_level` | 否 | `off`、`light` 或 `expressive`；API 默认 `off`，只确定性调整标题和正文，不改图片理解摘要 |
| `related_tags` | 否 | `true` 或 `false`；API 默认 `false`，只从版本化本地目录补充相关标签，不读取实时热榜 |

curl 示例：

```bash
curl -X POST 'http://127.0.0.1:8000/api/v1/generations' \
  -H 'accept: application/json' \
  -F 'image=@/absolute/path/example.jpg;type=image/jpeg' \
  -F 'product_name=玫瑰果卸妆油' \
  -F 'target_audience=大学生' \
  -F 'tone=轻松自然' \
  -F 'emoji_level=light' \
  -F 'related_tags=true'
```

所有可选字段都可以省略。为保持旧客户端行为，API 在未提交增强字段时不添加 Emoji 或
相关标签；当前前端工作台默认选择轻量 Emoji 并开启相关标签，用户可以随时关闭。

## 成功响应

成功状态码：`200 OK`

```json
{
  "generation_id": "550e8400-e29b-41d4-a716-446655440000",
  "image_summary": "浅粉色瓶身搭配浅色标签，标签可见品牌、品类和容量信息。",
  "title": "玫瑰果卸妆油开箱",
  "body": "浅粉色瓶身搭配简约标签，包装上可见 CLEANSING OIL、ROSE HIP 和 100 ML。",
  "tags": ["#卸妆油", "#玫瑰果", "#粉色包装", "#护肤分享"],
  "created_at": "2026-08-07T07:39:32.921950Z",
  "risk_assessment": {
    "rule_version": "content-risk-hints-2026-08-14.2",
    "findings": []
  }
}
```

`generation_id` 和 `created_at` 每次请求都会变化。`risk_assessment` 是本地、非权威的
发布前提示：没有提示不代表已通过平台审核，出现提示也不等于内容一定会被处罚或限流。
接口会在完整模型结果通过结构与事实检查并成功保存后一次性返回；页面的加载动画不是
未经审核的模型 Token 流。

生成字段采用统一的后端硬边界：`image_summary` 最多 2,000 个字符，`title` 最多 20 个字符，
`body` 最多 10,000 个字符，单个规范化标签最多 100 个字符（包含前导 `#`），标签总数为
3–5 个。模型解析、成功写库与历史读取都会复验这些限制。

## 历史记录接口（兼容模式与账号隔离）

前端顶部的“历史记录”入口会在用户主动进入时读取最近成功结果，不会触发图片上传、
OCR 或 Qwen 调用。页面展示受限缩略图、图片理解摘要、标题、正文、话题标签和本地格式化
时间，并允许载回工作台、复制或删除单条记录；不会展示本地图片路径、用户输入、失败原因
或数据库内部字段。

```http
GET /api/v1/generations?limit=20
```

- 只返回 `success` 记录，按创建时间从新到旧排列。
- `limit` 默认为 `20`，允许范围为 `1` 到 `50`。
- 响应沿用生成结果的 `generation_id`、`image_summary`、`title`、`body`、
  `tags`、`created_at`、`risk_assessment` 字段，每项增加 `has_image_preview` 与受控的
  `image_preview_url`，顶层额外返回 `count`。
- `risk_assessment` 返回生成成功时持久化的规则版本与提示；规则升级不会静默重写旧记录。
  006 以前的 `NULL` 记录会明确返回“无法还原生成时快照”的提示，而不是按新规则冒充重算结果。
- 不返回本地图片路径、用户输入、失败原因或数据库内部字段。
- 响应带有 `Cache-Control: no-store`，避免浏览器或代理缓存生成文案。
- `DATABASE_ENABLED=false` 时 No-op 持久化不会保存数据，因此历史固定为空。
- `AUTH_ENABLED=false` 时只读取 `user_id IS NULL` 的本地兼容分区，不会混入账号记录。
- `AUTH_ENABLED=true` 时必须先登录，且只读取当前会话账号的记录；旧 NULL 归档记录不可见。

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
      "created_at": "2026-08-07T07:39:32.921950Z",
      "risk_assessment": {
        "rule_version": "content-risk-hints-2026-08-14.2",
        "findings": []
      },
      "has_image_preview": true,
      "image_preview_url": "/api/v1/generations/550e8400-e29b-41d4-a716-446655440000/image-preview"
    }
  ],
  "count": 1
}
```

新生成记录会保存最长边 480px、去元数据且不超过 256KiB 的 WebP 预览。预览读取与软删除
始终按当前 owner 分区过滤；软删除会清除该记录的文案和预览内容，重复删除保持幂等。
旧记录没有预览时仍可载回并查看文案，只显示“暂无图片预览”占位。

模板与本地 `.env` 仍默认使用 `AUTH_ENABLED=false` 的 NULL-owner 兼容模式；账号演示时
必须在完成受控数据库迁移后，再同步开启后端 `AUTH_ENABLED` 与前端
`VITE_AUTH_ENABLED`。即使账号读写隔离与前端会话闭环均已具备，本项目的 Cookie、内存
限流与运维边界仍只为本地测试和演示设计，不得直接绑定 `0.0.0.0` 暴露到局域网或公网。

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
  → 可选的确定性 Emoji / 相关标签增强
  → 结构与事实规则复验
  → 生成并保存版本化发布风险快照
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
- 图片理解摘要、正文和单标签长度边界，以及生成时风险快照、旧记录兼容和损坏快照拒绝。
- 前端路由、账号隔离、Mock 增强、loopback API 边界、按需组件加载和真实 Chrome 主流程。

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

CI 包含五个互相独立的任务：

- Python 3.12：安装后端开发依赖并运行全部 `pytest`。
- Node.js 24：使用 `npm ci` 按锁文件安装前端依赖，并分别验证兼容模式与账号模式的生产构建。
- Chrome：使用固定模型替身和临时 SQLite，真实验证 HttpOnly Cookie、上传骨架、风险提示、
  历史缩略图、载回、删除与退出；不会读取根 `.env`、连接开发者 MySQL 或调用第三方模型。
- Docker：构建后端镜像，以非 root、只读文件系统、无网络和无 Linux capabilities 的
  容器执行 `/api/health` 冒烟检查；只使用无效占位 Key，不连接模型或 MySQL。
- Docker Compose：使用临时占位 Key、随机数据库密码、内部无外网 bridge 和全新命名卷，
  实际启动 MySQL 与后端；验证非 root/只读/capability 边界、secret 不进入容器环境变量、
  3306 不发布、应用账号只有 `SELECT`/`INSERT`/`UPDATE`、持久化写读及容器重建后数据仍在，
  最后只删除该次 CI project 的容器、网络、测试记录和数据卷。

该 workflow 只有仓库内容读取权限，不使用真实硅基流动 Key、不连接任何外部 MySQL、
不登录镜像仓库、不推送镜像、不部署应用，也不会自动修改或合并 PR。重复推送同一分支
时，较旧的运行会被取消。

## 团队整合状态与后续工作

当前 `main` 已通过 PR 完成 A、B、C 三部分的整合：

- 成员 A 的 Vue 页面默认调用真实 FastAPI，包含上传、Loading、结果展示、复制和重试状态。
- 成员 B 的多模态生成、事实安全、历史查询及 HEIC/HEIF 后端转换已经接入。
- 成员 C 的数据库职责已通过异步 SQLAlchemy 适配器进入同一生成生命周期。
- 前端允许上传 HEIC/HEIF；浏览器无法原生预览时显示文件占位说明，后端只接受单帧文件并执行最终安全校验与 JPEG 转换。
- 本地账号注册、登录、会话恢复与退出已接入前端；账号模式下生成和历史按用户隔离。
- 工具首页、生成工作台与历史记录已拆成 Vue Router 页面；共享工作区只保留在当前页面内存，账号边界变化时会统一清空。

后续增值功能仍通过独立功能分支和 Pull Request 进入 `main`；禁止直接推送 `main`。
