"""QQ 在线状态监控插件（MaiBot 移植版）。

- 定时探测 NapCat 连接状态，登录/掉线时通过 QQ 邮箱 SMTP 发送通知邮件。
- 群聊/私聊发送「在线状态」可查看最新的登录与掉线记录。
"""

from __future__ import annotations

__version__ = "1.0.3"
