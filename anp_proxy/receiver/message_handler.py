"""Message handling for Receiver."""

from collections.abc import Callable
from typing import Any

from ..common.log_base import get_logger
from ..protocol import ANPXDecoder, ANPXEncoder, ANPXMessage, MessageType
from .app_adapter import ASGIAdapter

logger = get_logger(__name__)


class MessageHandler:
    """Handles ANPX messages for the receiver."""

    def __init__(self, asgi_adapter: ASGIAdapter, chunk_size: int = 64 * 1024) -> None:
        """
        Initialize message handler.

        Args:
            asgi_adapter: ASGI application adapter
            chunk_size: Maximum chunk size for responses
        """
        self.asgi_adapter = asgi_adapter
        self.decoder = ANPXDecoder()
        self.encoder = ANPXEncoder(chunk_size)
        self.send_callback: Callable | None = None

        logger.info("Message handler initialized")

    def set_send_callback(self, callback: Callable[[ANPXMessage], None]) -> None:
        """Set callback for sending messages back to gateway."""
        self.send_callback = callback

    async def handle_message(self, message_data: bytes) -> None:
        """
        Handle incoming message from gateway.

        Args:
            message_data: Raw ANPX message bytes
        """
        try:
            logger.debug(
                "Received raw message",
                size=len(message_data),
                first_16_bytes=message_data[:16].hex()
                if len(message_data) >= 16
                else message_data.hex(),
            )

            # Decode message
            message = self.decoder.decode_message(message_data)
            if not message:
                # Chunked message not complete yet
                logger.debug("Chunked message not complete yet")
                return

            logger.debug(
                "Message decoded successfully",
                message_type=message.header.message_type,
                total_length=message.header.total_length,
                tlv_count=len(message.tlv_fields),
            )

            # Handle based on message type
            if message.header.message_type == MessageType.HTTP_REQUEST:
                await self._handle_http_request(message)
            elif message.header.message_type == MessageType.ERROR:
                await self._handle_error_message(message)
            else:
                logger.warning(
                    "Unsupported message type", message_type=message.header.message_type
                )

        except Exception as e:
            logger.error(
                "Failed to handle message",
                error=str(e),
                error_type=type(e).__name__,
                message_size=len(message_data),
                first_32_bytes=message_data[:32].hex()
                if len(message_data) >= 32
                else message_data.hex(),
            )
            # Try to send error response if we can extract request_id
            try:
                partial_message = self._try_decode_partial(message_data)
                if partial_message:
                    request_id = partial_message.get_request_id()
                    if request_id:
                        await self._send_error_response(request_id, str(e))
            except Exception as decode_error:
                logger.error(
                    "Failed to decode partial message for error response",
                    error=str(decode_error),
                )

    def _try_decode_partial(self, message_data: bytes) -> ANPXMessage | None:
        """Try to decode partial message to extract request_id."""
        try:
            from ..protocol import ANPXHeader, TLVField

            if len(message_data) < ANPXHeader.HEADER_SIZE:
                return None

            header = ANPXHeader.decode(message_data[: ANPXHeader.HEADER_SIZE])
            body_data = message_data[ANPXHeader.HEADER_SIZE :]

            # Try to extract just the request_id TLV
            if len(body_data) >= 5:  # Tag(1) + Length(4)
                field, _ = TLVField.decode(body_data, 0)
                if field.tag == 0x01:  # REQUEST_ID
                    message = ANPXMessage(header=header, tlv_fields=[field])
                    return message

        except Exception:
            pass

        return None

    async def _handle_http_request(self, message: ANPXMessage) -> None:
        """Handle HTTP request message."""
        request_id = message.get_request_id()
        if not request_id:
            logger.error("HTTP request missing request_id")
            return

        logger.debug("Handling HTTP request", request_id=request_id)

        try:
            # Process request through ASGI app
            response_message = await self.asgi_adapter.process_request(message)

            # Send response back to gateway
            await self._send_response(response_message)

        except Exception as e:
            logger.error(
                "Failed to process HTTP request", request_id=request_id, error=str(e)
            )
            await self._send_error_response(request_id, str(e))

    async def _handle_error_message(self, message: ANPXMessage) -> None:
        """Handle error message from gateway."""
        request_id = message.get_request_id()
        error_body = message.get_http_body()
        error_text = error_body.decode("utf-8") if error_body else "Unknown error"

        logger.warning(
            "Received error message from gateway",
            request_id=request_id,
            error=error_text,
        )

    async def _send_response(self, message: ANPXMessage) -> None:
        """Send response message to gateway."""
        if not self.send_callback:
            logger.error("No send callback configured")
            return

        try:
            await self.send_callback(message)

        except Exception as e:
            logger.error("Failed to send response", error=str(e))

    async def _send_error_response(self, request_id: str, error_message: str) -> None:
        """Send error response to gateway."""
        try:
            error_message_obj = self.encoder.encode_error(error_message, request_id)
            await self._send_response(error_message_obj)

        except Exception as e:
            logger.error("Failed to send error response", error=str(e))

    def get_stats(self) -> dict[str, Any]:
        """Get message handler statistics."""
        decoder_stats = self.decoder.get_pending_chunks()

        return {"pending_chunks": len(decoder_stats), "chunk_details": decoder_stats}
