"""
BF1性能优化缓存管理器
专门针对-pl指令的性能瓶颈进行优化
"""

import asyncio
import time
from typing import Any
from loguru import logger


class PlayerStatCache:
    """玩家战绩缓存

    缓存玩家的detailedStatsByPersonaId结果，减少重复API调用
    """

    def __init__(self):
        self._cache: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

        # 缓存配置
        self.ttl = 300  # 5分钟缓存，战绩数据变化较慢

        # 启动清理任务
        asyncio.create_task(self._cleanup_expired_cache())

    async def get_player_stat(self, pid: int) -> dict | None:
        """获取玩家战绩缓存"""
        async with self._lock:
            if pid in self._cache:
                item = self._cache[pid]
                if time.time() - item["timestamp"] < self.ttl:
                    logger.debug(f"玩家战绩缓存命中: {pid}")
                    return item["data"]
                else:
                    # 清理过期缓存
                    del self._cache[pid]
                    logger.debug(f"玩家战绩缓存过期: {pid}")
        return None

    async def cache_player_stat(self, pid: int, data: dict) -> None:
        """缓存玩家战绩数据"""
        async with self._lock:
            self._cache[pid] = {"data": data, "timestamp": time.time()}
            logger.debug(f"玩家战绩已缓存: {pid}")

    async def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        async with self._lock:
            return {
                "cached_players": len(self._cache),
                "cache_hit_potential": len(self._cache),
            }

    async def _cleanup_expired_cache(self) -> None:
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟清理一次
                async with self._lock:
                    current_time = time.time()
                    expired_pids = [
                        pid
                        for pid, item in self._cache.items()
                        if current_time - item["timestamp"] > self.ttl
                    ]
                    for pid in expired_pids:
                        del self._cache[pid]

                    if expired_pids:
                        logger.debug(f"清理过期玩家战绩缓存: {len(expired_pids)}个")
            except Exception as e:
                logger.error(f"清理玩家战绩缓存时出错: {e}")


class PlatoonCache:
    """Platoon信息缓存

    缓存战队信息，减少getActivePlatoon API调用
    """

    def __init__(self):
        self._cache: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

        # 缓存配置
        self.ttl = 600  # 10分钟缓存，战队信息变化很慢

        # 启动清理任务
        asyncio.create_task(self._cleanup_expired_cache())

    async def get_platoon_info(self, pid: int) -> dict | None:
        """获取Platoon信息缓存"""
        async with self._lock:
            if pid in self._cache:
                item = self._cache[pid]
                if time.time() - item["timestamp"] < self.ttl:
                    logger.debug(f"Platoon缓存命中: {pid}")
                    return item["data"]
                else:
                    # 清理过期缓存
                    del self._cache[pid]
                    logger.debug(f"Platoon缓存过期: {pid}")
        return None

    async def cache_platoon_info(self, pid: int, data: dict) -> None:
        """缓存Platoon信息"""
        async with self._lock:
            self._cache[pid] = {"data": data, "timestamp": time.time()}
            logger.debug(f"Platoon信息已缓存: {pid}")

    async def batch_get_platoon_info(self, pids: list[int]) -> dict[int, dict | None]:
        """批量获取Platoon信息"""
        result = {}
        async with self._lock:
            current_time = time.time()
            for pid in pids:
                if pid in self._cache:
                    item = self._cache[pid]
                    if current_time - item["timestamp"] < self.ttl:
                        result[pid] = item["data"]
                    else:
                        del self._cache[pid]
                        result[pid] = None
                else:
                    result[pid] = None
        return result

    async def batch_cache_platoon_info(self, platoon_data: dict[int, dict]) -> None:
        """批量缓存Platoon信息"""
        async with self._lock:
            current_time = time.time()
            for pid, data in platoon_data.items():
                self._cache[pid] = {"data": data, "timestamp": current_time}
            logger.debug(f"批量缓存Platoon信息: {len(platoon_data)}个")

    async def _cleanup_expired_cache(self) -> None:
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(120)  # 每2分钟清理一次
                async with self._lock:
                    current_time = time.time()
                    expired_pids = [
                        pid
                        for pid, item in self._cache.items()
                        if current_time - item["timestamp"] > self.ttl
                    ]
                    for pid in expired_pids:
                        del self._cache[pid]

                    if expired_pids:
                        logger.debug(f"清理过期Platoon缓存: {len(expired_pids)}个")
            except Exception as e:
                logger.error(f"清理Platoon缓存时出错: {e}")


class BF1PerformanceCache:
    """BF1性能优化缓存管理器

    统一管理所有缓存组件
    """

    def __init__(self):
        self.player_stat_cache = PlayerStatCache()
        self.platoon_cache = PlatoonCache()

    async def get_performance_stats(self) -> dict[str, Any]:
        """获取性能统计信息"""
        player_stats = await self.player_stat_cache.get_cache_stats()
        return {
            "player_stat_cache": player_stats,
            "platoon_cache_size": len(self.platoon_cache._cache),
            "total_cached_items": player_stats["cached_players"]
            + len(self.platoon_cache._cache),
        }

    async def clear_all_cache(self) -> None:
        """清空所有缓存"""
        async with self.player_stat_cache._lock:
            self.player_stat_cache._cache.clear()
        async with self.platoon_cache._lock:
            self.platoon_cache._cache.clear()
        logger.info("所有性能缓存已清空")


# 全局缓存管理器实例
bf1_performance_cache = BF1PerformanceCache()
