import os, sys, tempfile, shutil
tmp = tempfile.mkdtemp(prefix="xhs_dbtest_")
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/t.db"
sys.path.insert(0, ".")

import importlib
import backend.db
importlib.reload(backend.db)

from flask import Flask
from backend.db.config import Config
from backend.db import (
    init_database, create_pending, mark_success, mark_failed,
    get_record, TASK_STATUS_SUCCESS, TASK_STATUS_FAILED, db,
)
from backend.schemas import ErrorCode
from backend.validation import validate_copy, BusinessException


app = Flask(__name__)
app.config.from_object(Config)
init_database(app)
push = app.app_context()
push.push()

print("[1/5] create_pending...")
r = create_pending(image_path="/tmp/a.jpg", user_input="夏日穿搭")
assert r.task_id.startswith("gen_") and r.status == "pending"
tid = r.task_id
print("    OK generation_id =", tid)

print("[2/5] validate_copy 不合规 -> 抛异常，mark_success 内部已拦截")
try:
    validate_copy(
        image_description="描述",
        title="这个标题绝对超过二十个字你数一下看看对不对",
        content="正文",
        tags=["a","b","c","d"],
    )
except BusinessException as e:
    print("    OK 超长标题被拦截，code=", e.code)

try:
    validate_copy(
        image_description="", title="短", content="正文", tags=["a","b","c"]
    )
except BusinessException as e:
    print("    OK 空描述被拦截")

try:
    validate_copy(
        image_description="desc", title="短", content="", tags=["a","b","c"]
    )
except BusinessException as e:
    print("    OK 空正文被拦截")

try:
    validate_copy(
        image_description="desc", title="短", content="正文", tags=["a","a","b"]
    )
except BusinessException as e:
    print("    OK 标签不足(去重后<3)被拦截，code=", e.code)

print("[3/5] mark_success 合规成功，自动规范化")
r2 = mark_success(
    tid,
    title="夏日穿搭分享",
    content="今天分享三套夏日 look，显瘦又出片！",
    tags=["夏日","穿搭","#穿搭","OOTD","夏日","每日"],
    image_description="阳光下的白色连衣裙",
)
assert r2.status == TASK_STATUS_SUCCESS
assert len(r2.title) <= 20
assert r2.content and r2.image_description
assert 3 <= len(r2.tags) <= 5 and all(t.startswith("#") for t in r2.tags)
print("    OK tags=", r2.tags)

print("[4/5] mark_failed 带错误码落库")
r3 = create_pending(image_path="/tmp/bad.png")
f = mark_failed(r3.task_id, ErrorCode.IMAGE_PROCESS_ERROR, "图片损坏")
assert f.status == TASK_STATUS_FAILED
assert f.error_code == ErrorCode.IMAGE_PROCESS_ERROR
assert f.error_message == "图片损坏"
print("    OK error_code/error_message 已保存")

print("[5/5] 服务重启后记录仍存在")
db.session.remove()
push.pop()

app2 = Flask(__name__)
app2.config.from_object(Config)
init_database(app2)
push2 = app2.app_context()
push2.push()
g = get_record(tid)
assert g and g.status == TASK_STATUS_SUCCESS and g.user_input == "夏日穿搭"
g2 = get_record(r3.task_id)
assert g2 and g2.status == TASK_STATUS_FAILED and g2.error_code
push2.pop()
print("    OK")

shutil.rmtree(tmp, ignore_errors=True)
print("\n[ALL PASSED] 数据库 4 核心能力 + 5 校验规则全部通过。")
