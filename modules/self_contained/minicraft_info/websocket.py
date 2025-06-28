import asyncio
import json
from collections.abc import Callable

import websockets
from graia.ariadne.message.chain import MessageChain
from loguru import logger

from core.models import response_model

from .database import get_server_bound_groups
from .models import McServer


class McWebSocketConnection:
    """单个 WebSocket 连接的封装"""

    def __init__(self, server: McServer, message_handler: Callable):
        self.server_id = server.id  # 只保存服务器ID，不保存对象引用
        self.server_name = server.server_name  # 保存服务器名称用于日志
        self.message_handler = message_handler
        self.websocket = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # 秒
        self.listen_task = None  # 消息监听任务
        self._reconnecting = False  # 重连状态标志

    async def connect(self):
        """建立 WebSocket 连接"""
        # 动态获取最新的服务器信息
        from .database import get_mc_server_by_id

        server = await get_mc_server_by_id(self.server_id)
        if not server:
            logger.error(f"无法获取服务器 ID {self.server_id} 的信息")
            return False

        # 更新服务器名称（可能已更改）
        self.server_name = server.server_name

        if not server.websocket_url:
            logger.warning(f"服务器 {server.server_name} 没有配置 WebSocket URL")
            return False

        try:
            headers = server.websocket_headers or {}
            if "x-self-name" not in headers:
                headers["x-self-name"] = server.server_name

            logger.info(
                f"正在连接到服务器 {server.server_name} 的 WebSocket: {server.websocket_url}"
            )
            logger.debug(f"使用的请求头: {headers}")

            self.websocket = await websockets.connect(
                server.websocket_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            )

            self.is_connected = True
            self.reconnect_attempts = 0
            logger.success(f"成功连接到服务器 {server.server_name} 的 WebSocket")

            # 取消旧的监听任务（如果存在）
            if self.listen_task and not self.listen_task.done():
                self.listen_task.cancel()
                try:
                    await self.listen_task
                except asyncio.CancelledError:
                    pass

            # 启动新的消息监听任务
            self.listen_task = asyncio.create_task(self._listen_messages(server))
            return True

        except Exception as e:
            logger.error(f"连接到服务器 {server.server_name} 的 WebSocket 失败: {e}")
            self.is_connected = False
            return False

    async def disconnect(self):
        """断开 WebSocket 连接"""
        self.is_connected = False

        # 取消监听任务
        if self.listen_task and not self.listen_task.done():
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass

        # 关闭 WebSocket 连接
        if self.websocket:
            try:
                if not self.websocket.closed:
                    await self.websocket.close()
                    logger.debug(f"服务器 {self.server_name} WebSocket 连接已主动关闭")
                else:
                    logger.debug(
                        f"服务器 {self.server_name} WebSocket 连接已处于关闭状态"
                    )
            except Exception as e:
                logger.warning(
                    f"关闭服务器 {self.server_name} WebSocket 连接时出错: {e}"
                )
            finally:
                self.websocket = None

        logger.info(f"已断开服务器 {self.server_name} 的 WebSocket 连接")

    def _is_connection_valid(self) -> bool:
        """检查WebSocket连接是否有效可用"""
        if not self.is_connected:
            return False
        if not self.websocket:
            return False
        if self.websocket.closed:
            # 连接已关闭，同步更新状态
            self.is_connected = False
            return False
        return True

    async def send_message(self, message: str):
        """发送消息到 MC 服务器"""
        if not self._is_connection_valid():
            status_info = []
            if not self.is_connected:
                status_info.append("is_connected=False")
            if not self.websocket:
                status_info.append("websocket=None")
            elif self.websocket.closed:
                status_info.append("websocket.closed=True")

            logger.warning(
                f"服务器 {self.server_name} WebSocket 连接无效，无法发送消息 "
                f"({', '.join(status_info)})"
            )
            return False

        try:
            # 构造鹊桥 API 期望的消息格式
            # 参考: https://github.com/17TheWord/QueQiao/wiki/5.-API#broadcast--send-message
            message_data = {"api": "broadcast", "data": {"message": message}}

            await self.websocket.send(json.dumps(message_data))
            logger.debug(f"向服务器 {self.server_name} 发送消息: {message}")
            return True

        except websockets.exceptions.ConnectionClosed as e:
            # 连接已关闭，更新状态并记录详细信息
            self.is_connected = False
            logger.error(
                f"向服务器 {self.server_name} 发送消息失败: WebSocket连接已关闭 "
                f"(code={e.code}, reason='{e.reason}')"
            )
            return False
        except Exception as e:
            logger.error(f"向服务器 {self.server_name} 发送消息失败: {e}")
            # 如果是连接相关错误，更新连接状态
            if "connection" in str(e).lower() or "closed" in str(e).lower():
                self.is_connected = False
            return False

    async def _listen_messages(self, server):
        """监听来自 MC 服务器的消息"""
        try:
            async for message in self.websocket:
                # 在处理每条消息前检查连接状态
                if not self._is_connection_valid():
                    logger.warning(
                        f"服务器 {self.server_name} 连接状态异常，停止消息监听"
                    )
                    break

                try:
                    data = json.loads(message)
                    await self.message_handler(server, data)
                except json.JSONDecodeError:
                    logger.warning(f"收到无效的 JSON 消息: {message}")
                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")

        except websockets.exceptions.ConnectionClosed as e:
            # 记录详细的连接关闭信息
            self.is_connected = False
            close_info = f"code={e.code}"
            if e.reason:
                close_info += f", reason='{e.reason}'"

            logger.warning(
                f"服务器 {self.server_name} WebSocket 连接已关闭 ({close_info})"
            )

            # 根据关闭代码决定是否重连
            if e.code in [1000, 1001]:  # 正常关闭或going away
                logger.info(f"服务器 {self.server_name} 正常关闭连接，尝试重连")
            elif e.code in [1002, 1003]:  # 协议错误或不支持的数据
                logger.error(f"服务器 {self.server_name} 因协议错误关闭连接，停止重连")
                return

            # 尝试重连（避免重复重连）
            if not self._reconnecting:
                await self._reconnect()
        except Exception as e:
            logger.error(f"监听服务器 {self.server_name} 消息时出错: {e}")
            self.is_connected = False

    async def _reconnect(self):
        """重连机制"""
        if self._reconnecting:
            logger.debug(f"服务器 {self.server_name} 已在重连中，跳过")
            return

        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"服务器 {self.server_name} 重连次数已达上限，停止重连")
            return

        self._reconnecting = True
        try:
            self.reconnect_attempts += 1
            logger.info(
                f"尝试重连服务器 {self.server_name} (第 {self.reconnect_attempts} 次)"
            )

            await asyncio.sleep(self.reconnect_delay)

            if await self.connect():
                logger.success(f"服务器 {self.server_name} 重连成功")
                self._reconnecting = False
            else:
                # 递增延迟时间
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)
                self._reconnecting = False
                await self._reconnect()
        except Exception as e:
            logger.error(f"重连过程中出错: {e}")
            self._reconnecting = False


class McWebSocketManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.connections: dict[int, McWebSocketConnection] = {}
        self.recent_messages: dict[str, float] = {}  # 消息去重缓存
        self.message_cache_duration = 5.0  # 消息缓存时间（秒）

    async def add_connection(self, server: McServer):
        """添加服务器连接"""
        if server.id in self.connections:
            existing_connection = self.connections[server.id]
            if existing_connection.is_connected:
                logger.warning(
                    f"服务器 {server.server_name} 的连接已存在且正常，跳过重复连接"
                )
                return
            else:
                logger.info(
                    f"服务器 {server.server_name} 的连接已存在但已断开，先清理旧连接"
                )
                await self.remove_connection(server.id)

        logger.info(f"正在为服务器 {server.server_name} 创建新的 WebSocket 连接")
        connection = McWebSocketConnection(server, self._handle_mc_message)

        if await connection.connect():
            self.connections[server.id] = connection
            logger.success(f"成功添加服务器 {server.server_name} 的 WebSocket 连接")
        else:
            logger.error(f"添加服务器 {server.server_name} 的 WebSocket 连接失败")

    async def remove_connection(self, server_id: int):
        """移除服务器连接"""
        if server_id in self.connections:
            await self.connections[server_id].disconnect()
            del self.connections[server_id]
            logger.info(f"已移除服务器 ID {server_id} 的 WebSocket 连接")

    async def send_to_server(self, server_id: int, message: str):
        """向指定服务器发送消息"""
        if server_id in self.connections:
            connection = self.connections[server_id]
            if connection._is_connection_valid():
                return await connection.send_message(message)
            else:
                logger.warning(
                    f"服务器 ID {server_id} ({connection.server_name}) 的连接无效，无法发送消息"
                )
                return False
        else:
            logger.warning(f"服务器 ID {server_id} 的连接不存在")
            return False

    def get_connection_status(self) -> dict:
        """获取所有连接的详细状态"""
        status = {}
        for server_id, connection in self.connections.items():
            # 使用连接的有效性检查方法
            is_valid = connection._is_connection_valid()

            status[server_id] = {
                "server_name": connection.server_name,
                "is_connected": connection.is_connected,
                "is_connection_valid": is_valid,
                "reconnect_attempts": connection.reconnect_attempts,
                "reconnect_delay": connection.reconnect_delay,
                "is_reconnecting": connection._reconnecting,
                "has_listen_task": connection.listen_task is not None
                and not connection.listen_task.done(),
                "websocket_exists": connection.websocket is not None,
                "websocket_closed": connection.websocket is None
                or connection.websocket.closed
                if connection.websocket
                else True,
            }
        return status

    def _is_duplicate_message(self, message_key: str) -> bool:
        """检查是否为重复消息"""
        import time

        current_time = time.time()

        # 清理过期的消息缓存
        expired_keys = [
            key
            for key, timestamp in self.recent_messages.items()
            if current_time - timestamp > self.message_cache_duration
        ]
        for key in expired_keys:
            del self.recent_messages[key]

        # 检查是否为重复消息
        if message_key in self.recent_messages:
            logger.debug(f"检测到重复消息，跳过: {message_key}")
            return True

        # 记录新消息
        self.recent_messages[message_key] = current_time
        return False

    async def _handle_mc_message(self, server: McServer, data: dict):
        """处理来自 MC 服务器的消息"""
        try:
            # 解析消息类型和内容
            # 参考: https://github.com/17TheWord/QueQiao/wiki/4.4-Forge%E7%AB%AF%E4%BA%8B%E4%BB%B6%E7%B1%BB%E5%9E%8B
            post_type = data.get("post_type", "")
            sub_type = data.get("sub_type", "")
            event_name = data.get("event_name", "")

            logger.debug(
                f"收到来自服务器 {server.server_name} 的消息: post_type={post_type}, sub_type={sub_type}, event_name={event_name}"
            )

            if post_type == "message":
                if sub_type == "chat" and event_name == "ServerChatEvent":
                    await self._handle_chat_message(server, data)
                elif sub_type == "death" and event_name == "PlayerDeathEvent":
                    await self._handle_death_message(server, data)
                elif sub_type == "player_command" and event_name == "CommandEvent":
                    # 通常不转发命令消息到 QQ 群
                    logger.debug(f"收到玩家命令事件，不转发: {data.get('message', '')}")
                else:
                    logger.debug(f"收到未处理的消息事件: {data}")
            elif post_type == "notice":
                if sub_type == "join" and event_name == "PlayerLoggedInEvent":
                    await self._handle_join_message(server, data)
                elif sub_type == "quit" and event_name == "PlayerLoggedOutEvent":
                    await self._handle_quit_message(server, data)
                else:
                    logger.debug(f"收到未处理的通知事件: {data}")
            else:
                logger.debug(f"收到未处理的消息类型: {data}")

        except Exception as e:
            logger.error(f"处理 MC 消息时出错: {e}")
            logger.exception("详细错误信息:")

    async def _handle_chat_message(self, server: McServer, data: dict):
        """处理聊天消息 (ServerChatEvent)"""
        try:
            # 根据 Forge 端事件类型文档，玩家信息在 player 字段中
            player_info = data.get("player", {})
            player_name = player_info.get("nickname", "未知玩家")
            message = data.get("message", "")

            # 创建消息唯一标识符用于去重
            message_key = (
                f"chat_{server.id}_{player_name}_{message}_{data.get('timestamp', '')}"
            )

            # 检查是否为重复消息
            if self._is_duplicate_message(message_key):
                logger.debug(f"跳过重复聊天消息: {player_name}: {message}")
                return

            # 格式化消息
            formatted_message = f"[{server.server_name}] {player_name}: {message}"

            logger.info(f"处理聊天消息: {formatted_message}")

            # 发送到绑定的群组
            await self._send_to_bound_groups(server.id, formatted_message)

        except Exception as e:
            logger.error(f"处理聊天消息时出错: {e}")
            logger.exception("详细错误信息:")

    async def _handle_death_message(self, server: McServer, data: dict):
        """处理玩家死亡消息 (PlayerDeathEvent)"""
        try:
            # 根据 Forge 端事件类型文档，玩家信息在 player 字段中
            player_info = data.get("player", {})
            player_name = player_info.get("nickname", "未知玩家")
            death_message = data.get("message", f"{player_name} 死亡了")

            # 格式化消息
            formatted_message = f"[{server.server_name}] {death_message}"

            # 发送到绑定的群组
            await self._send_to_bound_groups(server.id, formatted_message)

        except Exception as e:
            logger.error(f"处理死亡消息时出错: {e}")
            logger.exception("详细错误信息:")

    async def _handle_join_message(self, server: McServer, data: dict):
        """处理玩家加入消息 (PlayerLoggedInEvent)"""
        try:
            # 根据 Forge 端事件类型文档，玩家信息在 player 字段中
            player_info = data.get("player", {})
            player_name = player_info.get("nickname", "未知玩家")

            # 格式化消息
            formatted_message = f"[{server.server_name}] {player_name} 加入了服务器"

            # 发送到绑定的群组
            await self._send_to_bound_groups(server.id, formatted_message)

        except Exception as e:
            logger.error(f"处理加入消息时出错: {e}")
            logger.exception("详细错误信息:")

    async def _handle_quit_message(self, server: McServer, data: dict):
        """处理玩家离开消息 (PlayerLoggedOutEvent)"""
        try:
            # 根据 Forge 端事件类型文档，玩家信息在 player 字段中
            player_info = data.get("player", {})
            player_name = player_info.get("nickname", "未知玩家")

            # 格式化消息
            formatted_message = f"[{server.server_name}] {player_name} 离开了服务器"

            # 发送到绑定的群组
            await self._send_to_bound_groups(server.id, formatted_message)

        except Exception as e:
            logger.error(f"处理离开消息时出错: {e}")
            logger.exception("详细错误信息:")

    async def _send_to_bound_groups(self, server_id: int, message: str):
        """发送消息到绑定的群组"""
        try:
            # 获取 account_controller 实例
            account_controller = response_model.get_acc_controller()

            # 查询启用了聊天同步的绑定群组
            bound_groups = await get_server_bound_groups(
                server_id, sync_enabled_only=True
            )

            if not bound_groups:
                logger.debug(f"服务器 ID {server_id} 没有启用聊天同步的绑定群组")
                return

            # 向每个绑定的群组发送消息
            for bind, _ in bound_groups:
                try:
                    # 动态获取 Ariadne 应用实例
                    app, group = await account_controller.get_app_from_total_groups(
                        bind.group_id
                    )

                    if not app or not group:
                        logger.warning(
                            f"无法获取群 {bind.group_id} 的 Ariadne 应用实例"
                        )
                        continue

                    await app.send_group_message(bind.group_id, MessageChain(message))
                    logger.debug(f"已向群 {bind.group_id} 发送MC消息: {message}")
                except Exception as e:
                    logger.error(f"向群 {bind.group_id} 发送消息失败: {e}")

        except Exception as e:
            logger.error(f"发送消息到绑定群组时出错: {e}")
            logger.exception("详细错误信息:")


# 全局 WebSocket 管理器实例
ws_manager = McWebSocketManager()
