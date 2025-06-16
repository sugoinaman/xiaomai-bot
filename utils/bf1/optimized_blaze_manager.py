"""
优化的Blaze连接管理器
针对性能问题进行优化，确保单连接高效复用
"""

import asyncio
import time
from loguru import logger

from utils.bf1.blaze.BlazeSocket import BlazeSocket
from utils.bf1.data_handle import BlazeData
from utils.bf1.default_account import BF1DA
from utils.bf1.gateway_api import api_instance
from utils.bf1.performance_cache import bf1_performance_cache


class OptimizedBlazeManager:
    """优化的Blaze管理器

    主要优化：
    1. 降低超时时间
    2. 智能连接复用
    3. 集成缓存机制
    4. 批量处理优化
    """

    def __init__(self):
        self._connection_cache: dict[int, dict] = {}
        self._lock = asyncio.Lock()

        # 优化的超时配置
        self.connection_timeout = 20  # 从60秒降低到20秒
        self.api_timeout = 15  # API调用超时15秒

    async def get_optimized_player_list(
        self,
        game_ids: list[int],
        origin: bool = False,
        platoon: bool = False,
        enable_stat_cache: bool = True,
    ) -> dict | None | str:
        """优化的玩家列表获取

        主要优化：
        1. 降低超时时间
        2. 集成Platoon缓存
        3. 为后续战绩缓存做准备
        """
        # 检查game_ids类型
        if not isinstance(game_ids, list):
            game_ids = [game_ids]
        game_ids = [int(game_id) for game_id in game_ids]

        # 获取Blaze连接
        blaze_socket = await self._get_optimized_connection()
        if not blaze_socket:
            return "BlazeClient初始化出错!"

        # 构建请求包
        packet = {
            "method": "GameManager.getGameDataFromId",
            "type": "Command",
            "data": {
                "DNAM 1": "csFullGameList",
                "GLST 40": game_ids,
            },
        }

        try:
            # 使用优化的超时时间
            response = await asyncio.wait_for(
                blaze_socket.send(packet), timeout=self.connection_timeout
            )
        except asyncio.TimeoutError:
            try:
                await blaze_socket.close()
            except Exception as e:
                logger.error(f"关闭连接时出错: {e}")
            logger.error("Blaze后端超时!")
            return "Blaze后端超时!"

        if origin:
            return response

        # 处理响应数据
        response = BlazeData.player_list_handle(response)
        if not isinstance(response, dict):
            blaze_socket.authenticated = False
            return response

        # 优化的Platoon处理
        if platoon:
            response = await self._process_platoon_with_cache(response, game_ids)

        return response

    async def _get_optimized_connection(self) -> BlazeSocket | None:
        """获取优化的Blaze连接"""
        pid = int(BF1DA.pid)

        async with self._lock:
            # 检查现有连接
            if pid in self._connection_cache:
                connection_info = self._connection_cache[pid]
                socket = connection_info["socket"]

                # 检查连接是否仍然有效
                if socket.authenticated and socket.connect:
                    # 检查连接年龄，超过5分钟重新连接
                    if time.time() - connection_info["created_at"] < 300:
                        logger.debug(f"复用现有Blaze连接: {pid}")
                        return socket
                    else:
                        logger.debug(f"Blaze连接过期，重新建立: {pid}")
                        try:
                            await socket.close()
                        except Exception:
                            pass
                        del self._connection_cache[pid]
                else:
                    logger.debug(f"Blaze连接无效，重新建立: {pid}")
                    del self._connection_cache[pid]

        # 建立新连接
        return await self._create_new_connection(pid)

    async def _create_new_connection(self, pid: int) -> BlazeSocket | None:
        """创建新的Blaze连接"""
        try:
            # 导入必要的模块
            from utils.bf1.blaze.BlazeClient import BlazeClientManagerInstance

            # 获取连接
            blaze_socket = await BlazeClientManagerInstance.get_socket_for_pid(pid)
            if not blaze_socket:
                logger.error("无法获取到BlazeSocket")
                return None

            # 获取账号实例
            bf1_account = api_instance.get_api_instance(
                pid=pid, remid=BF1DA.remid, sid=BF1DA.sid
            )

            # 获取认证码
            auth_code = await bf1_account.getBlazeAuthcode()
            logger.debug(f"获取到Blaze AuthCode: {auth_code}")

            # Blaze登录
            login_packet = {
                "method": "Authentication.login",
                "type": "Command",
                "id": 0,
                "length": 28,
                "data": {"AUTH 1": auth_code, "EXTB 2": "", "EXTI 0": 0},
            }

            # 使用优化的超时时间
            response = await asyncio.wait_for(
                blaze_socket.send(login_packet), timeout=self.api_timeout
            )

            # 验证登录结果
            if "data" in response and "DSNM" in response["data"]:
                name = response["data"]["DSNM"]
                pid_response = response["data"]["PID"]
                uid = response["data"]["UID"]
                CGID = response["data"]["CGID"][2]
                logger.success(
                    f"Blaze登录成功: Name:{name} Pid:{pid_response} Uid:{uid} CGID:{CGID}"
                )

                blaze_socket.authenticated = True

                # 缓存连接
                async with self._lock:
                    self._connection_cache[pid] = {
                        "socket": blaze_socket,
                        "created_at": time.time(),
                    }

                return blaze_socket
            else:
                logger.error(f"Blaze登录失败: {response}")
                return None

        except asyncio.TimeoutError:
            logger.error("Blaze连接超时!")
            return None
        except Exception as e:
            logger.error(f"Blaze连接失败: {e}")
            return None

    async def _process_platoon_with_cache(
        self, response: dict, game_ids: list[int]
    ) -> dict:
        """使用缓存优化Platoon信息处理"""
        bf1_account = await BF1DA.get_api_instance()

        for game_id in game_ids:
            if game_id not in response:
                continue

            # 收集所有玩家PID
            pid_list = [player["pid"] for player in response[game_id]["players"]]

            # 批量检查缓存
            cached_platoons = (
                await bf1_performance_cache.platoon_cache.batch_get_platoon_info(
                    pid_list
                )
            )

            # 识别需要API调用的PID
            uncached_pids = [
                pid
                for pid, cached_data in cached_platoons.items()
                if cached_data is None
            ]

            logger.debug(
                f"Platoon缓存命中: {len(pid_list) - len(uncached_pids)}/{len(pid_list)}"
            )

            # 对未缓存的PID进行API调用
            new_platoon_data = {}
            if uncached_pids:
                platoon_tasks = [
                    bf1_account.getActivePlatoon(pid) for pid in uncached_pids
                ]
                try:
                    platoon_results = await asyncio.wait_for(
                        asyncio.gather(*platoon_tasks, return_exceptions=True),
                        timeout=self.api_timeout,
                    )

                    for i, result in enumerate(platoon_results):
                        pid = uncached_pids[i]
                        if isinstance(result, dict) and "result" in result:
                            platoon_data = result["result"] if result["result"] else {}
                            new_platoon_data[pid] = platoon_data
                        else:
                            new_platoon_data[pid] = {}

                    # 批量缓存新数据
                    await bf1_performance_cache.platoon_cache.batch_cache_platoon_info(
                        new_platoon_data
                    )

                except asyncio.TimeoutError:
                    logger.warning("Platoon API调用超时")
                    for pid in uncached_pids:
                        new_platoon_data[pid] = {}
                except Exception as e:
                    logger.error(f"获取Platoon信息失败: {e}")
                    for pid in uncached_pids:
                        new_platoon_data[pid] = {}

            # 合并缓存数据和新数据
            all_platoon_data = {**cached_platoons, **new_platoon_data}

            # 应用到响应数据
            platoons = []
            for i, player in enumerate(response[game_id]["players"]):
                pid = player["pid"]
                platoon_info = all_platoon_data.get(pid, {})

                if platoon_info and platoon_info not in platoons:
                    platoons.append(platoon_info)

                response[game_id]["players"][i]["platoon"] = platoon_info

            response[game_id]["platoons"] = platoons

        return response

    async def close_all_connections(self) -> None:
        """关闭所有连接"""
        async with self._lock:
            for connection_info in self._connection_cache.values():
                try:
                    await connection_info["socket"].close()
                except Exception as e:
                    logger.error(f"关闭Blaze连接时出错: {e}")
            self._connection_cache.clear()
            logger.info("所有Blaze连接已关闭")


# 全局优化管理器实例
optimized_blaze_manager = OptimizedBlazeManager()
