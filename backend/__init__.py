"""Backend package.

成员 C 仅负责以下子模块：
- backend/db/           ORM 模型 + repository + 数据库初始化入口
- backend/schemas/      统一响应结构、错误码、业务异常
- backend/validation/   文案规则校验：validate_copy、标签去重补#
"""
