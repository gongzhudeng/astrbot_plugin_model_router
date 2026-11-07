"""切换决策与日志文案:纯逻辑,不碰 AstrBot API。

注意:本插件对聊天全程无感——切换与长期不可用只写日志,不发送任何聊天消息。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .prober import STATUS_ERROR, STATUS_OK, STATUS_SLOW, ProbeResult

REASON_OK = "ok"                    # 有合格模型,按优先级取最靠前者
REASON_FASTEST_SLOW = "fastest_slow"  # 全超线,取成功返回里延迟最低者
REASON_ALL_FAILED = "all_failed"    # 全失败,本轮不动


@dataclass(slots=True)
class Decision:
    """一轮探测后的路由决策。"""

    selected_id: str | None
    reason: str
    selected_latency_ms: int = 0


def decide(results: Sequence[ProbeResult], order_ids: Sequence[str]) -> Decision:
    """按定案规则选择模型。

    1. 合格的按优先级:order_ids 顺序里第一个 ok。
    2. 全都不合格:在成功返回(超线)里挑延迟最低的,不看出身。
    3. 全都失败:不选择。
    """
    results_by_id = {r.provider_id: r for r in results}

    for pid in order_ids:
        result = results_by_id.get(pid)
        if result is not None and result.status == STATUS_OK:
            return Decision(selected_id=pid, reason=REASON_OK, selected_latency_ms=result.latency_ms)

    slow_results = [r for r in results if r.status == STATUS_SLOW]
    if slow_results:
        best = min(slow_results, key=lambda r: r.latency_ms)
        return Decision(
            selected_id=best.provider_id,
            reason=REASON_FASTEST_SLOW,
            selected_latency_ms=best.latency_ms,
        )

    return Decision(selected_id=None, reason=REASON_ALL_FAILED)


def should_switch(
    decision: Decision,
    current_id: str | None,
    current_status: str | None,
    *,
    force_align: bool,
) -> bool:
    """是否执行切换。

    - force_align(强制归位):每轮都归位到决策结果,手动切换只在两轮之间有效。
    - 否则仅当当前模型本轮被判定失败(error)时才切换。
    - 当前模型不在优先级链里(current_status 为 None)时,force_align=False 不动它。
    """
    if decision.selected_id is None:
        return False
    if decision.selected_id == current_id:
        return False
    if force_align:
        return True
    return current_status == STATUS_ERROR


def build_long_unavailable_notice(provider_id: str, consecutive: int) -> str:
    """长期不可用的日志文案(仅写日志,不发送)。"""
    return (
        f"模型 {provider_id} 已连续 {consecutive} 轮检测失败,"
        "建议检查该中转站或把它移出优先级链。"
    )


def current_status_of(
    results: Sequence[ProbeResult],
    provider_id: str | None,
) -> str | None:
    """取当前模型本轮的状态;不在结果里(不在优先级链)返回 None。"""
    if provider_id is None:
        return None
    for result in results:
        if result.provider_id == provider_id:
            return result.status
    return None
