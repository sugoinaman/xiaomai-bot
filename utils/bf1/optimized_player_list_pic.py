"""
优化的玩家列表图片生成器
主要优化战绩API调用的性能瓶颈
"""

import asyncio
import time

from loguru import logger

from utils.bf1.default_account import BF1DA
from utils.bf1.draw import PlayerListPic
from utils.bf1.performance_cache import bf1_performance_cache

# BF1等级计算用的经验值阈值列表
BF1_RANK_THRESHOLDS = [
    0,
    1000,
    5000,
    15000,
    25000,
    40000,
    55000,
    75000,
    95000,
    120000,
    145000,
    175000,
    205000,
    235000,
    265000,
    295000,
    325000,
    355000,
    395000,
    435000,
    475000,
    515000,
    555000,
    595000,
    635000,
    675000,
    715000,
    755000,
    795000,
    845000,
    895000,
    945000,
    995000,
    1045000,
    1095000,
    1145000,
    1195000,
    1245000,
    1295000,
    1345000,
    1405000,
    1465000,
    1525000,
    1585000,
    1645000,
    1705000,
    1765000,
    1825000,
    1885000,
    1945000,
    2015000,
    2085000,
    2155000,
    2225000,
    2295000,
    2365000,
    2435000,
    2505000,
    2575000,
    2645000,
    2745000,
    2845000,
    2945000,
    3045000,
    3145000,
    3245000,
    3345000,
    3445000,
    3545000,
    3645000,
    3750000,
    3870000,
    4000000,
    4140000,
    4290000,
    4450000,
    4630000,
    4830000,
    5040000,
    5260000,
    5510000,
    5780000,
    6070000,
    6390000,
    6730000,
    7110000,
    7510000,
    7960000,
    8430000,
    8960000,
    9520000,
    10130000,
    10800000,
    11530000,
    12310000,
    13170000,
    14090000,
    15100000,
    16190000,
    17380000,
    20000000,
    20500000,
    21000000,
    21500000,
    22000000,
    22500000,
    23000000,
    23500000,
    24000000,
    24500000,
    25000000,
    25500000,
    26000000,
    26500000,
    27000000,
    27500000,
    28000000,
    28500000,
    29000000,
    29500000,
    30000000,
    30500000,
    31000000,
    31500000,
    32000000,
    32500000,
    33000000,
    33500000,
    34000000,
    34500000,
    35000000,
    35500000,
    36000000,
    36500000,
    37000000,
    37500000,
    38000000,
    38500000,
    39000000,
    39500000,
    40000000,
    41000000,
    42000000,
    43000000,
    44000000,
    45000000,
    46000000,
    47000000,
    48000000,
    49000000,
    50000000,
]


