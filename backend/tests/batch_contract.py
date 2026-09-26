"""创建批次副作用的合同断言辅助。

测例只编排场景并调用本模块：
- 核对批次条数、拒绝记录条数、甘特端点；
- reset_to_seed 把库恢复成种子三批（由 fixture 在每个场景结束后调用）。
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.models.models import Batch, ConflictLog, Oven, Product
from app.services.seed import seed_if_empty

# 种子基线（与 app.services.seed.seed_if_empty 保持一致）
SEED_BATCH_COUNT = 3
SEED_CONFLICT_COUNT = 1
SEED_GANTT_BLOCK_COUNT = SEED_BATCH_COUNT * 2  # 每批 ferment+bake 两块
BO_0900_CODE = "BO-0900"
BO_0900_BAKE_END = 9 * 60 + 40 + 35  # 615：BO-0900 烘烤段结束端点（半开区间）


class BatchContract:
    """创建批次副作用的断言与库恢复辅助。"""

    def __init__(self, client, session_factory: sessionmaker):
        self._client = client
        self._session_factory = session_factory

    # ---- 场景编排原语 ----

    def create(self, *, product: str, oven: str, start_min: int, code: str | None = None):
        """按名称解析产品/炉位后调用 POST /api/batches。"""
        payload = {
            "product_id": self._product_id(product),
            "oven_id": self._oven_id(oven),
            "start_min": start_min,
        }
        if code is not None:
            payload["code"] = code
        return self._client.post("/api/batches", json=payload)

    # ---- 响应断言 ----

    @staticmethod
    def assert_created(resp, *, start_min: int | None = None) -> dict:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if start_min is not None:
            assert body["start_min"] == start_min, body
        return body

    @staticmethod
    def assert_rejected(resp, *, status: int | None = None) -> None:
        if status is not None:
            assert resp.status_code == status, resp.text
        else:
            assert resp.status_code >= 400, resp.text

    # ---- 条数断言 ----

    def assert_batch_count(self, expected: int) -> None:
        rows = self._client.get("/api/batches").json()
        assert len(rows) == expected, f"批次条数 {len(rows)} != 期望 {expected}"

    def assert_conflict_count(self, expected: int) -> None:
        rows = self._client.get("/api/conflicts").json()
        assert len(rows) == expected, f"拒绝记录条数 {len(rows)} != 期望 {expected}"

    # ---- 甘特端点断言 ----

    def gantt_blocks(self, code: str) -> list[dict]:
        rows = self._client.get("/api/gantt").json()
        return [b for b in rows if b["code"] == code]

    def assert_gantt_block_count(self, expected: int) -> None:
        rows = self._client.get("/api/gantt").json()
        assert len(rows) == expected, f"甘特块数 {len(rows)} != 期望 {expected}"

    def assert_gantt_endpoint(
        self,
        code: str,
        phase: str,
        *,
        start_min: int | None = None,
        end_min: int | None = None,
    ) -> None:
        blocks = [b for b in self.gantt_blocks(code) if b["phase"] == phase]
        assert len(blocks) == 1, f"{code} 的 {phase} 块应有且仅有一块，实际 {len(blocks)}"
        block = blocks[0]
        if start_min is not None:
            assert block["start_min"] == start_min, block
        if end_min is not None:
            assert block["end_min"] == end_min, block

    def assert_gantt_occupancy(self, code: str, expected: int) -> None:
        blocks = self.gantt_blocks(code)
        assert len(blocks) == expected, f"{code} 占炉块数 {len(blocks)} != 期望 {expected}"

    # ---- 拒绝记录断言 ----

    def assert_latest_conflict(self, code: str, *, phase: str) -> None:
        rows = self._client.get("/api/conflicts").json()
        assert rows, "拒绝记录为空"
        latest = rows[0]  # 接口按 id 倒序
        assert latest["batch_code"] == code, latest
        assert phase in latest["detail"], f"拒绝详情读不到对手阶段 {phase}: {latest['detail']}"

    # ---- 库恢复 ----

    def reset_to_seed(self) -> None:
        """把库恢复成种子状态：三批 + 一条种子拒绝记录。"""
        db = self._session_factory()
        try:
            for model in (Batch, ConflictLog, Product, Oven):
                db.execute(delete(model))
            db.commit()
            seed_if_empty(db)
        finally:
            db.close()

    # ---- 内部 ----

    def _product_id(self, name: str) -> int:
        rows = self._client.get("/api/products").json()
        return next(p["id"] for p in rows if p["name"] == name)

    def _oven_id(self, label: str) -> int:
        rows = self._client.get("/api/ovens").json()
        return next(o["id"] for o in rows if o["label"] == label)
