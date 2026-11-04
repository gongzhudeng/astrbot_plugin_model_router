"""候选列表图测试:数据组装、文字兜底、模板渲染。"""

import time

from core.dashboard import (
    LIST_TEMPLATE,
    build_list_context,
    build_list_rows,
    build_list_text,
)

NOW = time.time()


def _state(**history):
    return {"history": history}


def test_list_rows_without_history():
    rows = build_list_rows(
        order_ids=["a", "b"],
        name_map={"a": "A 模型", "b": "B 模型"},
        current_id="a",
        state=_state(),
    )
    assert [r["index"] for r in rows] == [1, 2]
    assert rows[0]["is_current"] is True and rows[1]["is_current"] is False
    assert rows[0]["status"] == "none"
    assert rows[0]["status_label"] == "未测"
    assert rows[0]["latency_text"] == ""
    assert rows[0]["time_text"] == ""


def test_list_rows_with_history():
    state = _state(
        a=[{"ts": NOW - 3600, "status": "ok", "latency_ms": 3200, "error": ""}],
        b=[{"ts": NOW - 7200, "status": "error", "latency_ms": 50100, "error": "HTTP 502"}],
    )
    rows = build_list_rows(
        order_ids=["a", "b", "c"],
        name_map={},
        current_id="b",
        state=state,
    )
    a, b, c = rows
    assert a["status_label"] == "合格" and a["latency_text"] == "3.2s"
    assert a["time_text"]  # 有格式化后的时间
    assert b["status_label"] == "失败" and b["latency_text"] == "—"
    assert b["is_current"] is True
    assert c["status"] == "none"


def test_list_text_fallback():
    rows = build_list_rows(
        order_ids=["a", "b"],
        name_map={"a": "A", "b": "B"},
        current_id="a",
        state=_state(a=[{"ts": NOW, "status": "slow", "latency_ms": 38500, "error": ""}]),
    )
    text = build_list_text(rows=rows, current_display="A")
    assert "共 2 个" in text
    assert "1. A" in text and "✅当前" in text
    assert "上次超线 38.5s" in text


def test_list_template_renders():
    """模板必须能被 jinja2 正常渲染(html_render 用同一语法)。"""
    from jinja2 import Template

    rows = build_list_rows(
        order_ids=["a", "b"],
        name_map={"a": "A(模型1)", "b": "B(模型2)"},
        current_id="a",
        state=_state(b=[{"ts": NOW, "status": "error", "latency_ms": 50000, "error": "x"}]),
    )
    ctx = build_list_context(rows=rows, current_display="A(模型1)", generated_at="2026-10-30 17:00:00")
    html = Template(LIST_TEMPLATE).render(**ctx)
    assert "候选模型" in html
    assert "A(模型1)" in html and "B(模型2)" in html
    assert "当前使用" in html
    assert "&lt;编号&gt;" in html  # 模板内实体原样保留,浏览器显示为 <编号>
    assert "&amp;lt;" not in html  # 不得被二次转义
