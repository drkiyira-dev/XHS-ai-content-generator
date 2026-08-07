"""数据库层端到端测试。

运行：
    1) 配置 .env（可选，默认 SQLite）
    2) python -m backend.tests.test_database  或  python backend/tests/test_database.py

注意：
    - 本测试使用临时 SQLite 文件，不依赖真实 MySQL，也不产生任何持久化业务数据。
    - 真实 MySQL 验收时，将环境变量 DATABASE_URL=mysql+pymysql://... 运行同一份脚本即可。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestDatabaseWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xhs_db_test_")
        self.db_path = os.path.join(self.tmp, "test.db")
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path}"
        # 为避免和全局状态冲突，先清掉 SQLAlchemy singleton
        import importlib
        import backend.db
        importlib.reload(backend.db)

        from flask import Flask
        from backend.db.config import Config
        from backend.db import init_database
        app = Flask(__name__)
        app.config.from_object(Config)
        init_database(app)
        self.app = app
        self._push = self.app.app_context()
        self._push.push()

    def tearDown(self):
        from backend.db import db
        try:
            db.session.remove()
        finally:
            pass
        try:
            self._push.pop()
        finally:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------- helpers
    def ctx(self):
        return self.app.app_context()

    # ------- tests
    def test_01_create_pending(self):
        from backend.db import create_pending, list_records
        with self.ctx():
            r = create_pending(image_path="/tmp/a.jpg", user_input="夏日穿搭")
            self.assertTrue(r.task_id.startswith("gen_"))
            self.assertEqual(r.status, "pending")
            self.assertEqual(len(list_records()), 1)
            self._gen_id = r.task_id

    def test_02_mark_success_force_validation(self):
        from backend.db import create_pending, mark_success, TASK_STATUS_SUCCESS
        from backend.schemas import BusinessException, ErrorCode
        with self.ctx():
            r = create_pending()

            # 标题超 20 字 应被拦截
            with self.assertRaises(BusinessException) as cm:
                mark_success(
                    r.task_id,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦",
                    content="正文",
                    tags=["a","b","c","d"],
                    image_description="描述",
                )
            self.assertEqual(cm.exception.code, ErrorCode.VALIDATION_ERROR)

            # 合规输入：标记成功，标签会去重补 #，数量 3~5
            ok = mark_success(
                r.task_id,
                title="夏日穿搭分享",
                content="今天分享三套夏日 look，显瘦又出片！",
                tags=["夏日","穿搭","#穿搭","OOTD","夏日","每日"],
                image_description="阳光下的白色连衣裙",
            )
            self.assertEqual(ok.status, TASK_STATUS_SUCCESS)
            self.assertTrue(ok.title and len(ok.title) <= 20)
            self.assertTrue(ok.content)
            self.assertTrue(3 <= len(ok.tags) <= 5)
            for t in ok.tags:
                self.assertTrue(t.startswith("#"))

    def test_03_mark_failed(self):
        from backend.db import create_pending, mark_failed, TASK_STATUS_FAILED
        from backend.schemas import ErrorCode
        with self.ctx():
            r = create_pending(image_path="/tmp/bad.png")
            failed = mark_failed(
                r.task_id,
                error_code=ErrorCode.IMAGE_PROCESS_ERROR,
                error_message="图片损坏",
            )
            self.assertEqual(failed.status, TASK_STATUS_FAILED)
            self.assertEqual(failed.error_code, ErrorCode.IMAGE_PROCESS_ERROR)
            self.assertEqual(failed.error_message, "图片损坏")

    def test_04_persistence_after_restart(self):
        from backend.db import create_pending, mark_success, get_record, TASK_STATUS_SUCCESS
        from backend.db import db

        r = create_pending(user_input="持久化测试")
        tid = r.task_id
        mark_success(
            tid,
            title="标题",
            content="正文正文正文正文正文",
            tags=["a","b","c","d"],
            image_description="好图",
        )
        db.session.remove()
        self._push.pop()

        # 模拟「重启」：重新建 Flask app，SQLAlchemy 引擎
        from flask import Flask
        from backend.db.config import Config
        from backend.db import init_database
        app2 = Flask(__name__)
        app2.config.from_object(Config)
        init_database(app2)
        push2 = app2.app_context()
        push2.push()
        try:
            again = get_record(tid)
            self.assertIsNotNone(again)
            self.assertEqual(again.status, TASK_STATUS_SUCCESS)
            self.assertEqual(again.user_input, "持久化测试")
            self.assertTrue(again.title and again.content and again.tags)
        finally:
            try:
                from backend.db import db as db2
                db2.session.remove()
            finally:
                pass
            push2.pop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
