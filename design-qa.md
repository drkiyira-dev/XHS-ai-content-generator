# 生成工作台设计 QA

## 对照基线

- Source visual truth: `docs/qa/generation-workspace-reference-1488x1058.png`
- Implementation screenshot: `docs/qa/generation-workspace-implementation-1488x1058.png`
- Full-view comparison: `docs/qa/generation-workspace-comparison.png`
- Focused result-editor comparison: `docs/qa/generation-workspace-focus-comparison.png`
- Mobile evidence: `docs/qa/generation-workspace-mobile-390x844.png`
- Route: `/app/generate`
- State: 免登录 Mock；已有图片与当前结果；提示词在生成后被修改；上一版本抽屉打开；风险提示为 0 项。
- Source pixels: `1488 × 1058`
- Implementation pixels: `1488 × 1058`
- CSS viewport: `1488 × 1058`
- Device pixel ratio: `1`
- Density normalization: 无需缩放；两张主图按相同像素、相同 CSS 视口和相同页面状态并排比较。

## Findings

没有剩余可执行的 P0、P1 或 P2 问题。

实现保留了参考稿的主要信息结构：左侧创作条件、中间可编辑结果、右侧上一版本；风险提示位于复制和编辑之前；重新生成不会先清空旧稿。实际产品约束与参考视觉存在少量有意差异，例如标题仍使用后端真实的 20 字上限、正文保留 10000 字上限，属于合同约束而不是视觉偏差。

## 必查设计面

- 字体与排版：参考图与实现都使用系统中文无衬线栈。标题、分区标题、标签和辅助说明的字重层次一致；长正文的行高与输入区高度足够，没有截断或拥挤。实现中的计数文字略收敛，但仍清晰可读。
- 间距与布局节奏：桌面三栏比例、卡片半径、分割线、留白和按钮层级与参考方向一致。左栏保持创作上下文，中栏承担主任务，版本抽屉不会压住编辑控件。`900px` 与 `390px` 检查均满足 `scrollWidth === clientWidth`。
- 颜色与视觉 token：暖白背景、深红主按钮、浅粉风险表面和浅黄“设置已修改”提示与参考稿映射一致；语义颜色没有只靠颜色传达状态。
- 图片质量与资产：实现使用仓库内真实 WebP 风景图，比例稳定、无拉伸、无透明边缘或压缩伪影。Logo 与操作图标使用 Element Plus 图标库，没有用文字、Emoji、手绘 SVG 或 CSS 图形代替可见资产。
- 文案与内容：界面明确区分“当前编辑稿”“上一版”“生成时风险快照”和“当前设置已修改”。风险提示说明不代表平台审核，也不能预测限流或处罚；重新生成失败会保留当前稿件。
- 交互与状态：结果可本地编辑和复制；编辑不会回写历史；重新生成期间旧稿继续可见；新结果成功后才轮换当前/上一版本；历史载回会标记为历史来源并要求重新上传图片后才能再生成。
- 无障碍：结果与风险区使用命名区域，标题/正文/标签有明确可访问名称，风险定位按钮可聚焦对应字段，移动端无水平溢出，并保留减少动态效果支持。

## Full-view comparison evidence

`docs/qa/generation-workspace-comparison.png` 将同尺寸参考图与浏览器实现放在同一张对照图中。整体构图、主次关系、三栏边界、顶部导航、按钮位置和信息密度均保持一致。实现根据真实数据增加了图片理解摘要折叠区和完整标签编辑控件，但没有改变主要任务层级。

## Focused region comparison evidence

`docs/qa/generation-workspace-focus-comparison.png` 对照了结果编辑区。风险提示均位于复制与编辑字段之前；标题、正文和标签的编辑层级一致；实现额外保留“检查详情”和真实字段上限。该局部足以检查字体、边框、控件间距、风险层级和正文密度，因此无需再追加更小的像素级裁切。

## Responsive and browser evidence

- `390 × 844`：导航纵向重排，上传图与输入占满可用宽度，`clientWidth=390`、`scrollWidth=390`。
- `900 × 1000`：左右区域按断点堆叠，`clientWidth=900`、`scrollWidth=900`。
- 浏览器控制台只有 Vite 连接调试信息，没有 error 或 warning。
- 当前本地预览使用 Mock 数据，不会把 QA 图片发往第三方模型。

## Primary interactions tested

- 上传图片后生成，并在初次请求时显示结果骨架。
- 编辑提示词后出现“设置已修改”，当前结果仍保留。
- 编辑标题后复制当前编辑稿，不修改服务端结果快照。
- 重新生成期间保留旧稿；成功后自动形成上一版本并可复制、展开和关闭。
- 历史记录不包含本地未保存编辑；载回后不能在没有新图片时直接重新生成。
- 账号 Cookie、风险提示、缩略图、载回、删除和退出由隔离 FastAPI + SQLite 的真实 Chrome E2E 覆盖。

## Comparison history

- Pass 1: 同尺寸全图与结果编辑区局部对照未发现 P0/P1/P2 问题，因此没有产生修复后重拍迭代。
- 实施阶段已经在进入本次 QA 前完成：风险前置、服务端快照与本地草稿分离、非破坏式重新生成、上一版本抽屉和响应式布局。

## Open Questions

- 无视觉交付阻断。真实第三方模型的文案长度和失败时长不属于本次 Mock 视觉对照；API 长度边界与失败保留行为由自动化合同覆盖。

## Implementation Checklist

- [x] 风险提示位于复制和编辑动作之前。
- [x] 当前结果与上一版本独立，只有成功响应才轮换。
- [x] 编辑稿不冒充生成时风险快照，也不自动回写历史。
- [x] 重新生成加载期继续显示旧稿。
- [x] 桌面、平板、390px 移动端无水平溢出。
- [x] 双模式生产构建、全量 Python 测试与真实 Chrome E2E 通过。
- [x] 浏览器渲染证据与同尺寸对照图已保存到仓库 QA 目录。

## Follow-up Polish

- 可选 P3：以后若加入更多版本，可把当前“上一版”抽屉扩展为版本时间线；当前双版本模型已满足本轮范围。

final result: passed
