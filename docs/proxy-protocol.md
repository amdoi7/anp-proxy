## ANPX 二进制协议 · **正式版 v1.0.0**

> 更新时间 2025-08-07
> **适用场景**：在 WebSocket/WSS 信道中高效传输 **HTTP Request / Response**（含大文件分片）

---

### 0. 要解决的核心问题

| 编号 | 痛点                             | 描述                                                        |
| -- | ------------------------------ | --------------------------------------------------------- |
| P1 | 私网 / 局域网 Agent **无公网 HTTP 入口** | 外部无法直接调用，能力孤岛化                                            |
| P2 | HTTP 报文元素多且不定长                 | 需一次性封装 `method / path / headers / query / body` 并保持顺序与完整性 |
| P3 | NAT / 防火墙穿透复杂                  | 传统端口映射或 VPN 成本高；WebSocket 可用单出站端口直连                       |
| P4 | **大文件 & 流式内容**                 | 需要分片传输，边收边处理，且保证顺序和完整性                                    |
| P5 | 多语言 / 多框架异构                    | 希望协议层统一，前后端任意技术栈均可解析                                      |
| P6 | 数据完整性 & 调试友好                   | 必须提供端到端校验 (HeaderCRC / BodyCRC) 且抓包易读                     |
| P7 | 长期扩展与兼容                        | 新增字段不破坏旧实现，可平滑演进                                          |

> **ANPX** 通过“固定 24 B 头 + 可扩展 TLV”设计，将任意 HTTP 请求/响应安全隧道化至 WebSocket/WSS，支持 GB 级文件分片与跨语言互操作。

---

### 0. 术语速览

| 关键词            | 说明                                                          |
| -------------- | ----------------------------------------------------------- |
| **HTTP Req**   | Type `0x01`：HTTP 请求帧                                        |
| **HTTP Resp**  | Type `0x02`：HTTP 响应帧                                        |
| **Error**      | Type `0xFF`：错误帧                                             |
| **http\_meta** | Tag `0x02`：将 `method / path / headers / query` 打包为 **JSON** |
| **http\_body** | Tag `0x03`：HTTP 原始 Body（二进制）                                |
| **resp\_meta** | Tag `0x04`：将 `status / reason` 打包为 **JSON**                 |

---

## 1. 固定 24 B 头（Header）

```
┌──────────── 固定 24 B Header ────────────┐┌──── 可变长 TLV Body ────┐
│ Magic(4) │Ver(1)│Type(1)│Flag(1)│Res(1)││ TLV #1 │ TLV #2 │ ... │
│ TotalLen(4) │ HeaderCRC(4) │ BodyCRC(4) │└─────────────────────────┘
└──────────────────────────────────────────┘
```

| 偏移 | 字段            | 长度  | 描述                                            |
| -- | ------------- | --- | --------------------------------------------- |
| 0  | **Magic**     | 4 B | ASCII `"ANPX"`                                |
| 4  | **Ver**       | 1 B | 协议版本，当前 `0x01`                                |
| 5  | **Type**      | 1 B | `0x01`=HTTP Req `0x02`=HTTP Resp `0xFF`=Error |
| 6  | **Flag**      | 1 B | **bit 0 = 1 ⇒ 分片帧**；其余位恒 0                    |
| 7  | **Resv**      | 1 B | 预留 `0x00`                                     |
| 8  | **TotalLen**  | 4 B | Header + Body 总长度（大端）                         |
| 12 | **HeaderCRC** | 4 B | CRC-32（字节 0 \~ 11）                            |
| 16 | **BodyCRC**   | 4 B | CRC-32（完整 Body；分片时对 **重组后的整体** 计算）            |
| 20 | —             | —   | **Header 固定 24 B**                            |

---

## 2. TLV Body

```
┌ Tag(1) ┬ Len(4,BE) ┬ Value(变长) ┐
```

