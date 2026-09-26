"""创建批次的副作用合同测例。

只编排场景并调用 tests.batch_contract 的断言辅助；
库清理由辅助模块在每个场景结束后恢复成种子三批，这里不写任何清理语句。
"""

from tests.batch_contract import (
    BO_0900_BAKE_END,
    BO_0900_CODE,
    SEED_BATCH_COUNT,
    SEED_CONFLICT_COUNT,
    SEED_GANTT_BLOCK_COUNT,
)

OVEN_1 = "一层 1 号炉"
OVEN_3 = "二层石板炉"


def test_overlap_with_bo0900_bake_rejected(contract):
    # 布朗尼 585 起，bake [585,615) 与 BO-0900 烘烤段 [580,615) 重叠
    resp = contract.create(product="布朗尼", oven=OVEN_1, start_min=585, code="BO-T01")

    contract.assert_rejected(resp, status=409)
    contract.assert_batch_count(SEED_BATCH_COUNT)
    contract.assert_conflict_count(SEED_CONFLICT_COUNT + 1)
    contract.assert_latest_conflict("BO-T01", phase="bake")
    contract.assert_gantt_block_count(SEED_GANTT_BLOCK_COUNT)
    contract.assert_gantt_occupancy("BO-T01", 0)


def test_touching_bo0900_bake_end_succeeds(contract):
    # 发酵起点正好接上 BO-0900 烘烤结束端点 615（半开区间不相交）
    resp = contract.create(product="乡村欧包", oven=OVEN_3, start_min=BO_0900_BAKE_END, code="BO-T02")

    contract.assert_created(resp, start_min=BO_0900_BAKE_END)
    contract.assert_batch_count(SEED_BATCH_COUNT + 1)
    contract.assert_conflict_count(SEED_CONFLICT_COUNT)
    contract.assert_gantt_endpoint(BO_0900_CODE, "bake", end_min=BO_0900_BAKE_END)
    contract.assert_gantt_endpoint("BO-T02", "ferment", start_min=BO_0900_BAKE_END)


def test_duplicate_code_adds_no_occupancy(contract):
    # 复用已有批次号 BO-0900：唯一约束拦截，不得多出占炉
    resp = contract.create(product="乡村欧包", oven=OVEN_3, start_min=800, code=BO_0900_CODE)

    contract.assert_rejected(resp)
    contract.assert_batch_count(SEED_BATCH_COUNT)
    contract.assert_conflict_count(SEED_CONFLICT_COUNT)
    contract.assert_gantt_block_count(SEED_GANTT_BLOCK_COUNT)
    contract.assert_gantt_occupancy(BO_0900_CODE, 2)  # 仍只有原批的 ferment+bake


def test_overlapping_the_touching_batch_rejected(contract):
    # 先相接成功（同场景二），再用重叠开工撞这批新批次
    contract.assert_created(
        contract.create(product="乡村欧包", oven=OVEN_3, start_min=BO_0900_BAKE_END, code="BO-T02"),
        start_min=BO_0900_BAKE_END,
    )
    resp = contract.create(product="乡村欧包", oven=OVEN_3, start_min=BO_0900_BAKE_END + 5, code="BO-T03")

    contract.assert_rejected(resp, status=409)
    contract.assert_batch_count(SEED_BATCH_COUNT + 1)
    contract.assert_conflict_count(SEED_CONFLICT_COUNT + 1)  # 只再多一条
    contract.assert_latest_conflict("BO-T03", phase="ferment")
    contract.assert_gantt_endpoint("BO-T02", "ferment", start_min=BO_0900_BAKE_END)  # 仍留在甘特上
    contract.assert_gantt_occupancy("BO-T02", 2)
    contract.assert_gantt_occupancy("BO-T03", 0)
