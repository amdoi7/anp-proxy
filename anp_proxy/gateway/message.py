"""
消息处理模块 - 单一职责：HTTP 请求与 WebSocket 消息的转换和转发
- HTTP 请求到 WebSocket 消息的转换
- WebSocket 消息到 HTTP 响应的转换
- 消息转发和响应处理
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from websockets.server import WebSocketServerProtocol

try:
    from ..common.log_base import get_logger
    from ..protocol import (
        ANPXEncoder,
        ANPXHeader,
        ANPXMessage,
        HTTPMeta,
        MessageType as ANPXMessageType,
        TLVTag,
    )
except ImportError:
    from anp_proxy.common.log_base import get_logger
    from anp_proxy.protocol import (
        ANPXEncoder,
        ANPXHeader,
        ANPXMessage,
        MessageType as ANPXMessageType,
        TLVTag,
    )

logger = get_logger(__name__)


class MessageType(Enum):
    """消息类型"""

    HTTP_REQUEST = "http_request"
    HTTP_RESPONSE = "http_response"
    WEBSOCKET_MESSAGE = "websocket_message"
    ERROR = "error"


@dataclass
class HttpMessage:
    """HTTP 消息封装"""

    message_id: str
    method: str
    path: str
    headers: dict[str, str]
    query_params: dict[str, str]
    body: bytes
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "message_id": self.message_id,
            "method": self.method,
            "path": self.path,
            "headers": dict(self.headers),
            "query_params": dict(self.query_params),
            "body": self.body.decode("utf-8") if self.body else "",
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_request(cls, request: Request) -> "HttpMessage":
        """从 Starlette Request 创建"""
        return cls(
            message_id=str(uuid.uuid4()),
            method=request.method,
            path=str(request.url.path),
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            body=b"",  # 将在后续填充
            timestamp=time.time(),
        )


@dataclass
class PendingResponse:
    """待处理的响应"""

    message_id: str
    future: asyncio.Future
    created_at: float
    timeout: float

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        return time.time() - self.created_at > self.timeout


class MessageProcessor:
    """消息处理器 - 单一职责：消息格式转换"""

    def __init__(self, chunk_size: int = 64 * 1024):
        self.anpx_encoder = ANPXEncoder(chunk_size)
        logger.info("MessageProcessor initialized with ANPX encoder")

    async def process_http_request(self, request: Request) -> HttpMessage:
        """处理 HTTP 请求"""
        http_message = HttpMessage.from_request(request)

        # 读取请求体
        try:
            body = await request.body()
            http_message.body = body
        except Exception as e:
            logger.warning(f"Failed to read request body: {e}")
            http_message.body = b""

        logger.debug(
            "HTTP request processed",
            message_id=http_message.message_id,
            method=http_message.method,
            path=http_message.path,
            body_size=len(http_message.body),
        )

        return http_message

    def create_anpx_message(self, http_message: HttpMessage) -> ANPXMessage:
        """创建符合 ANPX 协议规范的消息"""
        logger.debug(
            "🔧 [ANPX_CREATE] Creating ANPX message from HTTP request",
            message_id=http_message.message_id,
            method=http_message.method,
            path=http_message.path,
        )

        # 创建 HTTP 元数据 JSON（按协议规范格式）
        http_meta_json = {
            "method": http_message.method,
            "path": http_message.path,
            "headers": http_message.headers,
            "query": http_message.query_params,
        }

        logger.debug(
            "🔧 [ANPX_CREATE] HTTP meta JSON created", http_meta=http_meta_json
        )

        # 创建 ANPX Header（Type 0x01 = HTTP Request）
        anpx_header = ANPXHeader(
            message_type=ANPXMessageType.HTTP_REQUEST,  # 0x01
            flags=0,  # 非分片
            total_length=0,  # 将在后面自动计算
            header_crc=0,  # 将在编码时计算
            body_crc=0,  # 将在编码时计算
        )

        # 创建 ANPX 消息
        anpx_message = ANPXMessage(header=anpx_header)

        # 按协议规范添加必需的 TLV 字段：
        # 0x01: request_id (UUID)
        anpx_message.add_tlv_field(TLVTag.REQUEST_ID, http_message.message_id)

        # 0x02: http_meta (JSON格式)
        import json

        anpx_message.add_tlv_field(TLVTag.HTTP_META, json.dumps(http_meta_json))

        # 0x03: http_body (二进制数据，如果存在)
        if http_message.body:
            anpx_message.add_tlv_field(TLVTag.HTTP_BODY, http_message.body)
            logger.debug(
                "🔧 [ANPX_CREATE] Added HTTP body", body_size=len(http_message.body)
            )

        logger.debug(
            "🔧 [ANPX_CREATE] ANPX message created successfully",
            message_id=http_message.message_id,
            message_type=anpx_message.header.message_type,
            tlv_fields_count=len(anpx_message.tlv_fields),
        )

        return anpx_message

    def process_websocket_response(self, ws_message: dict[str, Any]) -> Response | None:
        """处理 WebSocket 响应消息"""
        try:
            if ws_message.get("type") != MessageType.HTTP_RESPONSE.value:
                logger.warning(
                    "Invalid WebSocket response type", type=ws_message.get("type")
                )
                return None

            data = ws_message.get("data", {})

            # 提取响应信息
            status_code = data.get("status_code", 200)
            headers = data.get("headers", {})
            body = data.get("body", "")

            # 创建响应
            if isinstance(body, dict):
                response = JSONResponse(
                    content=body, status_code=status_code, headers=headers
                )
            else:
                response = Response(
                    content=body, status_code=status_code, headers=headers
                )

            logger.debug(
                "WebSocket response processed",
                message_id=data.get("message_id"),
                status_code=status_code,
            )

            return response

        except Exception as e:
            logger.error(f"Failed to process WebSocket response: {e}")
            return None

    def create_error_response(
        self, message_id: str, error: str, status_code: int = 500
    ) -> Response:
        """创建错误响应"""
        error_data = {
            "error": error,
            "message_id": message_id,
            "timestamp": time.time(),
        }

        return JSONResponse(
            content=error_data,
            status_code=status_code,
            headers={"Content-Type": "application/json"},
        )


class MessageForwarder:
    """消息转发器 - 单一职责：消息在连接间的转发"""

    def __init__(self, response_timeout: float = 30.0):
        self.response_timeout = response_timeout

        # 待处理响应
        self._pending_responses: dict[str, PendingResponse] = {}

        # 清理任务
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

        logger.info("MessageForwarder initialized", timeout=response_timeout)

    async def start(self) -> None:
        """启动转发器"""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_responses())
        logger.info("MessageForwarder started")

    async def stop(self) -> None:
        """停止转发器"""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 取消所有待处理的响应
        for pending in self._pending_responses.values():
            if not pending.future.done():
                pending.future.set_exception(asyncio.CancelledError())

        self._pending_responses.clear()
        logger.info("MessageForwarder stopped")

    async def forward_to_websocket(
        self, websocket: WebSocketServerProtocol, message: ANPXMessage
    ) -> bool:
        """转发 ANPX 消息到 WebSocket"""
        try:
            logger.debug(
                "🔧 [WEBSOCKET_SEND] Encoding ANPX message for transmission",
                request_id=message.get_request_id(),
                message_type=message.header.message_type,
            )

            # 编码 ANPX 消息为二进制数据
            message_data = message.encode()

            logger.debug(
                "🔧 [WEBSOCKET_SEND] Sending binary ANPX message",
                request_id=message.get_request_id(),
                message_size=len(message_data),
            )

            # 发送二进制数据
            await websocket.send(message_data)

            logger.info(
                "🟢 [WEBSOCKET_SEND] ANPX message sent successfully",
                request_id=message.get_request_id(),
                message_size=len(message_data),
            )

            return True

        except Exception as e:
            logger.error(
                "🔴 [WEBSOCKET_SEND] Failed to send ANPX message",
                request_id=message.get_request_id() or "unknown",
                error=str(e),
            )
            return False

    async def wait_for_response(
        self, message_id: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """等待响应消息"""
        timeout = timeout or self.response_timeout

        # 创建 Future
        future = asyncio.Future()
        pending = PendingResponse(
            message_id=message_id,
            future=future,
            created_at=time.time(),
            timeout=timeout,
        )

        self._pending_responses[message_id] = pending

        try:
            # 等待响应或超时
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.debug("Response received", message_id=message_id)
            return response

        except TimeoutError:
            logger.warning("Response timeout", message_id=message_id)
            raise
        except Exception as e:
            logger.error(f"Error waiting for response: {e}")
            raise
        finally:
            # 清理
            self._pending_responses.pop(message_id, None)

    def handle_websocket_response(self, ws_message: dict[str, Any]) -> bool:
        """处理 WebSocket 响应"""
        try:
            data = ws_message.get("data", {})
            message_id = data.get("message_id")

            if not message_id:
                logger.warning("WebSocket response missing message_id")
                return False

            # 查找待处理的响应
            pending = self._pending_responses.get(message_id)
            if not pending:
                logger.debug("No pending response found", message_id=message_id)
                return False

            # 设置响应结果
            if not pending.future.done():
                pending.future.set_result(ws_message)
                logger.debug("Response delivered", message_id=message_id)
                return True

            return False

        except Exception as e:
            logger.error(f"Error handling WebSocket response: {e}")
            return False

    async def _cleanup_expired_responses(self) -> None:
        """清理过期的响应"""
        while self._running:
            try:
                expired_ids = []

                for message_id, pending in self._pending_responses.items():
                    if pending.is_expired:
                        expired_ids.append(message_id)
                        if not pending.future.done():
                            pending.future.set_exception(
                                TimeoutError(
                                    f"Response timeout for message {message_id}"
                                )
                            )

                # 移除过期的响应
                for message_id in expired_ids:
                    self._pending_responses.pop(message_id, None)

                if expired_ids:
                    logger.info(f"Cleaned up {len(expired_ids)} expired responses")

                await asyncio.sleep(5.0)  # 每5秒清理一次

            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                await asyncio.sleep(1.0)

    def get_stats(self) -> dict[str, Any]:
        """获取转发器统计"""
        return {
            "pending_responses": len(self._pending_responses),
            "response_timeout": self.response_timeout,
            "running": self._running,
        }


class MessageHandler:
    """消息处理器 - 整合消息处理和转发功能"""

    def __init__(self, response_timeout: float = 30.0, chunk_size: int = 64 * 1024):
        self.processor = MessageProcessor(chunk_size)
        self.forwarder = MessageForwarder(response_timeout)

        logger.info("MessageHandler initialized with ANPX support")

    async def start(self) -> None:
        """启动消息处理器"""
        await self.forwarder.start()

    async def stop(self) -> None:
        """停止消息处理器"""
        await self.forwarder.stop()

    async def handle_http_request(
        self, request: Request, target_websocket: WebSocketServerProtocol
    ) -> Response:
        """处理 HTTP 请求并转发到 WebSocket"""
        try:
            # 1. 处理 HTTP 请求
            http_message = await self.processor.process_http_request(request)

            # 2. 创建 ANPX 消息
            anpx_message = self.processor.create_anpx_message(http_message)

            # 3. 转发 ANPX 消息到 WebSocket
            forward_success = await self.forwarder.forward_to_websocket(
                target_websocket, anpx_message
            )
            if not forward_success:
                return self.processor.create_error_response(
                    http_message.message_id, "Failed to forward request", 502
                )

            # 4. 等待响应
            try:
                ws_response = await self.forwarder.wait_for_response(
                    http_message.message_id
                )

                # 5. 处理响应
                response = self.processor.process_websocket_response(ws_response)
                if response:
                    return response
                else:
                    return self.processor.create_error_response(
                        http_message.message_id, "Invalid response format", 502
                    )

            except TimeoutError:
                return self.processor.create_error_response(
                    http_message.message_id, "Response timeout", 504
                )

        except Exception as e:
            logger.error(f"Error handling HTTP request: {e}")
            return self.processor.create_error_response(
                "", f"Internal server error: {str(e)}", 500
            )

    def handle_websocket_message(self, ws_message: dict[str, Any]) -> bool:
        """处理来自 WebSocket 的消息"""
        return self.forwarder.handle_websocket_response(ws_message)

    async def handle_anpx_response(self, anpx_message: ANPXMessage) -> bool:
        """处理 ANPX 响应消息"""
        try:
            request_id = anpx_message.get_request_id()
            logger.info(
                "🔄 [ANPX_RESPONSE] Processing ANPX response message",
                request_id=request_id,
                message_type=anpx_message.header.message_type,
            )

            # 将 ANPX 响应转换为内部格式并转发给等待的 HTTP 客户端
            if anpx_message.header.message_type == ANPXMessageType.HTTP_RESPONSE:
                # 获取响应元数据（从TLV字段）
                resp_meta_json = anpx_message.get_tlv_value_str(TLVTag.RESP_META)
                http_body = anpx_message.get_tlv_field(TLVTag.HTTP_BODY)

                # 解析响应元数据
                resp_meta = {}
                if resp_meta_json:
                    import json

                    resp_meta = json.loads(resp_meta_json)

                # 构建响应数据格式（兼容现有的等待机制）
                response_data = {
                    "type": MessageType.HTTP_RESPONSE.value,
                    "data": {
                        "message_id": request_id,
                        "status_code": resp_meta.get("status", 200),
                        "headers": resp_meta.get("headers", {}),
                        "body": http_body.value.decode("utf-8")
                        if http_body and http_body.value
                        else "",
                    },
                }

                logger.debug(
                    "🔍 [ANPX_RESPONSE] Converted ANPX response to internal format",
                    request_id=request_id,
                    status_code=response_data["data"]["status_code"],
                )

                return self.forwarder.handle_websocket_response(response_data)
            else:
                logger.warning(
                    "🔍 [ANPX_RESPONSE] Unexpected ANPX message type",
                    message_type=anpx_message.header.message_type,
                )
                return False

        except Exception as e:
            logger.error(
                "🔴 [ANPX_RESPONSE] Error handling ANPX response",
                request_id=anpx_message.get_request_id() or "unknown",
                error=str(e),
            )
            return False

    async def handle_raw_message(self, connection, message: bytes) -> None:
        """处理原始二进制消息（ANPX 协议）"""
        try:
            logger.debug(
                "🔍 [RAW_MSG] Processing raw binary message",
                connection_id=connection.connection_id,
                message_size=len(message),
            )

            # 使用 ANPX 解码器解析消息
            from ..protocol import ANPXDecoder

            decoder = ANPXDecoder()
            anpx_message = decoder.decode_message(message)

            if anpx_message:
                logger.info(
                    "🔄 [RAW_MSG] Successfully decoded ANPX message",
                    connection_id=connection.connection_id,
                    message_type=anpx_message.header.message_type,
                    request_id=anpx_message.get_request_id(),
                )

                # 处理 ANPX 响应消息
                await self.handle_anpx_response(anpx_message)
            else:
                logger.warning(
                    "🔍 [RAW_MSG] Failed to decode ANPX message",
                    connection_id=connection.connection_id,
                )

        except Exception as e:
            logger.error(
                "🔴 [RAW_MSG] Error handling raw message",
                connection_id=connection.connection_id,
                error=str(e),
            )

    def get_handler_stats(self) -> dict[str, Any]:
        """获取处理器统计"""
        return {
            "forwarder": self.forwarder.get_stats(),
        }