| Tag       | Req 用途         | Resp 用途        | Value 类型 | 说明             |
| --------- | -------------- | -------------- | -------- | -------------- |
| **0x01**  | request\_id    | request\_id    | UTF-8    | UUID-v4        |
| **0x02**  | **http\_meta** | —              | JSON     | 方法、路径、头、查询     |
| **0x03**  | **http\_body** | http\_body     | Binary   | 原始字节           |
| **0x04**  | —              | **resp\_meta** | JSON     | 状态码 & 原因       |
| **0x0A**  | chunk\_idx     | chunk\_idx     | UInt32   | 分片序号 (0-based) |
| **0x0B**  | chunk\_tot     | chunk\_tot     | UInt32   | 总片数（可选）        |
| **0x0C**  | final\_chunk   | final\_chunk   | UInt8    | `0x01`=最后一片    |
| 0xF0-0xFF | 预留             | 预留             | —        | 用户扩展区          |

> 未知 Tag → 直接跳过 `1 + 4 + Len`，保证向前兼容。

### 2.1 `http_meta` JSON 示例

```json
{
  "method":  "POST",
  "path":    "/api/v1/run",
  "headers": { "content-type": "application/json" },
  "query":   { "q": "test" }
}
```

### 2.2 `resp_meta` JSON 示例

```json
{
  "status": 200,
  "reason": "OK"
}
```

---

## 3. 封包流程

1. **组装 TLV**

   * 必带：`0x01 request_id`
   * HTTP Req 需 `0x02 http_meta` + `0x03 http_body`
   * HTTP Resp 需 `0x03 http_body` + `0x04 resp_meta`
   * 分片帧再加 `0x0A / 0x0B / 0x0C`
2. `Flag.bit0` = 1 ⇔ 分片帧
3. 计算 `BodyCRC`，填写 Header 全字段
4. Header + Body 拼接发送

---

## 4. 解包流程

1. 读 24 B Header → 校验 Magic & HeaderCRC
2. 读取 Body (TotalLen-24) → 校验 BodyCRC
3. 逐 TLV 解析；未知 Tag 跳过
4. 若 `Flag.bit0==1` → **分片重组**：

   * 以 `request_id` 为键累积分片
   * **完成条件**：已收齐 `chunk_tot` 片 **或** 收到 `final_chunk=1`
   * 重组后校验整体 CRC，再交上层处理

---

## 5. 单帧示例（HTTP Request）

| Tag  | Value                                                                                                       |
| ---- | ----------------------------------------------------------------------------------------------------------- |
| 0x01 | `"550e8400-e29b-41d4-a716-446655440000"`                                                                    |
| 0x02 | `{"method":"POST","path":"/api/v1/run","headers":{"content-type":"application/json"},"query":{"q":"test"}}` |
| 0x03 | `{ "foo": "bar" }`                                                                                          |

---

## 6. 三片响应示例（HTTP Resp，分片）

| 片序 | 关键 TLV                                                                                          |
| -- | ----------------------------------------------------------------------------------------------- |
| 0  | 0x01 rid, 0x0A idx=0, 0x03 body\_part                                                           |
| 1  | 0x01 rid, 0x0A idx=1, 0x03 body\_part                                                           |
| 2  | 0x01 rid, 0x0A idx=2, 0x03 body\_part, 0x0C final\_chunk=1, 0x04 `{"status":200,"reason":"OK"}` |

---

## 7. 内存十六进制示意（单帧）

```
41 4E 50 58 01 01 00 00   ← Magic "ANPX", Ver, Type=0x01, Flag=0x00
00 00 00 F0               ← TotalLen (240 B)
12 34 56 78               ← HeaderCRC
9A BC DE F0               ← BodyCRC
… TLV 序列 …
```

---

### ✅ 特性回顾

* **固定 24 B 头** → 零拷贝、抓包易读
* **TLV 可扩展** → 新字段无痛升级
* **分片流** → 轻松搬运 GB 级文件
* **CRC 双层校验** → 端到端完整性保障

如需 **多语言 SDK、解析脚本或参考代码**，留言即可！
