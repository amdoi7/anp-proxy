"""HTTP响应处理器 - 合并了原MessageForwarder功能"""

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import WebSocket
from starlette.responses import JSONResponse, Response

from ..common.log_base import get_logger
from ..protocol import (
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


class ResponseHandler:
    """响应处理器 - 整合消息转发和响应处理功能"""

    def __init__(self, response_timeout: float = 30.0):
        self.response_timeout = response_timeout

        # 待处理响应
        self._pending_responses: dict[str, PendingResponse] = {}

        # 清理任务
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

        logger.info("ResponseHandler initialized", timeout=response_timeout)

    async def start(self) -> None:
        """启动响应处理器"""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_responses())
        logger.info("ResponseHandler started")

    async def stop(self) -> None:
        """停止响应处理器"""
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
        logger.info("ResponseHandler stopped")

    async def forward_to_websocket(
        self, websocket: WebSocket, message: ANPXMessage
    ) -> bool:
        """转发ANPX消息到WebSocket"""
        try:
            logger.debug(
                "🔧 [WEBSOCKET_SEND] Encoding ANPX message for transmission",
                request_id=message.get_request_id(),
                message_type=message.header.message_type,
            )

            # 编码ANPX消息为二进制数据 - 添加详细调试
            logger.info(
                f"🔧 [WEBSOCKET_SEND] Before encoding: message type={type(message)}"
            )
            logger.info(
                f"🔧 [WEBSOCKET_SEND] TLV fields count: {len(message.tlv_fields)}"
            )

            # 检查TLV字段内容
            for i, tlv in enumerate(message.tlv_fields):
                logger.info(
                    f"🔧 [WEBSOCKET_SEND] TLV {i}: tag={tlv.tag}, value_type={type(tlv.value)}, value_len={len(tlv.value)}"
                )

            try:
                message_data = message.encode()
                logger.info(
                    f"🔧 [WEBSOCKET_SEND] Encoding successful, result type: {type(message_data)}"
                )
            except Exception as encode_error:
                logger.error(f"🔴 [WEBSOCKET_SEND] Encoding failed: {encode_error}")
                return False

            # KISS: 确保数据类型正确
            if not isinstance(message_data, bytes):
                logger.error(
                    f"🔴 [WEBSOCKET_SEND] Invalid message data type: {type(message_data)}"
                )
                return False

            logger.debug(
                "🔧 [WEBSOCKET_SEND] Sending binary ANPX message",
                request_id=message.get_request_id(),
                message_size=len(message_data),
            )

            # 发送二进制数据 - 添加类型和方法检查
            logger.info(f"🔧 [WEBSOCKET_SEND] WebSocket type: {type(websocket)}")
            logger.info(
                f"🔧 [WEBSOCKET_SEND] WebSocket send method: {type(websocket.send)}"
            )

            # 尝试不同的发送方式
            if hasattr(websocket, "send_bytes"):
                await websocket.send_bytes(message_data)
            elif hasattr(websocket, "send"):
                await websocket.send(message_data)
            else:
                raise ValueError(f"WebSocket {type(websocket)} has no send method")

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

        # 创建Future
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
        """处理WebSocket响应"""
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

    async def handle_anpx_response(self, anpx_message: ANPXMessage) -> bool:
        """处理ANPX响应消息"""
        try:
            request_id = anpx_message.get_request_id()
            logger.info(
                "🔄 [ANPX_RESPONSE] Processing ANPX response message",
                request_id=request_id,
                message_type=anpx_message.header.message_type,
            )

            # 将ANPX响应转换为内部格式并转发给等待的HTTP客户端
            if anpx_message.header.message_type == ANPXMessageType.HTTP_RESPONSE:
                # 获取响应元数据（从TLV字段）
                resp_meta_json = anpx_message.get_tlv_value_str(TLVTag.RESP_META)
                http_body = anpx_message.get_tlv_field(TLVTag.HTTP_BODY)

                # 解析响应元数据
                resp_meta = {}
                if resp_meta_json:
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

                return self.handle_websocket_response(response_data)
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

    def process_websocket_response(self, ws_message: dict[str, Any]) -> Response | None:
        """处理WebSocket响应消息"""
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

    async def handle_raw_message(self, connection, message: bytes) -> None:
        """处理原始二进制消息（ANPX协议）"""
        try:
            logger.debug(
                "🔍 [RAW_MSG] Processing raw binary message",
                connection_id=connection.connection_id,
                message_size=len(message),
            )

            # 使用ANPX解码器解析消息
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

                # 处理ANPX响应消息
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

    async def handle_response(self, request_id: str, message: ANPXMessage) -> None:
        """处理ANPX响应消息"""
        try:
            # 查找待处理的响应
            pending = self._pending_responses.get(request_id)
            if not pending:
                logger.warning(
                    "Received response for unknown request", request_id=request_id
                )
                return

            # 转换ANPX消息为HTTP响应
            response = await self._convert_anpx_to_http_response(message)

            # 设置响应结果
            if not pending.future.done():
                pending.future.set_result(response)
                logger.info("ANPX response delivered", request_id=request_id)

            # 清理
            del self._pending_responses[request_id]

        except Exception as e:
            logger.error(
                "Failed to handle ANPX response", request_id=request_id, error=str(e)
            )

    async def handle_error(self, request_id: str, message: ANPXMessage) -> None:
        """处理ANPX错误消息"""
        try:
            pending = self._pending_responses.get(request_id)
            if not pending:
                logger.warning(
                    "Received error for unknown request", request_id=request_id
                )
                return

            # 创建错误响应
            error_body = message.get_http_body()
            error_text = error_body.decode("utf-8") if error_body else "Unknown error"

            error_response = Response(
                content=error_text,
                status_code=500,
                headers={"content-type": "text/plain"},
            )

            if not pending.future.done():
                pending.future.set_result(error_response)
                logger.info(
                    "ANPX error delivered", request_id=request_id, error=error_text
                )

            # 清理
            del self._pending_responses[request_id]

        except Exception as e:
            logger.error(
                "Failed to handle ANPX error", request_id=request_id, error=str(e)
            )

    async def _convert_anpx_to_http_response(self, message: ANPXMessage) -> Response:
        """将ANPX响应消息转换为HTTP响应"""
        # 提取响应元数据
        resp_meta = message.get_resp_meta()
        if not resp_meta:
            raise ValueError("Response message missing metadata")

        # 提取响应体
        body = message.get_http_body()

        # 创建HTTP响应
        return Response(
            content=body, status_code=resp_meta.status, headers=resp_meta.headers
        )

    def get_handler_stats(self) -> dict[str, Any]:
        """获取处理器统计"""
        return {
            "pending_responses": len(self._pending_responses),
            "response_timeout": self.response_timeout,
            "running": self._running,
        }
