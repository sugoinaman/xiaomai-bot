"""
BF1性能优化缓存管理器
专门针对-pl指令的性能瓶颈进行优化
"""

import asyncio
import time
from typing import Any, TypedDict

from loguru import logger


class PlayerStatCacheEntry(TypedDict):
    """玩家战绩缓存条目类型定义"""

    data: dict[str, Any]
    timestamp: float


class PlatoonCacheEntry(TypedDict):
    """Platoon缓存条目类型定义"""

    data: dict[str, Any]
    timestamp: float


class PlayerStatCache:
    """玩家战绩缓存

    缓存玩家的detailedStatsByPersonaId结果，减少重复API调用
    """

    def __init__(self):
        self._cache: dict[int, PlayerStatCacheEntry] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task = None

        # 缓存配置
        self.ttl = 1800  # 30分钟缓存，战绩数据变化较慢
        self.max_cache_size = 1000  # 最大缓存1000个玩家战绩

    def _ensure_cleanup_task(self):
        """确保清理任务已启动

        采用延迟初始化模式的原因：
        1. 避免在模块导入时创建asyncio任务（此时可能没有事件循环）
        2. 确保只在实际使用缓存时才启动清理任务
        3. 兼容不同的应用启动模式和测试环境
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._cleanup_expired_cache())
                logger.debug("玩家战绩缓存清理任务已启动")
            except RuntimeError:
                # 如果没有运行的事件循环，稍后再启动
                # 这种情况通常发生在模块导入阶段或测试环境中
                logger.debug("事件循环未就绪，缓存清理任务将延迟启动")

    async def get_player_stat(self, pid: int) -> dict | None:
        """获取玩家战绩缓存"""
        self._ensure_cleanup_task()  # 确保清理任务已启动

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
            # 检查缓存大小限制
            if len(self._cache) >= self.max_cache_size:
                # 删除最旧的缓存项
                oldest_pid = min(
                    self._cache.keys(), key=lambda k: self._cache[k]["timestamp"]
                )
                del self._cache[oldest_pid]
                logger.debug(f"缓存已满，删除最旧项: {oldest_pid}")

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
        self._cache: dict[int, PlatoonCacheEntry] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task = None

        # 缓存配置
        self.ttl = 600  # 10分钟缓存，战队信息变化很慢
        self.max_cache_size = 500  # 最大缓存500个战队信息

    def _ensure_cleanup_task(self):
        """确保清理任务已启动

        采用延迟初始化模式，与PlayerStatCache保持一致
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._cleanup_expired_cache())
                logger.debug("Platoon缓存清理任务已启动")
            except RuntimeError:
                # 如果没有运行的事件循环，稍后再启动
                logger.debug("事件循环未就绪，Platoon缓存清理任务将延迟启动")

    async def get_platoon_info(self, pid: int) -> dict | None:
        """获取Platoon信息缓存"""
        self._ensure_cleanup_task()  # 确保清理任务已启动

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
            # 检查缓存大小限制
            if len(self._cache) >= self.max_cache_size:
                # 删除最旧的缓存项
                oldest_pid = min(
                    self._cache.keys(), key=lambda k: self._cache[k]["timestamp"]
                )
                del self._cache[oldest_pid]
                logger.debug(f"Platoon缓存已满，删除最旧项: {oldest_pid}")

            self._cache[pid] = {"data": data, "timestamp": time.time()}
            logger.debug(f"Platoon信息已缓存: {pid}")

    async def batch_get_platoon_info(self, pids: list[int]) -> dict[int, dict | None]:
        """批量获取Platoon信息"""
        result: dict[int, dict | None] = {}
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
