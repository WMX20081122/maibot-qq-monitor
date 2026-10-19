"""QQ 在线监控插件 — 配置模型。"""

from __future__ import annotations

from typing import ClassVar, List

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    __ui_label__: ClassVar[str] = "插件开关"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用本插件。",
        json_schema_extra={
            "hint": "插件总开关，关闭后不再探测状态、不发送通知邮件。",
            "label": "启用插件",
            "order": 0,
        },
    )
    config_version: str = Field(
        default="1.0.0",
        json_schema_extra={"disabled": True, "hidden": True, "label": "配置版本", "order": 99},
    )


class AdminSection(PluginConfigBase):
    __ui_label__: ClassVar[str] = "管理员"
    __ui_order__: ClassVar[int] = 1

    superusers: List[str] = Field(
        default_factory=list,
        description="管理员 QQ 号列表。",
        json_schema_extra={
            "hint": "只有这些 QQ 能查看监控记录（配合「仅管理员可见」开关）。",
            "label": "管理员 QQ",
            "order": 0,
            "placeholder": "请输入 QQ 号",
        },
    )
    nickname: List[str] = Field(
        default_factory=list,
        description="机器人昵称列表。",
        json_schema_extra={
            "hint": "用于在邮件中标识机器人昵称。",
            "label": "机器人昵称",
            "order": 1,
            "placeholder": "请输入昵称",
        },
    )


class MailSection(PluginConfigBase):
    __ui_label__: ClassVar[str] = "邮件通知"
    __ui_order__: ClassVar[int] = 2

    mail_enable: bool = Field(
        default=True,
        description="是否启用邮件通知。",
        json_schema_extra={
            "hint": "登录/掉线时向目标邮箱发送通知邮件。",
            "label": "启用邮件",
            "order": 0,
        },
    )
    smtp_host: str = Field(
        default="smtp.qq.com",
        description="SMTP 服务器地址。",
        json_schema_extra={"hint": "QQ 邮箱为 smtp.qq.com。", "label": "SMTP 服务器", "order": 1},
    )
    smtp_port: int = Field(
        default=465, ge=1, le=65535,
        json_schema_extra={"hint": "QQ 邮箱 SSL 端口为 465。", "label": "SMTP 端口", "order": 2, "step": 1},
    )
    mail_sender: str = Field(
        default="",
        description="发件邮箱地址（需为该邮箱开启 SMTP 服务并获取授权码）。",
        json_schema_extra={"hint": "填写发件邮箱完整地址。", "label": "发件邮箱", "order": 3, "placeholder": "xxx@qq.com"},
    )
    mail_auth_code: str = Field(
        default="",
        description="发件邮箱 SMTP 授权码（非登录密码）。",
        json_schema_extra={
            "hint": "在 QQ 邮箱 设置→账号 中开启 SMTP 后生成。",
            "label": "邮箱授权码",
            "order": 4,
            "placeholder": "16 位授权码",
        },
    )
    mail_to: str = Field(
        default="",
        description="目标接收邮箱地址。",
        json_schema_extra={"hint": "登录/掉线通知发送到此邮箱。", "label": "目标邮箱", "order": 5, "placeholder": "xxx@qq.com"},
    )
    mail_subject_prefix: str = Field(
        default="【QQ在线监控】",
        description="邮件标题前缀。",
        json_schema_extra={"hint": "用于在收件箱中快速识别通知邮件。", "label": "标题前缀", "order": 6},
    )


