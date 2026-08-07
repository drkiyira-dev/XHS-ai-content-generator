"""一键自测脚本（同步 SQLite 默认临时文件；MYSQL_TEST_URL 真实 MySQL 开关）。

MYSQL_TEST_CLEANUP 安全策略（严格白名单）：
- 库名必须匹配：字母开头 + [字母数字下划线_]{0,63}；
- 且满足：
    1) xhs_test（精确）
    2) 前缀 xhs_test_* 或 test_*
    3) 后缀 *_test（必须整个是 xxx_test，且 xxx 段不含 test 本身以避免 test_prod / production_test 误匹配）
- 任何情况下：库名包含 prod / production / online / master / live / release 等关键词一律拒绝，
  同时严格禁止字符集外的符号（反引号 / -- / ; / unicode 等）。
"""
from __future__ import annotations

import os
import re as _re
import shutil
import sys
import tempfile
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
from backend.core.config import Settings, mask_database_url, parse_mysql_url
from backend.schemas import BusinessException, ErrorCode
from backend.validation import validate_copy, validate_generation_result
from sqlalchemy.orm import close_all_sessions


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
    parsed = parse_mysql_url(settings.DATABASE_URL or "")
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


_SAFE_TEST_DB_PREFIXES = ("xhs_test_", "test_")
_SAFE_TEST_DB_SUFFIX = "_test"
_SAFE_TEST_DB_EXACT = {"xhs_test"}
_FORBIDDEN_DB_TOKENS = (
    "prod",
    "production",
    "online",
    "master",
    "live",
    "release",
    "staging",
    "uat",
    "pre",
    "preprod",
)
_DB_NAME_RE = _re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _is_safe_test_db(dbname: str) -> bool:
    """严格白名单：任何不满足的库名一律拒绝 DROP。"""
    if not dbname or not isinstance(dbname, str):
        return False
    # 1) 字符集强约束：字母开头 + 字母数字下划线 <=64
    if not _DB_NAME_RE.match(dbname):
        return False
    # 2) 绝对禁止的关键词（大小写不敏感）
    low = dbname.lower()
    for tok in _FORBIDDEN_DB_TOKENS:
        if tok in low:
            return False
    # 3) 白名单匹配：精确 / 前缀 / 后缀（后缀必须整段是 xxx_test，且 xxx 段不得含 "test"，防 production_test 误匹配）
    if low in _SAFE_TEST_DB_EXACT:
        return True
    for p in _SAFE_TEST_DB_PREFIXES:
        if low.startswith(p):
            return True
    if low.endswith(_SAFE_TEST_DB_SUFFIX):
        head = low[: -len(_SAFE_TEST_DB_SUFFIX)]
        # head 不能再含 "test"（避免 production_test / test_prod 这类组合被放行）
        if head and "test" not in head:
            return True
    return False


