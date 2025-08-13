# ANP Proxy

Agent Network Proxy (ANP) - 高性能 HTTP over WebSocket 隧道，用于私有网络服务的安全对外暴露。

## 特性

- 🚀 **高性能异步架构** - 基于 asyncio 的纯异步实现
- 🔒 **安全可靠** - WSS (TLS) + 双向认证 + CRC 校验
- 📦 **二进制协议** - 自定义 ANPX 协议，支持大文件分片传输
- 🔧 **框架无关** - 支持任意 ASGI 应用 (FastAPI, Django, Flask 等)
- 🔄 **自动重连** - 断线自动重连，指数退避策略
- 📊 **监控友好** - 详细的日志和统计信息
- ⚙️ **易于配置** - TOML 配置文件 + 命令行参数

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
# 使用 UV 安装 (推荐)
uv add anp-proxy

# 或使用 pip
pip install anp-proxy
```

### 基本使用

#### 1. 开发模式 (Gateway + Receiver 一体)

```bash
# 启动一体化代理，服务本地 FastAPI 应用
anp-proxy --mode both --local-app "myapp:app" --gateway-port 8080
```

#### 2. 生产模式 - Gateway (公网部署)

```bash
# 在公网服务器启动 Gateway
anp-proxy --mode gateway --gateway-host 0.0.0.0 --gateway-port 80 --wss-port 443
```

#### 3. 生产模式 - Receiver (私网部署)

```bash
# 在私网启动 Receiver，连接到公网 Gateway
anp-proxy --mode receiver --gateway-url "wss://your-gateway.com:443" --local-app "myapp:app"
```

### 配置文件

创建 `config.toml`:

```toml
mode = "both"  # gateway, receiver, both

[gateway]
host = "0.0.0.0"
port = 8080
wss_port = 8765

[receiver]
gateway_url = "wss://localhost:8765"
local_app_module = "myapp:app"

[logging]
level = "INFO"
```

使用配置文件：

```bash
anp-proxy --config config.toml
```

## 详细配置

### Gateway 配置

```toml
[gateway]
# HTTP 服务器设置
host = "0.0.0.0"
port = 8080

# WebSocket 服务器设置
wss_host = "0.0.0.0"
wss_port = 8765

# 连接设置
max_connections = 100
timeout = 30.0
chunk_size = 65536  # 64KB

[gateway.tls]
enabled = true
cert_file = "server.crt"
key_file = "server.key"
verify_mode = "required"

[gateway.auth]
enabled = true
shared_secret = "your-secret-key"
token_expiry = 3600
```

### Receiver 配置

```toml
[receiver]
# Gateway 连接
gateway_url = "wss://gateway.example.com:8765"

# 本地应用设置
local_app_module = "myapp:app"  # ASGI 应用

# 重连设置
reconnect_enabled = true
reconnect_delay = 5.0
max_reconnect_attempts = 10

[receiver.tls]
enabled = true
ca_file = "ca.crt"
verify_mode = "required"

[receiver.auth]
enabled = true
shared_secret = "your-secret-key"
```

## Python API

### 编程方式使用

```python
import asyncio
from anp_proxy import ANPProxy, ANPConfig

# 创建配置
config = ANPConfig(mode="both")
config.gateway.port = 8080
config.receiver.local_app_module = "myapp:app"

# 创建并运行代理
async def main():
    proxy = ANPProxy(config)

    if config.mode == "gateway":
        gateway = proxy.create_gateway_server()
        await gateway.run()
    elif config.mode == "receiver":
        receiver = proxy.create_receiver_client()
        await receiver.run()

asyncio.run(main())
```

### 集成到现有应用

```python
from fastapi import FastAPI
from anp_proxy import ReceiverClient, ReceiverConfig

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

# 创建 ANP Receiver
config = ReceiverConfig(gateway_url="wss://gateway.example.com:8765")
receiver = ReceiverClient(config, app)

# 启动 receiver (在后台任务中)
import asyncio
asyncio.create_task(receiver.run())
```

## ANPX 协议

ANP Proxy 使用自定义的 ANPX 二进制协议，支持：

- **固定 24B 头部** - 魔数、版本、类型、标志、长度、CRC 校验
- **TLV 扩展体** - 灵活的标签-长度-值格式
- **分片传输** - 支持大文件和流式内容
- **端到端校验** - CRC-32 双层校验保证数据完整性

详细协议规范请参考 [docs/proxy-protocol.md](docs/proxy-protocol.md)

## 监控和运维

### 健康检查

```bash
# 检查 Gateway 状态
curl http://localhost:8080/health

# 获取统计信息
curl http://localhost:8080/stats
```

### 日志配置

```toml
[logging]
level = "INFO"  # DEBUG, INFO, WARNING, ERROR
format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
file = "anp-proxy.log"
max_size = "10MB"
backup_count = 5
```

### 性能调优

```toml
[gateway]
chunk_size = 131072  # 128KB，适合大文件传输
max_connections = 1000  # 最大连接数
timeout = 60.0  # 超时时间

[receiver]
chunk_size = 131072
reconnect_delay = 2.0  # 重连延迟
```

## 部署示例

### Docker 部署

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
EXPOSE 8080 8765

CMD ["anp-proxy", "--config", "config.toml"]
```

### Systemd 服务

```ini
[Unit]
Description=ANP Proxy Gateway
After=network.target

[Service]
Type=exec
User=anp-proxy
WorkingDirectory=/opt/anp-proxy
ExecStart=/opt/anp-proxy/venv/bin/anp-proxy --config config.toml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 故障排除

### 常见问题

1. **连接失败**
   - 检查防火墙设置
   - 验证 WebSocket URL 是否正确
   - 确认 TLS 证书配置

2. **认证失败**
   - 检查 shared_secret 是否一致
   - 验证时间同步 (重要)

3. **性能问题**
   - 调整 chunk_size
   - 增加 max_connections
   - 检查网络延迟

### 调试模式

```bash
anp-proxy --debug --log-level DEBUG
```

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 贡献

欢迎提交 Issue 和 Pull Request！

## 支持

- 📖 [文档](docs/)
- 🐛 [Issue Tracker](https://github.com/your-org/anp-proxy/issues)
- 💬 [讨论区](https://github.com/your-org/anp-proxy/discussions)
