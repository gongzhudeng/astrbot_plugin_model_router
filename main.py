"""模型路由插件(astrbot_plugin_model_router)

对话模型自动路由:按优先级链接力探查模型可用性,自动切换当前会话模型。

- 探测:真实调用 provider.text_chat 发送轻量提示词,干净单轮。
- 接力:前者超过合格线仍无结果就启动下一个,前者继续跑到放弃线;合格即停。
- 选择:合格者按优先级取最靠前;全超线取成功返回里延迟最低;全失败不动。
- 切换:provider_manager.set_provider(umo),与 /provider 命令完全等价。
"""

from __future__ import annotations

import asyncio
import random
import time
import traceback
from datetime import datetime
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.provider import LLMResponse, ProviderType
from astrbot.api.star import Context, Star, register

from .core import dashboard, quiet, router as router_core, watcher
from .core.prober import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_SLOW,
    ProbeResult,
    ProbeTarget,
    relay_probe,
)
from .core.storage import (
    StateStore,
    consecutive_failures,
    record_round,
    should_auto_skip,
    should_notice_long_unavailable,
)

_DEFAULT_PROBE_PROMPT = "请只回复 OK,不要任何其他内容。"
_LOOP_TICK_SECONDS = 30.0
_REVIVE_CHECK_SECONDS = 86400.0
# 单轮检测总预算:超时放弃本轮,保证定时循环不被个别挂起的调用拖死
_ROUND_BUDGET_SECONDS = 240.0


