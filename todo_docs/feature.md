  1. WebSocket认证传输机制

  认证组件架构

  - DID-WBA认证：使用 anp_proxy/common/did_wba.py:52-95 中的
  DidWbaVerifierAdapter
  - 认证流程：基于DID (Decentralized Identity) Web-Based
  Authentication

  Gateway端认证验证 (websocket_manager.py:116-128)

  # 在WebSocket握手时验证DID headers
  if self.config.auth.did_wba_enabled:
      did_result = await self._verify_did_headers(websocket)
      if not did_result.success:
          await websocket.close(code=4003, reason="DID
  authentication failed")
          return
      conn_info.authenticated = True
      conn_info.user_id = did_result.did or "did-user"

  Receiver端认证发起 (client.py:147-174)

  # 构建认证headers
  extra_headers: dict[str, str] =
  build_auth_headers(self.config.auth, self.config.gateway_url)
  # 在WebSocket连接时附加认证headers
  self.websocket = await websockets.connect(
      self.config.gateway_url,
      extra_headers=extra_headers,
      **connect_kwargs,
  )

  认证流程细节

  1. Header构建 (did_wba.py:97-118): Receiver使用
  DIDWbaAuthHeader 构建Authorization header
  2. Header验证 (did_wba.py:74-94):
  Gateway通过SDK验证器验证Authorization header
  3. 域名验证: 从WebSocket host提取域名进行DID验证
  4. 权限检查: 支持allowed_dids白名单机制

  2. Gateway处理外部HTTP请求模块

  请求处理流程 (server.py:111-175)

  核心组件

  - RequestMapper (request_mapper.py): HTTP→ANPX协议转换
  - ResponseHandler (response_handler.py): ANPX→HTTP响应重建
  - WebSocketManager: 与receiver通信管理

  处理流程

  1. 请求映射 (request_mapper.py:26-72):
  # 将HTTP请求转换为ANPX消息
  request_id, anpx_messages = await
  self.request_mapper.map_request(request)
    - 提取HTTP方法、路径、headers、query、body
    - 生成唯一request_id
    - 支持大文件分块传输
  2. 请求转发 (server.py:130-137):
  # 通过WebSocket发送到receiver
  for message in anpx_messages:
      if await self.websocket_manager.send_request(request_id,
  message):
          success = True
  3. 响应等待 (server.py:148-167):
  # 创建pending request并等待响应
  pending_request = await
  self.response_handler.create_pending_request(request_id,
  timeout)
  response = await pending_request.wait()

  负载均衡机制 (websocket_manager.py:250-261)

  # 简单轮询算法选择可用连接
  available_connections = [conn for conn in
  self.connections.values()
                          if conn.authenticated and
  len(conn.pending_requests) < 100]
  conn_info = min(available_connections, key=lambda c:
  len(c.pending_requests))

  错误处理

  - 超时处理: 504 Gateway Timeout (server.py:161-167)
  - 无可用receiver: 503 Service Unavailable (server.py:138-145)
  - 连接断开: 自动清理pending requests
  (websocket_manager.py:311-328)

  请求追踪

  - 请求路由表: request_routing: dict[str, str] = {}
  映射request_id到connection_id
  - 待处理请求: 每个连接维护 pending_requests: set[str]

  中间件支持 (server.py:64-68)

  - SecurityHeadersMiddleware: 安全headers
  - CORSMiddleware: 跨域支持
  - RateLimitMiddleware: 限流保护
  - LoggingMiddleware: 请求日志

  这个架构实现了Gateway作为HTTP代理，通过WebSocket安全地转发请求
  到receiver，支持认证、负载均衡和错误处理。


  1. WSS认证传输机制验证

  DID-WBA认证流程验证（test_did_wba_comprehensive.py:88-110）

  认证配置验证：
  # Gateway端配置 - 启用DID-WBA验证
  auth=AuthConfig(
      enabled=True,
      did_wba_enabled=True,
      allowed_dids=[DID],  # 白名单验证
      # JWT密钥用于令牌验证
      jwt_private_key_path=Path("docs/jwt_rs256/private.pem"),
      jwt_public_key_path=Path("docs/jwt_rs256/public.pem"),
  )

  # Receiver端配置 - 提供DID凭证
  auth=AuthConfig(
      enabled=True,
      did_wba_enabled=True,
      did=DID,                              # DID标识符
      did_document_path=DID_DOC_PATH,       # DID文档路径
      private_key_path=DID_PRIVATE_KEY_PATH, # 私钥路径
  )

  认证验证流程：
  # 验证认证连接建立（test_did_wba_comprehensive.py:97-107）
  for _ in range(8):
      await asyncio.sleep(0.5)
      stats =
  self.gateway.websocket_manager.get_connection_stats()
      if stats.get("authenticated_connections", 0) > 0:
          authenticated = True
          break

  if not authenticated:
      raise RuntimeError("DID-WBA handshake failed")

  未授权连接拒绝验证（test_did_wba_comprehensive.py:152-186）

  # 创建无DID凭证的receiver
  unauthorized_config = ReceiverConfig(
      gateway_url=f"ws://127.0.0.1:{self.wss_port}",
      auth=AuthConfig(enabled=False),  # 无DID认证
  )
  # 验证连接被正确拒绝

  2. HTTP请求处理模块验证

  协议转换验证（test_protocol.py:84-113）

  ANPX协议编码测试：
  # HTTP请求→ANPX消息转换验证
  encoder = ANPXEncoder()
  messages = encoder.encode_http_request(
      method="GET",
      path="/test",
      headers={"host": "example.com"},
      query={"q": "value"},
      body=b"test body",
      request_id="test-123"
  )
  # 验证编码结果的正确性
  assert message.get_request_id() == "test-123"
  assert http_meta.method == "GET"

  分块传输验证（test_protocol.py:139-170）：
  # 大文件分块处理验证
  encoder = ANPXEncoder(chunk_size=1024)
  large_body = b"x" * 5000  # 5KB数据
  messages = encoder.encode_http_request(...)

  # 验证分块处理
  assert len(messages) > 1  # 应该产生多个消息块
  for message in messages:
      assert message.is_chunked()  # 每个消息都标记为分块

  HTTP请求类型覆盖验证（test_gateway_receiver.py:99-187）

  支持的HTTP方法验证：
  # GET请求测试
  response = await
  client.get(f"http://127.0.0.1:{self.gateway_port}/")
  assert response.status_code == 200

  # POST请求测试
  test_data = {"name": "ANP Proxy", "version": "1.0"}
  response = await client.post(
      f"http://127.0.0.1:{self.gateway_port}/echo",
      json=test_data
  )

  # 路径参数和查询参数测试
  response = await
  client.get(f".../echo/test-item?q=test-query")
  assert data["item"] == "test-item"
  assert data["query"] == "test-query"

  负载均衡和超时处理验证（test_gateway_receiver.py:190-212）

  # 慢响应超时处理验证
  async with httpx.AsyncClient(timeout=15.0) as client:
      start_time = time.time()
      response = await client.get(".../slow-response")
      elapsed = time.time() - start_time
      assert elapsed >= 2.0  # 验证实际等待时间

  3. 实际部署验证

  组件启动顺序验证（test_gateway_receiver.py:61-73）

  # 正确的启动顺序
  self.gateway_task = asyncio.create_task(self.gateway.start())
  await asyncio.sleep(1)  # 等待Gateway启动

  self.receiver_task =
  asyncio.create_task(self.receiver.start())
  await asyncio.sleep(2)  # 等待Receiver连接

  ASGI应用集成验证（simple_fastapi_app.py）

  测试应用提供了完整的API端点覆盖：
  - 根路径 /
  - 健康检查 /health
  - 路径参数 /echo/{item}
  - POST数据处理 /echo
  - 大响应 /large-response
  - 慢响应 /slow-response

  这些测试验证了之前分析的所有关键组件都能正确工作：WebSocket认
  证、HTTP-ANPX协议转换、请求路由、响应处理以及错误处理机制。




 1. 连接建立与认证阶段

 Receiver 使用 DID 凭证通过 WebSocket 连接到 Gateway
 Gateway 验证 DID-WBA 认证，建立 {did: conn_id} 映射
 Gateway 查询数据库获取该 DID 对应的 service_url 列表
 构建 {service_url: conn_id} 路由表，实现服务发现

 2. 请求转发阶段

 Client 发送 HTTP 请求到 Gateway
 Gateway 从请求 Host 头提取 service_url
 通过路由表查找对应的 conn_id
 将 HTTP 请求编码为 ANPX 协议消息
 通过 WebSocket 隧道发送到目标 Receiver

 3. 请求处理阶段

 Receiver 解码 ANPX 消息，还原 HTTP 请求
 通过 ASGI 适配器调用本地应用
 将应用响应编码为 ANPX 消息
 通过 WebSocket 返回给 Gateway（Gateway await 等待处理）
 Gateway 还原 HTTP 响应并返回给 Client


  1. Chat Agent
    - DID: did:wba:api.agent.com:chat
    - Service URLs:
        - api.agent.com/v1/chat
      - api.agent.com/v1/conversation
  2. Completion Agent
    - DID: did:wba:api.agent.com:completion
    - Service URLs:
        - api.agent.com/v1/completion
      - api.agent.com/v1/generate
  3. Multi Agent (Gateway)
    - DID: did:wba:api.agent.com:gateway
    - Service URLs:
        - api.agent.com/v1/chat
      - api.agent.com/v1/completion
      - api.agent.com/v1/admin
