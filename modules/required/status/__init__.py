import base64
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from creart import create
from graia.ariadne.app import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.chain import MessageChain
from graia.ariadne.message.element import Image, Source
from graia.ariadne.message.parser.twilight import (
    ArgumentMatch,
    FullMatch,
    MatchResult,
    SpacePolicy,
    Twilight,
)
from graia.ariadne.model import Friend, Group
from graia.ariadne.util.saya import decorate, dispatch, listen
from graia.saya import Channel, Saya
from graia.scheduler import timers
from graia.scheduler.saya import SchedulerSchema
from loguru import logger

from core.bot import Umaru
from core.config import GlobalConfig
from core.control import Distribute, FrequencyLimitation, Function, Permission
from core.models import response_model, saya_model
from utils.text2img import template2img
from utils.version_info import get_full_version_info

config = create(GlobalConfig)
core = create(Umaru)
module_controller = saya_model.get_module_controller()
account_controller = response_model.get_acc_controller()

saya = Saya.current()
channel = Channel.current()
channel.meta["name"] = "Status"
channel.meta["description"] = "查询BOT运行状态"
channel.meta["author"] = "13"
channel.metadata = module_controller.get_metadata_from_path(Path(__file__))


@dataclass
class NetworkInfo:
    """网络连接信息"""

    bytes_sent: int
    bytes_recv: int
    packets_sent: int
    packets_recv: int
    connections: int


@dataclass
class ProcessInfo:
    """进程信息"""

    pid: int
    cpu_percent: float
    memory_percent: float
    memory_rss_mb: float
    threads: int
    open_files: int


