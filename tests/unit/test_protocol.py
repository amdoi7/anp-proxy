"""Unit tests for ANPX protocol."""

from anp_proxy.protocol import (
    ANPXDecoder,
    ANPXEncoder,
    ANPXHeader,
    ANPXMessage,
    HTTPMeta,
    MessageType,
    ResponseMeta,
    TLVTag,
)


class TestANPXMessage:
    """Test ANPX message structure."""

    def test_create_http_request_message(self):
        """Test creating HTTP request message."""
        header = ANPXHeader(message_type=MessageType.HTTP_REQUEST)
        message = ANPXMessage(header=header)

        # Add request components
        message.add_tlv_field(TLVTag.REQUEST_ID, "test-request-123")

        http_meta = HTTPMeta(
            method="GET",
            path="/test",
            headers={"host": "example.com"},
            query={"q": "test"}
        )
        message.add_tlv_field(TLVTag.HTTP_META, http_meta.to_json())
        message.add_tlv_field(TLVTag.HTTP_BODY, b"test body")

        # Verify fields
        assert message.get_request_id() == "test-request-123"
        assert message.get_http_meta().method == "GET"
        assert message.get_http_meta().path == "/test"
        assert message.get_http_body() == b"test body"

    def test_create_http_response_message(self):
        """Test creating HTTP response message."""
        header = ANPXHeader(message_type=MessageType.HTTP_RESPONSE)
        message = ANPXMessage(header=header)

        # Add response components
        message.add_tlv_field(TLVTag.REQUEST_ID, "test-request-123")

        resp_meta = ResponseMeta(
            status=200,
            reason="OK",
            headers={"content-type": "application/json"}
        )
        message.add_tlv_field(TLVTag.RESP_META, resp_meta.to_json())
        message.add_tlv_field(TLVTag.HTTP_BODY, b'{"result": "success"}')

        # Verify fields
        assert message.get_request_id() == "test-request-123"
        assert message.get_resp_meta().status == 200
        assert message.get_resp_meta().reason == "OK"
        assert message.get_http_body() == b'{"result": "success"}'

    def test_chunked_message(self):
        """Test chunked message flags."""
        header = ANPXHeader(message_type=MessageType.HTTP_REQUEST)
        message = ANPXMessage(header=header)

        # Set as chunked
        header.set_chunked(True)
        assert header.is_chunked
        assert message.is_chunked()

        # Add chunk information
        message.add_tlv_field(TLVTag.CHUNK_IDX, 0)
        message.add_tlv_field(TLVTag.CHUNK_TOT, 3)
        message.add_tlv_field(TLVTag.FINAL_CHUNK, 0)

        chunk_idx, chunk_tot, is_final = message.get_chunk_info()
        assert chunk_idx == 0
        assert chunk_tot == 3
        assert not is_final


class TestANPXEncoder:
    """Test ANPX encoder."""

    def test_encode_simple_request(self):
        """Test encoding simple HTTP request."""
        encoder = ANPXEncoder()

        messages = encoder.encode_http_request(
            method="GET",
            path="/test",
            headers={"host": "example.com"},
            query={"q": "value"},
            body=b"test body",
            request_id="test-123"
        )

        assert len(messages) == 1
        message = messages[0]

        assert message.header.message_type == MessageType.HTTP_REQUEST
        assert not message.is_chunked()
        assert message.get_request_id() == "test-123"

        http_meta = message.get_http_meta()
        assert http_meta.method == "GET"
        assert http_meta.path == "/test"
        assert http_meta.headers["host"] == "example.com"
        assert http_meta.query["q"] == "value"
        assert message.get_http_body() == b"test body"

    def test_encode_simple_response(self):
        """Test encoding simple HTTP response."""
        encoder = ANPXEncoder()

        messages = encoder.encode_http_response(
            status=200,
            reason="OK",
            headers={"content-type": "application/json"},
            body=b'{"success": true}',
            request_id="test-123"
        )

        assert len(messages) == 1
        message = messages[0]

        assert message.header.message_type == MessageType.HTTP_RESPONSE
        assert not message.is_chunked()
        assert message.get_request_id() == "test-123"

        resp_meta = message.get_resp_meta()
        assert resp_meta.status == 200
        assert resp_meta.reason == "OK"
        assert resp_meta.headers["content-type"] == "application/json"
        assert message.get_http_body() == b'{"success": true}'

    def test_encode_large_request_chunking(self):
        """Test chunking large requests."""
        encoder = ANPXEncoder(chunk_size=1024)  # Small chunk size for testing

        # Create large body
        large_body = b"x" * 5000  # 5KB body

        messages = encoder.encode_http_request(
            method="POST",
            path="/upload",
            headers={"content-type": "application/octet-stream"},
            body=large_body,
            request_id="test-large"
        )

        # Should be chunked
        assert len(messages) > 1

        # All messages should be chunked
        for message in messages:
            assert message.is_chunked()
            assert message.get_request_id() == "test-large"

        # First message should have metadata
        first_message = messages[0]
        assert first_message.get_http_meta() is not None

        # Last message should be marked as final
        last_message = messages[-1]
        _, _, is_final = last_message.get_chunk_info()
        assert is_final

    def test_encode_error(self):
        """Test encoding error message."""
        encoder = ANPXEncoder()

        message = encoder.encode_error("Test error message", "test-123")

        assert message.header.message_type == MessageType.ERROR
        assert message.get_request_id() == "test-123"

        error_body = message.get_http_body()
        assert error_body == b"Test error message"


