"""创建批次副作用合同的独立断言辅助。

职责：
- 核对批次条数、拒绝记录条数、甘特端点；
- 提供 scenario() 场景上下文：进入时把库恢复成种子三批，
  每个场景结束后再次恢复并校验种子状态。

测例只编排场景并调用本模块，不得自己写清理语句。
"""

from __future__ import annotations

import atexit
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

# 未显式配置 DATABASE_URL 时，用独立临时 SQLite 库跑合同测例，不碰开发库；
# docker 环境（docker compose exec api pytest）沿用已配置的 Postgres。
_fd, _db_path = tempfile.mkstemp(prefix="bakeoven_contract_", suffix=".db")
os.close(_fd)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path}")


def _remove_tmp_db() -> None:
    if os.path.exists(_db_path):
        os.remove(_db_path)


atexit.register(_remove_tmp_db)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import Batch, ConflictLog, Oven, Product  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402

# 种子状态常量（与 app/services/seed.py 对齐：三批、一条试排拒绝记录）
SEED_BATCH_COUNT = 3
SEED_CONFLICT_COUNT = 1
SEED_GANTT_BLOCK_COUNT = 6  # 三批 × 发酵+烘烤两段占炉


@contextmanager
def scenario() -> Iterator[TestClient]:
    """一个副作用场景：进入时拿到种子三批的库，结束后恢复并校验种子状态。"""
    # raise_server_exceptions=False：重复批次号触发唯一约束时要读到 5xx 响应而非抛异常
    with TestClient(app, raise_server_exceptions=False) as client:
        restore_seed_state()
        try:
            yield client
        finally:
            restore_seed_state()
            assert_batch_count(client, SEED_BATCH_COUNT)
            assert_conflict_count(client, SEED_CONFLICT_COUNT)
            assert_gantt_block_count(client, SEED_GANTT_BLOCK_COUNT)


def restore_seed_state() -> None:
    """把库恢复成种子三批：清空业务表后重跑种子（种子的唯一来源是 seed_if_empty）。"""
    db = SessionLocal()
    try:
        for model in (ConflictLog, Batch, Product, Oven):
            db.execute(delete(model))
        db.commit()
        seed_if_empty(db)
    finally:
        db.close()


# --- 查询辅助（只读，全部走 API） ---


def list_batches(client: TestClient) -> list[dict]:
    return client.get("/api/batches").json()


def list_conflicts(client: TestClient) -> list[dict]:
    return client.get("/api/conflicts").json()


def list_gantt(client: TestClient) -> list[dict]:
    return client.get("/api/gantt").json()


def product_id(client: TestClient, name: str) -> int:
    for p in client.get("/api/products").json():
        if p["name"] == name:
            return p["id"]
    raise AssertionError(f"产品不存在：{name}")


def oven_id(client: TestClient, label: str) -> int:
    for o in client.get("/api/ovens").json():
        if o["label"] == label:
            return o["id"]
    raise AssertionError(f"炉位不存在：{label}")


def gantt_blocks(client: TestClient, code: str) -> list[dict]:
    return [b for b in list_gantt(client) if b["code"] == code]


def gantt_window(client: TestClient, code: str, phase: str) -> tuple[int, int]:
    """某批次某阶段在甘特上的半开区间端点 [start, end)。"""
    for b in gantt_blocks(client, code):
        if b["phase"] == phase:
            return b["start_min"], b["end_min"]
    raise AssertionError(f"甘特上找不到 {code} 的 {phase} 段")


def latest_conflict(client: TestClient) -> dict:
    rows = list_conflicts(client)
    assert rows, "没有拒绝记录"
    return rows[0]  # 接口按 id 倒序，第一条即最新


def post_batch(
    client: TestClient,
    *,
    product_id: int,
    oven_id: int,
    start_min: int,
    code: str | None = None,
):
    """编排用：发起一次创建批次请求，原样返回响应。"""
    body: dict = {"product_id": product_id, "oven_id": oven_id, "start_min": start_min}
    if code is not None:
        body["code"] = code
    return client.post("/api/batches", json=body)


# --- 断言辅助 ---


def assert_batch_count(client: TestClient, expected: int) -> None:
    rows = list_batches(client)
    assert len(rows) == expected, (
        f"批次条数应为 {expected}，实际 {len(rows)}：{[r['code'] for r in rows]}"
    )


def assert_conflict_count(client: TestClient, expected: int) -> None:
    rows = list_conflicts(client)
    assert len(rows) == expected, f"拒绝记录条数应为 {expected}，实际 {len(rows)}"


def assert_gantt_block_count(client: TestClient, expected: int) -> None:
    rows = list_gantt(client)
    assert len(rows) == expected, f"甘特占炉条数应为 {expected}，实际 {len(rows)}"


def assert_gantt_endpoints(
    client: TestClient,
    code: str,
    *,
    ferment: tuple[int, int] | None = None,
    bake: tuple[int, int] | None = None,
) -> None:
    """核对某批次在甘特上的阶段端点；ferment/bake 传 (start, end)。"""
    blocks = gantt_blocks(client, code)
    assert blocks, f"甘特上找不到批次 {code}"
    for phase, want in (("ferment", ferment), ("bake", bake)):
        if want is None:
            continue
        got = [(b["start_min"], b["end_min"]) for b in blocks if b["phase"] == phase]
        assert got == [want], f"{code} 的 {phase} 端点应为 {want}，实际 {got}"


def assert_conflict_logged(client: TestClient, code: str, phase: str) -> None:
    """最新一条拒绝记录属于 code，且能读到对手阶段 phase。"""
    row = latest_conflict(client)
    assert row["batch_code"] == code, (
        f"最新拒绝记录批次号应为 {code}，实际 {row['batch_code']}"
    )
    assert phase in row["detail"], f"拒绝记录应能读到对手阶段 {phase}，实际：{row['detail']}"
