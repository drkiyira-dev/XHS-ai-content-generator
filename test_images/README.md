# 测试图片目录

本目录用于存放测试图片，共需准备：

## 一、合法测试图（5~10 张）
放在 `valid/` 目录下，建议：
- 风景照、人物照、美食照、产品照、插画等各 1~2 张
- 均为常见格式：.jpg / .jpeg / .png
- 文件大小控制在 5MB 以内（可上传的正常范围）

示例文件名：
- valid/scenery_01.jpg
- valid/food_02.png
- valid/product_03.jpeg
- ...

## 二、失败样例（不要放入 valid，建议放在 invalid/）

### 1. 伪扩展名（扩展名与内容不匹配）
- invalid/fake_image.png.txt  （把 .txt 改名为 .png，再放进来测试）
  即：invalid/fake.png  →  实际内容是文本文件

### 2. 损坏图
- invalid/corrupt.jpg  →  随意截断或编辑损坏的 jpg 文件（二进制残缺）

### 3. 超限图
- invalid/huge_image.jpg  →  超过 10MB（或你设置的上限）的大图

> 说明：以上失败样例用于验收：
> - 伪扩展名：服务应返回 PARAMS_ERROR，不应崩溃
> - 损坏图：后续识图接口应返回 IMAGE_PROCESS_ERROR，任务被 mark_failed，记录到 MySQL
> - 超限图：上传接口应直接拒绝，返回错误码
