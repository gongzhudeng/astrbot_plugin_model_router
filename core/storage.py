"""检测数据沉淀:状态读写、记录追加、统计聚合。

数据结构(KV 单键):
{
    "history": {provider_id: [{"ts", "status", "latency_ms", "error"}, ...]},
    "consec_fail": {provider_id: int},   # 连续失败轮数(ok/slow 归零,skipped 不动)
    "last_probe_ts": {provider_id: ts},  # 上次真实发起探测的时间(复活复测用)
}

skipped 的轮次不写入 history:没有请求就没有数据,统计保持自然空态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from .prober import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, STATUS_SLOW, ProbeResult

SECONDS_PER_DAY = 86400.0

# 供状态图使用的状态中文标签
STATUS_LABELS = {
    STATUS_OK: "合格",
    STATUS_SLOW: "超线",
    STATUS_ERROR: "失败",
    STATUS_SKIPPED: "未测",
}


class KVLike(Protocol):
    """Star 基类自带的 KV 接口(put_kv_data/get_kv_data)。"""

    async def put_kv_data(self, key: str, value: Any) -> None: ...

    async def get_kv_data(self, key: str, default: Any) -> Any: ...


def new_state() -> dict[str, Any]:
    return {"history": {}, "consec_fail": {}, "last_probe_ts": {}}


class StateStore:
    """router_state 的 KV 读写包装。"""

    def __init__(self, kv: KVLike, key: str = "router_state") -> None:
        self._kv = kv
        self._key = key

    async def load(self) -> dict[str, Any]:
        state = await self._kv.get_kv_data(self._key, None)
        if not isinstance(state, dict):
            return new_state()
        state.setdefault("history", {})
        state.setdefault("consec_fail", {})
        state.setdefault("last_probe_ts", {})
        return state

    async def save(self, state: Mapping[str, Any]) -> None:
        await self._kv.put_kv_data(self._key, dict(state))


def record_round(
    state: dict[str, Any],
    results: Iterable[ProbeResult],
    *,
    ts: float,
    history_max: int,
) -> None:
    """把一轮探测结果追加进 state(skipped 不落历史,连续失败计数也不动)。"""
    history: dict[str, list] = state.setdefault("history", {})
    consec: dict[str, int] = state.setdefault("consec_fail", {})
    last_ts: dict[str, float] = state.setdefault("last_probe_ts", {})

    for result in results:
        if result.status == STATUS_SKIPPED:
            continue
        records = history.setdefault(result.provider_id, [])
        records.append(
            {
                "ts": ts,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "error": result.error,
            }
        )
        if len(records) > history_max:
            del records[: len(records) - history_max]
        last_ts[result.provider_id] = ts
        if result.status == STATUS_ERROR:
            consec[result.provider_id] = consec.get(result.provider_id, 0) + 1
        else:
            consec[result.provider_id] = 0


def consecutive_failures(state: Mapping[str, Any], provider_id: str) -> int:
    return int(state.get("consec_fail", {}).get(provider_id, 0))


def should_auto_skip(
    state: Mapping[str, Any],
    provider_id: str,
    *,
    skip_threshold: int,
    now: float,
    revive_check_seconds: float = SECONDS_PER_DAY,
) -> bool:
    """是否跳过该模型的日常检测。

    - skip_threshold <= 0 表示关闭自动跳过。
    - 跳过的模型每 revive_check_seconds 仍复测一次,用于发现恢复。
    """
    if skip_threshold <= 0:
        return False
    if consecutive_failures(state, provider_id) < skip_threshold:
        return False
    last_ts = state.get("last_probe_ts", {}).get(provider_id)
    if last_ts is None:
        return False
    return (now - float(last_ts)) < revive_check_seconds


def should_notice_long_unavailable(
    state_before_fail: int,
    state_after_fail: int,
    *,
    notice_threshold: int,
) -> bool:
    """长期不可用提醒:达到阈值时提醒一次,之后每再累积同样轮数提醒一次。

    state_before_fail / state_after_fail 是本轮记录前的连续失败数与本轮后的值。
    """
    if notice_threshold <= 0:
        return False
    if state_after_fail < notice_threshold:
        return False
    # 恰好跨过阈值,或在阈值整数倍里程碑上
    return state_before_fail < notice_threshold or (
        (state_after_fail - notice_threshold) % notice_threshold == 0
    )


@dataclass(slots=True)
class ModelStats:
    """单个模型的聚合统计。"""

    total: int = 0
    ok: int = 0
    slow: int = 0
    error: int = 0
    available_rate: float | None = None  # 窗口内 (ok+slow)/total,无数据为 None
    avg_latency_ms: int | None = None    # 窗口内成功轮平均延迟,无数据为 None
    recent: list[dict[str, Any]] = field(default_factory=list)


def aggregate(
    history: Iterable[Mapping[str, Any]],
    *,
    now: float,
    window_seconds: float,
    recent_n: int,
) -> ModelStats:
    """聚合单个模型的历史:窗口内可用率/平均延迟 + 最近 N 条(时间正序)。"""
    stats = ModelStats()
    records = list(history)
    stats.recent = [
        {"status": r.get("status"), "latency_ms": r.get("latency_ms", 0)}
        for r in records[-recent_n:]
    ]
    window_records = [r for r in records if now - float(r.get("ts", 0)) <= window_seconds]
    stats.total = len(window_records)
    if not window_records:
        return stats
    stats.ok = sum(1 for r in window_records if r.get("status") == STATUS_OK)
    stats.slow = sum(1 for r in window_records if r.get("status") == STATUS_SLOW)
    stats.error = sum(1 for r in window_records if r.get("status") == STATUS_ERROR)
    stats.available_rate = (stats.ok + stats.slow) / stats.total
    latencies = [int(r.get("latency_ms", 0)) for r in window_records if r.get("status") in (STATUS_OK, STATUS_SLOW)]
    if latencies:
        stats.avg_latency_ms = sum(latencies) // len(latencies)
    return stats
