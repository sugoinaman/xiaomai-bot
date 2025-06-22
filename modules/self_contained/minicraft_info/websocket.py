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
        self.server = server
        self.message_handler = message_handler
        self.websocket = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # 秒

    async def connect(self):
        """建立 WebSocket 连接"""
        if not self.server.websocket_url:
            logger.warning(f"服务器 {self.server.server_name} 没有配置 WebSocket URL")
            return False

        try:
            headers = self.server.websocket_headers or {}
            if "x-self-name" not in headers:
                headers["x-self-name"] = self.server.server_name

            logger.info(
                f"正在连接到服务器 {self.server.server_name} 的 WebSocket: {self.server.websocket_url}"
            )
            logger.debug(f"使用的请求头: {headers}")

            self.websocket = await websockets.connect(
                self.server.websocket_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            )

            self.is_connected = True
            self.reconnect_attempts = 0
            logger.success(f"成功连接到服务器 {self.server.server_name} 的 WebSocket")

            # 启动消息监听
            asyncio.create_task(self._listen_messages())
            return True

        except Exception as e:
            logger.error(
                f"连接到服务器 {self.server.server_name} 的 WebSocket 失败: {e}"
            )
            self.is_connected = False
            return False

    async def disconnect(self):
        """断开 WebSocket 连接"""
        self.is_connected = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            logger.info(f"已断开服务器 {self.server.server_name} 的 WebSocket 连接")

    async def send_message(self, message: str):
        """发送消息到 MC 服务器"""
        if not self.is_connected or not self.websocket:
            logger.warning(
                f"服务器 {self.server.server_name} WebSocket 未连接，无法发送消息"
            )
            return False

        try:
            # 构造鹊桥 API 期望的消息格式
            # 参考: https://github.com/17TheWord/QueQiao/wiki/5.-API#broadcast--send-message
            message_data = {"api": "broadcast", "data": {"message": message}}

            await self.websocket.send(json.dumps(message_data))
            logger.debug(f"向服务器 {self.server.server_name} 发送消息: {message}")
            return True

        except Exception as e:
            logger.error(f"向服务器 {self.server.server_name} 发送消息失败: {e}")
            return False

    async def _listen_messages(self):
        """监听来自 MC 服务器的消息"""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self.message_handler(self.server, data)
                except json.JSONDecodeError:
                    logger.warning(f"收到无效的 JSON 消息: {message}")
                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"服务器 {self.server.server_name} WebSocket 连接已关闭")
            self.is_connected = False
            # 尝试重连
            await self._reconnect()
        except Exception as e:
            logger.error(f"监听服务器 {self.server.server_name} 消息时出错: {e}")
            self.is_connected = False

    async def _reconnect(self):
        """重连机制"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"服务器 {self.server.server_name} 重连次数已达上限，停止重连")
            return

        self.reconnect_attempts += 1
        logger.info(
            f"尝试重连服务器 {self.server.server_name} (第 {self.reconnect_attempts} 次)"
        )

        await asyncio.sleep(self.reconnect_delay)

        if await self.connect():
            logger.success(f"服务器 {self.server.server_name} 重连成功")
        else:
            # 递增延迟时间
            self.reconnect_delay = min(self.reconnect_delay * 2, 60)
            await self._reconnect()


class McWebSocketManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.connections: dict[int, McWebSocketConnection] = {}

    async def add_connection(self, server: McServer):
        """添加服务器连接"""
        if server.id in self.connections:
            logger.warning(f"服务器 {server.server_name} 的连接已存在")
            return

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
            return await self.connections[server_id].send_message(message)
        else:
            logger.warning(f"服务器 ID {server_id} 的连接不存在")
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

            # 格式化消息
            formatted_message = f"[{server.server_name}] {player_name}: {message}"

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
