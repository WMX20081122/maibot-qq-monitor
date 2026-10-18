"""QQ 邮箱 SMTP 邮件发送模块。

使用标准库 smtplib + email.mime，无第三方依赖。
同步阻塞部分通过 asyncio.to_thread 放到线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger("plugin.WMX20081122.maibot-qq-monitor")


class MailSender:
    """基于 smtplib 的邮件发送器。"""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        sender: str,
        auth_code: str,
        mail_to: str,
        subject_prefix: str,
    ) -> None:
        self._host = smtp_host or "smtp.qq.com"
        self._port = int(smtp_port or 465)
        self._sender = sender or ""
        self._auth_code = auth_code or ""
        self._mail_to = mail_to or ""
        self._subject_prefix = subject_prefix or "【QQ在线监控】"

    def is_configured(self) -> bool:
        """发件/目标/授权码均已填写才算配置完成。"""
        return bool(self._sender and self._auth_code and self._mail_to)

    @staticmethod
    def _mask(address: str) -> str:
        """邮箱打码，日志里不出现完整地址。"""
        address = address or ""
        name, _, domain = address.partition("@")
        if not domain:
            return "***"
        return f"{name[:2]}***@{domain}"

    async def send(self, subject: str, body: str) -> bool:
        """异步发送邮件，失败自动重试。

        QQ 掉线时常伴随本机网络故障，掉线告警若不重试就会静默丢失，
        导致「只收到上线邮件、收不到掉线邮件」。这里按 0/10/30/60 秒
        重试四次，网络恢复后告警仍能送达。
        """
        if not self.is_configured():
            logger.debug(
                "邮件配置不完整，跳过发送（sender=%s to=%s）",
                self._mask(self._sender),
                self._mask(self._mail_to),
            )
            return False
        delays = (0, 10, 30, 60)
        for attempt, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                if await asyncio.to_thread(self._send_sync, subject, body):
                    if attempt:
                        logger.info("邮件重试第 %d 次后发送成功", attempt)
                    return True
            except Exception as exc:
                logger.warning("发送邮件失败(第 %d/%d 次): %s", attempt + 1, len(delays), exc)
        logger.error("邮件重试 %d 次后仍失败，放弃: %s", len(delays), subject)
        return False

    def _send_sync(self, subject: str, body: str) -> bool:
        """同步发送（在 to_thread 线程中执行）。"""
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"{self._subject_prefix}{subject}"
        try:
            msg["From"] = formataddr((str(Header("QQ在线监控", "utf-8")), self._sender))
        except Exception:
            msg["From"] = self._sender
        msg["To"] = self._mail_to

        with smtplib.SMTP_SSL(self._host, self._port, timeout=15) as server:
            server.login(self._sender, self._auth_code)
            server.sendmail(self._sender, [self._mail_to], msg.as_string())
        logger.info("邮件已发送: %s -> %s", self._mask(self._sender), self._mask(self._mail_to))
        return True
