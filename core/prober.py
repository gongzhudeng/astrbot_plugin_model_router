"""接力探查算法。

规则(与用户定案一致):
1. 按优先级顺序依次启动探测。
2. 前一个模型超过合格线(eligible_timeout)仍无结果,或已经出结果(失败/超线成功),
   就启动下一个;前一个继续跑到放弃线(probe_deadline)。
3. 一旦有模型在合格线内成功返回,立即采用它:取消所有仍在跑的、不再启动剩余的。
4. 单个请求最多观察放弃线,到点仍无结果判为失败。
5. 探测函数只负责"成功或抛异常",超时控制全部由本模块接管,便于测试注入。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

# 状态常量(存储与状态图共用)
STATUS_OK = "ok"            # 合格:成功且延迟 <= 合格线
STATUS_SLOW = "slow"        # 超线:成功但延迟 > 合格线
STATUS_ERROR = "error"      # 失败:报错或超过放弃线
STATUS_SKIPPED = "skipped"  # 未参与:被早停跳过 / 自动跳过 / 上轮未测

# 探测函数约定:await probe_fn(target) 正常返回即成功,抛异常即失败。
# 内部不应自行做超时控制,统一由 relay_probe 用放弃线包裹。
ProbeFn = Callable[[Any], Awaitable[None]]

_ERROR_MAX_LEN = 160


@dataclass(slots=True)
class ProbeTarget:
    """一个待探测的模型(provider 粒度)。"""

    provider_id: str
    provider: Any


@dataclass(slots=True)
class ProbeResult:
    """单个模型的探测结果。"""

    provider_id: str
    status: str
    latency_ms: int = 0
    error: str = ""


def short_error(exc: Exception) -> str:
    """把异常压成一行短文本,用于存储与展示。"""
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:_ERROR_MAX_LEN]


async def relay_probe(
    targets: Sequence[ProbeTarget],
    probe_fn: ProbeFn,
    *,
    eligible_timeout: float,
    probe_deadline: float,
) -> list[ProbeResult]:
    """按优先级接力探测,返回与 targets 等长的结果列表。

    - 未被启动或被早停取消的模型,状态为 STATUS_SKIPPED。
    - 合格线大于放弃线属于配置错误,按放弃线处理(防御)。
    """
    effective_eligible = min(eligible_timeout, probe_deadline)
    effective_eligible_ms = effective_eligible * 1000.0  # latency 以毫秒记录,阈值同步换算
    n = len(targets)
    results: list[ProbeResult | None] = [None] * n
    tasks: list[asyncio.Task] = []
    eligible_hit = asyncio.Event()

    async def _run_one(index: int, target: ProbeTarget) -> None:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(probe_fn(target), timeout=probe_deadline)
        except asyncio.TimeoutError:
            latency = int((time.perf_counter() - started) * 1000)
            results[index] = ProbeResult(
                provider_id=target.provider_id,
                status=STATUS_ERROR,
                latency_ms=latency,
                error=f"超过放弃线 {probe_deadline:g}s 无返回",
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 探测失败要兜住一切异常
            latency = int((time.perf_counter() - started) * 1000)
            results[index] = ProbeResult(
                provider_id=target.provider_id,
                status=STATUS_ERROR,
                latency_ms=latency,
                error=short_error(exc),
            )
            return
        latency = int((time.perf_counter() - started) * 1000)
        if latency <= effective_eligible_ms:
            results[index] = ProbeResult(
                provider_id=target.provider_id,
                status=STATUS_OK,
                latency_ms=latency,
            )
            eligible_hit.set()
        else:
            results[index] = ProbeResult(
                provider_id=target.provider_id,
                status=STATUS_SLOW,
                latency_ms=latency,
            )

    prev_task: asyncio.Task | None = None
    prev_started_at = 0.0
    try:
        for index, target in enumerate(targets):
            if eligible_hit.is_set():
                break
            if prev_task is not None:
                # 等前一个:出结果(失败/超线成功 → 立即接力)或超过合格线(未决 → 接力但前者继续跑)。
                remaining = effective_eligible - (time.perf_counter() - prev_started_at)
                if remaining > 0:
                    try:
                        await asyncio.wait_for(asyncio.shield(prev_task), timeout=remaining)
                    except asyncio.TimeoutError:
                        pass  # 前一个仍未决 → 接力启动下一个
                    if eligible_hit.is_set():
                        break
            prev_started_at = time.perf_counter()
            task = asyncio.create_task(_run_one(index, target))
            tasks.append(task)
            prev_task = task
    finally:
        if eligible_hit.is_set():
            for task in tasks:
                if not task.done():
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    return [
        results[i] if results[i] is not None else ProbeResult(targets[i].provider_id, STATUS_SKIPPED)
        for i in range(n)
    ]