def _mysql_cleanup(settings: Settings) -> None:
    if os.environ.get("MYSQL_TEST_CLEANUP", "0") != "1":
        return
    parsed = parse_mysql_url(settings.DATABASE_URL or "")
    if not parsed:
        return
    user, password, host, port, database = parsed
    # 再做一次字符集强校验（DROP 前最后一道门）
    if not _DB_NAME_RE.match(database or ""):
        print(
            f"[SAFE] 跳过 DROP：database={database!r} 不满足 MySQL 合法库名字符集强约束。"
        )
        return
    if not _is_safe_test_db(database):
        print(
            f"[SAFE] 跳过 DROP：database='{database}' 不匹配严格测试库白名单 "
            f"（允许：xhs_test、xhs_test_*、test_*、xxx_test 且 xxx 不含 test/prod/online 等关键词），"
            f"防止误删正式库。"
        )
        return
    import pymysql  # type: ignore

    try:
        conn = pymysql.connect(host=host, port=port, user=user, password=password or "", charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{database}`;")
            conn.commit()
            print(f"[CLEANUP] 已删除测试库 `{database}`")
        finally:
            conn.close()
    except Exception as e:
        print(f"[WARN] MySQL 清理失败（不影响结果）：{e}")


def _workflow_5_steps(settings: Settings) -> None:
    engine, session_factory = init_database(settings)
    try:
        print("[1/5] create_pending...")
        with new_session(session_factory=session_factory) as s:
            r = create_pending(
                s,
                image_path="/tmp/a.jpg",
                user_input="夏日穿搭",
                image_summary="阳光下的连衣裙",
            )
            assert r.task_id.count("-") == 4, f"标准 UUID4 应含 4 个 '-', got {r.task_id}"
            assert r.status == "pending"
            tid = r.task_id
        print(f"    OK generation_id = {tid} (标准 UUID4)")

        print("[2/5] validate_copy/validate_generation_result + mark_success 内部校验不写脏数据")

        def _expect_val(desc="好图", title="短", content="正文", tags=None):
            if tags is None:
                tags = ["a", "b", "c"]
            try:
                validate_copy(image_description=desc, title=title, content=content, tags=tags)
                return None
            except BusinessException as e:
                return e

        e = _expect_val(title="这个标题绝对超过二十个字你数一下看看对不对哦")
        assert e and e.code == ErrorCode.VALIDATION_ERROR
        print("    OK 超长标题拦截，code=", e.code)

        e = _expect_val(desc="")
        assert e and e.data
        print("    OK 空描述拦截")

        e = _expect_val(content="")
        assert e
        print("    OK 空正文拦截")

        e = _expect_val(tags=["a", "a", "b"])
        assert e and e.code == ErrorCode.VALIDATION_ERROR
        print("    OK 标签不足（去重后<3）拦截")

        # validate_generation_result：A 对齐字段 image_summary / body
        try:
            validate_generation_result({
                "image_summary": "",
                "body": "正文正文",
                "title": "标题",
                "tags": ["a", "b", "c", "d"],
            })
            assert False, "应抛 image_summary 空而失败"
        except BusinessException as e2:
            assert "image_summary" in (e2.data or {}), f"应含 image_summary 错误，实际 {e2.data}"
        print("    OK validate_generation_result: image_summary 空拦截")

        with new_session(session_factory=session_factory) as s:
            r = create_pending(s, image_path="/tmp/bad.jpg")
            try:
                mark_success(
                    s,
                    task_id=r.task_id,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦哦",
                    body="正文",
                    tags=["a", "b", "c", "d"],
                    image_summary="描述",
                )
            except BusinessException as ee:
                assert ee.code == ErrorCode.VALIDATION_ERROR
                again = get_record(s, task_id=r.task_id)
                assert again and again.status == "pending", "mark_success 不合规不应写脏数据"
        print("    OK mark_success 内部再次校验，不合规不写库")

        print("[3/5] mark_success（image_summary/body 字段别名 → DB image_description/content")
        with new_session(session_factory=session_factory) as s:
            r2 = mark_success(
                s,
                task_id=tid,
                title="夏日穿搭分享",
                body="今天分享三套夏日 look，显瘦又出片！",
                tags=["夏日", "穿搭", "#穿搭", "OOTD", "夏日", "每日"],
                image_summary="阳光下的白色连衣裙",
            )
            assert r2.status == TASK_STATUS_SUCCESS
            assert len(r2.title) <= 20
            assert r2.image_description == "阳光下的白色连衣裙", (
                f"image_summary 应映射到 image_description，实际 {r2.image_description!r}"
            )
            assert r2.content == "今天分享三套夏日 look，显瘦又出片！", (
                f"body 应映射到 content，实际 {r2.content!r}"
            )
            assert 3 <= len(r2.tags or []) <= 5
            assert all(t.startswith("#") for t in (r2.tags or []))
        print("    OK tags =", r2.tags)

        print("[4/5] mark_failed 带错误码落库")
        with new_session(session_factory=session_factory) as s:
            r3 = create_pending(s, image_path="/tmp/bad.png")
            failed_tid = r3.task_id
            f = mark_failed(
                s,
                task_id=r3.task_id,
                error_code=ErrorCode.IMAGE_PROCESS_ERROR,
                error_message="图片损坏",
                image_summary="坏图描述",
            )
            assert f.status == TASK_STATUS_FAILED
            assert f.error_code == ErrorCode.IMAGE_PROCESS_ERROR
            assert f.error_message == "图片损坏"
            assert f.image_description == "坏图描述"
        print("    OK error_code/error_message 已保存；image_summary→image_description 生效")

        print("[5/5] 服务重启后记录仍存在")
        try:
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
                assert g2 and g2.status == TASK_STATUS_FAILED
                cnt = len(list_records(s))
                assert cnt >= 2
        finally:
            try:
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
        print(f"[MODE] 真实 MySQL 验收（MYSQL_TEST_URL={mask_database_url(mysql_settings.DATABASE_URL or '')}）")
        try:
            _workflow_5_steps(mysql_settings)
        finally:
            _mysql_cleanup(mysql_settings)
        print("\n[ALL PASSED] 真实 MySQL 验证通过：成功 + 失败 + 重启持久化。")
        return 0

    tmp, _db_path, settings = _sqlite_tmp_setup()
    try:
        print(f"[MODE] 临时 SQLite（DATABASE_URL={mask_database_url(settings.DATABASE_URL or '')}）")
        _workflow_5_steps(settings)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n[ALL PASSED] 数据库核心能力 + 校验规则 + 字段映射全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