class MonitorSection(PluginConfigBase):
    __ui_label__: ClassVar[str] = "监控设置"
    __ui_order__: ClassVar[int] = 3

    probe_interval: int = Field(
        default=10, ge=5,
        description="正常状态下的探测间隔（秒）。",
        json_schema_extra={
            "hint": "本地 NapCat API 调用开销很低，10 秒可让掉线通知稳定在十几秒内。",
            "label": "探测间隔(s)",
            "order": 0,
            "step": 5,
        },
    )
    probe_retry_interval: int = Field(
        default=10, ge=2,
        description="疑似掉线或已掉线时的复检间隔（秒）。",
        json_schema_extra={
            "hint": "探测失败或处于掉线态时改用短间隔复检，加快确认掉线与恢复上线。",
            "label": "加速复检间隔(s)",
            "order": 1,
            "step": 1,
        },
    )
    probe_timeout: int = Field(
        default=10, ge=3,
        description="单次接口调用的超时时间（秒）。",
        json_schema_extra={
            "hint": "主机繁忙时接口偶发响应变慢，超时设太短会把「慢」误判成「掉线」。",
            "label": "调用超时(s)",
            "order": 2,
            "step": 1,
        },
    )
    probe_fail_threshold: int = Field(
        default=3, ge=1,
        description="连续探测失败多少次才进入掉线候选。",
        json_schema_extra={
            "hint": "仅用于模糊失败（超时等）；NapCat 明确未连接时会立即判定，不等阈值。",
            "label": "掉线判定阈值",
            "order": 3,
            "step": 1,
        },
    )
    offline_confirm_seconds: int = Field(
        default=60, ge=0,
        description="持续失败多少秒才确认掉线并发送通知（秒）。",
        json_schema_extra={
            "hint": "与阈值同时满足才通知；短暂抖动（例如 30 秒内恢复）不会再发「掉线+上线」成对邮件。",
            "label": "掉线确认时长(s)",
            "order": 4,
            "step": 10,
        },
    )
    recover_success_threshold: int = Field(
        default=3, ge=1,
        description="连续探测成功多少次才判定恢复上线。",
        json_schema_extra={
            "hint": "与掉线判定对称，防止连接抖动时反复触发上线。",
            "label": "上线确认阈值",
            "order": 5,
            "step": 1,
        },
    )
    recover_confirm_seconds: int = Field(
        default=20, ge=0,
        description="持续成功多少秒才确认恢复上线（秒）。",
        json_schema_extra={"hint": "恢复通知同样需要一段稳定期，避免「上线又掉线」反复。", "label": "上线确认时长(s)", "order": 6, "step": 5},
    )
    grace_seconds: int = Field(
        default=30, ge=5,
        description="最近收到消息的宽限期（秒）。",
        json_schema_extra={
            "hint": "此时间内探测失败不判掉线（有消息说明机器人仍在线）；明确断开信号不受宽限期影响。",
            "label": "消息宽限期(s)",
            "order": 7,
            "step": 5,
        },
    )
    http_probe_host: str = Field(
        default="127.0.0.1",
        description="二次校验用的 NapCat HTTP 主机。",
        json_schema_extra={"hint": "仅在端口非 0 时生效，与 NapCat 同机时保持默认即可。", "label": "校验主机", "order": 8},
    )
    http_probe_port: int = Field(
        default=0, ge=0, le=65535,
        description="NapCat HTTP 端口，0 表示关闭二次校验。",
        json_schema_extra={
            "hint": "填 NapCat 的 HTTP 端口（如 2000）：判定掉线前先确认端口仍可连接，能挡住「接口卡顿」类误报。",
            "label": "校验端口",
            "order": 9,
            "step": 1,
        },
    )
    notify_on_load: bool = Field(
        default=True,
        description="插件加载探测到在线时是否发送「上线」邮件。",
        json_schema_extra={
            "hint": "每次重启 MaiBot 都会触发一次上线通知，若觉得吵可关闭。",
            "label": "启动发上线通知",
            "order": 10,
        },
    )
    liveness_enabled: bool = Field(
        default=True,
        description="活性探针：识别「僵尸在线」（NapCat 活着但 QQ 会话已死）。",
        json_schema_extra={
            "hint": "get_status 读的是 NapCat 本地缓存，QQ 会话断开时仍显示在线；探针定期用 no_cache 查询自身资料强制走腾讯服务器，连续失败即判定掉线。",
            "label": "启用活性探针",
            "order": 11,
        },
    )
    liveness_interval: int = Field(
        default=60, ge=15,
        description="活性探针间隔（秒）。",
        json_schema_extra={"hint": "探针会强制服务器往返，间隔不宜小于 15 秒。", "label": "探针间隔(s)", "order": 12, "step": 15},
    )
    liveness_fail_threshold: int = Field(
        default=2, ge=1,
        description="活性探针连续失败多少次判定掉线。",
        json_schema_extra={"hint": "默认 2 次 × 60 秒 ≈ 掉线 2 分钟内告警。", "label": "探针失败阈值", "order": 13, "step": 1},
    )
    record_max: int = Field(
        default=50, ge=10,
        description="本地最多保留的记录条数。",
        json_schema_extra={"hint": "超出后自动丢弃最旧记录。", "label": "记录条数上限", "order": 14, "step": 10},
    )


class CommandSection(PluginConfigBase):
    __ui_label__: ClassVar[str] = "查看命令"
    __ui_order__: ClassVar[int] = 4

    ps_command: List[str] = Field(
        default_factory=lambda: ["在线状态", "上下线记录"],
        description="查看登录/掉线记录的触发指令。",
        json_schema_extra={
            "hint": "群聊或私聊发送该指令即可查看记录。",
            "label": "触发指令",
            "order": 0,
            "placeholder": "在线状态",
        },
    )
    ps_only_su: bool = Field(
        default=True,
        description="仅管理员可用查看命令。",
        json_schema_extra={"hint": "关闭后任何人可查看记录。", "label": "仅管理员", "order": 1},
    )


class QQMonitorConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    admin: AdminSection = Field(default_factory=AdminSection)
    mail: MailSection = Field(default_factory=MailSection)
    monitor: MonitorSection = Field(default_factory=MonitorSection)
    command: CommandSection = Field(default_factory=CommandSection)
