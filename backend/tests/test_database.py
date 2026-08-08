"""数据库端到端测试（FastAPI + SQLAlchemy 2.x，Unittest 风格）。"""
from __future__ import annotations

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
    mark_failed,
    mark_success,
    new_session,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCESS,
)
from backend.db.config import DatabaseConfig
from backend.schemas import BusinessException, ErrorCode
from sqlalchemy.orm import close_all_sessions


class TestDatabaseWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xhs_db_test_")
        self.db_path = Path(self.tmp) / "test.db"
        self.cfg = DatabaseConfig(
            DATABASE_URL=f"sqlite:///{self.db_path}",
            MYSQL_DATABASE="",
            _env_file=None,
        )
        self.engine, self.session_factory = init_database(self.cfg)

    def tearDown(self):
        close_global_engine_session()
        try:
            close_all_sessions()
        except Exception:
            pass
        try:
            self.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def s(self):
        return new_session(session_factory=self.session_factory)

    def test_01_create_pending_uuid4(self):
        with self.s() as db:
            r = create_pending(db, image_path="/tmp/a.jpg", user_input="夏日穿搭")
            self.assertTrue(r.task_id.count("-") == 4, f"标准 UUID4，got {r.task_id}")
            self.assertEqual(r.status, "pending")

    def test_02_mark_success_force_validation_alias_fields(self):
        with self.s() as db:
            r = create_pending(db)
            with self.assertRaises(BusinessException) as cm:
                mark_success(
                    db,
                    task_id=r.task_id,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦",
                    body="正文",
                    tags=["a", "b", "c", "d"],
                    image_summary="描述",
                )
            self.assertEqual(cm.exception.code, ErrorCode.VALIDATION_ERROR)

            ok = mark_success(
                db,
                task_id=r.task_id,
                title="夏日穿搭分享",
                body="今天分享三套夏日 look，显瘦又出片！",
                tags=["夏日", "穿搭", "#穿搭", "OOTD", "夏日", "每日"],
                image_summary="阳光下的白色连衣裙",
            )
            self.assertEqual(ok.status, TASK_STATUS_SUCCESS)
            self.assertEqual(ok.image_description, "阳光下的白色连衣裙")
            self.assertEqual(ok.content, "今天分享三套夏日 look，显瘦又出片！")
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
                image_summary="坏图",
            )
            self.assertEqual(failed.status, TASK_STATUS_FAILED)
            self.assertEqual(failed.error_code, ErrorCode.IMAGE_PROCESS_ERROR)
            self.assertEqual(failed.error_message, "图片损坏")
            self.assertEqual(failed.image_description, "坏图")

    def test_04_persistence_after_restart(self):
        with self.s() as db:
            r = create_pending(db, user_input="持久化测试")
            tid = r.task_id
            mark_success(
                db,
                task_id=tid,
                title="标题",
                body="正文正文正文正文正文",
                tags=["a", "b", "c", "d"],
                image_summary="好图",
            )

        close_global_engine_session()
        try:
            close_all_sessions()
        except Exception:
            pass
        try:
            self.engine.dispose()
        except Exception:
            pass

        engine2, sf2 = init_database(self.cfg)
        try:
            with new_session(session_factory=sf2) as db:
                again = get_record(db, task_id=tid)
                self.assertIsNotNone(again)
                self.assertEqual(again.status, TASK_STATUS_SUCCESS)
                self.assertEqual(again.user_input, "持久化测试")
                self.assertEqual(again.image_description, "好图")
                self.assertTrue(again.title and again.content and again.tags)
        finally:
            try:
                close_all_sessions()
            except Exception:
                pass
            try:
                engine2.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
