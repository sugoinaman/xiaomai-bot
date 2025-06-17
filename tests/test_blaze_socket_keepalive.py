"""
pytest测试文件：BlazeSocket keepalive机制修复验证

测试覆盖范围：
1. _is_writer_valid()方法的各种场景
2. _cleanup_connection()方法
3. keepalive()方法在连接异常时的行为
4. SSL连接状态检查逻辑

使用mock对象进行完全隔离的单元测试，不依赖外部服务。

运行方式：
- 运行所有测试：uv run pytest tests/test_blaze_socket_keepalive.py
- 运行详细输出：uv run pytest tests/test_blaze_socket_keepalive.py -v
- 运行特定测试：uv run pytest tests/test_blaze_socket_keepalive.py::TestBlazeSocketKeepalive::test_is_writer_valid_with_none_writer
"""

import os
import sys
from unittest.mock import AsyncMock, Mock, patch

import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.bf1.blaze.BlazeSocket import BlazeSocket


class TestBlazeSocketKeepalive:
    """BlazeSocket keepalive机制修复的pytest测试类"""

    @pytest.fixture
    def blaze_socket(self):
        """创建BlazeSocket实例的fixture"""
        return BlazeSocket("test.com", 443)

    def test_is_writer_valid_with_none_writer(self, blaze_socket):
        """测试writer为None时的有效性检查"""
        blaze_socket.writer = None

        result = blaze_socket._is_writer_valid()
        assert not result, "writer为None时应返回False"

    def test_is_writer_valid_with_closing_writer(self, blaze_socket):
        """测试writer正在关闭时的有效性检查"""
        mock_writer = Mock()
        mock_writer.is_closing.return_value = True
        blaze_socket.writer = mock_writer

        result = blaze_socket._is_writer_valid()
        assert not result, "writer正在关闭时应返回False"

    def test_is_writer_valid_with_closing_transport(self, blaze_socket):
        """测试transport正在关闭时的有效性检查"""
        mock_transport = Mock()
        mock_transport.is_closing.return_value = True

        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = mock_transport
        blaze_socket.writer = mock_writer

        result = blaze_socket._is_writer_valid()
        assert not result, "transport正在关闭时应返回False"

    def test_is_writer_valid_with_none_transport(self, blaze_socket):
        """测试transport为None时的有效性检查"""
        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = None
        blaze_socket.writer = mock_writer

        result = blaze_socket._is_writer_valid()
        assert not result, "transport为None时应返回False"

    def test_is_writer_valid_with_none_ssl_protocol(self, blaze_socket):
        """测试SSL协议层为None时的有效性检查"""
        mock_transport = Mock()
        mock_transport.is_closing.return_value = False
        mock_transport._ssl_protocol = None

        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = mock_transport
        blaze_socket.writer = mock_writer

        result = blaze_socket._is_writer_valid()
        assert not result, "SSL协议层为None时应返回False"

    def test_is_writer_valid_with_valid_writer(self, blaze_socket):
        """测试有效writer的检查"""
        mock_transport = Mock()
        mock_transport.is_closing.return_value = False
        mock_transport._ssl_protocol = Mock()  # 非None的SSL协议层

        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = mock_transport
        blaze_socket.writer = mock_writer

        result = blaze_socket._is_writer_valid()
        assert result, "有效writer应返回True"

    def test_is_writer_valid_without_ssl_protocol_attribute(self, blaze_socket):
        """测试transport没有_ssl_protocol属性时的检查（非SSL连接）"""
        mock_transport = Mock()
        mock_transport.is_closing.return_value = False
        # 删除_ssl_protocol属性，模拟非SSL连接
        if hasattr(mock_transport, "_ssl_protocol"):
            delattr(mock_transport, "_ssl_protocol")

        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = mock_transport
        blaze_socket.writer = mock_writer

        result = blaze_socket._is_writer_valid()
        assert result, "没有SSL协议层属性的有效writer应返回True"

    def test_cleanup_connection(self, blaze_socket):
        """测试连接清理方法"""
        blaze_socket.writer = Mock()
        blaze_socket.reader = Mock()

        blaze_socket._cleanup_connection()

        assert blaze_socket.writer is None, "清理连接后writer应为None"
        assert blaze_socket.reader is None, "清理连接后reader应为None"

    @pytest.mark.asyncio
    async def test_keepalive_with_invalid_writer(self, blaze_socket):
        """测试keepalive在writer无效时的行为"""
        blaze_socket.connect = True
        blaze_socket.writer = None  # 无效的writer

        # 使用patch来模拟asyncio.sleep，避免实际等待
        with patch("asyncio.sleep") as mock_sleep:
            # 设置sleep被调用后立即返回
            mock_sleep.return_value = AsyncMock()

            # 运行keepalive，它应该检测到writer无效并退出
            await blaze_socket.keepalive()

            # 验证结果
            assert not blaze_socket.connect, (
                "keepalive在writer无效时应设置connect=False"
            )
            assert blaze_socket.writer is None, "writer应保持为None"
            assert blaze_socket.reader is None, "reader应被清理为None"
            mock_sleep.assert_called_once_with(60)

    @pytest.mark.asyncio
    async def test_keepalive_with_write_exception(self, blaze_socket):
        """测试keepalive在写入异常时的行为"""
        blaze_socket.connect = True

        # 模拟有效的writer但写入时抛出异常
        mock_transport = Mock()
        mock_transport.is_closing.return_value = False
        mock_transport._ssl_protocol = Mock()

        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = mock_transport
        mock_writer.write.side_effect = Exception("模拟写入异常")
        mock_writer.drain = AsyncMock()

        blaze_socket.writer = mock_writer

        with patch("asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = AsyncMock()

            # 运行keepalive，它应该捕获异常并清理连接
            await blaze_socket.keepalive()

            # 验证结果
            assert not blaze_socket.connect, "keepalive在写入异常时应设置connect=False"
            assert blaze_socket.writer is None, "writer应被清理为None"
            assert blaze_socket.reader is None, "reader应被清理为None"

    @pytest.mark.asyncio
    async def test_keepalive_normal_operation(self, blaze_socket):
        """测试keepalive正常运行时的行为"""
        blaze_socket.connect = True

        # 模拟有效的writer
        mock_transport = Mock()
        mock_transport.is_closing.return_value = False
        mock_transport._ssl_protocol = Mock()

        mock_writer = Mock()
        mock_writer.is_closing.return_value = False
        mock_writer.transport = mock_transport
        mock_writer.drain = AsyncMock()

        blaze_socket.writer = mock_writer

        # 控制循环次数
        call_count = 0

        async def mock_sleep(duration):  # noqa: F401
            nonlocal call_count
            call_count += 1
            if call_count >= 2:  # 运行两次后停止
                blaze_socket.connect = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await blaze_socket.keepalive()

            # 验证writer.write被调用
            assert mock_writer.write.call_count >= 1, "keepalive应该调用writer.write"
            assert mock_writer.drain.call_count >= 1, "keepalive应该调用writer.drain"


if __name__ == "__main__":
    # 支持直接运行测试文件
    pytest.main([__file__, "-v"])
