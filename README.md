# ANP Proxy

Agent Network Proxy (ANP) - 高性能 HTTP over WebSocket 隧道，用于私有网络服务的安全对外暴露。

## 特性

- 🚀 **高性能异步架构** - 基于 asyncio 的纯异步实现
- 🔒 **安全可靠** - WSS (TLS) + 双向认证 + CRC 校验
- 📦 **二进制协议** - 自定义 ANPX 协议，支持大文件分片传输
- 🔧 **框架无关** - 支持任意 ASGI 应用 (FastAPI, Django, Flask 等)
- 🔄 **自动重连** - 断线自动重连，指数退避策略
- 📊 **监控友好** - 详细的日志和统计信息
- ⚙️ **易于配置** - TOML 配置文件，无需命令行参数
- 🛠️ **模块化设计** - 清晰的模块边界，避免循环依赖

## 最近更新 (2025-08-28)

### 🔧 问题修复

- **修复 total_length 字段赋值错误** - 解决了 `ANPXHeader` 不可变字段导致的协议编码问题
- **解决循环导入问题** - 重构了 `ConnectInfo` 模块，消除了 `server.py` 和 `routing.py` 之间的循环依赖
- **优化代码结构** - 创建独立的 `connection.py` 模块，提高代码可维护性

### 📦 架构改进

- 模块职责更加清晰，遵循单一职责原则
- 导入关系更加合理，避免循环依赖
- 遵循 KISS 原则，保持代码简洁

## 架构概览

```
┌────────────┐            WSS (TLS)           ┌──────────────┐
│  Client    │ ─HTTP→ ┌──────────────┐ ─────→ │  Receiver &  │
│  外部调用者 │        │  Gateway     │        │  Internal App│
└────────────┘ ←HTTP─ └──────────────┘ ←───── │  (FastAPI …) │
                 ↑                ↓           └──────────────┘
           Request 包装       Response 包装
```

## 快速开始

### 安装

```bash
# 克隆项目
git clone <repository-url>
cd anp-proxy

# 安装依赖
uv sync
```

### 基本使用

#### 启动服务

```bash
# 方式1：直接使用 uv
uv run anp-proxy

# 方式2：使用管理脚本
./manage.sh start

# 方式3：使用管理脚本的交互式菜单
./manage.sh
```

服务将自动：

- 以 `gateway` 模式运行
- 监听 `0.0.0.0:9877` 端口
- 从 `config.toml` 文件加载所有配置

#### 管理服务

```bash
# 查看状态
./manage.sh status

# 停止服务
./manage.sh stop

# 重启服务
./manage.sh restart

# 查看日志
./manage.sh logs
```

### 配置文件

项目根目录下的 `config.toml` 文件包含所有配置：

```toml
# Enable debug mode
debug = false

[logging]
level = "INFO"  # INFO or DEBUG only
environment = "development"  # development or production
# log_dir is optional, defaults to anp_proxy/logs

[gateway]
# HTTP 服务器设置
host = "0.0.0.0"
port = 9877

# 连接限制
max_connections = 100
timeout = 120.0
keepalive_timeout = 60.0
```

## 详细配置

### Gateway 配置

```toml
[gateway]
# HTTP 服务器设置
host = "0.0.0.0"  # 监听地址
port = 9877       # HTTP 服务端口

# 连接设置
max_connections = 100      # 最大连接数
timeout = 120.0           # 连接超时时间
keepalive_timeout = 60.0  # 保活超时时间

# 协议设置
chunk_size = 65536        # 分片大小 (64KB)
ping_interval = 10.0      # Ping 间隔

# 智能路由配置
enable_smart_routing = true
service_cache_ttl = 300   # 服务发现缓存 TTL
```

### TLS 配置

```toml
[gateway.tls]
enabled = true
cert_file = "server.crt"      # 证书文件路径
key_file = "server.key"       # 私钥文件路径
ca_file = "ca.crt"           # CA 证书文件路径
verify_mode = "required"     # 验证模式：none, optional, required
```

### 认证配置

```toml
[gateway.auth]
enabled = true
shared_secret = "your-secret-key"  # 共享密钥
token_expiry = 3600                # Token 过期时间

# DID-WBA 配置
did = "did:example:123"
resolver_base_url = "https://resolver.example.com"
nonce_window_seconds = 300

# JWT 配置
jwt_private_key_path = "private.pem"
jwt_public_key_path = "public.pem"
```

### 数据库配置

```toml
[gateway.database]
enabled = true
host = "localhost"
port = 3306
user = "anp_user"
password = ""
database = "anp_proxy"
charset = "utf8mb4"
connect_timeout = 10.0
min_connections = 2
max_connections = 20
```

## 开发指南

### 项目结构

```
anp-proxy/
├── anp_proxy/              # 主代码目录
│   ├── anp_sdk/           # ANP SDK
│   ├── common/            # 公共工具和配置
│   ├── gateway/           # Gateway 服务
│   ├── protocol/          # ANPX 协议实现
│   ├── app.py            # 应用入口
│   └── cli.py            # CLI 入口
├── config.toml            # 配置文件
├── manage.sh              # 管理脚本
├── pyproject.toml         # 项目配置
└── docs/                  # 文档目录
```

### 开发模式

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest

# 代码格式化
uv run ruff format

# 代码检查
uv run ruff check
```

## ANPX 协议

ANP Proxy 使用自定义的 ANPX 二进制协议，支持：

- **固定 24B 头部** - 魔数、版本、类型、标志、长度、CRC 校验
- **TLV 扩展体** - 灵活的标签-长度-值格式
- **分片传输** - 支持大文件和流式内容
- **端到端校验** - CRC-32 双层校验保证数据完整性

详细协议规范请参考 [docs/proxy-protocol.md](docs/proxy-protocol.md)
