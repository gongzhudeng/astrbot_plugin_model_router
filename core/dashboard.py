"""状态图:数据组装(纯函数)+ HTML 模板(html_render 用)。

视觉风格抄自 astrbot_plugin_model_connectivity 的深色卡片看板:
深底 + 三色光晕 + 网格线、毛玻璃卡片、发光状态 pill 与指示灯、渐变大标题。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .prober import ProbeResult
from .storage import STATUS_LABELS, ModelStats, aggregate

STATUS_COLORS = {
    "ok": "#18e78d",
    "slow": "#ffb11a",
    "error": "#ff4d5e",
    "skipped": "#3a3f4a",
}

_WINDOW_SECONDS = 7 * 86400.0


@dataclass(slots=True)
class RowData:
    """状态图里一行模型的数据。"""

    index: int
    provider_id: str
    display: str
    status: str
    status_label: str
    latency_text: str
    rate_text: str
    avg_latency_text: str
    error_text: str
    stripe: list[str]  # 颜色列表,最近 N 条


def build_rows(
    *,
    state: Mapping[str, Any],
    results: Sequence[ProbeResult],
    order_ids: Sequence[str],
    name_map: Mapping[str, str],
    now: float,
    recent_n: int,
) -> list[RowData]:
    """按优先级顺序组装每行数据;本轮没测到的模型也照常显示。"""
    results_by_id = {r.provider_id: r for r in results}
    history_all: Mapping[str, Any] = state.get("history", {})
    rows: list[RowData] = []
    for index, pid in enumerate(order_ids, start=1):
        result = results_by_id.get(pid)
        status = result.status if result is not None else "skipped"
        stats: ModelStats = aggregate(
            history_all.get(pid, []),
            now=now,
            window_seconds=_WINDOW_SECONDS,
            recent_n=recent_n,
        )
        rows.append(
            RowData(
                index=index,
                provider_id=pid,
                display=name_map.get(pid, pid),
                status=status,
                status_label=STATUS_LABELS.get(status, status),
                latency_text=_latency_text(result),
                rate_text=_rate_text(stats),
                avg_latency_text=_avg_latency_text(stats),
                error_text=(result.error if result is not None else ""),
                stripe=[STATUS_COLORS.get(s, STATUS_COLORS["skipped"]) for s in _stripe_statuses(stats)],
            )
        )
    return rows


def _stripe_statuses(stats: ModelStats) -> list[str]:
    return [str(item.get("status") or "skipped") for item in stats.recent]


def _latency_text(result: ProbeResult | None) -> str:
    if result is None or result.status == "skipped":
        return "—"
    if result.status == "error" and result.latency_ms <= 0:
        return "—"
    return f"{result.latency_ms / 1000:.1f}s"


def _rate_text(stats: ModelStats) -> str:
    if stats.available_rate is None:
        return "积累中"
    return f"{stats.available_rate * 100:.0f}%"


def _avg_latency_text(stats: ModelStats) -> str:
    if stats.avg_latency_ms is None:
        return "—"
    return f"{stats.avg_latency_ms / 1000:.1f}s"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800;900&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    margin: 0;
    width: 1500px;
    color: #f5f7fb;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(circle at 15% 50%, rgba(20, 255, 140, 0.08), transparent 30%),
      radial-gradient(circle at 85% 30%, rgba(255, 77, 94, 0.08), transparent 30%),
      radial-gradient(circle at 50% 80%, rgba(26, 177, 255, 0.08), transparent 30%),
      linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px),
      #0a0b0c;
    background-size: 100% 100%, 100% 100%, 100% 100%, 38px 38px, 38px 38px;
  }

  .page { padding: 48px; }

  .topline {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 24px;
    margin-bottom: 30px;
  }

  .eyebrow {
    display: flex;
    align-items: center;
    gap: 12px;
    color: #9499a3;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
  }

  .pulse-icon {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 10px;
    color: #000;
    background: linear-gradient(135deg, #fff, #e0e5ec);
    box-shadow: 0 4px 12px rgba(255, 255, 255, 0.15);
    font-size: 16px;
  }

  .title-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 18px;
    margin: 14px 0 0;
  }

  h1 {
    margin: 0;
    font-size: 52px;
    line-height: 1;
    font-weight: 900;
    letter-spacing: -1px;
    background: linear-gradient(135deg, #ffffff, #a0a5b0);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .glass-pill {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 10px 17px;
    border-radius: 999px;
    border: 1px solid rgba(176, 196, 222, 0.35);
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.20), rgba(255, 255, 255, 0.05) 55%, rgba(0, 0, 0, 0.04)),
      rgba(30, 41, 59, 0.20);
    backdrop-filter: blur(26px) saturate(170%);
    -webkit-backdrop-filter: blur(26px) saturate(170%);
    box-shadow: 0 12px 26px rgba(0, 0, 0, 0.24), inset 0 1px 1px rgba(255, 255, 255, 0.30);
    color: #e2e8f0;
    font-size: 15px;
    font-weight: 800;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 26px;
    padding-top: 26px;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border-radius: 999px;
    padding: 8px 14px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0.02) 100%);
    backdrop-filter: blur(20px) saturate(150%);
    -webkit-backdrop-filter: blur(20px) saturate(150%);
    box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2), inset 0 1px 1px rgba(255, 255, 255, 0.1);
    color: #d9dde5;
    font-size: 14px;
    font-weight: 700;
  }

  .pill .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 8px currentColor;
  }

  .pill.ok { color: #18e78d; background: linear-gradient(135deg, rgba(11, 159, 93, 0.2), rgba(11, 159, 93, 0.05)); border-color: rgba(24, 231, 141, 0.3); box-shadow: 0 0 15px rgba(24, 231, 141, 0.15), inset 0 1px 1px rgba(255, 255, 255, 0.1); }
  .pill.slow { color: #ffb11a; background: linear-gradient(135deg, rgba(255, 177, 26, 0.2), rgba(255, 177, 26, 0.05)); border-color: rgba(255, 177, 26, 0.3); box-shadow: 0 0 15px rgba(255, 177, 26, 0.15), inset 0 1px 1px rgba(255, 255, 255, 0.1); }
  .pill.error { color: #ff4d5e; background: linear-gradient(135deg, rgba(255, 77, 94, 0.2), rgba(255, 77, 94, 0.05)); border-color: rgba(255, 77, 94, 0.3); box-shadow: 0 0 15px rgba(255, 77, 94, 0.15), inset 0 1px 1px rgba(255, 255, 255, 0.1); }
  .pill.skipped { color: #8b9099; background: linear-gradient(135deg, rgba(139, 144, 153, 0.16), rgba(139, 144, 153, 0.04)); border-color: rgba(139, 144, 153, 0.3); }

  .card {
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 23px;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.062) 0%, rgba(255, 255, 255, 0.014) 100%);
    backdrop-filter: blur(40px) saturate(180%);
    -webkit-backdrop-filter: blur(40px) saturate(180%);
    box-shadow: 0 16px 38px rgba(0, 0, 0, 0.18), inset 0 1px 1px rgba(255, 255, 255, 0.08);
    overflow: hidden;
    padding: 14px 18px 18px;
  }

  .card::before {
    content: "";
    display: block;
    height: 1px;
    margin: 0 8px 8px;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.18), transparent);
  }

  .model-row {
    display: grid;
    grid-template-columns: 10px minmax(0, 420px) 132px 150px 150px 132px minmax(240px, 1fr);
    align-items: center;
    gap: 16px;
    min-height: 74px;
    padding: 14px 18px 14px 12px;
    border: 1px solid rgba(255, 255, 255, 0.065);
    border-radius: 16px;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.042) 0%, rgba(255, 255, 255, 0.012) 100%);
    box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.045);
  }

  .model-row + .model-row { margin-top: 10px; }

  .rail {
    justify-self: center;
    width: 6px;
    height: 42px;
    border-radius: 999px;
    background: #3a3f4a;
  }

  .rail.ok { background: #18e78d; box-shadow: 0 0 12px rgba(24, 231, 141, 0.55); }
  .rail.slow { background: #ffb11a; box-shadow: 0 0 12px rgba(255, 177, 26, 0.55); }
  .rail.error { background: #ff4d5e; box-shadow: 0 0 12px rgba(255, 77, 94, 0.55); }

  .model-cell { min-width: 0; }

  .model-cell .idx {
    display: inline-block;
    margin-right: 10px;
    color: #566072;
    font-size: 15px;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
  }

  .model-cell .name {
    color: #f5f7fb;
    font-size: 19px;
    font-weight: 850;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    vertical-align: middle;
    max-width: 360px;
    display: inline-block;
  }

  .model-cell .pid {
    margin: 5px 0 0 2px;
    color: #8b9099;
    font-size: 12px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .metric { min-width: 0; }

  .metric .k {
    color: #7d828c;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.5px;
    margin-bottom: 5px;
  }

  .metric .v {
    color: #eef1f6;
    font-size: 17px;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .stripe { display: flex; gap: 4px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }

  .stripe span {
    width: 12px;
    height: 26px;
    border-radius: 3px;
    display: inline-block;
  }

  .err {
    margin: 8px 4px 0 28px;
    color: #ff8b96;
    font-size: 13px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .foot {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    margin-top: 26px;
  }

  .foot-card {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 20px;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 16px;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.055) 0%, rgba(255, 255, 255, 0.012) 100%);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.05);
  }

  .foot-card .k { color: #7d828c; font-size: 13px; font-weight: 700; white-space: nowrap; flex-shrink: 0; }

  .foot-card .v {
    color: #ffffff;
    font-size: 15px;
    font-weight: 800;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .switch-line {
    margin-top: 18px;
    display: flex;
    align-items: center;
    gap: 10px;
    color: #a7abb2;
    font-size: 15px;
    font-weight: 700;
  }

  .switch-line .dot { width: 8px; height: 8px; border-radius: 50%; background: #18e78d; box-shadow: 0 0 8px #18e78d; }
</style>
</head>
<body>
<div class="page">
  <div class="topline">
    <div>
      <div class="eyebrow"><span class="pulse-icon">⚡</span>MODEL ROUTER</div>
      <div class="title-row">
        <h1>模型路由</h1>
        <span class="glass-pill"><span style="color:#18e78d;">●</span>{{ generated_at }}</span>
      </div>
    </div>
    <div class="summary">
      <span class="pill ok"><span class="dot"></span>{{ ok_count }} 合格</span>
      <span class="pill slow"><span class="dot"></span>{{ slow_count }} 超线</span>
      <span class="pill error"><span class="dot"></span>{{ error_count }} 失败</span>
      <span class="pill skipped"><span class="dot"></span>{{ skipped_count }} 未测</span>
    </div>
  </div>

  <div class="card">
    {% for row in rows %}
    <div class="model-row">
      <div class="rail {{ row.status }}"></div>
      <div class="model-cell">
        <span class="idx">{{ '%02d' % row.index }}</span><span class="name" title="{{ row.provider_id }}">{{ row.display }}</span>
        <div class="pid">{{ row.provider_id }}</div>
      </div>
      <div class="metric"><div class="k">状态</div><div class="v" style="color: {{ status_colors[row.status] }};">{{ row.status_label }}</div></div>
      <div class="metric"><div class="k">本轮延迟</div><div class="v">{{ row.latency_text }}</div></div>
      <div class="metric"><div class="k">7天可用</div><div class="v">{{ row.rate_text }}</div></div>
      <div class="metric"><div class="k">均延迟</div><div class="v">{{ row.avg_latency_text }}</div></div>
      <div class="stripe">
        {% for color in row.stripe %}<span style="background: {{ color }};"></span>{% endfor %}
      </div>
    </div>
    {% if row.error_text %}
    <div class="err">⚠ {{ row.error_text }}</div>
    {% endif %}
    {% endfor %}
  </div>

  <div class="foot">
    <div class="foot-card"><span class="k">当前会话模型</span><span class="v">{{ current_display or "默认" }}</span></div>
    <div class="foot-card"><span class="k">路由建议</span><span class="v">{{ selected_display or "不切换" }}</span></div>
    <div class="foot-card"><span class="k">免检测时段</span><span class="v">{{ quiet_text }}</span></div>
    <div class="foot-card"><span class="k">下次自动检测</span><span class="v">{{ next_run_text }}</span></div>
  </div>

  {% if switch_text %}
  <div class="switch-line"><span class="dot"></span>{{ switch_text }}</div>
  {% endif %}
</div>
</body>
</html>
"""