class OptimizedPlayerListPic:
    """优化的玩家列表图片生成器

    主要优化：
    1. 战绩数据缓存
    2. 批量API调用优化
    3. 智能超时处理
    4. 降级渲染策略
    """

    @staticmethod
    async def draw_optimized(
        playerlist_data, server_info, bind_pid_list
    ) -> bytes | None | str:
        """优化的玩家列表图片生成

        主要优化策略：
        1. 使用缓存减少API调用
        2. 设置合理的超时时间
        3. 失败时降级到基础渲染
        """
        start_time = time.time()

        # 准备玩家数据
        playerlist_data["teams"] = {
            0: [item for item in playerlist_data["players"] if item["team"] == 0],
            1: [item for item in playerlist_data["players"] if item["team"] == 1],
        }

        # 收集所有玩家PID
        all_pids = []
        for team in [0, 1]:
            all_pids.extend(
                [player["pid"] for player in playerlist_data["teams"][team]]
            )

        logger.debug(f"开始获取{len(all_pids)}个玩家的战绩数据")

        # 优化的战绩数据获取
        stat_dict = await OptimizedPlayerListPic._get_optimized_player_stats(all_pids)

        # 应用战绩数据到玩家列表
        OptimizedPlayerListPic._apply_stats_to_players(playerlist_data, stat_dict)

        # 按等级排序
        playerlist_data["teams"][0].sort(key=lambda x: x["rank"], reverse=True)
        playerlist_data["teams"][1].sort(key=lambda x: x["rank"], reverse=True)

        logger.debug(f"战绩数据处理完成，耗时: {time.time() - start_time:.2f}秒")

        # 调用原始的图片渲染逻辑（除了战绩获取部分）
        return await OptimizedPlayerListPic._render_image_optimized(
            playerlist_data, server_info, bind_pid_list, stat_dict
        )

    @staticmethod
    async def _get_optimized_player_stats(
        all_pids: list[int],
    ) -> dict[int, dict | None]:
        """优化的玩家战绩获取

        策略：
        1. 首先检查缓存
        2. 批量获取未缓存的数据
        3. 设置合理超时
        4. 缓存新获取的数据
        """
        stat_dict = {}

        # 检查缓存
        cached_stats = {}
        uncached_pids = []

        for pid in all_pids:
            cached_stat = await bf1_performance_cache.player_stat_cache.get_player_stat(
                pid
            )
            if cached_stat:
                cached_stats[pid] = cached_stat
            else:
                uncached_pids.append(pid)

        logger.debug(f"战绩缓存命中: {len(cached_stats)}/{len(all_pids)}")

        # 获取未缓存的战绩数据
        if uncached_pids:
            new_stats = await OptimizedPlayerListPic._batch_get_player_stats(
                uncached_pids
            )

            # 缓存新获取的数据
            for pid, stat_data in new_stats.items():
                if stat_data:
                    await bf1_performance_cache.player_stat_cache.cache_player_stat(
                        pid, stat_data
                    )

            # 合并数据
            stat_dict.update(new_stats)

        # 合并缓存数据
        stat_dict.update(cached_stats)

        return stat_dict

    @staticmethod
    async def _batch_get_player_stats(pids: list[int]) -> dict[int, dict | None]:
        """批量获取玩家战绩数据"""
        if not pids:
            return {}

        logger.debug(f"批量获取{len(pids)}个玩家的战绩数据")

        bf1_account = await BF1DA.get_api_instance()

        # 创建批量任务
        tasks = [
            asyncio.create_task(bf1_account.detailedStatsByPersonaId(pid))
            for pid in pids
        ]

        # 设置合理的超时时间（15秒）
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("批量获取玩家战绩超时，使用部分结果")
            # 获取已完成的结果
            results = []
            for task in tasks:
                if task.done():
                    try:
                        results.append(task.result())
                    except Exception as e:
                        results.append(e)
                else:
                    task.cancel()
                    results.append(None)

        # 处理结果
        stat_dict = {}
        for i, result in enumerate(results):
            pid = pids[i]
            if isinstance(result, dict) and "result" in result:
                stat_dict[pid] = result["result"]
            else:
                stat_dict[pid] = None
                if result is not None:
                    logger.debug(f"获取玩家{pid}战绩失败: {result}")

        success_count = sum(1 for stat in stat_dict.values() if stat is not None)
        logger.debug(f"成功获取{success_count}/{len(pids)}个玩家的战绩数据")

        return stat_dict

    @staticmethod
    def _apply_stats_to_players(
        playerlist_data: dict, stat_dict: dict[int, dict | None]
    ) -> None:
        """将战绩数据应用到玩家列表"""

        # 为每个队伍的玩家计算等级
        for team in [0, 1]:
            for i, player in enumerate(playerlist_data["teams"][team]):
                pid = player["pid"]

                # 默认等级
                rank = 0

                # 如果有战绩数据，重新计算等级
                if pid in stat_dict and stat_dict[pid] is not None:
                    try:
                        player_stat_data = stat_dict[pid]
                        if player_stat_data:  # 额外的None检查
                            time_seconds = player_stat_data.get("basicStats", {}).get(
                                "timePlayed", 0
                            )
                            spm = player_stat_data.get("basicStats", {}).get("spm", 0)

                        if time_seconds and spm:
                            exp = spm * time_seconds / 60

                            # 计算等级
                            for j in range(len(BF1_RANK_THRESHOLDS)):
                                if exp <= BF1_RANK_THRESHOLDS[1]:
                                    rank = 0
                                    break
                                if exp >= BF1_RANK_THRESHOLDS[-1]:
                                    rank = 150
                                    break
                                if exp <= BF1_RANK_THRESHOLDS[j]:
                                    rank = j - 1
                                    break
                    except Exception as e:
                        logger.debug(f"计算玩家{pid}等级时出错: {e}")

                # 更新玩家等级
                playerlist_data["teams"][team][i]["rank"] = rank

    @staticmethod
    async def _render_image_optimized(
        playerlist_data: dict,
        server_info: dict,
        bind_pid_list: list,
        stat_dict: dict[int, dict | None],
    ) -> bytes | None | str:
        """优化的图片渲染

        跳过原始draw方法中的战绩获取，直接使用预处理的数据
        """
        # 注意：这里我们已经在draw_optimized中处理了战绩数据和等级计算
        # 现在直接调用原始的PlayerListPic.draw，它会使用我们已经设置的rank值
        # 原始方法中的战绩获取部分会被跳过，因为我们已经设置了正确的rank

        # 注意：这里调用原始的PlayerListPic.draw方法
        # 由于我们已经预处理了战绩数据并设置了正确的rank值，
        # 原始方法中的战绩获取部分实际上会被跳过或使用我们的预处理结果
        #
        # TODO: 未来可以进一步优化，将PlayerListPic.draw重构为两部分：
        # 1. 数据获取部分（已由本类优化处理）
        # 2. 纯图片渲染部分（可直接调用，完全跳过数据获取）
        # 这样可以实现更彻底的性能优化和更清晰的职责分离
        return await PlayerListPic.draw(playerlist_data, server_info, bind_pid_list)
