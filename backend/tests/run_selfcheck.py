"""一键自测脚本（同步 SQLite 默认临时文件；MYSQL_TEST_URL 真实 MySQL 开关）。

MySQL 双开关确认（防误操作正式库，必须同时满足才会真正连接 MySQL：
  1) MYSQL_TEST_URL=<mysql 连接串>
  2) MYSQL_TEST_CONFIRM=YES_I_KNOW_IT_IS_A_TEST_DB

并且：MYSQL_TEST_URL 指向的数据库名必须是严格测试库白名单：
  - 精确 xhs_test；或 前缀 xhs_test_ / test_；或 后缀 *_test（且 *_test 的前缀头段不得含 test/prod/...）。
  - 任何库名中包含 prod / production / online / master / live / release / staging / uat / pre / preprod 直接拒绝。
  - 库名严格匹配 ^[A-Za-z][A-Za-z0-9_]{0,63}$。

不满足时脚本在执行任何连接 / CREATE DATABASE / DROP DATABASE / 建表 / 写入之前，
直接 SystemExit 退出，不产生任何副作用。
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
from backend.db.config import (
    DatabaseConfig,
    INVALID_DB_URL_PLACEHOLDER,
    mask_database_url,
    parse_mysql_url,
)
from backend.schemas import BusinessException, ErrorCode
from backend.validation import validate_copy, validate_generation_result
from sqlalchemy.orm import close_all_sessions


# 安全 MySQL 双开关常量
MYSQL_TEST_CONFIRM_VAR = "MYSQL_TEST_CONFIRM"
MYSQL_TEST_CONFIRM_EXPECTED = "YES_I_KNOW_IT_IS_A_TEST_DB"

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
    if not dbname or not isinstance(dbname, str):
        return False
    if not _DB_NAME_RE.match(dbname):
        return False
    low = dbname.lower()
    for tok in _FORBIDDEN_DB_TOKENS:
        if tok in low:
            return False
    if low in _SAFE_TEST_DB_EXACT:
        return True
    for p in _SAFE_TEST_DB_PREFIXES:
        if low.startswith(p):
            return True
    if low.endswith(_SAFE_TEST_DB_SUFFIX):
        head = low[: -len(_SAFE_TEST_DB_SUFFIX)]
        if head and "test" not in head:
            return True
    return False


def _mysql_dual_switch_or_exit(raw_url: str) -> None:
    """在任何 MySQL 连接 / CREATE / DROP 前执行：不满足双开关 + 白名单 → 直接 SystemExit。"""
    if os.environ.get(MYSQL_TEST_CONFIRM_VAR, "") != MYSQL_TEST_CONFIRM_EXPECTED:
        raise SystemExit(
            "[BLOCKED] MySQL 自测第二个确认开关未打开。\n"
            f"    请同时设置环境变量 MYSQL_TEST_CONFIRM={MYSQL_TEST_CONFIRM_EXPECTED}\n"
            f"    （用于防止误操作正式库。当前 MYSQL_TEST_URL={mask_database_url(raw_url)}）"
        )
    parsed = parse_mysql_url(raw_url)
    if not parsed:
        raise SystemExit(
            f"[BLOCKED] MYSQL_TEST_URL 无法可靠解析（占位符={INVALID_DB_URL_PLACEHOLDER}），"
            f"拒绝连接任何数据库。"
        )
    _user, _pwd, _host, _port, database = parsed
    if not _is_safe_test_db(database):
        raise SystemExit(
            f"[BLOCKED] 拒绝在数据库名 '{database}' 上运行自测：\n"
            f"    库名必须匹配严格测试白名单（xhs_test / xhs_test_* / test_* / xxx_test 且 xxx 段不含 test），\n"
            f"    且不包含 prod/production/online/master/live/release/staging/uat/pre/preprod。\n"
            f"    为避免误删正式库，脚本在任何连接/建库/建表/写入前直接退出。"
        )


def _sqlite_tmp_setup():
    tmp = tempfile.mkdtemp(prefix="xhs_dbtest_")
    db_path = Path(tmp) / "t.db"
    cfg = DatabaseConfig(
        DATABASE_URL=f"sqlite:///{db_path}",
        MYSQL_DATABASE="",
        _env_file=None,
    )
    return tmp, db_path, cfg


def _mysql_setup_from_env():
    """真实 MySQL 入口：**在任何连接/CREATE 之前先做双开关 + 白名单校验**。"""
    raw = os.environ.get("MYSQL_TEST_URL")
    if not raw:
        return None
    # 最前置门：双开关 + 白名单 + 合法库名；不满足直接 SystemExit
    _mysql_dual_switch_or_exit(raw)

    cfg = DatabaseConfig(DATABASE_URL=raw, _env_file=None)
    parsed = parse_mysql_url(cfg.DATABASE_URL or "")
    assert parsed, f"白名单校验已经通过，这里 parse 必然成功"
    user, _pwd, host, port, database = parsed
    import pymysql  # type: ignore

    # 到这里才允许真正 connect + CREATE DATABASE IF NOT EXISTS（且 database 已白名单）
    conn = pymysql.connect(host=host, port=port, user=user, password=_pwd or "", charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return cfg


def _mysql_cleanup(cfg: DatabaseConfig) -> None:
    # 再跑一次最前置门：保证双开关 + 白名单都满足才允许 DROP（哪怕 MYSQL_TEST_CLEANUP=1）
    _mysql_dual_switch_or_exit(cfg.DATABASE_URL or "")
    if os.environ.get("MYSQL_TEST_CLEANUP", "0") != "1":
        return
    parsed = parse_mysql_url(cfg.DATABASE_URL or "")
    if not parsed:
        return
    user, pwd, host, port, database = parsed
    # 双重确认：DROP 前最后再字符集 + 白名单
    if not _DB_NAME_RE.match(database or "") or not _is_safe_test_db(database):
        print(
            f"[SAFE] 跳过 DROP：database={database!r} 不满足严格测试库白名单 + 合法标识符强约束。"
        )
        return
    import pymysql  # type: ignore

    try:
        conn = pymysql.connect(host=host, port=port, user=user, password=pwd or "", charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{database}`;")
            conn.commit()
            print(f"[CLEANUP] 已删除测试库 `{database}`")
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        print(f"[WARN] MySQL 清理失败（不影响结果）：{type(e).__name__}")


DEMO_GEN_ID = "gen-demo0001-1234-5678-9abc-def012345678"
DEMO_GEN_ID_2 = "gen-demo0002-1111-2222-3333-444455556666"


def _workflow_5_steps(cfg: DatabaseConfig) -> None:
    # cfg 也可以是 B 的真实 settings（只要有 DATABASE_URL 属性，init_database 就能用）
    engine, session_factory = init_database(cfg)
    try:
        print("[1/5] create_pending（必须使用 B 传入的 generation_id，不自行生成）...")
        tid = DEMO_GEN_ID
        with new_session(session_factory=session_factory) as s:
            r = create_pending(
                s,
                generation_id=tid,
                image_path="/tmp/a.jpg",
                user_input="夏日穿搭",
                image_summary="阳光下的连衣裙",
            )
            assert r.task_id == tid, f"内部 task_id 必须等于 B 传入的 generation_id, got {r.task_id}"
            assert r.to_dict()["generation_id"] == tid
            assert "task_id" not in r.to_dict(), "对外输出禁止同时保留 task_id 别名"
            assert r.status == "pending"
        print(f"    OK generation_id = {tid}（B 传入字符串，未被修改或重新生成）")

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

        tmp_gid = "gen-tmp-9f9f9f9f-0000-1111-2222-333344445555"
        with new_session(session_factory=session_factory) as s:
            create_pending(s, generation_id=tmp_gid, image_path="/tmp/bad.jpg")
            try:
                mark_success(
                    s,
                    generation_id=tmp_gid,
                    title="这个标题绝对超过二十个字你数一下看看对不对哦哦",
                    body="正文",
                    tags=["a", "b", "c", "d"],
                    image_summary="描述",
                )
            except BusinessException as ee:
                assert ee.code == ErrorCode.VALIDATION_ERROR
                again = get_record(s, generation_id=tmp_gid)
                assert again and again.status == "pending", "mark_success 不合规不应写脏数据"
        print("    OK mark_success 内部再次校验，不合规不写库")

        print("[3/5] mark_success（image_summary/body 字段别名 → DB image_description/content；同一 generation_id）")
        with new_session(session_factory=session_factory) as s:
            r2 = mark_success(
                s,
                generation_id=tid,
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

        print("[4/5] mark_failed 带错误码落库（同一 generation_id）")
        failed_tid = DEMO_GEN_ID_2
        with new_session(session_factory=session_factory) as s:
            create_pending(s, generation_id=failed_tid, image_path="/tmp/bad.png")
            f = mark_failed(
                s,
                generation_id=failed_tid,
                error_code=ErrorCode.IMAGE_PROCESS_ERROR,
                error_message="图片处理失败",
                image_summary="坏图描述",
            )
            assert f.to_dict()["generation_id"] == failed_tid
            assert f.status == TASK_STATUS_FAILED
            assert f.error_code == ErrorCode.IMAGE_PROCESS_ERROR
            # 七：错误落库只存 error_code + 安全固定文案，禁止写第三方异常原文
            assert f.error_message == "图片处理失败"
            assert f.image_description == "坏图描述"
        print("    OK error_code + 固定错误文案已保存；generation_id 与 pending 完全相同")

        print("[5/5] 服务重启后记录仍存在（按 generation_id 查询）")
        try:
            close_all_sessions()
        except Exception:
            pass
        try:
            engine.dispose()
        except Exception:
            pass
        engine2, sf2 = init_database(cfg)
        try:
            with new_session(session_factory=sf2) as s:
                g = get_record(s, generation_id=tid)
                assert g and g.status == TASK_STATUS_SUCCESS and g.user_input == "夏日穿搭"
                g2 = get_record(s, generation_id=failed_tid)
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
    mysql_cfg = _mysql_setup_from_env()
    if mysql_cfg is not None:
        print(
            f"[MODE] 真实 MySQL 验收（双开关已打开）\n"
            f"       MYSQL_TEST_URL={mask_database_url(mysql_cfg.DATABASE_URL or '')}"
        )
        try:
            _workflow_5_steps(mysql_cfg)
        finally:
            _mysql_cleanup(mysql_cfg)
        print("\n[ALL PASSED] 真实 MySQL 验证通过：成功 + 失败 + 重启持久化。")
        return 0

    tmp, _db_path, cfg = _sqlite_tmp_setup()
    try:
        print(f"[MODE] 临时 SQLite（DATABASE_URL={mask_database_url(cfg.DATABASE_URL or '')}）")
        _workflow_5_steps(cfg)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n[ALL PASSED] 数据库核心能力 + 校验规则 + 字段映射全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