def build_dashboard_context(
    *,
    rows: Sequence[RowData],
    generated_at: str,
    current_display: str | None,
    selected_display: str | None,
    quiet_text: str,
    next_run_text: str,
    switch_text: str,
) -> dict[str, Any]:
    """组装模板上下文(jinja2 变量),汇总计数从 rows 直接统计。"""
    return {
        "rows": [asdict(row) for row in rows],
        "status_colors": STATUS_COLORS,
        "generated_at": generated_at,
        "ok_count": sum(1 for r in rows if r.status == "ok"),
        "slow_count": sum(1 for r in rows if r.status == "slow"),
        "error_count": sum(1 for r in rows if r.status == "error"),
        "skipped_count": sum(1 for r in rows if r.status == "skipped"),
        "current_display": current_display,
        "selected_display": selected_display,
        "quiet_text": quiet_text,
        "next_run_text": next_run_text,
        "switch_text": switch_text,
    }


def build_text_summary(
    *,
    rows: Sequence[RowData],
    current_display: str | None,
    selected_display: str | None,
    quiet_text: str,
) -> str:
    """渲染失败时的文字兜底。"""
    lines = ["📡 模型路由状态"]
    for row in rows:
        lines.append(f"{row.index}. {row.display} [{row.status_label}] 本轮 {row.latency_text} · 7天可用 {row.rate_text}")
    lines.append(f"当前:{current_display or '默认'} → 建议:{selected_display or '不切换'}")
    lines.append(f"免检测时段:{quiet_text}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 候选列表图(/模型路由 列表)
# ----------------------------------------------------------------------

_LIST_STATUS_COLORS = {
    "ok": "#18e78d",
    "slow": "#ffb11a",
    "error": "#ff4d5e",
    "none": "#8b9099",
}


def build_list_rows(
    *,
    order_ids: Sequence[str],
    name_map: Mapping[str, str],
    current_id: str | None,
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """组装候选列表行:编号、名称、是否当前、上次探测状态/延迟/时间。"""
    history: Mapping[str, Any] = state.get("history") or {}
    rows: list[dict[str, Any]] = []
    for index, pid in enumerate(order_ids, start=1):
        records = history.get(pid) or []
        last = records[-1] if records else None
        status = str(last.get("status")) if last else "none"
        label = STATUS_LABELS.get(status, "未测")
        latency_text = ""
        time_text = ""
        if last is not None:
            latency_text = (
                f"{int(last.get('latency_ms', 0) or 0) / 1000:.1f}s"
                if status in ("ok", "slow")
                else "—"
            )
            ts = last.get("ts")
            if ts:
                try:
                    time_text = datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
                except (TypeError, ValueError, OSError):
                    time_text = ""
        rows.append(
            {
                "index": index,
                "display": name_map.get(pid, pid),
                "provider_id": pid,
                "is_current": pid == current_id,
                "status": status,
                "status_label": label,
                "color": _LIST_STATUS_COLORS.get(status, _LIST_STATUS_COLORS["none"]),
                "latency_text": latency_text,
                "time_text": time_text,
            }
        )
    return rows


def build_list_context(
    *,
    rows: Sequence[Mapping[str, Any]],
    current_display: str | None,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "rows": list(rows),
        "current_display": current_display or "默认",
        "generated_at": generated_at,
        "count": len(rows),
    }


def build_list_text(
    *,
    rows: Sequence[Mapping[str, Any]],
    current_display: str | None,
) -> str:
    """列表图渲染失败时的文字兜底。"""
    lines = [f"📋 模型路由候选(共 {len(rows)} 个,当前:{current_display or '默认'})"]
    for row in rows:
        mark = "  ✅当前" if row.get("is_current") else ""
        extra = ""
        if row.get("status") in ("ok", "slow"):
            extra = f" · 上次{row.get('status_label')} {row.get('latency_text')}"
        elif row.get("status") == "error":
            extra = f" · 上次{row.get('status_label')}"
        lines.append(f"{row.get('index')}. {row.get('display')}{mark}{extra}")
    lines.append("")
    lines.append("发送 /模型路由 <编号> 即可切换(仅影响当前对话,下轮检测会按优先级归位)")
    return "\n".join(lines)


LIST_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800;900&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    margin: 0;
    width: 1200px;
    color: #f5f7fb;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(circle at 15% 50%, rgba(20, 255, 140, 0.08), transparent 30%),
      radial-gradient(circle at 85% 30%, rgba(255, 77, 94, 0.08), transparent 30%),
      radial-gradient(circle at 50% 80%, rgba(26, 177, 255, 0.08), transparent 30%),
      linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px),
      #0a0b0c;
    background-size: 100% 100%, 100% 100%, 100% 100%, 38px 38px, 38px 38px;
  }

  .page { padding: 44px 48px 40px; }

  .eyebrow {
    display: flex;
    align-items: center;
    gap: 12px;
    color: #9499a3;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
  }

  .pulse-icon {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    border-radius: 10px;
    color: #000;
    background: linear-gradient(135deg, #fff, #e0e5ec);
    box-shadow: 0 4px 12px rgba(255, 255, 255, 0.15);
    font-size: 16px;
  }

  .title-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 18px;
    margin: 14px 0 0;
  }

  h1 {
    margin: 0;
    font-size: 48px;
    line-height: 1;
    font-weight: 900;
    letter-spacing: -1px;
    background: linear-gradient(135deg, #ffffff, #a0a5b0);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .glass-pill {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 10px 17px;
    border-radius: 999px;
    border: 1px solid rgba(176, 196, 222, 0.35);
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.20), rgba(255, 255, 255, 0.05) 55%, rgba(0, 0, 0, 0.04)),
      rgba(30, 41, 59, 0.20);
    backdrop-filter: blur(26px) saturate(170%);
    -webkit-backdrop-filter: blur(26px) saturate(170%);
    box-shadow: 0 12px 26px rgba(0, 0, 0, 0.24), inset 0 1px 1px rgba(255, 255, 255, 0.30);
    color: #e2e8f0;
    font-size: 15px;
    font-weight: 800;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin: 22px 0 24px;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border-radius: 999px;
    padding: 8px 14px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0.02) 100%);
    color: #d9dde5;
    font-size: 14px;
    font-weight: 700;
  }

  .card {
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 23px;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.062) 0%, rgba(255, 255, 255, 0.014) 100%);
    backdrop-filter: blur(40px) saturate(180%);
    -webkit-backdrop-filter: blur(40px) saturate(180%);
    box-shadow: 0 16px 38px rgba(0, 0, 0, 0.18), inset 0 1px 1px rgba(255, 255, 255, 0.08);
    overflow: hidden;
    padding: 14px 18px 18px;
  }

  .card::before {
    content: "";
    display: block;
    height: 1px;
    margin: 0 8px 8px;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.18), transparent);
  }

  .model-row {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr) 120px 120px 170px;
    align-items: center;
    gap: 16px;
    min-height: 74px;
    padding: 12px 20px 12px 14px;
    border: 1px solid rgba(255, 255, 255, 0.065);
    border-radius: 16px;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.042) 0%, rgba(255, 255, 255, 0.012) 100%);
    box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.045);
  }

  .model-row + .model-row { margin-top: 10px; }

  .idx {
    color: #566072;
    font-size: 26px;
    font-weight: 900;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .name-wrap { min-width: 0; }

  .name {
    color: #f5f7fb;
    font-size: 19px;
    font-weight: 850;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pid {
    margin-top: 4px;
    color: #8b9099;
    font-size: 12px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cur {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 999px;
    color: #18e78d;
    border: 1px solid rgba(24, 231, 141, 0.35);
    background: linear-gradient(135deg, rgba(11, 159, 93, 0.2), rgba(11, 159, 93, 0.05));
    font-size: 13px;
    font-weight: 800;
    white-space: nowrap;
    text-align: center;
  }

  .cur.empty {
    color: #4a5160;
    border-color: rgba(255, 255, 255, 0.08);
    background: transparent;
  }

  .status {
    display: inline-block;
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 800;
    text-align: center;
  }

  .meta {
    color: #9aa3b5;
    font-size: 14px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .meta .t {
    color: #6f7788;
    font-size: 12px;
    font-weight: 600;
    margin-left: 6px;
  }

  .foot {
    margin-top: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
    color: #a7abb2;
    font-size: 14px;
    font-weight: 700;
  }

  .foot .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #18e78d;
    box-shadow: 0 0 8px #18e78d;
  }
</style>
</head>
<body>
<div class="page">
  <div class="eyebrow"><span class="pulse-icon">⚡</span>MODEL ROUTER</div>
  <div class="title-row">
    <h1>候选模型</h1>
    <span class="glass-pill"><span style="color:#18e78d;">●</span>{{ generated_at }}</span>
  </div>

  <div class="summary">
    <span class="pill">共 {{ count }} 个候选</span>
    <span class="pill">当前:{{ current_display }}</span>
  </div>

  <div class="card">
    {% for row in rows %}
    <div class="model-row">
      <div class="idx">{{ '%02d' % row.index }}</div>
      <div class="name-wrap">
        <div class="name" title="{{ row.provider_id }}">{{ row.display }}</div>
        <div class="pid">{{ row.provider_id }}</div>
      </div>
      {% if row.is_current %}<div class="cur">当前使用</div>{% else %}<div class="cur empty">—</div>{% endif %}
      <div class="status" style="color: {{ row.color }}; border: 1px solid {{ row.color }}55;">{{ row.status_label }}</div>
      <div class="meta">{{ row.latency_text }}{% if row.time_text %}<span class="t">{{ row.time_text }}</span>{% endif %}</div>
    </div>
    {% endfor %}
  </div>

  <div class="foot"><span class="dot"></span>发送 /模型路由 &lt;编号&gt; 即可切换(仅影响当前对话,下轮检测会按优先级归位)</div>
</div>
</body>
</html>
"""
