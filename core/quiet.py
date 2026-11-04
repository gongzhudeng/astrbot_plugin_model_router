"""免检测时段:解析与判断,纯逻辑。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping


def parse_hhmm(text: str) -> tuple[int, int] | None:
    """解析 HH:MM,非法返回 None。"""
    parts = str(text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def quiet_window(
    config: Mapping[str, object],
    *,
    enabled_key: str = "quiet_enabled",
    start_key: str = "quiet_start",
    end_key: str = "quiet_end",
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """从配置读取免检测时段;未启用或配置非法(含 start==end)返回 None。"""
    if not bool(config.get(enabled_key, False)):
        return None
    start = parse_hhmm(str(config.get(start_key, "")))
    end = parse_hhmm(str(config.get(end_key, "")))
    if start is None or end is None or start == end:
        return None
    return start, end


def in_quiet(now: datetime, window: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    """判断 now 是否在免检测时段内,支持跨午夜(开始 > 结束)。"""
    start, end = window
    minutes = now.hour * 60 + now.minute
    start_m, end_m = start[0] * 60 + start[1], end[0] * 60 + end[1]
    if start_m < end_m:
        return start_m <= minutes < end_m
    return minutes >= start_m or minutes < end_m  # 跨午夜


def quiet_end_datetime(now: datetime, window: tuple[tuple[int, int], tuple[int, int]]) -> datetime:
    """下一次免检测结束时刻(用于结束后立即补测一轮)。"""
    _, end = window
    candidate = now.replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
