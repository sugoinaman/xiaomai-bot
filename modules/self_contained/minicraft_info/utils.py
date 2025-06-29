from loguru import logger
from mcstatus import BedrockServer, JavaServer


async def detect_server_type(server_address: str) -> str:
    """自动检测服务器类型"""
    try:
        # 先尝试Java版
        server = await JavaServer.async_lookup(server_address)
        await server.async_status()
        return "java"
    except Exception:
        try:
            server = BedrockServer.lookup(server_address)
            await server.async_status()
            return "bedrock"
        except Exception:
            # 默认返回Java版
            return "java"


async def get_minecraft_server_info(server_host: str) -> dict | str:
    """
    获取Minecraft服务器信息
    :param server_host: 服务器地址
    :return: 成功返回服务器信息-dict, 失败返回错误信息-str
    """
    try:
        server = await JavaServer.async_lookup(server_host)
        status = await server.async_status()
    except ConnectionRefusedError as e:
        logger.exception(f"[MC查询]无法连接到服务器 {server_host}: {e}")
        return f"无法连接到服务器「{server_host}」，请检查服务器地址和端口是否正确"
    except TimeoutError as e:
        logger.exception(f"[MC查询]连接服务器 {server_host} 超时: {e}")
        return f"连接服务器「{server_host}」超时，请稍后重试"
    except ConnectionResetError as e:
        logger.exception(f"[MC查询]连接服务器 {server_host} 被重置: {e}")
        return f"连接服务器「{server_host}」被重置，服务器可能暂时不可用"
    except OSError as e:
        logger.exception(f"[MC查询]查询服务器 {server_host} 出现网络错误: {e}")
        return f"查询服务器「{server_host}」时出现网络错误，请检查网络连接"
    except Exception as e:
        logger.exception(f"[MC查询]查询服务器 {server_host} 出现未知错误: {e}")
        return f"查询服务器「{server_host}」时出现未知错误，请稍后重试"

    try:
        query_result = await JavaServer.async_query(server)
        players = query_result.players.names
    except Exception as e:
        logger.exception(f"[MC查询]查询服务器 {server_host} 玩家列表失败: {e}")
        players = []  # 玩家列表查询失败时使用空列表，不影响基本信息显示

    return {
        "server_host": server_host,
        "description": "".join(
            [item for item in status.motd.parsed if isinstance(item, str)]
        ),
        "version": status.version.name,
        "protocol": status.version.protocol,
        "online_players": status.players.online,
        "max_players": status.players.max,
        "ping": round(status.latency, 2),
        "players": [item.name for item in status.players.sample]
        if status.players.sample
        else players,
        "favicon": status.icon,
    }
