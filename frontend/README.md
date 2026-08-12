# 图文种草助手 · 前端

本项目是「图文种草助手」的前端部分（成员 A 负责），基于 Vue 3 + TypeScript + Vite + Element Plus 构建。

首屏提供工具介绍、功能亮点、四步使用流程、FAQ 和创作入口。可选的本地账号模式提供注册、登录、会话恢复与退出；启用后，生成和历史入口只对已登录账号开放，历史记录由后端按账号隔离。首页、生成工作台和历史记录使用独立 URL；同一账号在路由间切换时，未提交的工作状态仍保存在当前页面内存中。退出、会话失效或切换账号时会主动清除图片、输入、结果和历史缓存，避免跨账号残留。

## 技术栈

- **Vue 3** + `<script setup>` 组合式 API
- **TypeScript** 类型安全
- **Vite** 构建工具
- **Element Plus** UI 组件库
- **Vue Router 4** 页面路由与私有页面导航守卫

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
VITE_AUTH_ENABLED=false
```

开发阶段如需切换后端地址，修改 `.env` 即可，不要提交 `.env` 文件到 Git。
账号模式必须与后端根目录 `.env` 的 `AUTH_ENABLED` 同步：只有数据库已完成
`003_auth_tables.sql` 与 `004_generation_ownership.sql` 后，才可把两端都设为 `true`。
前端从不保存会话令牌；浏览器只使用后端设置的 `HttpOnly` Cookie。

### 3. 启动开发服务器

```bash
npm run dev -- --host 127.0.0.1
```

浏览器固定访问 <http://127.0.0.1:5173>。可直接打开以下页面：

- 工具首页：<http://127.0.0.1:5173/>
- 生成工作台：<http://127.0.0.1:5173/app/generate>
- 历史记录：<http://127.0.0.1:5173/app/history>

Vite 开发服务器和 `vite preview` 支持这些路径的直接刷新。若以后把 `dist/` 交给其他
静态服务器，必须将非文件页面请求回退到 `index.html`，同时不得让 `/api/` 请求落入
SPA 回退。账号模式下不要把前端打开为 `localhost`、
同时把 API 指向 `127.0.0.1`；两端 hostname 不一致会使 SameSite Cookie 无法可靠发送。

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
│   ├── App.vue             # 持久应用外壳、共享工作状态与跨账号界面清理
│   ├── components/
│   │   ├── AuthDialog.vue  # 注册 / 登录对话框（密码仅保存在临时状态）
│   │   └── LandingPage.vue # 工具介绍、功能亮点、流程、FAQ 与入口
│   ├── views/              # 工具首页、生成工作台与历史记录路由页面
│   ├── state/              # 会话单例与工作区依赖契约
│   ├── router.ts           # 公开/私有路由、会话恢复和安全 next 白名单
│   ├── main.ts             # 应用与 Router 入口
│   └── services/
│       ├── api.ts          # 本地 API、Cookie / CSRF 与安全错误边界
│       ├── auth.ts         # 注册、登录、会话恢复、退出
│       └── generation.ts   # 小红书内容生成与历史记录接口
├── .env.example            # 环境变量模板
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

## 功能说明

- **工具首页**：工具介绍、功能亮点、使用流程、FAQ，以及进入生成工作台的 CTA。
- **独立路由**：`/`、`/app/generate`、`/app/history` 支持地址栏直达、刷新和前进/后退。
- **本地账号**：邮箱自由注册、登录、刷新恢复会话和退出；演示版不发送激活邮件，邮箱保持未验证状态。
- **工作台保护**：账号模式下，生成与历史只允许已登录用户进入；身份只来自 HttpOnly Cookie，前端不发送 `user_id`。
- **图片上传**：支持 JPG、JPEG、PNG、WebP 以及单帧 HEIC/HEIF，最大 10MB。
- **前端校验**：校验文件扩展名、MIME 类型、文件大小及文件头；HEIC/HEIF 使用有界 `ftyp` 品牌检查并由后端转换为 JPEG。
- **可选提示**：主题或名称、目标读者、表达风格；接口字段继续使用 `product_name`、`target_audience`、`tone`。
- **生成结果**：展示图片理解摘要、标题、正文和话题标签。
- **摘要折叠**：当前结果与历史卡片中的图片理解摘要默认收起，使用原生键盘可操作控件展开。
- **加载与错误状态**：请求过程中禁用提交按钮，遇到错误展示中文提示。
- **历史记录**：主动进入后读取当前账号最近 20 条成功记录，支持缩略图/占位、载回工作台、复制、刷新和确认删除。
- **状态隔离**：同一账号切换路由时由应用外壳保留工作状态；退出、会话失效或账号变化会清除私有界面数据，并丢弃旧请求的迟到响应。工作区不写入浏览器持久存储，刷新页面会清空未提交草稿。

## 接口说明

`src/services/generation.ts` 默认通过 `POST /api/v1/generations` 调用真实后端。只有显式设置
`VITE_USE_MOCK=true` 时才使用本地 Mock 数据；接口字段保持 snake_case：

```json
{
  "generation_id": "uuid",
  "image_summary": "图片理解摘要",
  "title": "生成标题",
  "body": "生成正文",
  "tags": ["#标签1", "#标签2"],
  "created_at": "2024-01-01T00:00:00Z"
}
```

历史页通过 `GET /api/v1/generations?limit=20` 读取本地 MySQL 中最近成功的结果，并使用
owner-scoped `GET /api/v1/generations/{generation_id}/image-preview` 显示缩略图；删除使用
`DELETE /api/v1/generations/{generation_id}`。该列表请求不会触发 OCR 或 Qwen；Mock 模式下
只展示当前浏览器会话内生成的 Mock 记录，并在浏览器内生成受限预览。
同时启用账号与 Mock 时，Mock 身份和历史也按规范化邮箱隔离；它们只存在于当前页面内存，刷新即清空。
数据库持久化未启用时，真实接口会返回安全的空历史状态。

账号模式使用以下接口：

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`

注册成功即登录，但 `email_verified` 保持 `false`。注册、登录、退出和生成请求发送固定
`X-XHS-CSRF: 1`，账号模式下所有真实请求都使用 `credentials: 'include'`。原始 Cookie
不可由 JavaScript 读取，也不会进入响应 JSON、localStorage 或 sessionStorage。

## 注意事项

- API Key（`SILICONFLOW_API_KEY`）仅由后端管理，前端不保存、不使用。
- 点击生成后，图片会由后端发送到已配置的第三方视觉模型服务；请勿上传敏感或无授权图片，并在发布前核对 AI 输出。
- 本地账号与记录归属仅用于 `127.0.0.1` 测试和演示；仍缺少公网所需的邮件激活、HTTPS、密码找回和多实例限流，不得直接公开部署。
- 上传图片时使用 `FormData`，不手动设置 `Content-Type`，由浏览器自动生成 multipart boundary。
- `.env`、`.vscode/`、`node_modules/`、`dist/` 已加入 `.gitignore`，不要提交到仓库。
