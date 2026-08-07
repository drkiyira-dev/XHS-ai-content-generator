"""数据库层端到端测试（FastAPI + 纯 SQLAlchemy 2.x，Unittest 风格）。

运行：
    python -m backend.tests.test_database -v
    或：python backend/tests/test_database.py

默认：临时 SQLite 文件，一键通过；不依赖本机 MySQL daemon，不产生任何持久化业务数据。
真实 MySQL 验收：设置 MYSQL_TEST_URL（参考 run_selfcheck.py 文档）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from backend.db import (
    close_global_engine_session,
    create_pending,
    get_record,
    init_database,
    list_records,
    mark_failed,
    mark_success,
    new_session,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCESS,
)
from backend.db.config import Settings
from backend.schemas import BusinessException, ErrorCode


class TestDatabaseWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xhs_db_test_")
        self.db_path = Path(self.tmp) / "test.db"
        self.settings = Settings(
            DATABASE_URL=f"sqlite:///{self.db_path}",
            MYSQL_DATABASE="",
            _env_file=None,
        )
        self.engine, self.session_factory = init_database(self.settings)

    def tearDown(self):
        close_global_engine_session()
        try:
            from sqlalchemy.orm import close_all_sessions
            close_all_sessions()
        except Exception:
            pass
        try:
            self.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----------------- helpers
    def s(self):
        return new_session(session_factory=self.session_factory)

    # ----------------- tests
    def test_01_create_pending(self):
        with self.s() as db:
            r = create_pending(db, image_path="/tmp/a.jpg", user_input="夏日穿搭")
            self.assertTrue(r.task_id.startswith("gen_"))
            self.assertEqual(r.status, "pending")
            self.assertEqual(len(list_records(db)), 1)

    def test_02_mark_success_force_validation(self):
        with self.s() as db:
            r = create_pending(db)
            with self.assertRaises(BusinessException) as cm:
                mark_success(
                    db,
                    task_id=r.task_id,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦",
                    content="正文",
                    tags=["a", "b", "c", "d"],
                    image_description="描述",
                )
            self.assertEqual(cm.exception.code, ErrorCode.VALIDATION_ERROR)

            ok = mark_success(
                db,
                task_id=r.task_id,
                title="夏日穿搭分享",
                content="今天分享三套夏日 look，显瘦又出片！",
                tags=["夏日", "穿搭", "#穿搭", "OOTD", "夏日", "每日"],
                image_description="阳光下的白色连衣裙",
            )
            self.assertEqual(ok.status, TASK_STATUS_SUCCESS)
            self.assertTrue(ok.title and len(ok.title) <= 20)
            self.assertTrue(ok.content)
            self.assertTrue(3 <= len(ok.tags or []) <= 5)
            for t in ok.tags or []:
                self.assertTrue(t.startswith("#"))

    def test_03_mark_failed(self):
        with self.s() as db:
            r = create_pending(db, image_path="/tmp/bad.png")
            failed = mark_failed(
                db,
                task_id=r.task_id,
                error_code=ErrorCode.IMAGE_PROCESS_ERROR,
                error_message="图片损坏",
            )
            self.assertEqual(failed.status, TASK_STATUS_FAILED)
            self.assertEqual(failed.error_code, ErrorCode.IMAGE_PROCESS_ERROR)
            self.assertEqual(failed.error_message, "图片损坏")

    def test_04_persistence_after_restart(self):
        with self.s() as db:
            r = create_pending(db, user_input="持久化测试")
            tid = r.task_id
            mark_success(
                db,
                task_id=tid,
                title="标题",
                content="正文正文正文正文正文",
                tags=["a", "b", "c", "d"],
                image_description="好图",
            )

        close_global_engine_session()
        try:
            from sqlalchemy.orm import close_all_sessions
            close_all_sessions()
        except Exception:
            pass
        try:
            self.engine.dispose()
        except Exception:
            pass

        engine2, sf2 = init_database(self.settings)
        try:
            with new_session(session_factory=sf2) as db:
                again = get_record(db, task_id=tid)
                self.assertIsNotNone(again)
                self.assertEqual(again.status, TASK_STATUS_SUCCESS)
                self.assertEqual(again.user_input, "持久化测试")
                self.assertTrue(again.title and again.content and again.tags)
        finally:
            try:
                from sqlalchemy.orm import close_all_sessions
                close_all_sessions()
            except Exception:
                pass
            try:
                engine2.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
