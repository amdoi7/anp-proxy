
# anp-proxy 代码架构设计

## 依赖管理

使用 UV 管理项目依赖和虚拟环境。

## 总体架构设计

```
anp-proxy/
├── anp_proxy/                    # 主代码包
│   ├── __init__.py              # 包初始化
│   ├── anp_proxy.py             # 主程序入口（CLI + 应用逻辑）
│   ├── gateway/                 # Gateway 组件（公网侧）
│   ├── receiver/                # Receiver 组件（内网侧）
│   ├── protocol/                # ANPX 协议 SDK（可独立封装）
│   ├── common/                  # 公共工具和配置
│   └── examples/                # 使用示例
├── tests/                       # 测试代码
├── docs/                        # 文档
├── config.toml                  # 配置文件
├── pyproject.toml              # UV 配置文件
└── README.md
```

## 核心模块设计

### 1. Protocol Layer（协议层）- `anp_proxy/protocol/`

**职责**：实现 ANPX 二进制协议的编解码逻辑，未来可单独封装为 SDK

```
protocol/
├── __init__.py
├── message.py          # 消息结构定义（Header + TLV）
├── encoder.py          # 消息编码器
├── decoder.py          # 消息解码器
├── chunking.py         # 分片处理逻辑
├── crc.py              # CRC 校验工具
└── exceptions.py       # 协议相关异常
```

**核心功能**：
- 24B 固定头部处理
- TLV 格式编解码
- 分片机制实现
- CRC 校验计算
- 消息完整性验证

### 2. Gateway Layer（网关层）- `anp_proxy/gateway/`

**职责**：处理外部 HTTP 请求，通过 WSS 转发到内网

```
gateway/
├── __init__.py
├── server.py           # HTTP 服务器（FastAPI/Starlette）
├── websocket_manager.py # WSS 连接管理
├── request_mapper.py   # HTTP 请求映射和包装
├── response_handler.py # 响应处理和还原
└── middleware.py       # 认证、限流等中间件
```

**核心功能**：
- 接收外部 HTTP 请求
- 将 HTTP 请求打包为 ANPX 协议
- 通过 WSS 发送给 Receiver
- 接收响应并还原为 HTTP 响应
- 连接管理和负载均衡

### 3. Receiver Layer（接收层）- `anp_proxy/receiver/`

**职责**：维持 WSS 连接，调用本地应用

```
receiver/
├── __init__.py
├── client.py           # WSS 客户端连接管理
├── app_adapter.py      # 本地应用适配器（ASGI）
├── message_handler.py  # 消息处理逻辑
└── reconnect.py        # 断线重连机制
```

**核心功能**：
- 维持与 Gateway 的 WSS 长连接
- 解析 ANPX 协议消息
- 调用本地 ASGI 应用（使用 `httpx.AsyncClient`）
- 将应用响应打包回传
- 自动重连和容错处理

### 4. Common Layer（公共层）- `anp_proxy/common/`

**职责**：共享工具和配置

```
common/
├── __init__.py
├── config.py           # 配置管理
├── auth.py             # 双向认证
├── log_base.py          # 日志配置
├── utils.py            # 通用工具
└── constants.py        # 常量定义
```

## 数据流设计

### 正向流程（外部 → 内网）
```
HTTP Request → Gateway → Protocol Encoder → WSS → Protocol Decoder → Receiver → Local App
```

### 反向流程（内网 → 外部）
```
Local App → Receiver → Protocol Encoder → WSS → Protocol Decoder → Gateway → HTTP Response
```

## 技术选型

| 组件              | 技术选择            | 理由                 |
|-------------------|---------------------|----------------------|
| HTTP 服务         | FastAPI + Uvicorn   | 高性能异步、自动文档 |
| WSS 客户端/服务端 | `websockets`        | 纯异步、支持 TLS     |
| 序列化            | `pydantic`          | 类型安全、易扩展     |
| 本地调用          | `httpx.AsyncClient` | 零拷贝 ASGI 调用     |
| 日志              | `structlog`         | 结构化日志、调试友好 |
| 配置管理          | `pydantic-settings` | 环境变量 + 配置文件  |

## 部署模式

### Gateway 模式（公网部署）
```python
from anp_proxy.gateway import GatewayServer
server = GatewayServer(config)
server.run()
```

### Receiver 模式（内网部署）

**注意：Receiver 功能已迁移到独立的 octopus 项目中。**

如需使用 Receiver 功能，请参考 octopus 项目的文档和示例。

### 一体化模式（开发/测试）
```python
from anp_proxy import ANPProxy
proxy = ANPProxy(config)
proxy.run_both()  # 同时启动 Gateway 和 Receiver
```

## 设计原则

### 1. 模块解耦
- Protocol 层完全独立，可作为 SDK 单独发布
- Gateway/Receiver 依赖 Protocol，但相互独立
- Common 层提供基础设施，被其他层使用

### 2. 异步架构
- 全异步 I/O 设计（asyncio）
- 使用 asyncio.Queue 处理消息队列
- asyncio.Semaphore 实现流控

### 3. 扩展性设计
- TLV 格式支持协议扩展
- 插件化的中间件系统
- 多种本地应用适配器

### 4. 容错机制
- CRC 校验保证数据完整性
- 自动重连机制
- 优雅降级和错误处理

## 架构优势

**✅ 框架无关**：Protocol 层完全独立，Gateway/Receiver 可适配任意框架
**✅ 全量转发**：完整封装 HTTP 五元组，支持分片传输
**✅ 安全可靠**：WSS + 双向认证 + CRC 校验 + 断线重连
**✅ 高性能异步**：纯异步架构，单连接多并发，无磁盘落地
