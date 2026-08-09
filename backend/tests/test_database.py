"""数据库端到端测试（纯 SQLite + 同步 SQLAlchemy，零 Flask / 零 SiliconFlow / 零 FastAPI 路由）。"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    TASK_STATUS_PENDING,
)
from backend.db.config import DatabaseConfig, INVALID_DB_URL_PLACEHOLDER, mask_database_url
from backend.schemas import BusinessException, ErrorCode
from sqlalchemy.orm import close_all_sessions


FIXED_GEN_ID = "gen-b4ca4217-62e9-4321-9e47-f86e680ebcf4"
FIXED_GEN_ID_2 = "gen-fa1db372-00b9-4949-926c-c7e4a45b7c8d"


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

    # ------------------------------
    # 八.1 / 四.4 同一 generation_id 在三个阶段复用
    # ------------------------------
    def test_01_create_pending_same_generation_id(self):
        with self.s() as db:
            r = create_pending(
                db,
                generation_id=FIXED_GEN_ID,
                image_path="/tmp/a.jpg",
                user_input="夏日穿搭",
            )
            self.assertEqual(r.task_id, FIXED_GEN_ID, "内部 task_id 必须等于 B 传入的 generation_id")
            self.assertEqual(r.to_dict()["generation_id"], FIXED_GEN_ID, "对外只输出 generation_id 字段")
            self.assertFalse("task_id" in r.to_dict(), "对外输出禁止同时保留 task_id 别名")
            self.assertEqual(r.status, TASK_STATUS_PENDING)

    def test_02_pending_to_success_same_generation_id(self):
        with self.s() as db:
            r = create_pending(db, generation_id=FIXED_GEN_ID)
            ok = mark_success(
                db,
                generation_id=FIXED_GEN_ID,
                title="夏日穿搭分享",
                body="今天分享三套夏日 look，显瘦又出片！",
                tags=["夏日", "穿搭", "#穿搭", "OOTD", "每日"],
                image_summary="阳光下的白色连衣裙",
            )
            self.assertEqual(ok.to_dict()["generation_id"], FIXED_GEN_ID)
            self.assertEqual(ok.status, TASK_STATUS_SUCCESS)

    def test_03_pending_to_failed_same_generation_id(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID_2, image_path="/tmp/bad.png")
            failed = mark_failed(
                db,
                generation_id=FIXED_GEN_ID_2,
                error_code=ErrorCode.IMAGE_PROCESS_ERROR,
                error_message="图片处理失败",
                image_summary="坏图",
            )
            self.assertEqual(failed.to_dict()["generation_id"], FIXED_GEN_ID_2)
            self.assertEqual(failed.status, TASK_STATUS_FAILED)
            self.assertEqual(failed.error_code, ErrorCode.IMAGE_PROCESS_ERROR)
            self.assertEqual(failed.error_message, "图片处理失败")
            self.assertEqual(failed.image_description, "坏图")

    # ------------------------------
    # 八.2 文案校验：标题/正文/图描/标签
    # ------------------------------
    def test_04_mark_success_reject_title_over_20(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID)
            with self.assertRaises(BusinessException) as cm:
                mark_success(
                    db,
                    generation_id=FIXED_GEN_ID,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦",
                    body="正文",
                    tags=["a", "b", "c", "d"],
                    image_summary="描述",
                )
            self.assertEqual(cm.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_05_mark_success_reject_empty_body(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID)
            with self.assertRaises(BusinessException) as cm:
                mark_success(
                    db,
                    generation_id=FIXED_GEN_ID,
                    title="短标题",
                    body="   ",
                    tags=["a", "b", "c", "d"],
                    image_summary="描述",
                )
            self.assertEqual(cm.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_06_mark_success_reject_empty_image_summary(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID)
            with self.assertRaises(BusinessException) as cm:
                mark_success(
                    db,
                    generation_id=FIXED_GEN_ID,
                    title="短标题",
                    body="正文正文",
                    tags=["a", "b", "c", "d"],
                    image_summary="   ",
                )
            self.assertEqual(cm.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_07_mark_success_reject_tags_out_of_range(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID)
            # 少于 3
            with self.assertRaises(BusinessException):
                mark_success(
                    db,
                    generation_id=FIXED_GEN_ID,
                    title="短标题",
                    body="正文正文",
                    tags=["a", "a", "b"],  # 去重后只有 2
                    image_summary="描述",
                )
            # 多于 5
            with self.assertRaises(BusinessException):
                mark_success(
                    db,
                    generation_id=FIXED_GEN_ID,
                    title="短标题",
                    body="正文正文",
                    tags=["a", "b", "c", "d", "e", "f"],
                    image_summary="描述",
                )

    def test_08_mark_success_valid_range_passes(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID)
            ok = mark_success(
                db,
                generation_id=FIXED_GEN_ID,
                title="短标题",
                body="正文正文正文",
                tags=["a", "b", "c"],  # 3
                image_summary="描述",
            )
            self.assertEqual(ok.status, TASK_STATUS_SUCCESS)
            self.assertEqual(len(ok.tags or []), 3)

    # ------------------------------
    # 八.3 rollback：查询异常 / commit 异常
    # ------------------------------
    def test_09_get_record_query_exception_rollback_and_sanitise(self):
        with self.s() as db:
            create_pending(db, generation_id=FIXED_GEN_ID)

        rollback_calls = []

        # 强制 Session.execute 抛任意异常：应 rollback + BusinessException(DATABASE_ERROR)，只带类型名
        with self.s() as db:
            def _bad(*a, **kw):
                raise RuntimeError("driver boom — raw SQL & URL & password here")

            # 跟踪 rollback 调用
            orig_rollback = db.rollback

            def _rollback(*a, **kw):
                rollback_calls.append(1)
                return orig_rollback(*a, **kw)

            with patch.object(db, "rollback", _rollback):
                with patch.object(db, "execute", _bad):
                    try:
                        get_record(db, generation_id=FIXED_GEN_ID)
                    except BusinessException as e:
                        self.assertEqual(e.code, ErrorCode.DATABASE_ERROR)
                        self.assertNotIn("driver boom", e.message)
                        self.assertNotIn("raw SQL", e.message)
                        self.assertNotIn("password", e.message.lower())
                        # 但允许仅保留类型名
                        self.assertIn("RuntimeError", e.message)
                    else:
                        self.fail("预期 BusinessException(DATABASE_ERROR)")

        # rollback 被调用过
        self.assertGreaterEqual(len(rollback_calls), 1, "查询异常必须 rollback 至少 1 次")

    def test_10_commit_exception_rollback_and_sanitise(self):
        with self.s() as db2:
            create_pending(db2, generation_id=FIXED_GEN_ID_2)

        # 打 session.commit 强制抛异常：应 rollback 且不落 success 脏数据（保持 pending）
        class _Boom(Exception):
            pass

        rollback_calls = []

        with self.s() as db3:
            orig_rollback = db3.rollback

            def _rollback(*a, **kw):
                rollback_calls.append(1)
                return orig_rollback(*a, **kw)

            def fake_commit(*a, **kw):
                raise _Boom("disk full — pwd=TopSecret — raw_sql='...'")

            with patch.object(db3, "rollback", _rollback):
                with patch.object(db3, "commit", fake_commit):
                    try:
                        mark_success(
                            db3,
                            generation_id=FIXED_GEN_ID_2,
                            title="短标题",
                            body="正文正文",
                            tags=["a", "b", "c"],
                            image_summary="描述",
                        )
                    except BusinessException as e:
                        self.assertEqual(e.code, ErrorCode.DATABASE_ERROR)
                        self.assertNotIn("TopSecret", e.message)
                        self.assertNotIn("disk full", e.message)
                        self.assertNotIn("raw_sql", e.message)
                        self.assertNotIn("pwd=", e.message.lower())
                        self.assertIn(_Boom.__name__, e.message)
                    else:
                        self.fail("预期 commit 异常被转成 DATABASE_ERROR")

        # 1) rollback 调用过；2) 状态仍然是 pending（未写入脏 success）
        self.assertGreaterEqual(len(rollback_calls), 1, "commit 异常必须至少 rollback 1 次")
        with self.s() as db4:
            row = get_record(db4, generation_id=FIXED_GEN_ID_2)
            self.assertIsNotNone(row)
            self.assertEqual(row.status, TASK_STATUS_PENDING, "commit 异常应 rollback，状态保持 pending")

    # ------------------------------
    # 八.4 畸形 URL 不泄密
    # ------------------------------
    def test_11_malformed_database_url_does_not_leak_password(self):
        evil = "mysql+pymysql://user:TOP/SECRET@db.local:notaport/xhs"
        masked = mask_database_url(evil)
        self.assertEqual(masked, INVALID_DB_URL_PLACEHOLDER)
        # 二次保险：任何原始片段都不应残留
        self.assertNotIn("TOP", masked)
        self.assertNotIn("SECRET", masked)
        self.assertNotIn("notaport", masked)
        self.assertNotIn("db.local", masked)
        self.assertNotIn("xhs", masked)

    # ------------------------------
    # 八.5 非测试库在建立连接前被拒绝（通过双开关 + 白名单门逻辑）
    # ------------------------------
    def test_12_non_test_db_rejected_before_connect(self):
        from backend.tests.run_selfcheck import _mysql_dual_switch_or_exit, _is_safe_test_db

        # 1) 白名单逻辑本身拒 production/test_prod/production_test 等
        self.assertFalse(_is_safe_test_db("production"))
        self.assertFalse(_is_safe_test_db("test_prod"))
        self.assertFalse(_is_safe_test_db("production_test"))
        self.assertFalse(_is_safe_test_db("xhs_db_prod"))
        self.assertFalse(_is_safe_test_db(""))
        self.assertTrue(_is_safe_test_db("xhs_test"))
        self.assertTrue(_is_safe_test_db("xhs_test_20260809"))
        self.assertTrue(_is_safe_test_db("test_migration"))
        self.assertTrue(_is_safe_test_db("local_test"))

        # 2) 缺第二个确认开关时，任何 URL（哪怕是白名单 xhs_test）都应 SystemExit（在 connect 之前）
        clean_env = {}
        with patch.dict("os.environ", clean_env, clear=True):
            with self.assertRaises(SystemExit):
                _mysql_dual_switch_or_exit(
                    "mysql+pymysql://root:any@127.0.0.1:3306/xhs_test"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
