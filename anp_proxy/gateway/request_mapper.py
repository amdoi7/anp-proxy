"""HTTP请求映射和包装器 - 合并了原MessageProcessor功能"""

import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from starlette.requests import Request

from ..common.log_base import get_logger
from ..protocol import (
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
    """HTTP消息封装"""

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
        """从Starlette Request创建"""
        return cls(
            message_id=str(uuid.uuid4()),
            method=request.method,
            path=str(request.url.path),
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            body=b"",  # 将在后续填充
            timestamp=time.time(),
        )


class RequestMapper:
    """请求映射器 - 整合了HTTP请求处理和ANPX消息转换"""

    def __init__(self, chunk_size: int = 64 * 1024) -> None:
        """
        初始化请求映射器

        Args:
            chunk_size: 大请求的最大块大小
        """
        self.anpx_encoder = ANPXEncoder(chunk_size)
        logger.info("RequestMapper initialized with ANPX encoder")

    async def process_http_request(self, request: Request) -> HttpMessage:
        """处理HTTP请求"""
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
        """创建符合ANPX协议规范的消息"""
        logger.debug(
            "🔧 [ANPX_CREATE] Creating ANPX message from HTTP request",
            message_id=http_message.message_id,
            method=http_message.method,
            path=http_message.path,
        )

        # 创建HTTP元数据JSON（按协议规范格式）
        http_meta_json = {
            "method": http_message.method,
            "path": http_message.path,
            "headers": http_message.headers,
            "query": http_message.query_params,
        }

        logger.debug(
            "🔧 [ANPX_CREATE] HTTP meta JSON created", http_meta=http_meta_json
        )

        # 创建ANPX Header（Type 0x01 = HTTP Request）
        anpx_header = ANPXHeader(
            message_type=ANPXMessageType.HTTP_REQUEST,  # 0x01
            flags=0,  # 非分片
            total_length=0,  # 将在后面自动计算
            header_crc=0,  # 将在编码时计算
            body_crc=0,  # 将在编码时计算
        )

        # 创建ANPX消息
        anpx_message = ANPXMessage(header=anpx_header)

        # 按协议规范添加必需的TLV字段：
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

    async def map_request(self, request: Request) -> tuple[str, list[ANPXMessage]]:
        """
        将HTTP请求映射为ANPX消息

        Args:
            request: Starlette HTTP请求

        Returns:
            元组：(request_id, ANPX消息列表)
        """
        try:
            # 使用新的处理流程
            http_message = await self.process_http_request(request)
            anpx_message = self.create_anpx_message(http_message)

            logger.debug(
                "HTTP request mapped to ANPX",
                request_id=http_message.message_id,
                message_count=1,
            )

            return http_message.message_id, [anpx_message]

        except Exception as e:
            logger.error("Failed to map HTTP request", error=str(e))
            raise

    def create_error_response_message(
        self,
        request_id: str,
        status: int,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> list[ANPXMessage]:
        """
        创建错误响应消息

        Args:
            request_id: 要响应的请求ID
            status: HTTP状态码
            message: 错误消息
            headers: 可选的响应头

        Returns:
            包含单个错误响应消息的列表
        """
        try:
            response_headers = headers or {}
            response_headers["content-type"] = "application/json"

            error_body = {"error": message, "status": status, "request_id": request_id}

            import json

            body_bytes = json.dumps(error_body).encode("utf-8")

            messages = self.anpx_encoder.encode_http_response(
                status=status,
                reason=self._get_status_reason(status),
                headers=response_headers,
                body=body_bytes,
                request_id=request_id,
            )

            return messages

        except Exception as e:
            logger.error("Failed to create error response", error=str(e))
            # 返回基本错误消息
            basic_message = self.anpx_encoder.encode_error(
                f"Internal error: {e}", request_id
            )
            return [basic_message]

    def _get_status_reason(self, status: int) -> str:
        """获取HTTP状态原因短语"""
        status_reasons = {
            200: "OK",
            201: "Created",
            202: "Accepted",
            204: "No Content",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            408: "Request Timeout",
            413: "Payload Too Large",
            429: "Too Many Requests",
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
            504: "Gateway Timeout",
        }

        return status_reasons.get(status, "Unknown")

    # 保留原有的提取方法以保持功能完整性
    def _extract_path(self, request: Request) -> str:
        """提取带查询字符串的请求路径"""
        path = request.url.path
        if request.url.fragment:
            path += f"#{request.url.fragment}"
        return path

    def _extract_headers(self, request: Request) -> dict[str, str]:
        """提取HTTP头"""
        headers = {}

        for name, value in request.headers.items():
            # 为一致性将头名转换为小写
            headers[name.lower()] = value

        # 如果可用，添加客户端信息
        if request.client:
            headers["x-forwarded-for"] = request.client.host
            if hasattr(request.client, "port"):
                headers["x-forwarded-port"] = str(request.client.port)

        return headers

    def _extract_query(self, request: Request) -> dict[str, str]:
        """提取查询参数"""
        query = {}

        for key, value in request.query_params.items():
            query[key] = value

        return query

    async def _extract_body(self, request: Request) -> bytes | None:
        """提取请求体"""
        try:
            # 检查请求是否有体
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) == 0:
                return None

            if request.method in ["GET", "HEAD", "DELETE"]:
                # 这些方法通常没有体
                return None

            # 读取体
            body = await request.body()
            return body if body else None

        except Exception as e:
            logger.warning("Failed to extract request body", error=str(e))
            return None
