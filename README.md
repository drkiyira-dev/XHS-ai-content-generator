# XHS AI Content Generator

基于 FastAPI、PaddleOCR-VL 和 Qwen3-VL 的单图小红书文案生成后端。接口接收一张图片和可选的商品名、目标人群、语气要求，返回图片概述、标题、正文与标签。

## 已实现功能

- `POST /api/v1/generations`，使用 `multipart/form-data` 上传单张图片。
- 支持 JPG、JPEG、PNG、WebP；拒绝空文件、伪装格式、损坏图片、超限文件和异常尺寸。
- 自动处理 EXIF 方向、透明通道、颜色模式和大图等比例缩放。
- PaddleOCR-VL 尝试识别包装文字；OCR 失败或超时时自动降级为 Qwen3-VL 直接识图。
- Qwen3-VL 生成固定结构的 `image_summary`、`title`、`body`、`tags`。
- 输出 JSON 解析、有限重试、最长 20 字标题归一化和 3–5 个标签校验。
- 对未经图片证实的护肤功效、适用人群、使用感和明显品类冲突进行保守拦截。
- 统一处理模型失败、超时、输出无效和服务器内部错误，不向客户端暴露 Key 或堆栈。
- 成功和失败后都会清理上传及预处理临时文件。

当前尚未接入成员 C 的 MySQL 持久化接口，因此生成结果暂不写入数据库。

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

安全要求：不得提交真实 `.env`、API Key、上传图片、日志或模型原始响应。项目的 `.gitignore` 已忽略这些内容。

## 启动后端

激活虚拟环境后运行：

```bash
python -m uvicorn backend.main:app --reload
```

默认地址：

- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- 生成接口：`POST http://127.0.0.1:8000/api/v1/generations`

## API 请求

请求类型：`multipart/form-data`

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `image` | 是 | 单张 JPG、JPEG、PNG 或 WebP 图片，模板限制为 10MB |
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

B 侧已经预留 `DATABASE_ERROR`；当前仍使用 No-op 持久化实现，需在成员 C
适配器接入后完成真实 MySQL 失败验证。

## 处理流程

```text
上传图片
  → 文件类型、魔数、体积、解码和尺寸校验
  → EXIF、透明通道、RGB 与缩放预处理
  → create_pending（当前为 No-op）
  → PaddleOCR-VL（失败时安全降级）
  → Qwen3-VL 图片理解与文案生成
  → JSON 解析、字段归一化与有限重试
  → 事实安全校验
  → mark_success / mark_failed（当前为 No-op）
  → 返回固定 API 响应
```

正常情况下会调用一次 OCR 和一次 Qwen。只有 Qwen 首次输出需要修复时，才会使用唯一一次 Qwen 重试。

## 运行测试

```bash
python -m pytest
```

当前测试覆盖：

- API 请求字段、二进制上传和 CORS。
- 配置验证及敏感信息隐藏。
- 图片格式、魔数、体积、解码、尺寸和多文件拒绝。
- EXIF 方向、透明图片、大图缩放、UUID 临时文件和清理。
- OCR 降级、Qwen 请求、总超时、取消、有限重试和错误映射。
- JSON/schema 归一化、长标题处理和事实安全校验。

真实 API 测试会产生模型调用费用；自动化测试默认使用 Mock，不会调用硅基流动。

## 当前范围与后续工作

当前版本只完成成员 B 的独立生成后端。以下内容仍待团队联调：

- 接入成员 C 对 `GenerationPersistence` 的适配实现；适配器必须复用 B 生成的
  `generation_id`，并在 `mark_success` 内完成 C 的 `validate_copy` 与数据库写入。
- 使用真实 MySQL 验证 `DATABASE_ERROR`、事务回滚和状态一致性。
- 由成员 A 接入真实上传页、Loading、结果展示和复制按钮。
- 完成商品、食物、风景、带文字图片及异常路径的团队验收记录。
- 通过 Pull Request 将 `feat/b-generation-api` 合并到 `develop`；禁止直接推送 `main`。
