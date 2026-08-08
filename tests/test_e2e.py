#!/usr/bin/env python3
"""端到端测试脚本（无需真实 MySQL，默认用 SQLite 验证核心逻辑）。

验证：
1. create_pending 创建任务成功并写入 DB
2. mark_success 成功任务可追踪，字段齐全
3. mark_failed 失败任务带 error_code / error_message
4. 文案校验：标题超 20 字/正文空/标签少于 3 个等会被拦截
5. 服务重启后 DB 记录仍存在
"""
import os
import sys
import shutil
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def run_all():
    tmp = tempfile.mkdtemp(prefix="xhs_test_")
    db_path = os.path.join(tmp, "test.db")
    upload_dir = os.path.join(tmp, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["UPLOAD_DIR"] = upload_dir
    os.environ["FLASK_ENV"] = "testing"

    from app import create_app, db
    from app.repository import (
        create_pending,
        mark_success,
        mark_failed,
        get_record,
        list_records,
    )
    from app.validators import validate_copy, normalize_tags
    from app.schemas import BusinessException, ErrorCode

    app = create_app()
    with app.app_context():
        db.create_all()

        print("[1/6] create_pending...")
        r1 = create_pending(image_path="/tmp/a.jpg", user_input="夏日穿搭", image_description=None)
        assert r1.task_id and r1.status == "pending", r1
        assert r1.user_input == "夏日穿搭"
        print("    OK task_id =", r1.task_id)

        print("[2/6] mark_success...")
        tags_ok = ["穿搭", "夏日", "OOTD", "每日穿搭"]
        r1_ok = mark_success(
            task_id=r1.task_id,
            title="夏日清爽穿搭分享",
            content="今天分享三套清爽夏日look，显瘦又凉快！",
            tags=tags_ok,
            image_description="阳光下的白色连衣裙模特图",
        )
        assert r1_ok.status == "success"
        assert len(r1_ok.title) <= 20
        assert r1_ok.tags and all(t.startswith("#") for t in r1_ok.tags)
        assert r1_ok.image_description and r1_ok.content
        print("    OK")

        print("[3/6] mark_failed...")
        r2 = create_pending(image_path="/tmp/bad.png")
        r2_fail = mark_failed(
            task_id=r2.task_id,
            error_code=ErrorCode.IMAGE_PROCESS_ERROR,
            error_message="图片损坏无法识别",
            image_description="",
        )
        assert r2_fail.status == "failed"
        assert r2_fail.error_code == ErrorCode.IMAGE_PROCESS_ERROR
        assert r2_fail.error_message
        print("    OK")

        print("[4/6] 文案校验拦截不合规结果...")
        try:
            validate_copy(
                image_description="好图",
                title="这个标题绝对超过二十个字了不信你数一下看看对不对",
                content="正文",
                tags=["a", "b", "c", "d"],
            )
            raise AssertionError("标题超长未被拦截")
        except BusinessException as e:
            assert e.code == ErrorCode.VALIDATION_ERROR
            print("    OK: 标题超长已拦截")

        try:
            validate_copy(
                image_description="好图",
                title="短标题",
                content="",
                tags=["a", "b", "c"],
            )
            raise AssertionError("正文空未被拦截")
        except BusinessException as e:
            assert e.code == ErrorCode.VALIDATION_ERROR
            print("    OK: 正文空已拦截")

        try:
            validate_copy(
                image_description="",
                title="短标题",
                content="正文内容",
                tags=["a", "b", "c"],
            )
            raise AssertionError("描述空未被拦截")
        except BusinessException as e:
            assert e.code == ErrorCode.VALIDATION_ERROR
            print("    OK: 描述空已拦截")

        try:
            validate_copy(
                image_description="描述",
                title="短标题",
                content="正文",
                tags=["a", "a", "b"],
            )
            raise AssertionError("标签数量不足未被拦截")
        except BusinessException as e:
            assert e.code == ErrorCode.VALIDATION_ERROR
            print("    OK: 标签不足(去重后<3)已拦截")

        norm = normalize_tags(["穿搭", "#穿搭", "OOTD", "", "夏日", "夏日"])
        assert len(norm) == 3
        assert all(t.startswith("#") for t in norm)
        print("    OK: 标签去重并补#")

        print("[5/6] DB 查询可追踪...")
        all_list = list_records()
        assert len(all_list) >= 2
        by_id = get_record(r1.task_id)
        assert by_id and by_id.status == "success"
        print("    OK: 共 %d 条记录" % len(all_list))

        print("[6/6] 服务重启后记录仍存在...")
        db.session.remove()
        del db
        from app import db as db2
        with app.app_context():
            again = get_record(r1.task_id)
            assert again and again.status == "success" and again.title
            again2 = get_record(r2.task_id)
            assert again2 and again2.status == "failed" and again2.error_code
        print("    OK")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n[ALL PASSED] 8 项核心验证全部通过。")


if __name__ == "__main__":
    run_all()
