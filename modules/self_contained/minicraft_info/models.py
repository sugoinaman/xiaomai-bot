from datetime import datetime
from pathlib import Path

from sqlalchemy import BIGINT, JSON, Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import Mapped

from core.orm import orm

# 确保数据目录存在
DATA_DIR = Path("data/minecraft_info")
DATA_DIR.mkdir(parents=True, exist_ok=True)


# MC服务器信息表
class McServer(orm.Base):
    """MC服务器信息表"""

    __tablename__ = "mc_server"

    id: Mapped[int] = Column(Integer, primary_key=True)
    server_name: Mapped[str] = Column(String, nullable=False)
    server_address: Mapped[str] = Column(String, nullable=False, unique=True)
    server_type: Mapped[str] = Column(String, default="java")  # java/bedrock
    websocket_url: Mapped[str | None] = Column(String)
    websocket_headers: Mapped[dict | None] = Column(JSON)
    is_active: Mapped[bool] = Column(Boolean, default=True)
    created_time: Mapped[datetime] = Column(DateTime, default=datetime.now)
    updated_time: Mapped[datetime] = Column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


# 群组服务器绑定表
class McGroupBind(orm.Base):
    """群组服务器绑定表"""

    __tablename__ = "mc_group_bind"

    id: Mapped[int] = Column(Integer, primary_key=True)
    group_id: Mapped[int] = Column(BIGINT, nullable=False)
    server_id: Mapped[int] = Column(Integer, nullable=False)
    chat_sync_enabled: Mapped[bool] = Column(Boolean, default=False)
    created_time: Mapped[datetime] = Column(DateTime, default=datetime.now)
