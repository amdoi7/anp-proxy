"""
WebSocket 处理器 - 单一职责：处理 WebSocket 连接和消息
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect

from ..common.did_wba import DidAuthResult
from ..common.log_base import get_logger

if TYPE_CHECKING:
    from .server import ConnectInfo

logger = get_logger(__name__)


class WebSocketHandler:
    """WebSocket 处理器"""

    def __init__(self, gateway):
        self.gateway = gateway
        # 添加ANPX解码器
        from ..protocol import ANPXDecoder

        self.decoder = ANPXDecoder()
        logger.info("WebSocketHandler initialized")

    async def handle_connection(
        self,
        websocket: WebSocket,
        connection_id: str | None = None,
        auth_header: str = "",
    ) -> None:
        """处理 FastAPI WebSocket 连接"""
        if not connection_id:
            connection_id = str(uuid.uuid4())

        connection = None
        try:
            # 先接受 WebSocket 连接，允许未认证的连接
            await websocket.accept()
            logger.info(f"WebSocket connection accepted: {connection_id}")

            # 尝试进行DID-WBA认证验证（可选）
            did_result = await self._verify_did_headers(websocket)

            if did_result.success:
                # 如果认证成功，立即注册连接
                connection = await self.gateway.register_and_add_connection(
                    connection_id, websocket, did_result.did
                )

                if connection:
                    paths = self.gateway.get_connection_paths(connection_id)
                    logger.info(
                        f"Connection authenticated via DID-WBA: {connection_id} with paths: {paths}"
                    )
                else:
                    logger.warning(f"Service registration failed: {connection_id}")
                    # 即使注册失败，也保持连接，允许后续重试
                    connection = ConnectInfo(
                        connection_id=connection_id,
                        websocket=websocket,
                        authenticated=False,
                        did=None,
                    )
                    self.gateway.connections[connection_id] = connection
            else:
                # 认证失败，但保持连接，允许后续通过消息进行认证
                logger.info(
                    f"WebSocket connection established without authentication: {connection_id}"
                )
                # 创建一个未认证的连接对象
                connection = ConnectInfo(
                    connection_id=connection_id,
                    websocket=websocket,
                    authenticated=False,
                    did=None,
                )
                # 直接添加到连接管理器但不注册服务路径
                self.gateway.connections[connection_id] = connection

            # 处理消息（包括后续的认证消息）
            await self._handle_messages(connection)

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {connection_id}")
        except Exception as e:
            logger.error(f"WebSocket error for {connection_id}: {e}")
            # 只有在连接未关闭时才尝试关闭
            if connection and not connection.is_websocket_closed:
                try:
                    await websocket.close(code=5000, reason="Internal server error")
                except Exception as close_error:
                    logger.debug(
                        f"Error closing websocket during error handling: {close_error}"
                    )
        finally:
            # 确保连接被清理
            if connection_id:
                await self._cleanup_connection(connection_id)

    async def _verify_did_headers(self, websocket):
        """Verify DID-WBA headers during WS handshake."""
        try:
            # Extract domain from websocket headers
            domain = None

            # Try to get host from headers
            host_header = websocket.headers.get("host", "")
            if host_header:
                domain = host_header.split(":")[0]
            else:
                # Fallback to origin header
                origin_header = websocket.headers.get("origin", "")
                if origin_header:
                    from urllib.parse import urlparse

                    try:
                        parsed = urlparse(origin_header)
                        domain = parsed.hostname
                    except Exception:
                        pass

                if not domain:
                    domain = "127.0.0.1"

            logger.info(f"Verifying DID-WBA headers for domain: {domain}")

            # Get authorization header
            auth_header = websocket.headers.get("authorization", "")
            if not auth_header:
                logger.info("No authorization header provided")
                return DidAuthResult(success=False, error="No authorization header")

            # Verify with DID-WBA verifier
            result = await self.gateway.did_verifier.verify_auth_header(
                auth_header, domain
            )
            if result and "did" in result:
                logger.info("DID-WBA authenticated", did=result["did"])
                return DidAuthResult(success=True, did=result["did"])
            else:
                logger.warning("DID-WBA auth failed", error="Verification failed")
                return DidAuthResult(success=False, error="Verification failed")

        except Exception as e:
            logger.error("DID-WBA verification error", error=str(e))
            return DidAuthResult(success=False, error=str(e))

    def _extract_did_from_auth_header(self, auth_header: str) -> str | None:
        """从 Authorization header 中提取 DID token"""
        try:
            if not auth_header:
                return None

            # DID-WBA auth header format: "DIDWba did=did:wba:... signature=... timestamp=..."
            if auth_header.startswith("DIDWba"):
                # 解析 did= 部分
                parts = auth_header.split(" ")
                for part in parts:
                    if part.startswith("did="):
                        did_value = part[4:]  # 移除 "did=" 前缀
                        # 移除可能的引号
                        if did_value.startswith('"') and did_value.endswith('"'):
                            did_value = did_value[1:-1]
                        logger.debug(f"Extracted DID from header: {did_value}")
                        return did_value

            # 如果是 Bearer token 格式
            elif auth_header.startswith("Bearer "):
                token = auth_header[7:]  # 移除 "Bearer " 前缀
                if token.startswith("did:"):
                    return token

            logger.warning(f"Unsupported auth header format: {auth_header[:50]}...")
            return None

        except Exception as e:
            logger.error(f"Error extracting DID from auth header: {e}")
            return None

    async def _handle_messages(self, connection: ConnectInfo) -> None:
        """处理 FastAPI WebSocket 消息"""
        websocket = connection.websocket
        connection_id = connection.connection_id

        if not websocket:
            logger.error(f"No websocket for connection {connection_id}")
            return

        while True:
            try:
                # 检查连接状态，如果已断开或正在清理则退出
                from .server import ConnectionState  # 运行时导入避免循环导入

                if (
                    connection.state == ConnectionState.DISCONNECTED
                    or connection.is_cleaning_up
                ):
                    logger.debug(
                        f"Connection {connection_id} is disconnected or cleaning up, stopping message handling"
                    )
                    break

                # 接收消息（可能是文本JSON或二进制ANPX）
                raw_message = await websocket.receive()
                connection.update_websocket_activity()  # 使用WebSocket专用的活动更新

                # 检查消息类型
                if raw_message["type"] == "websocket.receive":
                    if "text" in raw_message:
                        # JSON控制消息
                        try:
                            ws_message = json.loads(raw_message["text"])
                            await self._process_message(connection, ws_message)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON message from {connection_id}")
                    elif "bytes" in raw_message:
                        # ANPX二进制消息
                        anpx_data = raw_message["bytes"]
                        logger.info(
                            f"Received ANPX message from {connection_id}, size: {len(anpx_data)}"
                        )

                        # 使用解码器解码ANPX消息
                        try:
                            message = self.decoder.decode_message(anpx_data)
                            if message:
                                await self._handle_anpx_message(connection, message)
                            else:
                                logger.warning(
                                    f"Failed to decode ANPX message from {connection_id}"
                                )
                        except Exception as decode_error:
                            logger.error(
                                f"ANPX decode error from {connection_id}: {decode_error}"
                            )
                    else:
                        logger.warning(f"Unknown message format from {connection_id}")

            except WebSocketDisconnect:
                logger.debug(f"WebSocket disconnected for {connection_id}")
                connection.mark_websocket_closed()
                break
            except Exception as e:
                # 检查是否为预期的断开连接错误
                error_msg = str(e).lower()
                expected_errors = [
                    "disconnect message has been received",
                    "websocket connection is closed",
                    "unexpected asgi message",
                    "websocket.close",
                ]

                if any(
                    expected_error in error_msg for expected_error in expected_errors
                ):
                    logger.debug(f"Expected disconnect for {connection_id}: {e}")
                    connection.mark_websocket_closed()
                else:
                    logger.error(f"Error processing message from {connection_id}: {e}")
                break

    async def _process_message(
        self, connection: ConnectInfo, message: dict[str, Any]
    ) -> None:
        """处理消息"""
        # 确保每次处理消息时都更新WebSocket活动时间
        connection.update_websocket_activity()

        message_type = message.get("type")
        connection_id = connection.connection_id

        logger.debug(f"Processing message from {connection_id}: type={message_type}")

        if message_type == "authentication":
            await self._handle_authentication(connection, message)
        elif message_type == "heartbeat":
            # 处理客户端心跳消息
            logger.info(f"🟢 [HEARTBEAT] Received heartbeat from {connection_id}")
            # 活动时间已在函数开始时更新，这里只需要日志记录
        elif message_type == "connection_ready":
            # octopus 客户端发送的连接就绪通知
            if connection.authenticated:
                logger.debug(
                    f"Connection ready notification received from authenticated connection: {connection_id}"
                )
            else:
                logger.info(
                    f"Connection ready notification from unauthenticated connection: {connection_id} (waiting for authentication)"
                )
        elif message_type == "http_response":
            self.gateway.response_handler.handle_websocket_response(message)
        else:
            logger.debug(f"Unknown message type from {connection_id}: {message_type}")

    async def _handle_authentication(
        self, connection: ConnectInfo, message: dict[str, Any]
    ) -> None:
        """处理认证消息"""
        data = message.get("data", {})
        did_token = data.get("did_token")

        if not did_token:
            logger.warning(
                f"Authentication failed - missing DID token: {connection.connection_id}"
            )
            return

        # 验证 DID token 格式
        if not did_token.startswith("did:"):
            logger.warning(
                f"Authentication message with non-DID token: {did_token[:50]}..."
            )
            return

        # 如果连接已经认证，忽略重复认证
        if connection.authenticated:
            logger.debug(
                f"Connection already authenticated: {connection.connection_id}"
            )
            return

        # 尝试注册连接
        registered_connection = await self.gateway.register_and_add_connection(
            connection.connection_id, connection.websocket, did_token
        )

        if registered_connection:
            # 更新连接状态
            connection.authenticated = True
            connection.did = did_token

            # 获取该连接的服务路径
            service_paths = self.gateway.get_connection_paths(connection.connection_id)

            logger.info(
                f"Connection authenticated via message: {connection.connection_id} with paths: {service_paths}"
            )
        else:
            logger.warning(f"Service registration failed: {connection.connection_id}")

    async def _handle_anpx_message(self, connection: ConnectInfo, message) -> None:
        """处理ANPX协议消息"""
        from ..protocol import MessageType

        request_id = message.get_request_id()
        logger.info(
            f"Processing ANPX message: type={message.header.message_type}, request_id={request_id}"
        )

        if message.header.message_type == MessageType.HTTP_RESPONSE:
            # HTTP响应消息 - 转发给响应处理器
            logger.info(f"Received HTTP response for request {request_id}")
            await self.gateway.response_handler.handle_response(request_id, message)

        elif message.header.message_type == MessageType.ERROR:
            # 错误消息
            logger.info(f"Received error response for request {request_id}")
            await self.gateway.response_handler.handle_error(request_id, message)

        else:
            logger.warning(
                f"Unexpected ANPX message type: {message.header.message_type}"
            )

    async def _cleanup_connection(self, connection_id: str) -> None:
        """清理连接"""
        try:
            # 使用网关的原子性清理方法，它会处理所有清理步骤
            await self.gateway.remove_connection(connection_id)
            logger.debug(f"Connection cleanup completed for: {connection_id}")
        except Exception as e:
            logger.error(f"Error during connection cleanup for {connection_id}: {e}")
