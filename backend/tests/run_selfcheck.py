"""一键自测脚本（FastAPI + 纯 SQLAlchemy 2.x）。

默认：临时 SQLite 文件，保证 5 步全部通过，一键执行：
    python backend/tests/run_selfcheck.py

真实 MySQL 验收模式（设置环境变量 MYSQL_TEST_URL）：
    MYSQL_TEST_URL="mysql+pymysql://u:p@127.0.0.1:3306/xhs_test?charset=utf8mb4" \
        python backend/tests/run_selfcheck.py
    - 自动 CREATE DATABASE IF NOT EXISTS
    - 跑一次 create_pending + mark_success + mark_failed
    - 完成后可选 MYSQL_TEST_CLEANUP=1 自动 DROP 临时库（默认不删，便于手动 SELECT 验收）
    - 所有输出中的密码均做 mask_database_url 脱敏
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
from backend.db.config import (
    Settings,
    mask_database_url,
    parse_mysql_url,
)
from backend.schemas import BusinessException, ErrorCode
from backend.validation import validate_copy


def _sqlite_tmp_setup():
    tmp = tempfile.mkdtemp(prefix="xhs_dbtest_")
    db_path = Path(tmp) / "t.db"
    settings = Settings(
        DATABASE_URL=f"sqlite:///{db_path}",
        MYSQL_DATABASE="",
        _env_file=None,
    )
    return tmp, db_path, settings


def _mysql_setup_from_env():
    raw = os.environ.get("MYSQL_TEST_URL")
    if not raw:
        return None
    settings = Settings(DATABASE_URL=raw, _env_file=None)
    # 真实 MySQL：先建库（保证 DATABASE 存在），之后用它 create_all
    parsed = parse_mysql_url(settings.DATABASE_URL)
    if not parsed:
        raise SystemExit(f"[FAIL] MYSQL_TEST_URL={mask_database_url(raw)} 解析失败")
    import pymysql  # type: ignore

    user, password, host, port, database = parsed
    conn = pymysql.connect(host=host, port=port, user=user, password=password or "", charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
        conn.commit()
    finally:
        conn.close()
    return settings


def _mysql_cleanup(settings: Settings) -> None:
    if os.environ.get("MYSQL_TEST_CLEANUP", "0") != "1":
        return
    parsed = parse_mysql_url(settings.DATABASE_URL)
    if not parsed:
        return
    import pymysql  # type: ignore

    user, password, host, port, database = parsed
    try:
        conn = pymysql.connect(host=host, port=port, user=user, password=password or "", charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{database}`;")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # pragma: no cover
        print(f"[WARN] MySQL 清理失败（不影响结果）：{e}")


def _workflow_5_steps(settings: Settings) -> None:
    engine, session_factory = init_database(settings)
    try:
        # 1. create_pending
        print("[1/5] create_pending...")
        with new_session(session_factory=session_factory) as s:
            r = create_pending(s, image_path="/tmp/a.jpg", user_input="夏日穿搭")
            assert r.task_id.startswith("gen_") and r.status == "pending"
            tid = r.task_id
        print("    OK generation_id =", tid)

        # 2. validate_copy 不合规拦截（4 种子情况）
        print("[2/5] validate_copy 不合规 -> 抛异常；mark_success 内部再校验一遍")

        def _expect_validation(title="短", desc="desc", content="正文", tags=["a", "b", "c"]):
            try:
                validate_copy(image_description=desc, title=title, content=content, tags=tags)
                return None
            except BusinessException as e:
                return e

        e = _expect_validation(title="这个标题绝对超过二十个字你数一下看看对不对")
        assert e and e.code == ErrorCode.VALIDATION_ERROR, "超长标题应被拦截"
        print("    OK 超长标题被拦截，code=", e.code)

        e = _expect_validation(desc="")
        assert e, "空描述应被拦截"
        print("    OK 空描述被拦截")

        e = _expect_validation(content="")
        assert e, "空正文应被拦截"
        print("    OK 空正文被拦截")

        e = _expect_validation(tags=["a", "a", "b"])
        assert e and e.code == ErrorCode.VALIDATION_ERROR, "标签去重后 <3 应被拦截"
        print("    OK 标签不足(去重后<3)被拦截，code=", e.code)

        # 校验 mark_success 自己也会再次校验（不合规 -> 不写库）
        with new_session(session_factory=session_factory) as s:
            r = create_pending(s, image_path="/tmp/bad.jpg")
            try:
                mark_success(
                    s,
                    task_id=r.task_id,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦",
                    content="正文",
                    tags=["a", "b", "c", "d"],
                    image_description="描述",
                )
            except BusinessException as ee:
                assert ee.code == ErrorCode.VALIDATION_ERROR
                # 确认写库前被拦截了：查出来应该还是 pending
                again = get_record(s, task_id=r.task_id)
                assert again and again.status == "pending", "mark_success 校验失败后不应写脏数据"
        print("    OK mark_success 内部再次校验，不合规不写库")

        # 3. mark_success 合规成功 + 标签规范化
        print("[3/5] mark_success 合规成功，自动规范化")
        with new_session(session_factory=session_factory) as s:
            r2 = mark_success(
                s,
                task_id=tid,
                title="夏日穿搭分享",
                content="今天分享三套夏日 look，显瘦又出片！",
                tags=["夏日", "穿搭", "#穿搭", "OOTD", "夏日", "每日"],
                image_description="阳光下的白色连衣裙",
            )
            assert r2.status == TASK_STATUS_SUCCESS
            assert len(r2.title) <= 20
            assert r2.content and r2.image_description
            assert 3 <= len(r2.tags or []) <= 5
            assert all(t.startswith("#") for t in (r2.tags or []))
        print("    OK tags=", r2.tags)

        # 4. mark_failed 带错误码落库
        print("[4/5] mark_failed 带错误码落库")
        with new_session(session_factory=session_factory) as s:
            r3 = create_pending(s, image_path="/tmp/bad.png")
            failed_tid = r3.task_id
            f = mark_failed(
                s,
                task_id=r3.task_id,
                error_code=ErrorCode.IMAGE_PROCESS_ERROR,
                error_message="图片损坏",
            )
            assert f.status == TASK_STATUS_FAILED
            assert f.error_code == ErrorCode.IMAGE_PROCESS_ERROR
            assert f.error_message == "图片损坏"
        print("    OK error_code/error_message 已保存")

        # 5. 模拟重启（销毁 engine+session 再重建），记录仍存在
        print("[5/5] 服务重启后记录仍存在")
        try:
            from sqlalchemy.orm import close_all_sessions
            close_all_sessions()
        except Exception:
            pass
        try:
            engine.dispose()
        except Exception:
            pass
        engine2, sf2 = init_database(settings)
        try:
            with new_session(session_factory=sf2) as s:
                g = get_record(s, task_id=tid)
                assert g and g.status == TASK_STATUS_SUCCESS and g.user_input == "夏日穿搭"
                g2 = get_record(s, task_id=failed_tid)
                assert g2 and g2.status == TASK_STATUS_FAILED and g2.error_code
                cnt = len(list_records(s))
                assert cnt >= 2
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
        print("    OK")
    finally:
        try:
            from sqlalchemy.orm import close_all_sessions
            close_all_sessions()
        except Exception:
            pass
        try:
            engine.dispose()
        except Exception:
            pass
        close_global_engine_session()


def main() -> int:
    mysql_settings = _mysql_setup_from_env()
    if mysql_settings is not None:
        print(f"[MODE] 真实 MySQL 验收（MYSQL_TEST_URL={mask_database_url(mysql_settings.DATABASE_URL)}）")
        try:
            _workflow_5_steps(mysql_settings)
        finally:
            _mysql_cleanup(mysql_settings)
        print("\n[ALL PASSED] 真实 MySQL 验证通过：成功 + 失败 + 重启持久化。")
        return 0

    tmp, _db_path, settings = _sqlite_tmp_setup()
    try:
        print(f"[MODE] 临时 SQLite（DATABASE_URL={mask_database_url(settings.DATABASE_URL)}）")
        _workflow_5_steps(settings)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n[ALL PASSED] 数据库 4 核心能力 + 5 校验规则全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