@register(
    "astrbot_plugin_model_router",
    "Mando",
    "对话模型自动路由:按优先级接力探查可用性并自动切换当前会话模型",
    "1.0.0",
)
class ModelRouterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._loop_task: asyncio.Task | None = None
        self._probing = False
        self._next_run_at = 0.0
        self._last_watch_trigger = 0.0
        self._watch_tasks: set[asyncio.Task] = set()
        self._llm_start: dict[str, float] = {}
        self._last_chat_success_ts = 0.0

    async def initialize(self) -> None:
        interval = self._cfg_float("interval_hours", 1.0)
        self._next_run_at = (
            time.time() + self._next_delay_seconds(max(interval, 0.05))
            if interval > 0
            else 0.0
        )
        self._loop_task = asyncio.create_task(self._auto_loop())
        logger.info(
            "[模型路由] 初始化完成 priority=%s interval=%.2fh quiet=%s",
            len(self._cfg_list("provider_priority")),
            interval,
            self._cfg_bool("quiet_enabled", False),
        )

    async def terminate(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._loop_task = None
        for task in list(self._watch_tasks):
            task.cancel()
        self._watch_tasks.clear()
        logger.info("[模型路由] 已停止")

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return bool(self._cfg(key, default))

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_str(self, key: str, default: str = "") -> str:
        return str(self._cfg(key, default) or default)

    def _cfg_list(self, key: str) -> list:
        value = self._cfg(key, [])
        return list(value) if isinstance(value, list) else []

    # ------------------------------------------------------------------
    # 免检测时段
    # ------------------------------------------------------------------

    def _quiet_window(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        return quiet.quiet_window(self.config)

    # ------------------------------------------------------------------
    # 探测主流程
    # ------------------------------------------------------------------

    def _order_ids(self) -> list[str]:
        return [str(x).strip() for x in self._cfg_list("provider_priority") if str(x).strip()]

    async def _probe_fn(self, target: ProbeTarget) -> None:
        """单次探测:成功正常返回,失败抛异常;超时由 relay_probe 统一控制。"""
        prompt = self._cfg_str("probe_prompt", _DEFAULT_PROBE_PROMPT)
        await target.provider.text_chat(prompt=prompt)

    def _provider_display(self, provider_id: str) -> str | None:
        provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            return None
        try:
            model = provider.meta().model
        except Exception:  # noqa: BLE001
            model = ""
        return f"{provider_id}({model})" if model else provider_id

    async def _current_provider_id(self) -> str | None:
        """读取被管理会话当前使用的 provider id;读不到返回 None(不抛异常)。"""
        target_umo = self._cfg_str("target_umo")
        if not target_umo:
            return None
        try:
            current = await self.context.get_using_provider_async(umo=target_umo)
            return current.meta().id if current else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[模型路由] 读取当前模型失败: %s", exc)
            return None

    async def run_probe_round(self, trigger: str) -> dict[str, Any] | None:
        """一轮完整流程:探测 → 记录 → 决策 → 切换 → 通知。返回结果上下文。"""
        order_ids = self._order_ids()
        if not order_ids:
            logger.warning("[模型路由] 优先级链为空,跳过本轮(%s)", trigger)
            return None
        if self._probing:
            logger.info("[模型路由] 上一轮探测仍在进行,跳过本轮(%s)", trigger)
            return None
        self._probing = True
        try:
            return await self._run_probe_round_inner(order_ids, trigger)
        finally:
            self._probing = False

    async def _run_probe_round_inner(
        self, order_ids: list[str], trigger: str
    ) -> dict[str, Any] | None:
        target_umo = self._cfg_str("target_umo")
        store = StateStore(self)
        state = await store.load()
        now_ts = time.time()

        # 组探测目标:不存在的剔除;自动跳过的按复活检查放行
        skip_threshold = self._cfg_int("auto_skip_consecutive", 0)
        targets: list[ProbeTarget] = []
        results_by_id: dict[str, ProbeResult] = {
            pid: ProbeResult(provider_id=pid, status=STATUS_SKIPPED) for pid in order_ids
        }
        for pid in order_ids:
            provider = self.context.get_provider_by_id(pid)
            if provider is None:
                logger.warning("[模型路由] 模型 %s 不存在,已跳过(请检查优先级链)", pid)
                continue
            if should_auto_skip(
                state, pid, skip_threshold=skip_threshold, now=now_ts,
                revive_check_seconds=_REVIVE_CHECK_SECONDS,
            ):
                logger.info("[模型路由] %s 连续失败进入跳过期,本轮不测(每 24h 复测)", pid)
                continue
            targets.append(ProbeTarget(provider_id=pid, provider=provider))

        # 探测
        probed = await relay_probe(
            targets,
            self._probe_fn,
            eligible_timeout=self._cfg_float("eligible_timeout_seconds", 30.0),
            probe_deadline=self._cfg_float("probe_deadline_seconds", 50.0),
        )
        for result in probed:
            results_by_id[result.provider_id] = result
        all_results = [results_by_id[pid] for pid in order_ids]

        # 记录 + 长期不可用提醒
        before_fails = {pid: consecutive_failures(state, pid) for pid in order_ids}
        record_round(
            state, all_results, ts=now_ts,
            history_max=self._cfg_int("history_max_per_model", 200),
        )
        notice_threshold = self._cfg_int("fail_notice_consecutive", 24)
        for pid in order_ids:
            after = consecutive_failures(state, pid)
            if after > 0 and should_notice_long_unavailable(
                before_fails[pid], after, notice_threshold=notice_threshold
            ):
                # 只写日志,不发消息(路由全程对聊天无感)
                logger.warning(
                    "[模型路由] %s", router_core.build_long_unavailable_notice(pid, after)
                )
        await store.save(state)

        # 决策 + 切换
        decision = router_core.decide(all_results, order_ids)
        current_id = await self._current_provider_id()
        current_status = router_core.current_status_of(all_results, current_id)

        switch_kind: str | None = None
        switch_text = ""
        if target_umo and router_core.should_switch(
            decision, current_id, current_status,
            force_align=self._cfg_bool("force_align", True),
        ):
            try:
                await self.context.provider_manager.set_provider(
                    provider_id=decision.selected_id,
                    provider_type=ProviderType.CHAT_COMPLETION,
                    umo=target_umo,
                )
                switch_kind = "fail" if current_status == STATUS_ERROR else "normal"
                switch_text = (
                    f"已切换至 {decision.selected_id}"
                    f"({decision.selected_latency_ms / 1000:.1f}s)"
                    if switch_kind == "normal"
                    else f"{current_id} 失败({results_by_id[current_id].error}),已切换至 {decision.selected_id}"
                )
                logger.info("[模型路由] %s -> %s(%s)", current_id, decision.selected_id, switch_kind)
            except Exception as exc:  # noqa: BLE001
                logger.error("[模型路由] 切换失败: %s\n%s", exc, traceback.format_exc())
        else:
            switch_text = f"保持 {current_id}" if current_id else "未切换"

        # 组装状态图上下文
        name_map = {pid: (self._provider_display(pid) or pid) for pid in order_ids}
        rows = dashboard.build_rows(
            state=state,
            results=all_results,
            order_ids=order_ids,
            name_map=name_map,
            now=now_ts,
            recent_n=20,
        )
        window = self._quiet_window()
        quiet_text = "关闭"
        if window is not None:
            quiet_text = (
                f"{self._cfg_str('quiet_start')} - {self._cfg_str('quiet_end')}"
                + ("(免检测中)" if quiet.in_quiet(datetime.now(), window) else "")
            )
        ctx = dashboard.build_dashboard_context(
            rows=rows,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            current_display=name_map.get(current_id, current_id) if current_id else None,
            selected_display=(
                name_map.get(decision.selected_id, decision.selected_id)
                if decision.selected_id and decision.reason != router_core.REASON_ALL_FAILED
                else None
            ),
            quiet_text=quiet_text,
            next_run_text=self._next_run_text(),
            switch_text=switch_text,
        )
        logger.info(
            "[模型路由] 本轮完成(%s):建议=%s 原因=%s 切换=%s",
            trigger, decision.selected_id, decision.reason, switch_kind or "无",
        )
        return {
            "dashboard": ctx,
            "decision": decision,
            "results": all_results,
            "name_map": name_map,
            "current_id": current_id,
            "switch_kind": switch_kind,
        }

    def _next_run_text(self) -> str:
        interval = self._cfg_float("interval_hours", 1.0)
        if interval <= 0 or self._next_run_at <= 0:
            return "已关闭"
        return datetime.fromtimestamp(self._next_run_at).strftime("%H:%M")

    async def _render_and_send(self, event: AstrMessageEvent, round_ctx: dict[str, Any] | None) -> None:
        """渲染状态图并发回会话;渲染失败回退文字摘要。"""
        if round_ctx is None:
            event.set_result(
                MessageEventResult().message("⚠️ 优先级链为空或上一轮仍在进行,未执行。请先在 WebUI 配置模型优先级链。")
            )
            return
        ctx = round_ctx["dashboard"]
        try:
            output = await self.html_render(
                dashboard.HTML_TEMPLATE,
                ctx,
                return_url=True,
                options={
                    "type": "png",
                    "full_page": True,
                    "timeout": self._cfg_int("render_timeout_seconds", 90) * 1000,
                    "animations": "disabled",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[模型路由] 状态图渲染失败,回退文字: %s", exc)
            rows = [dashboard.RowData(**row) for row in ctx["rows"]]
            event.set_result(
                MessageEventResult().message(
                    dashboard.build_text_summary(
                        rows=rows,
                        current_display=ctx["current_display"],
                        selected_display=ctx["selected_display"],
                        quiet_text=ctx["quiet_text"],
                    )
                )
            )
            return
        result = MessageEventResult()
        if isinstance(output, str) and output.startswith(("http://", "https://")):
            result.url_image(output)
        else:
            result.file_image(str(output))
        event.set_result(result)

    # ------------------------------------------------------------------
    # 回退感知(救火)
    # ------------------------------------------------------------------

    @filter.on_llm_response()
    async def on_llm_response_watch(self, event: AstrMessageEvent, response: LLMResponse) -> None:
        """响应钩子:实时对比响应实际模型与会话当前模型,发现回退立即救火探测。

        - 每次响应时刻实时重读当前模型:用户手动切换天然被尊重,不会误判。
        - 绝不阻塞对话流程:探测以 fire-and-forget 任务执行。
        - 救火不受免检测时段限制(免检测只拦定时主动探测)。
        """
        try:
            await self._watch_fallback(event, response)
        except Exception:  # noqa: BLE001
            logger.error("[模型路由] 回退感知钩子异常:\n%s", traceback.format_exc())

    @filter.on_llm_request()
    async def on_llm_request_mark(self, event: AstrMessageEvent, req) -> None:
        """请求钩子:记录本会话本次 LLM 请求开始时间,供慢响应判定。"""
        try:
            if not self._cfg_bool("fallback_watch_enabled", True):
                return
            target_umo = self._cfg_str("target_umo")
            if not target_umo or event.unified_msg_origin != target_umo:
                return
            self._llm_start[event.unified_msg_origin] = time.time()
        except Exception:  # noqa: BLE001
            pass  # 计时失败不影响对话

    async def _watch_fallback(self, event: AstrMessageEvent, response: LLMResponse) -> None:
        target_umo = self._cfg_str("target_umo")
        if not target_umo or event.unified_msg_origin != target_umo:
            return
        # 成功响应 = 模型此刻可用的实锤,记录时间供定时检测跳过判断(与救火开关无关)
        self._last_chat_success_ts = time.time()
        if not self._cfg_bool("fallback_watch_enabled", True):
            return
        if not self._order_ids():
            return
        actual_model = getattr(response, "model", None)
        try:
            current = await self.context.get_using_provider_async(umo=target_umo)
        except Exception:  # noqa: BLE001
            return
        current_model = current.meta().model if current else None

        # 信号 1:回退 —— 响应实际模型与会话当前模型不一致
        verdict = watcher.judge(actual_model, current_model)
        # 信号 2:慢响应 —— 本次对话耗时超过独立阈值(与探测合格线无关)
        slow_text = ""
        slow_hit = False
        start = self._llm_start.pop(event.unified_msg_origin, None)
        slow_threshold = self._cfg_int("chat_slow_seconds", 30)
        if start is not None and slow_threshold > 0:
            elapsed = time.time() - start
            if elapsed >= slow_threshold:
                slow_hit = True
                slow_text = f"对话耗时 {elapsed:.0f}s 超过慢响应阈值 {slow_threshold}s"
        if not verdict.fallback_detected and not slow_hit:
            return
        reason = verdict.reason if verdict.fallback_detected else slow_text
        cooldown_seconds = self._cfg_int("fallback_cooldown_minutes", 5) * 60
        now = time.time()
        if not watcher.should_trigger(now, self._last_watch_trigger, cooldown_seconds):
            logger.info("[模型路由] 救火信号命中但处于冷却期:%s", reason)
            return
        self._last_watch_trigger = now
        logger.warning("[模型路由] 🔥 %s → 触发救火探测", reason)
        task = asyncio.create_task(self.run_probe_round(trigger="fallback_watch"))
        self._watch_tasks.add(task)
        task.add_done_callback(self._watch_tasks.discard)

    # ------------------------------------------------------------------
    # 定时循环
    # ------------------------------------------------------------------

    def _next_delay_seconds(self, interval_hours: float) -> float:
        """下一轮间隔 = 基础间隔 + 0~jitter 分钟随机抖动(防整点齐刷)。

        抖动必须在"设定下一轮时间"时一次性加入;
        绝不能做成"到点后再随机推后"——那会导致每次到点都被再次推后,永远跑不到执行。
        """
        delay = interval_hours * 3600
        jitter = self._cfg_int("jitter_minutes", 0)
        if jitter > 0:
            delay += random.randint(0, jitter * 60)
        return delay

    async def _auto_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_LOOP_TICK_SECONDS)
                if not self._cfg_bool("enabled", True):
                    continue
                interval = self._cfg_float("interval_hours", 1.0)
                order_ids = self._order_ids()
                if interval <= 0 or not order_ids:
                    continue
                window = self._quiet_window()
                if window is not None and quiet.in_quiet(datetime.now(), window):
                    # 免检测中:把下次运行定在时段结束(结束后立即补测一轮)
                    self._next_run_at = quiet.quiet_end_datetime(datetime.now(), window).timestamp()
                    continue
                if self._next_run_at > 0 and time.time() < self._next_run_at:
                    continue
                # 聊天冷却:仅当"当前模型就是链首"且距上次成功对话过近才跳过。
                # 当前不是链首时必须检测:聊天只验证了当前模型,首位是否恢复未知,
                # 需要照常探测,以便首位恢复后自动归位。
                skip_minutes = self._cfg_int("timer_skip_after_chat_minutes", 30)
                if skip_minutes > 0:
                    current_id = await self._current_provider_id()
                    if current_id == order_ids[0] and watcher.should_skip_scheduled(
                        time.time(), self._last_chat_success_ts, skip_minutes * 60
                    ):
                        age_min = (time.time() - self._last_chat_success_ts) / 60
                        logger.info(
                            "[模型路由] 定时检测跳过:当前已是链首且距上次成功对话仅 %.0f 分钟(窗口 %d 分钟)",
                            age_min,
                            skip_minutes,
                        )
                        self._next_run_at = time.time() + self._next_delay_seconds(interval)
                        continue
                logger.info("[模型路由] 定时检测开始(auto)")
                try:
                    await asyncio.wait_for(
                        self.run_probe_round(trigger="auto"),
                        timeout=_ROUND_BUDGET_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[模型路由] 本轮定时检测超过 %.0f 秒未完成,已放弃(循环保活,下轮继续)",
                        _ROUND_BUDGET_SECONDS,
                    )
                self._next_run_at = time.time() + self._next_delay_seconds(interval)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.error("[模型路由] 定时循环异常:\n%s", traceback.format_exc())
                await asyncio.sleep(_LOOP_TICK_SECONDS)

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    @filter.command("模型路由", alias={"ModelRoute", "modelroute"})
    async def model_route_cmd(
        self,
        event: AstrMessageEvent,
        sub: str | int | None = None,
        arg: str | int | None = None,
    ):
        """模型路由:直接发=全链检测;发编号=切换;发"测+编号"=单测。"""
        # AstrBot 会把纯数字参数自动解析成 int;统一转字符串再处理
        sub_norm = str(sub if sub is not None else "").strip().lower()
        arg_norm = str(arg if arg is not None else "").strip()
        # 编号容错:支持 "3" / "测3" / "测 3" / "测试 3" / "切换 3"
        number = router_core.extract_number(arg_norm) or router_core.extract_number(sub_norm)

        # 1) 纯数字 = 直接切换(与 /provider <编号> 同款手感)
        if sub_norm.isdigit():
            await self._switch_by_index(event, sub_norm)
            return

        # 2) 含"测":带编号 = 单测该编号;不带编号的"测试/检测" = 全链
        if sub_norm.startswith("测") or sub_norm in ("检测", "probe", "check"):
            if number is not None:
                await self._probe_single_by_index(event, number)
                return
            if sub_norm != "测":
                await self._run_full_round(event)
                return
            event.set_result(
                MessageEventResult().message(
                    "用法:/模型路由 测3(也认「测 3」),先用「/模型路由 列表」看编号。"
                )
            )
            return

        # 3) 含"切":带编号就切换
        if sub_norm.startswith("切") or sub_norm == "switch":
            if number is None:
                event.set_result(
                    MessageEventResult().message(
                        "用法:/模型路由 3(也认「切换 3」),先用「/模型路由 列表」看编号。"
                    )
                )
                return
            await self._switch_by_index(event, number)
            return

        if sub_norm in ("列表", "list", "models"):
            await self._send_model_list(event)
            return

        if sub_norm in ("状态", "status", "看"):
            await self._render_status_only(event)
            return

        if sub_norm in ("绑定", "bind"):
            umo = event.unified_msg_origin
            self.config["target_umo"] = umo
            self.config.save_config()
            event.set_result(
                MessageEventResult().message(f"✅ 已绑定当前会话为被管理的会话:\n{umo}")
            )
            return

        if sub_norm in ("开", "开启", "on"):
            self.config["enabled"] = True
            self.config.save_config()
            event.set_result(MessageEventResult().message("✅ 自动路由已开启"))
            return

        if sub_norm in ("关", "关闭", "off"):
            self.config["enabled"] = False
            self.config.save_config()
            event.set_result(MessageEventResult().message("⛔ 自动路由已关闭(手动命令不受影响)"))
            return

        if sub_norm in ("", "测试", "run", "test", "全测"):
            await self._run_full_round(event)
            return

        event.set_result(
            MessageEventResult().message(
                "常用:\n"
                "· /模型路由          → 全链检测 + 自动切换 + 状态图\n"
                "· /模型列表          → 候选列表图\n"
                "· /模型测试 3        → 只测第 3 号\n"
                "· /模型切换 3        → 切到第 3 号(也认 /模型路由 3)\n"
                "· /模型状态          → 只看状态图,不检测\n"
                "· /模型路由 绑定 / 开 / 关"
            )
        )

    @filter.command("模型测试", alias={"模型测", "model_test"})
    async def model_test_cmd(self, event: AstrMessageEvent, arg: str | int | None = None) -> None:
        """单独检测指定编号的候选模型(不切换)。"""
        number = router_core.extract_number(arg)
        if number is None:
            event.set_result(
                MessageEventResult().message("用法:/模型测试 3,先用 /模型列表 看编号。")
            )
            return
        await self._probe_single_by_index(event, number)

    @filter.command("模型列表", alias={"model_list"})
    async def model_list_cmd(self, event: AstrMessageEvent) -> None:
        """候选列表图。"""
        await self._send_model_list(event)

    @filter.command("模型状态", alias={"model_status"})
    async def model_status_cmd(self, event: AstrMessageEvent) -> None:
        """状态图(不发起检测)。"""
        await self._render_status_only(event)

    @filter.command("模型切换", alias={"model_switch"})
    async def model_switch_cmd(self, event: AstrMessageEvent, arg: str | int | None = None) -> None:
        """按编号切换当前对话模型。"""
        number = router_core.extract_number(arg)
        if number is None:
            event.set_result(
                MessageEventResult().message("用法:/模型切换 3,先用 /模型列表 看编号。")
            )
            return
        await self._switch_by_index(event, number)

    async def _send_model_list(self, event: AstrMessageEvent) -> None:
        """候选列表图:只列优先级链里的模型,带编号、当前标记与上次探测结果。"""
        order_ids = self._order_ids()
        if not order_ids:
            event.set_result(
                MessageEventResult().message("⚠️ 优先级链为空,请先在 WebUI 插件配置里设置。")
            )
            return
        store = StateStore(self)
        state = await store.load()
        current_id = await self._current_provider_id()
        current_display = self._provider_display(current_id) if current_id else "默认"
        name_map = {pid: (self._provider_display(pid) or pid) for pid in order_ids}
        rows = dashboard.build_list_rows(
            order_ids=order_ids,
            name_map=name_map,
            current_id=current_id,
            state=state,
        )
        ctx = dashboard.build_list_context(
            rows=rows,
            current_display=current_display,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        try:
            output = await self.html_render(
                dashboard.LIST_TEMPLATE,
                ctx,
                return_url=True,
                options={
                    "type": "png",
                    "full_page": True,
                    "timeout": self._cfg_int("render_timeout_seconds", 90) * 1000,
                    "animations": "disabled",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[模型路由] 候选列表图渲染失败,回退文字: %s", exc)
            event.set_result(
                MessageEventResult().message(
                    dashboard.build_list_text(rows=rows, current_display=current_display)
                )
            )
            return
        result = MessageEventResult()
        if isinstance(output, str) and output.startswith(("http://", "https://")):
            result.url_image(output)
        else:
            result.file_image(str(output))
        event.set_result(result)

    async def _probe_single_by_index(self, event: AstrMessageEvent, arg: str | int | None) -> None:
        """单独探测指定编号的候选模型:只出结果并记入历史,不做切换决策。"""
        order_ids = self._order_ids()
        if not order_ids:
            event.set_result(
                MessageEventResult().message("⚠️ 优先级链为空,请先在 WebUI 插件配置里设置。")
            )
            return
        if arg is None or str(arg).strip() == "":
            event.set_result(
                MessageEventResult().message("用法:/模型路由 测 <编号>,先用 /模型路由 列表 看编号。")
            )
            return
        try:
            index = int(str(arg).strip())
        except (TypeError, ValueError):
            event.set_result(
                MessageEventResult().message("⚠️ 编号需要是数字,例如 /模型路由 测 3")
            )
            return
        if not 1 <= index <= len(order_ids):
            event.set_result(
                MessageEventResult().message(f"⚠️ 编号超出范围:有效范围 1~{len(order_ids)}")
            )
            return
        provider_id = order_ids[index - 1]
        provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            event.set_result(
                MessageEventResult().message(
                    f"⚠️ {provider_id} 当前不存在(可能已被删除),请检查优先级链。"
                )
            )
            return

        eligible = self._cfg_float("eligible_timeout_seconds", 30.0)
        deadline = self._cfg_float("probe_deadline_seconds", 50.0)
        try:
            await event.send(
                MessageEventResult().message(
                    f"🔍 正在检测第 {index} 号,最长等待 {deadline:g} 秒…"
                )
            )
        except Exception:  # noqa: BLE001
            pass  # 提示发送失败不影响检测

        results = await relay_probe(
            [ProbeTarget(provider_id=provider_id, provider=provider)],
            self._probe_fn,
            eligible_timeout=eligible,
            probe_deadline=deadline,
        )
        result = results[0]

        # 记入历史,让列表"上次"与状态图能看到;不做切换决策、不触发长期提醒
        store = StateStore(self)
        state = await store.load()
        record_round(
            state,
            [result],
            ts=time.time(),
            history_max=self._cfg_int("history_max_per_model", 200),
        )
        await store.save(state)

        display = self._provider_display(provider_id) or provider_id
        latency = result.latency_ms / 1000
        if result.status == STATUS_OK:
            text = f"✅ 第 {index} 号 {display}:合格,{latency:.1f}s"
        elif result.status == STATUS_SLOW:
            text = f"⚠️ 第 {index} 号 {display}:超线,{latency:.1f}s(合格线 {eligible:g}s)"
        else:
            text = f"❌ 第 {index} 号 {display}:失败({latency:.1f}s)\n{result.error}"
        logger.info(
            "[模型路由] 单测第 %d 号 %s -> %s(%dms)",
            index,
            provider_id,
            result.status,
            result.latency_ms,
        )
        event.set_result(MessageEventResult().message(text))

    async def _switch_by_index(self, event: AstrMessageEvent, arg: str | int | None) -> None:
        """按优先级链编号切换当前对话模型(等价手动 /provider)。"""
        order_ids = self._order_ids()
        if not order_ids:
            event.set_result(
                MessageEventResult().message("⚠️ 优先级链为空,请先在 WebUI 插件配置里设置。")
            )
            return
        if arg is None or str(arg).strip() == "":
            event.set_result(
                MessageEventResult().message("用法:/模型路由 <编号>,先用 /模型路由 列表 看编号。")
            )
            return
        try:
            index = int(str(arg).strip())
        except (TypeError, ValueError):
            event.set_result(
                MessageEventResult().message("⚠️ 编号需要是数字,例如 /模型路由 2")
            )
            return
        if not 1 <= index <= len(order_ids):
            event.set_result(
                MessageEventResult().message(f"⚠️ 编号超出范围:有效范围 1~{len(order_ids)}")
            )
            return
        provider_id = order_ids[index - 1]
        if self.context.get_provider_by_id(provider_id) is None:
            event.set_result(
                MessageEventResult().message(
                    f"⚠️ {provider_id} 当前不存在(可能已被删除),请检查优先级链。"
                )
            )
            return
        target_umo = self._cfg_str("target_umo")
        if not target_umo:
            event.set_result(
                MessageEventResult().message("⚠️ 还没有绑定会话,请先发送 /模型路由 绑定。")
            )
            return
        try:
            await self.context.provider_manager.set_provider(
                provider_id=provider_id,
                provider_type=ProviderType.CHAT_COMPLETION,
                umo=target_umo,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[模型路由] 手动切换失败: %s", exc)
            event.set_result(MessageEventResult().message(f"❌ 切换失败:{exc}"))
            return
        logger.info("[模型路由] 手动切换 -> %s (第 %d 号)", provider_id, index)
        display = self._provider_display(provider_id) or provider_id
        event.set_result(MessageEventResult().message(f"✅ 已切换到 {display}"))

    async def _run_full_round(self, event: AstrMessageEvent) -> None:
        """全链检测一轮并回状态图(手动触发)。"""
        if not self._order_ids():
            event.set_result(
                MessageEventResult().message("⚠️ 请先在 WebUI 插件配置里设置模型优先级链。")
            )
            return
        round_ctx = await self.run_probe_round(trigger="manual")
        await self._render_and_send(event, round_ctx)

    async def _render_status_only(self, event: AstrMessageEvent) -> None:
        order_ids = self._order_ids()
        if not order_ids:
            event.set_result(
                MessageEventResult().message("⚠️ 优先级链为空,请先在 WebUI 配置。")
            )
            return
        store = StateStore(self)
        state = await store.load()
        now_ts = time.time()
        name_map = {pid: (self._provider_display(pid) or pid) for pid in order_ids}
        rows = dashboard.build_rows(
            state=state, results=[], order_ids=order_ids, name_map=name_map,
            now=now_ts, recent_n=20,
        )
        current_id = await self._current_provider_id()
        window = self._quiet_window()
        quiet_text = "关闭"
        if window is not None:
            quiet_text = (
                f"{self._cfg_str('quiet_start')} - {self._cfg_str('quiet_end')}"
                + ("(免检测中)" if quiet.in_quiet(datetime.now(), window) else "")
            )
        ctx = dashboard.build_dashboard_context(
            rows=rows,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            current_display=name_map.get(current_id, current_id) if current_id else None,
            selected_display=None,
            quiet_text=quiet_text,
            next_run_text=self._next_run_text(),
            switch_text="",
        )
        await self._render_and_send(event, {"dashboard": ctx})