@dataclass
class SystemStatus:
    """系统状态数据模型"""

    # 基本信息
    bot_name: str
    version_info: str
    launch_time: str
    work_time: str

    # 系统资源
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float

    # Bot状态
    online_bots: int
    total_bots: int
    active_groups: int

    # 消息统计
    received_count: int
    sent_count: int
    real_time_received: int
    real_time_sent: int

    # 版本详情
    version_details: list[str]

    # 网络和进程信息
    network_info: NetworkInfo
    bot_process_info: ProcessInfo

    @classmethod
    async def collect_data(cls) -> "SystemStatus":
        """收集所有系统状态数据"""
        # 获取版本信息
        version, git_info, b2v_info = get_full_version_info()

        # 运行时长
        time_start = int(time.mktime(core.launch_time.timetuple()))
        m, s = divmod(int(time.time()) - time_start, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        work_time = f"{d}天{h}小时{m}分{s}秒"

        # 内存占用
        mem = psutil.virtual_memory()
        memory_used_mb = float(mem.used) / 1024 / 1024
        memory_total_mb = float(mem.total) / 1024 / 1024
        memory_percent = float(mem.percent)

        # CPU占用 - 使用非阻塞方式获取，提高性能
        cpu_percent = psutil.cpu_percent(interval=0.1, percpu=False)

        # 磁盘占用
        disk_usage = psutil.disk_usage("/")
        disk_used_gb = float(disk_usage.used) / 1024 / 1024 / 1024
        disk_total_gb = float(disk_usage.total) / 1024 / 1024 / 1024
        disk_percent = disk_usage.percent

        # 启动时间格式化
        launch_time = datetime.fromtimestamp(core.launch_time.timestamp()).strftime(
            "%Y年%m月%d日%H时%M分%S秒"
        )

        # 实时消息统计
        real_time_received = message_count.get_receive_count()
        real_time_sent = message_count.get_send_count()

        # Bot状态统计
        online_bots = len(
            [
                app_item
                for app_item in core.apps
                if Ariadne.current(app_item.account).connection.status.available
            ]
        )
        total_bots = len(core.apps)
        active_groups = len(account_controller.total_groups.keys())

        # 版本详情列表
        version_details = [f"版本信息：v{version}"]
        if git_info["commit_short"] != "未知":
            version_details.extend(
                [
                    f"Git分支：{git_info['branch']}",
                    f"最新提交：{git_info['commit_short']} ({git_info['commit_author']})",
                    f"提交信息：{git_info['commit_message']}",
                ]
            )

        if b2v_info["build_number"] != "开发环境":
            version_details.append(f"构建编号：{b2v_info['build_number']}")
            if b2v_info["build_date"]:
                version_details.append(f"构建日期：{b2v_info['build_date']}")
            version_details.append(f"构建类型：{b2v_info['build_type']}")

        # 获取网络信息 - 优化性能
        try:
            net_io = psutil.net_io_counters()
            # 简化连接数获取，避免耗时操作
            try:
                connections = len(psutil.net_connections(kind="inet"))
            except (psutil.AccessDenied, OSError):
                connections = 0

            network_info = NetworkInfo(
                bytes_sent=net_io.bytes_sent,
                bytes_recv=net_io.bytes_recv,
                packets_sent=net_io.packets_sent,
                packets_recv=net_io.packets_recv,
                connections=connections,
            )
        except (psutil.AccessDenied, AttributeError):
            network_info = NetworkInfo(
                bytes_sent=0,
                bytes_recv=0,
                packets_sent=0,
                packets_recv=0,
                connections=0,
            )

        # 获取当前进程信息
        try:
            current_process = psutil.Process()
            try:
                open_files_count = len(current_process.open_files())
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                open_files_count = 0

            bot_process_info = ProcessInfo(
                pid=current_process.pid,
                cpu_percent=current_process.cpu_percent(),
                memory_percent=current_process.memory_percent(),
                memory_rss_mb=current_process.memory_info().rss / 1024 / 1024,
                threads=current_process.num_threads(),
                open_files=open_files_count,
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            bot_process_info = ProcessInfo(
                pid=0,
                cpu_percent=0.0,
                memory_percent=0.0,
                memory_rss_mb=0.0,
                threads=0,
                open_files=0,
            )

        return cls(
            bot_name="小埋",
            version_info=f"v{version}",
            launch_time=launch_time,
            work_time=work_time,
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_used_mb=round(memory_used_mb),
            memory_total_mb=round(memory_total_mb),
            disk_percent=disk_percent,
            disk_used_gb=round(disk_used_gb, 1),
            disk_total_gb=round(disk_total_gb, 1),
            online_bots=online_bots,
            total_bots=total_bots,
            active_groups=active_groups,
            received_count=core.received_count,
            sent_count=core.sent_count + 1,
            real_time_received=real_time_received,
            real_time_sent=real_time_sent,
            version_details=version_details,
            network_info=network_info,
            bot_process_info=bot_process_info,
        )

    def get_template_data(self) -> dict[str, Any]:
        """获取模板渲染所需的数据"""

        def get_color_and_deg(percent: float) -> tuple[str, float]:
            """根据百分比获取颜色和角度"""
            if percent < 50:
                color = "#4CAF50"  # 绿色
            elif percent < 80:
                color = "#FF9800"  # 橙色
            else:
                color = "#F44336"  # 红色
            deg = (percent / 100) * 360
            return color, deg

        cpu_color, cpu_deg = get_color_and_deg(self.cpu_percent)
        memory_color, memory_deg = get_color_and_deg(self.memory_percent)
        disk_color, disk_deg = get_color_and_deg(self.disk_percent)

        # 格式化网络流量
        def format_bytes(bytes_value: int) -> str:
            """格式化字节数为可读格式"""
            value = float(bytes_value)
            for unit in ["B", "KB", "MB", "GB", "TB"]:
                if value < 1024.0:
                    return f"{value:.1f}{unit}"
                value /= 1024.0
            return f"{value:.1f}PB"

        # 获取头像base64编码
        avatar_base64 = get_avatar_base64()

        return {
            "bot_name": self.bot_name,
            "version_info": self.version_info,
            "launch_time": self.launch_time,
            "work_time": self.work_time,
            "avatar_base64": avatar_base64,
            "cpu_percent": int(self.cpu_percent),
            "cpu_color": cpu_color,
            "cpu_deg": cpu_deg,
            "memory_percent": int(self.memory_percent),
            "memory_color": memory_color,
            "memory_deg": memory_deg,
            "memory_used_mb": self.memory_used_mb,
            "memory_total_mb": self.memory_total_mb,
            "disk_percent": int(self.disk_percent),
            "disk_color": disk_color,
            "disk_deg": disk_deg,
            "disk_used_gb": self.disk_used_gb,
            "disk_total_gb": self.disk_total_gb,
            "online_bots": self.online_bots,
            "total_bots": self.total_bots,
            "active_groups": self.active_groups,
            "received_count": self.received_count,
            "sent_count": self.sent_count,
            "real_time_received": self.real_time_received,
            "real_time_sent": self.real_time_sent,
            "version_details": self.version_details,
            # 网络信息
            "network_bytes_sent": format_bytes(self.network_info.bytes_sent),
            "network_bytes_recv": format_bytes(self.network_info.bytes_recv),
            "network_packets_sent": self.network_info.packets_sent,
            "network_packets_recv": self.network_info.packets_recv,
            "network_connections": self.network_info.connections,
            # 进程信息
            "bot_pid": self.bot_process_info.pid,
            "bot_cpu_percent": f"{self.bot_process_info.cpu_percent:.1f}",
            "bot_memory_percent": f"{self.bot_process_info.memory_percent:.1f}",
            "bot_memory_rss_mb": f"{self.bot_process_info.memory_rss_mb:.1f}",
            "bot_memory_total_percent": f"{(self.bot_process_info.memory_rss_mb / self.memory_total_mb * 100):.2f}",
            "bot_threads": self.bot_process_info.threads,
            "bot_open_files": self.bot_process_info.open_files,
        }


class MessageCount:
    """
    用于管理和检索消息计数统计信息的类。
    """

    def __init__(self):
        """
        初始化 MessageCount 实例。
        """
        self.receive_queue: deque[tuple[int, float]] = deque()
        self.send_queue: deque[tuple[int, float]] = deque()

    async def update_message_counts(self) -> None:
        """
        更新消息计数。

        参数:
        - core: 包含消息计数数据的对象。

        注意:
        - 为更新消息计数日志，应定期调用此方法。
        """
        self.receive_queue.append((core.received_count, time.time()))
        self.send_queue.append((core.sent_count, time.time()))

    def get_receive_count(self, time_limit: int | float = 60) -> int:
        """
        获取过去指定时间内接收到的消息数量。

        参数:
        - time_limit: Union[int, float], 指定的时间范围，单位为秒，默认为60秒。

        返回:
        - int: 过去指定时间内接收到的消息数量。
        """
        current_time = time.time()
        # 从队列前端移除超过60秒的条目
        while (
            self.receive_queue and current_time - self.receive_queue[0][1] > time_limit
        ):
            self.receive_queue.popleft()

        # 计算并返回过去一分钟内消息计数的差值
        return (
            self.receive_queue[-1][0] - self.receive_queue[0][0]
            if self.receive_queue
            else 0
        )

    def get_send_count(self, time_limit: int | float = 60) -> int:
        """
        获取过去指定时间内发送的消息数量。

        参数:
        - time_limit: Union[int, float], 指定的时间范围，单位为秒，默认为60秒。

        返回:
        - int: 过去指定时间内发送的消息数量。
        """
        current_time = time.time()
        # 从队列前端移除超过60秒的条目
        while self.send_queue and current_time - self.send_queue[0][1] > time_limit:
            self.send_queue.popleft()

        # 计算并返回过去一分钟内消息计数的差值
        return self.send_queue[-1][0] - self.send_queue[0][0] if self.send_queue else 0


message_count = MessageCount()


def get_avatar_base64() -> str:
    """获取头像图片的base64编码"""
    try:
        avatar_path = Path("statics/Emoticons/高兴.jpg")
        if avatar_path.exists():
            with open(avatar_path, "rb") as f:
                image_data = f.read()
            return base64.b64encode(image_data).decode("utf-8")
    except Exception:
        pass
    return ""


async def render_visual_status(status_data: SystemStatus) -> bytes:
    """渲染可视化状态面板"""
    template_path = Path(__file__).parent / "status_template.html"
    template_data = status_data.get_template_data()

    # 优化渲染参数以提高性能
    page_option = {
        "viewport": {"width": 800, "height": 10},
        "device_scale_factor": 1.2,  # 降低缩放因子以提高性能
    }

    # 使用JPEG格式并设置质量参数以提高性能
    extra_screenshot_option = {
        "type": "jpeg",
        "quality": 85,  # 适中的质量设置
        "scale": "device",
    }

    return await template2img(
        template_path,
        template_data,
        page_option=page_option,
        extra_screenshot_option=extra_screenshot_option,
    )


@channel.use(SchedulerSchema(timers.every_custom_seconds(3)))
async def message_counter():
    await message_count.update_message_counts()


# 接收事件
@listen(GroupMessage, FriendMessage)
@decorate(
    Distribute.require(),
    Function.require(channel.module),
    FrequencyLimitation.require(channel.module),
    Permission.group_require(channel.metadata.level, if_noticed=True),
    Permission.user_require(Permission.User, if_noticed=True),
)
@dispatch(
    Twilight(
        [
            FullMatch("-bot").space(SpacePolicy.PRESERVE),
            ArgumentMatch("-t", "--text", action="store_true", optional=True)
            @ "text_mode",
        ]
    )
)
async def status(
    app: Ariadne, src_place: Group | Friend, source: Source, text_mode: MatchResult
):
    # 收集系统状态数据
    status_data = await SystemStatus.collect_data()

    # 检查是否使用文本模式
    is_text_mode = text_mode.matched

    if is_text_mode:
        # 原有的文本输出模式
        await send_text_status(app, src_place, source, status_data)
    else:
        # 新的可视化输出模式
        try:
            image_bytes = await render_visual_status(status_data)
            await app.send_message(
                src_place,
                MessageChain(Image(data_bytes=image_bytes)),
                quote=source,
            )
        except Exception as e:
            # 记录详细的异常信息用于调试
            logger.exception(f"可视化状态渲染失败: {str(e)}")
            await send_text_status(app, src_place, source, status_data)


async def send_text_status(
    app: Ariadne, src_place: Group | Friend, source: Source, status_data: SystemStatus
):
    """发送文本格式的状态信息（保持向后兼容）"""
    # 构建版本信息块
    version_info = "\n".join(status_data.version_details) + "\n"

    await app.send_message(
        src_place,
        MessageChain(
            f"开机时间：{status_data.launch_time}\n",
            f"运行时长：{status_data.work_time}\n",
            f"接收消息：{status_data.received_count}条 (实时:{status_data.real_time_received}条/m)\n"
            f"发送消息：{status_data.sent_count}条 (实时:{status_data.real_time_sent}条/m)\n"
            f"内存使用：{status_data.memory_used_mb}MB ({status_data.memory_percent:.0f}%)\n",
            f"CPU占比：{status_data.cpu_percent:.1f}%\n",
            f"磁盘占比：{status_data.disk_percent:.1f}%\n",
            f"在线bot数量：{status_data.online_bots}/{status_data.total_bots}\n",
            f"活动群组数量：{status_data.active_groups}\n",
            version_info,
            "项目地址：https://github.com/g1331/xiaomai-bot",
        ),
        quote=source,
    )
