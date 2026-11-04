"""回退感知:对比"响应实际模型"与"会话当前模型",发现回退即建议触发救火探测。

原理:回退链只影响单次请求,不改会话状态。因此在响应钩子里实时重读
会话当前模型,与 response.model 对比——不一致即说明本次请求发生过回退
(或内部换模型),这是"当前模型坏了"的强信号,值得立刻探测一轮。

设计原则:
- 宁漏勿错:任何一边拿不到模型名就不触发,交给定时检测兜底。
- 判定为纯函数,冷却与触发由调用方(main)控制。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WatchVerdict:
    """单次响应的回退判定结果。"""

    fallback_detected: bool
    reason: str


def judge(actual_model: str | None, current_model: str | None) -> WatchVerdict:
    """判定本次响应是否意味着发生过回退。

    - 任一侧模型名缺失 → 不判定(宁漏勿错)。
    - 两侧一致 → 正常。
    - 不一致 → 回退/内部换模型发生,建议探测。
    """
    actual = str(actual_model or "").strip()
    current = str(current_model or "").strip()
    if not actual:
        return WatchVerdict(False, "响应未携带模型名,跳过判定")
    if not current:
        return WatchVerdict(False, "会话无当前模型,跳过判定")
    if actual == current:
        return WatchVerdict(False, "响应与会话当前模型一致")
    return WatchVerdict(
        True,
        f"响应模型 {actual} 与会话当前模型 {current} 不一致,疑似发生过回退",
    )


def should_trigger(now: float, last_trigger_ts: float, cooldown_seconds: float) -> bool:
    """冷却控制:冷却期内不重复触发。首次(last=0)直接放行。"""
    if cooldown_seconds <= 0:
        return True
    if last_trigger_ts <= 0:
        return True
    return (now - last_trigger_ts) >= cooldown_seconds


def should_skip_scheduled(
    now: float, last_chat_success_ts: float, window_seconds: float
) -> bool:
    """定时检测跳过判断:距上次成功对话过近时,本轮不测。

    成功对话(LLM 正常响应)本身就是"模型此刻可用"的实锤,
    窗口内再定时探测属于重复劳动。窗口 <= 0 或无聊天记录时不跳过。
    """
    if window_seconds <= 0:
        return False
    if last_chat_success_ts <= 0:
        return False
    return (now - last_chat_success_ts) < window_seconds
