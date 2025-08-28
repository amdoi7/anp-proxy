# anp-proxy 代码架构设计

## 依赖管理

使用 UV 管理项目依赖和虚拟环境。

## 总体架构设计

```
anp-proxy/
├── anp_proxy/                    # 主代码包
│   ├── __init__.py              # 包初始化
│   ├── app.py                   # 主应用类 (ANPProxyApp)
│   ├── cli.py                   # CLI 入口点
│   ├── gateway/                 # Gateway 组件（公网侧）
│   │   ├── __init__.py          # 网关模块初始化
│   │   ├── connection.py        # 连接信息管理（新增）
│   │   ├── server.py            # 网关服务器
│   │   ├── websocket_handler.py # WebSocket 处理
│   │   ├── middleware.py        # 中间件
│   │   ├── routing.py           # 路由处理
│   │   ├── request_mapper.py    # 请求映射
│   │   └── response_handler.py  # 响应处理
│   ├── protocol/                # ANPX 协议 SDK
│   │   ├── message.py           # 消息结构
│   │   ├── encoder.py           # 消息编码
│   │   ├── decoder.py           # 消息解码
│   │   ├── chunking.py          # 分片处理
│   │   ├── crc.py               # CRC 校验
│   │   └── exceptions.py        # 协议异常
│   ├── common/                  # 公共工具和配置
│   │   ├── config.py            # 配置管理
│   │   ├── log_base.py          # 日志系统
│   │   ├── db_base.py           # 数据库基类
│   │   ├── utils.py             # 工具函数
│   │   ├── constants.py         # 常量定义
│   │   └── did_wba.py           # DID-WBA 认证
│   ├── anp_sdk/                 # ANP SDK 组件
│   │   ├── anp_auth/            # DID-WBA 认证
│   │   └── anp_crawler/         # ANP 爬虫
│   └── examples/                # 使用示例
├── tests/                       # 测试代码
├── docs/                        # 文档
├── config.toml                  # 配置文件
├── manage.sh                    # 服务管理脚本
├── pyproject.toml              # 项目配置文件
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

**最近修复的问题**：

- **total_length 字段赋值错误**：移除了 `ANPXHeader` 的 `frozen=True` 装饰器，允许字段修改
- **协议兼容性**：保持 `slots=True` 以获得内存优化，同时支持字段更新

### 2. Gateway Layer（网关层）- `anp_proxy/gateway/`

**职责**：处理外部 HTTP 请求，通过 WSS 转发到内网

```
gateway/
├── __init__.py           # 模块初始化
├── connection.py         # 连接信息管理
├── server.py            # HTTP 服务器（FastAPI/Starlette）
├── websocket_handler.py # WSS 连接处理
├── request_mapper.py    # HTTP 请求映射和包装
├── response_handler.py  # 响应处理和还原
├── routing.py           # 路由处理
└── middleware.py        # 认证、限流等中间件
```

**核心功能**：

- 接收外部 HTTP 请求
- 将 HTTP 请求打包为 ANPX 协议
- 通过 WSS 发送给 Receiver
- 接收响应并还原为 HTTP 响应
- 连接管理和负载均衡

### 3. Common Layer（公共层）- `anp_proxy/common/`

**职责**：共享工具和配置

```
common/
├── __init__.py
├── config.py           # 配置管理
├── log_base.py         # 日志配置
├── utils.py            # 通用工具
├── constants.py        # 常量定义
├── db_base.py          # 数据库基类
└── did_wba.py          # DID-WBA 认证
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
| ----------------- | ------------------- | -------------------- |
| HTTP 服务         | FastAPI + Uvicorn   | 高性能异步、自动文档 |
| WSS 客户端/服务端 | `websockets`        | 纯异步、支持 TLS     |
| 序列化            | `pydantic`          | 类型安全、易扩展     |
| 本地调用          | `httpx.AsyncClient` | 零拷贝 ASGI 调用     |
| 日志              | `loguru`            | 简洁易用、结构化日志 |
| 配置管理          | `pydantic-settings` | 环境变量 + 配置文件  |

## 启动模式

### CLI 启动（推荐）

```bash
# 使用 UV 启动
uv run anp-proxy

# 直接运行 CLI 模块
python anp_proxy/cli.py
```

### 编程方式启动

```python
from anp_proxy.app import ANPProxyApp, run_app
from anp_proxy.common.config import ANPProxyConfig

# 方式 1：使用配置文件
config = ANPProxyConfig.from_file("config.toml")
app = ANPProxyApp(config)
await app.run()

# 方式 2：使用 run_app 函数
await run_app(config)
```

### 管理脚本启动（生产环境）

```bash
# 后台启动
./manage.sh start

# 服务管理
./manage.sh status|stop|restart|logs
```

### 当前架构说明

- **Gateway 模式**：当前代码专注于网关功能
- **Receiver 功能**：已迁移到独立的 octopus 项目
- **配置驱动**：所有配置通过 `config.toml` 文件管理

## 设计原则

### 1. 模块解耦

- Protocol 层完全独立，可作为 SDK 单独发布
- Gateway/Receiver 依赖 Protocol，但相互独立
- Common 层提供基础设施，被其他层使用
- **新增**：Connection 模块独立管理连接状态，避免循环导入

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
