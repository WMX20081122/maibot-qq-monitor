"""QQ 在线状态监控插件 — 主入口。

监测 NapCat 连接状态（登录/掉线）：
- 后台定时调用适配器 API 探测在线状态；
- 收到任意消息刷新“活动时间”，作为在线证据；
- 登录/掉线事件：写入本地记录 + 通过 QQ 邮箱 SMTP 发送通知邮件；
- 群聊/私聊发送「在线状态」查看最新登录与掉线记录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, List

from maibot_sdk import Command, HookHandler, MaiBotPlugin
from maibot_sdk.types import HookMode

from . import config as cfg_mod
from .mailer import MailSender

__version__ = "1.0.4"

logger = logging.getLogger("plugin.WMX20081122.maibot-qq-monitor")

DEFAULT_COMMANDS = ["在线状态", "上下线记录"]


def _load_configured_commands() -> List[str]:
    """读取配置中的查看指令。

    @Command 的正则在模块导入期求值，此时 SDK 配置对象尚未注入，只能直接
    读插件目录的 config.toml；读不到就用默认值，不影响其他功能。
    """
    try:
        with (Path(__file__).resolve().parent / "config.toml").open("rb") as fp:
            raw = tomllib.load(fp)
        commands = [str(item).strip() for item in (raw.get("command") or {}).get("ps_command") or []]
    except Exception:
        logger.debug("读取自定义查看指令失败，使用默认指令", exc_info=True)
        commands = []
    return [item for item in commands if item] or DEFAULT_COMMANDS


COMMAND_PATTERN = r"^\s*(?:" + "|".join(re.escape(item) for item in _load_configured_commands()) + r")\s*$"


def _login_info_is_online(result: Any) -> bool | None:
    """get_login_info 只能证明「在线」：被踢下线后仍返回缓存 user_id，不能反推掉线。"""
    if not isinstance(result, dict) or not result:
        return None
    if result.get("error") or result.get("success") is False:
        return None
    data = result.get("data")
    if isinstance(data, dict) and str(data.get("user_id") or "").strip():
        return True
    if str(result.get("user_id") or "").strip():
        return True
    return None


def _status_verdict(result: Any) -> bool | None:
    """从 get_status 提取在线判定：online 与 good 同时为真才算在线，无标志返回 None。"""
    if not isinstance(result, dict) or not result:
        return None
    if result.get("error") or result.get("success") is False:
        return None
    for flags in (result.get("data"), result.get("status"), result):
        if not isinstance(flags, dict):
            continue
        if "online" in flags:
            return bool(flags.get("online")) and bool(flags.get("good", True))
        if "good" in flags:
            return bool(flags.get("good"))
    return None


def _describe_exc(exc: BaseException) -> str:
    """异常摘要：超时等异常的 str() 为空，这里补上类型名便于排查。"""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


HARD_OFFLINE_MARKERS = (
    "not connected",
    "not_connected",
    "未连接",
    "连接已断开",
    "连接断开",
    "connection refused",
    "拒绝网络连接",
    "cannot connect",
    "未找到 api 提供方",
    "api provider",
    "api 提供方插件",
)


def _is_hard_offline_signal(payload: Any) -> bool:
    """判断探测结果或异常是否属于「明确断开」，而非网络抖动。

    NapCat 未连接、连接被拒等错误语义明确：QQ 侧不可能在线，无需再用
    多次失败来确认。
    """
    text = str(payload).lower()
    return any(marker in text for marker in HARD_OFFLINE_MARKERS)


class QQMonitorPlugin(MaiBotPlugin):
    config_model = cfg_mod.QQMonitorConfig

    def __init__(self) -> None:
        super().__init__()
        self._online: bool | None = None  # True=在线 False=掉线 None=未知
        self._last_activity: float = 0.0
        self._fail_count: int = 0
        self._success_count: int = 0
        self._fail_since: float = 0.0
        self._recover_since: float = 0.0
        self._records: List[dict] = []
        self._records_file: Path = Path(__file__).resolve().parent / "data" / "monitor_records.json"
        self._mailer: MailSender | None = None
        self._probe_task: asyncio.Task | None = None
        self._mail_tasks: set[asyncio.Task] = set()
        self._last_probe_detail: str = ""
        self._cached_self_id: str = ""

    # ---- 生命周期 ----

    async def on_load(self) -> None:
        self._records_file = self._resolve_records_file()
        self._records_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_records()
        self._load_state()
        cfg = self.config
        m = cfg.mail
        self._mailer = MailSender(
            smtp_host=m.smtp_host,
            smtp_port=m.smtp_port,
            sender=m.mail_sender,
            auth_code=m.mail_auth_code,
            mail_to=m.mail_to,
            subject_prefix=m.mail_subject_prefix,
        )
        if not cfg.plugin.enabled:
            logger.info("QQ在线监控已禁用：不探测状态、不发送通知邮件")
            return
        self._last_activity = time.time()
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())
        logger.info(
            "QQ在线监控已加载：指令 %s，邮件发送 %s",
            " / ".join(cfg.command.ps_command),
            "开启" if (m.mail_enable and self._mailer.is_configured()) else "未配置",
        )

    async def on_unload(self) -> None:
        if self._probe_task is not None:
            self._probe_task.cancel()
            self._probe_task = None
        for task in list(self._mail_tasks):
            task.cancel()
        if self._mail_tasks:
            await asyncio.gather(*self._mail_tasks, return_exceptions=True)
        self._save_records()
        logger.info("QQ在线监控已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != "self":
            return
        m = self.config.mail
        self._mailer = MailSender(
            smtp_host=m.smtp_host,
            smtp_port=m.smtp_port,
            sender=m.mail_sender,
            auth_code=m.mail_auth_code,
            mail_to=m.mail_to,
            subject_prefix=m.mail_subject_prefix,
        )
        # WebUI 里开关切换即时生效：关闭停探测，重新打开无需重载插件
        if self.config.plugin.enabled:
            if self._probe_task is None or self._probe_task.done():
                self._last_activity = time.time()
                self._probe_task = asyncio.create_task(self._probe_loop())
                logger.info("QQ在线监控已启用，启动探测循环")
        elif self._probe_task is not None:
            self._probe_task.cancel()
            self._probe_task = None
            logger.info("QQ在线监控已禁用，停止探测循环")
        logger.info("QQ在线监控配置已更新(%s)", version)

    # ---- 探测与状态机 ----

    async def _probe_once(self) -> tuple[bool, bool]:
        """探测一次在线状态，返回 ``(是否在线, 是否明确断开)``。

        get_status 优先且具决定性，无法判定时才退回 get_login_info；
        掉线时适配器也可能返回错误包装 dict，必须做语义校验。
        """
        self._last_probe_detail: str = ""
        timeout = max(int(self.config.monitor.probe_timeout), 3)
        hard_offline = False
        for api_name, check in (
            ("adapter.napcat.system.get_status", _status_verdict),
            ("adapter.napcat.system.get_login_info", _login_info_is_online),
        ):
            try:
                result = await asyncio.wait_for(self.ctx.api.call(api_name), timeout=timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_probe_detail = f"{api_name} -> {_describe_exc(exc)}"
                if _is_hard_offline_signal(exc):
                    hard_offline = True
                continue

            self._last_probe_detail = f"{api_name} -> {result!r}"
            self._cache_self_id(result)
            verdict = check(result)
            if verdict is True:
                return True, False
            if verdict is False:
                # online=False（账号被踢下线）等明确标志：直接采信并立即判定
                return False, True
            if _is_hard_offline_signal(result):
                hard_offline = True
        return False, hard_offline

    def _cache_self_id(self, result: Any) -> None:
        """从探测结果缓存机器人自身 QQ，供邮件与记录标识使用。"""
        if not isinstance(result, dict):
            return
        data = result.get("data")
        source = data if isinstance(data, dict) else result
        self_id = str(source.get("user_id") or "").strip()
        if self_id:
            self._cached_self_id = self_id

    async def _napcat_port_reachable(self) -> bool:
        """直接连 NapCat HTTP 端口：可达说明只是调用通道卡顿，不是真掉线。"""
        m = self.config.monitor
        port = int(m.http_probe_port or 0)
        if port <= 0:
            return False
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(m.http_probe_host or "127.0.0.1", port),
                timeout=2,
            )
        except Exception:
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True

    async def _probe_loop(self) -> None:
        delay = 0.0  # 首轮立即探测，尽快确定初始状态
        while True:
            # 每轮重读配置，探测间隔/阈值等改动无需重载插件即可生效
            cfg = self.config
            interval = max(int(cfg.monitor.probe_interval), 5)
            retry_interval = max(int(cfg.monitor.probe_retry_interval), 2)
            threshold = max(int(cfg.monitor.probe_fail_threshold), 1)
            recover_threshold = max(int(cfg.monitor.recover_success_threshold), 1)
            grace = max(float(cfg.monitor.grace_seconds), 5)
            offline_confirm = max(int(cfg.monitor.offline_confirm_seconds), 0)
            recover_confirm = max(int(cfg.monitor.recover_confirm_seconds), 0)
            await asyncio.sleep(delay)
            if not cfg.plugin.enabled:
                delay = interval
                continue
            try:
                online, hard_offline = await self._probe_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("探测异常", exc_info=True)
                delay = retry_interval
                continue

            now = time.time()
            if online:
                self._fail_count = 0
                self._fail_since = 0.0
                if self._online is True:
                    self._success_count = 0
                    self._recover_since = 0.0
                    delay = interval
                    continue
                # 首次判定与掉线恢复共用同一套「稳定多久才算数」的确认逻辑
                if self._recover_since <= 0:
                    self._recover_since = now
                self._success_count += 1
                stable = now - self._recover_since
                if self._success_count >= recover_threshold and stable >= recover_confirm:
                    first = self._online is None
                    self._success_count = 0
                    self._recover_since = 0.0
                    self._online = True
                    delay = interval
                    if first and not cfg.monitor.notify_on_load:
                        self._record_event("online")
                    else:
                        if not first:
                            logger.info("稳定 %d 秒探测成功，判定恢复上线，发送上线通知", int(stable))
                        await self._handle_event("online", notify_mail=True)
                else:
                    delay = retry_interval  # 仍在确认期，短间隔复检
            else:
                self._success_count = 0
                self._recover_since = 0.0
                idle_for = now - self._last_activity
                # 宽限期仅对「模糊失败」生效；明确断开说明连接已断，不必再等
                if not hard_offline and self._online is not False and idle_for < grace:
                    logger.info(
                        "探测失败但仍在消息宽限期（距判定掉线还需 %.0f 秒）。详情: %s",
                        grace - idle_for,
                        self._last_probe_detail,
                    )
                    self._fail_count = 0
                    self._fail_since = 0.0
                    delay = retry_interval
                    continue
                if self._fail_since <= 0:
                    self._fail_since = now
                self._fail_count += 1
                immediate = hard_offline and self._online is not False
                persisted = now - self._fail_since
                should_notify = immediate or (self._fail_count >= threshold and persisted >= offline_confirm)
                notifying = should_notify and self._online is not False
                log_failure = logger.info if notifying or self._fail_count <= threshold else logger.debug
                log_failure(
                    "探测失败，累计 %d/%d（已持续 %.0f 秒）。详情: %s",
                    self._fail_count,
                    threshold,
                    persisted,
                    self._last_probe_detail,
                )
                if notifying:
                    # 判定前再确认 NapCat 端口：接口卡顿不该被当成掉线
                    if not immediate and await self._napcat_port_reachable():
                        logger.info("二次校验：NapCat 端口可达，判定为调用通道抖动，暂不发掉线通知")
                        self._fail_count = 0
                        self._fail_since = 0.0
                        delay = interval
                        continue
                    self._online = False
                    logger.warning("判定掉线，发送掉线通知。探测详情: %s", self._last_probe_detail)
                    await self._handle_event("offline", notify_mail=True)
                # 掉线态与疑似掉线态都用短间隔复检，尽快确认与恢复
                delay = retry_interval


    # ---- 事件处理 ----

    def _record_event(self, evt: str) -> None:
        """写入一条记录（上线/掉线），保留最近 record_max 条。"""
        nick = next(iter(self.config.admin.nickname), None) or ""
        self._records.append(
            {
                "type": evt,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "nick": nick,
                "self_id": self._resolve_self_id(),
            }
        )
        max_records = max(int(self.config.monitor.record_max), 10)
        if len(self._records) > max_records:
            self._records = self._records[-max_records:]
        self._save_records()
        self._save_state()

    def _state_file(self) -> Path:
        return self._records_file.with_name("monitor_state.json")

    def _load_state(self) -> None:
        """恢复上次判定的在线状态：重启时若本来就掉线，不再重复发一封掉线邮件。"""
        try:
            raw = json.loads(self._state_file().read_text(encoding="utf-8"))
        except Exception:
            return
        online = raw.get("online") if isinstance(raw, dict) else None
        if isinstance(online, bool):
            self._online = online
            logger.info("恢复上次在线状态：%s", "在线" if online else "掉线")

    def _save_state(self) -> None:
        if self._online is None:
            return
        try:
            self._state_file().write_text(
                json.dumps(
                    {
                        "online": self._online,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("保存在线状态失败", exc_info=True)

    async def _handle_event(self, evt: str, notify_mail: bool) -> None:
        """记录事件并按「每次状态翻转一封」直接发邮件（翻转已由阈值防抖）。"""
        self._record_event(evt)
        if not notify_mail or not self.config.mail.mail_enable:
            return
        self._spawn_mail(evt)

    def _spawn_mail(self, evt: str) -> None:
        """后台发送通知邮件，避免 SMTP 耗时阻塞探测循环。"""
        task = asyncio.create_task(self._send_event_mail(evt))
        self._mail_tasks.add(task)
        task.add_done_callback(self._mail_tasks.discard)

    async def _send_event_mail(self, evt: str) -> None:
        """构造并发送上线/掉线通知邮件。"""
        try:
            nick = next(iter(self.config.admin.nickname), None) or "Bot"
            self_id = self._resolve_self_id()
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if evt == "online":
                subject = f"Bot 已上线（{now}）"
                body = (
                    f"机器人已恢复在线。\n\n"
                    f"时间：{now}\n昵称：{nick}\nQQ：{self_id}\n"
                    f"状态：🟢 在线"
                )
            else:
                subject = f"Bot 已掉线（{now}）"
                body = (
                    f"机器人已掉线！\n\n"
                    f"时间：{now}\n昵称：{nick}\nQQ：{self_id}\n"
                    f"状态：🔴 掉线\n"
                    f"请尽快检查 NapCat / 网络连接。"
                )
            await self._mailer.send(subject, body)
        except Exception:
            logger.exception("发送 %s 通知邮件失败", evt)

    def _resolve_records_file(self) -> Path:
        """记录写入运行时分配的统一数据目录，避免落在插件源码目录内。"""
        data_dir = getattr(getattr(self.ctx, "paths", None), "data_dir", None)
        if data_dir:
            return Path(data_dir) / "monitor_records.json"
        return Path(__file__).resolve().parent / "data" / "monitor_records.json"

    @staticmethod
    def _legacy_records_file() -> Path:
        """旧版本把记录写在插件源码目录，保留读取以便平滑迁移。"""
        return Path(__file__).resolve().parent / "data" / "monitor_records.json"

    def _resolve_self_id(self) -> str:
        """返回机器人自身 QQ（优先取已登录信息缓存，暂无则返回 unknown）。"""
        cached = getattr(self, "_cached_self_id", "")
        return str(cached or "unknown")

    # ---- 消息 Hook：活动时间刷新 ----

    @HookHandler(hook="chat.receive.after_process", mode=HookMode.OBSERVE)
    async def _on_received(self, **kwargs: Any) -> None:
        self._last_activity = time.time()
        self._fail_count = 0
        # 从消息报文里取机器人自身 QQ：优先顶层 account_id，兼容旧版 additional_config
        try:
            msg = kwargs.get("message") or {}
            sid = msg.get("account_id")
            if not sid:
                mi = msg.get("message_info") or {}
                sid = (mi.get("additional_config") or {}).get("self_id")
            if sid:
                self._cached_self_id = str(sid).strip()
        except Exception:
            pass

    # ---- 命令：查看记录 ----

    @staticmethod
    def _extract_sender_id(kwargs: dict) -> str:
        """取发送者 ID。

        运行时的命令调用参数里会直接带 ``user_id``（Host 侧 component_query 组装），
        文档承诺的 ``message`` 报文里则是 ``message.message_info.user_info.user_id``。
        """
        message = kwargs.get("message")
        if isinstance(message, dict):
            info = message.get("message_info") or {}
            user_info = info.get("user_info") if isinstance(info, dict) else None
            if isinstance(user_info, dict) and user_info.get("user_id"):
                return str(user_info["user_id"]).strip()
        return str(kwargs.get("user_id") or "").strip()

    @Command(
        "在线状态",
        description="查看最新登录/掉线记录",
        pattern=COMMAND_PATTERN,
    )
    async def cmd_status(self, stream_id: str = "", **kwargs: Any) -> tuple:
        if not stream_id:
            return True, None, 0
        if not self.config.plugin.enabled:
            await self.ctx.send.text("QQ在线监控当前已禁用", stream_id)
            return True, None, 0

        sender_id = self._extract_sender_id(kwargs)

        if self.config.command.ps_only_su:
            su = set(str(x).strip() for x in self.config.admin.superusers)
            if sender_id not in su:
                await self.ctx.send.text("你没有使用此指令的权限", stream_id)
                return True, None, 0

        lines = []
        # 当前状态
        if self._online is True:
            lines.append("当前状态：🟢 在线")
        elif self._online is False:
            lines.append("当前状态：🔴 掉线")
        else:
            lines.append("当前状态：⚪ 未知（等待首次探测）")
        lines.append("")

        onlines = [r for r in self._records if r["type"] == "online"][-5:]
        offlines = [r for r in self._records if r["type"] == "offline"][-5:]

        lines.append("— 最近登录记录 —")
        if onlines:
            lines.extend(f"🟢 {r['time']}" for r in reversed(onlines))
        else:
            lines.append("（暂无登录记录）")
        lines.append("")
        lines.append("— 最近掉线记录 —")
        if offlines:
            lines.extend(f"🔴 {r['time']}" for r in reversed(offlines))
        else:
            lines.append("（暂无掉线记录）")

        await self.ctx.send.text("\n".join(lines), stream_id)
        return True, None, 0

    # ---- 记录持久化 ----

    def _load_records(self) -> None:
        """读取历史记录；统一数据目录无文件时回退旧式插件目录。"""
        source = self._records_file
        if not source.is_file():
            legacy = self._legacy_records_file()
            if legacy.is_file():
                source = legacy
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._records = []
            return
        except Exception:
            logger.debug("读取记录文件失败", exc_info=True)
            self._records = []
            return
        if isinstance(data, dict):
            # 过滤字段缺失的脏数据，避免查看命令因 KeyError 中断
            self._records = [
                record
                for record in data.get("records", [])
                if isinstance(record, dict)
                and record.get("type") in {"online", "offline"}
                and isinstance(record.get("time"), str)
            ]

    def _save_records(self) -> None:
        try:
            self._records_file.write_text(
                json.dumps({"records": self._records}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("写入记录文件失败", exc_info=True)


def create_plugin() -> QQMonitorPlugin:
    return QQMonitorPlugin()