class TestANPXDecoder:
    """Test ANPX decoder."""

    def test_decode_simple_message(self):
        """Test decoding simple message."""
        # Create and encode a message
        encoder = ANPXEncoder()
        messages = encoder.encode_http_request(
            method="GET",
            path="/test",
            headers={"host": "example.com"},
            body=b"test",
            request_id="test-123"
        )

        original_message = messages[0]
        encoded_data = original_message.encode()

        # Decode the message
        decoder = ANPXDecoder()
        decoded_message = decoder.decode_message(encoded_data)

        assert decoded_message is not None
        assert decoded_message.header.message_type == MessageType.HTTP_REQUEST
        assert decoded_message.get_request_id() == "test-123"

        http_meta = decoded_message.get_http_meta()
        assert http_meta.method == "GET"
        assert http_meta.path == "/test"
        assert decoded_message.get_http_body() == b"test"

    def test_decode_chunked_message(self):
        """Test decoding chunked message."""
        encoder = ANPXEncoder(chunk_size=100)  # Very small chunks
        decoder = ANPXDecoder()

        # Create large request
        large_body = b"data" * 100  # 400 bytes
        chunk_messages = encoder.encode_http_request(
            method="POST",
            path="/upload",
            body=large_body,
            request_id="chunked-test"
        )

        assert len(chunk_messages) > 1

        # Decode chunks one by one
        complete_message = None
        for chunk_message in chunk_messages:
            chunk_data = chunk_message.encode()
            result = decoder.decode_message(chunk_data)

            if result is not None:
                complete_message = result
                break

        # Should have complete message after all chunks
        assert complete_message is not None
        assert complete_message.get_request_id() == "chunked-test"
        assert complete_message.get_http_body() == large_body
        assert not complete_message.is_chunked()  # Assembled message is not chunked


class TestHTTPMeta:
    """Test HTTP metadata handling."""

    def test_http_meta_json_roundtrip(self):
        """Test HTTP metadata JSON serialization."""
        original = HTTPMeta(
            method="POST",
            path="/api/test",
            headers={"content-type": "application/json", "authorization": "Bearer token"},
            query={"param1": "value1", "param2": "value2"}
        )

        json_str = original.to_json()
        restored = HTTPMeta.from_json(json_str)

        assert restored.method == original.method
        assert restored.path == original.path
        assert restored.headers == original.headers
        assert restored.query == original.query


class TestResponseMeta:
    """Test response metadata handling."""

    def test_response_meta_json_roundtrip(self):
        """Test response metadata JSON serialization."""
        original = ResponseMeta(
            status=201,
            reason="Created",
            headers={"content-type": "application/json", "location": "/api/resource/123"}
        )

        json_str = original.to_json()
        restored = ResponseMeta.from_json(json_str)

        assert restored.status == original.status
        assert restored.reason == original.reason
        assert restored.headers == original.headers
