# 小红书文案生成平台 · 前端

本项目是「图片小红书文案生成平台」的前端部分（成员 A 负责），基于 Vue 3 + TypeScript + Vite + Element Plus 构建。

用户上传一张商品图片，填写可选参数（商品名称、目标受众、语气），前端将图片与参数发送到后端，后端调用多模态模型生成小红书风格文案，前端展示生成的标题、正文、标签和图片摘要。

## 技术栈

- **Vue 3** + `<script setup>` 组合式 API
- **TypeScript** 类型安全
- **Vite** 构建工具
- **Element Plus** UI 组件库

## 快速开始

### 1. 安装依赖

```bash
cd frontend
npm install
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

默认指向本地后端：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

开发阶段如需切换后端地址，修改 `.env` 即可，不要提交 `.env` 文件到 Git。

### 3. 启动开发服务器

```bash
npm run dev
```

默认打开 http://localhost:5173。

### 4. 构建生产版本

```bash
npm run build
```

构建产物输出到 `frontend/dist/`。

## 项目结构

```
frontend/
├── public/                 # 静态资源
├── src/
│   ├── App.vue             # 主页面：生成工作台、结果展示、历史记录
│   ├── main.ts             # 应用入口
│   └── services/
│       └── generation.ts   # 小红书文案生成接口服务层
├── .env.example            # 环境变量模板
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

## 功能说明

- **图片上传**：支持 JPG、JPEG、PNG、WebP 以及单帧 HEIC/HEIF，最大 10MB。
- **前端校验**：校验文件扩展名、MIME 类型、文件大小及文件头；HEIC/HEIF 使用有界 `ftyp` 品牌检查并由后端转换为 JPEG。
- **可选参数**：商品名称、目标受众、语气。
- **生成结果**：展示标题、正文、标签和图片摘要。
- **加载与错误状态**：请求过程中禁用提交按钮，遇到错误展示中文提示。
- **历史记录**：主动进入后读取最近 20 条成功记录，支持空状态、错误重试、刷新和单条复制。

## 接口说明

`src/services/generation.ts` 默认通过 `POST /api/v1/generations` 调用真实后端。只有显式设置
`VITE_USE_MOCK=true` 时才使用本地 Mock 数据；接口字段保持 snake_case：

```json
{
  "generation_id": "uuid",
  "image_summary": "图片摘要",
  "title": "生成标题",
  "body": "生成正文",
  "tags": ["#标签1", "#标签2"],
  "created_at": "2024-01-01T00:00:00Z"
}
```

历史页通过 `GET /api/v1/generations?limit=20` 读取本地 MySQL 中最近成功的结果。
该请求不会触发 OCR 或 Qwen；Mock 模式下只展示当前浏览器会话内生成的 Mock 记录。
数据库持久化未启用时，真实接口会返回安全的空历史状态。

## 注意事项

- API Key（`SILICONFLOW_API_KEY`）仅由后端管理，前端不保存、不使用。
- 历史页目前仅限 `127.0.0.1` 本地单用户演示；没有鉴权与记录所有权隔离时不得公开部署。
- 上传图片时使用 `FormData`，不手动设置 `Content-Type`，由浏览器自动生成 multipart boundary。
- `.env`、`.vscode/`、`node_modules/`、`dist/` 已加入 `.gitignore`，不要提交到仓库。
