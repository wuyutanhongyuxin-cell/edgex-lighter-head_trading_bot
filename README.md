<div align="center">

# 🚀 EdgeX-Lighter 跨交易所套利系统

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/JavaScript-ES6+-yellow.svg" alt="JavaScript">
  <img src="https://img.shields.io/badge/WebSocket-Real--time-green.svg" alt="WebSocket">
  <img src="https://img.shields.io/badge/License-MIT-purple.svg" alt="License">
  <img src="https://img.shields.io/badge/Status-Production--Ready-brightgreen.svg" alt="Status">
</p>

<p align="center">
  <b>一个高性能的混合架构跨交易所套利机器人</b><br>
  <sub>EdgeX 浏览器前端 + Lighter Python 后端 | 动态阈值策略 | 实时 Telegram 通知</sub>
</p>

<p align="center">
  <a href="#-特性">特性</a> •
  <a href="#-系统架构">架构</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-策略原理">策略</a> •
  <a href="#-配置说明">配置</a> •
  <a href="#-监控与告警">监控</a>
</p>

---

</div>

## ✨ 特性

| 特性 | 描述 |
|:---:|:---|
| 🔄 **混合架构** | EdgeX 浏览器 JS 前端 + Lighter Python 后端，充分利用两端优势 |
| 📊 **动态阈值** | 基于实时价差采样的自适应套利阈值，避免虚假信号 |
| ⚡ **延迟优化** | 预测性定价 + 自适应阈值补偿前端延迟 |
| 🛡️ **全面风控** | 仓位限制、熔断机制、延迟监控、紧急平仓 |
| 📱 **Telegram 推送** | 实时交易通知、状态报告、错误告警 |
| 📈 **数据日志** | 完整的交易记录和策略快照，便于分析优化 |
| 🔌 **本地通信** | WebSocket 本地通信，低延迟高可靠 |

---

## 🏗 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                      🌐 浏览器 (EdgeX 前端)                           │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │  EdgeX WS    │───▶│  WS Bridge   │───▶│   Order      │          │
│  │  行情订阅    │    │  本地桥接    │    │   Executor   │          │
│  └──────────────┘    └──────┬───────┘    └──────────────┘          │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ ws://localhost:8765
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      🐍 Python 后端                                   │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │  WS Server   │───▶│  Arbitrage   │───▶│   Lighter    │          │
│  │  消息路由    │    │   Engine     │    │   Client     │          │
│  └──────────────┘    └──────────────┘    └──────────────┘          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │  Position    │    │    Risk      │    │   Latency    │          │
│  │  Manager     │    │   Manager    │    │   Monitor    │          │
│  └──────────────┘    └──────────────┘    └──────────────┘          │
│  ┌──────────────┐    ┌──────────────┐                               │
│  │  Data Logger │    │   Telegram   │                               │
│  │  数据记录    │    │     Bot      │                               │
│  └──────────────┘    └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
```

### 数据流

```
EdgeX BBO ──┬──▶ 策略引擎 ──▶ 检测套利机会 ──▶ 发送指令到前端
            │                                      │
Lighter BBO ┘                                      ▼
                                           EdgeX 下单执行
                                                   │
                                                   ▼
                                           成交回报上传
                                                   │
                                                   ▼
                                           Lighter 对冲执行
                                                   │
                                                   ▼
                                           仓位更新 & 记录
```

---

## 🎯 策略原理

### 套利逻辑

本系统采用**价差回归**策略，核心假设是两个交易所的同一标的价差会在一定范围内波动。

| 方向 | 条件 | 操作 |
|:---:|:---|:---|
| 🟢 **做多** | `Lighter买一 - EdgeX卖一 > 阈值` | EdgeX 买入 + Lighter 卖出 |
| 🔴 **做空** | `EdgeX买一 - Lighter卖一 > 阈值` | EdgeX 卖出 + Lighter 买入 |

### 动态阈值计算

```python
# 采样阶段 (100 个样本)
samples = collect_spread_samples(100)

# 计算阈值
long_threshold  = mean(long_spreads)  + offset
short_threshold = mean(short_spreads) + offset

# offset 默认值: 10 (可配置)
```

### 延迟补偿

由于浏览器前端延迟较高 (~100-200ms)，系统采用以下优化：

| 优化策略 | 说明 |
|:---|:---|
| **自适应阈值** | `threshold = base + (latency_ms // 50) * tick_size` |
| **预测性定价** | 根据价格变化速率预测执行时价格 |
| **POST_ONLY 限价单** | 避免吃单，获取 maker 返佣 |

---

## 📦 快速开始

### 环境要求

- Python 3.9+
- 现代浏览器 (Chrome/Firefox/Edge)
- EdgeX 和 Lighter 账户

### 1️⃣ 克隆仓库

```bash
git clone https://github.com/wuyutanhongyuxin-cell/edgex-lighter-head_trading_bot.git
cd edgex-lighter-head_trading_bot
```

### 2️⃣ 安装后端依赖

```bash
cd backend
pip install -r requirements.txt
```

### 3️⃣ 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# Lighter 配置 (必填)
API_KEY_PRIVATE_KEY=your_lighter_private_key
LIGHTER_ACCOUNT_INDEX=0
LIGHTER_API_KEY_INDEX=0

# 策略配置
TICKER=BTC
ORDER_QUANTITY=0.001
MAX_POSITION=0.01
THRESHOLD_OFFSET=10

# Telegram 配置 (可选)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4️⃣ 启动后端

```bash
python main.py --ticker BTC --size 0.001 --max-position 0.01
```

### 5️⃣ 启动前端

1. 打开 EdgeX 网页: https://pro.edgex.exchange
2. **登录你的账户**
3. 按 `F12` 打开开发者工具
4. 切换到 `Console` 标签
5. 将 `frontend/edgex_arbitrage.js` 的内容粘贴到 Console
6. **修改配置中的 `accountId` 和 `starkPrivateKey`**
7. 按 `Enter` 执行

### 6️⃣ 验证连接

**后端日志应显示:**
```
✓ EdgeX frontend connected: ticker=BTC, contractId=10002
✓ System ready! Starting trading loop...
```

**前端 Console 应显示:**
```
[EdgeX] Connected to backend
[EdgeX] EdgeX Frontend Ready!
```

---

## ⚙️ 配置说明

### 命令行参数

```bash
python main.py [OPTIONS]
```

| 参数 | 类型 | 默认值 | 描述 |
|:---|:---:|:---:|:---|
| `--ticker` | TEXT | BTC | 交易对 |
| `--size` | FLOAT | 0.001 | 每笔订单数量 |
| `--max-position` | FLOAT | 0.01 | 最大持仓量 |
| `--threshold-offset` | FLOAT | 10 | 阈值偏移量 |
| `--port` | INT | 8765 | WebSocket 端口 |
| `--log-level` | TEXT | INFO | 日志级别 |

### 前端配置

在 `edgex_arbitrage.js` 中修改：

```javascript
const CONFIG = {
    edgex: {
        accountId: 'YOUR_ACCOUNT_ID',        // 你的 EdgeX 账户 ID
        starkPrivateKey: 'YOUR_STARK_KEY',   // 你的 Stark 私钥
        contractId: '10002'                   // BTC-USD
    },
    backend: {
        serverUrl: 'ws://localhost:8765'     // 后端地址
    }
};
```

### 交易对 ID 映射

| 交易对 | Contract ID |
|:---:|:---:|
| BTC-USD | 10002 |
| ETH-USD | 10001 |

---

## 📱 Telegram 通知

### 配置 Bot

1. 在 Telegram 中找到 `@BotFather`
2. 发送 `/newbot` 创建机器人
3. 获取 Bot Token
4. 获取你的 Chat ID (可使用 `@userinfobot`)
5. 填入 `.env` 配置文件

### 通知类型

| 类型 | 描述 | 示例 |
|:---|:---|:---|
| 🚀 **启动通知** | 系统启动时推送 | 账户、时间、状态 |
| 🟢🔴 **交易通知** | 每笔成交推送 | 方向、数量、价格、延迟 |
| ⚠️ **错误告警** | 发生错误时推送 | 错误类型、详情 |
| 🚨 **熔断告警** | 触发熔断时推送 | 错误次数、恢复时间 |
| 📊 **状态报告** | 定时推送 (30分钟) | 仓位、盈亏、统计 |
| 📈 **每日汇总** | 每日交易总结 | 总交易、成功率、盈亏 |

### 通知示例

```
🟢 交易成交 - 做多

📍 账户: A1
📦 数量: 0.001
💰 EdgeX: 42150.5
💰 Lighter: 42165.2
📊 价差: 14.7
⚡ 延迟: 125ms

📈 EdgeX仓位: 0.003
📉 Lighter仓位: -0.003
💵 预估盈亏: +2.35

⏰ 14:32:15
```

---

## 📊 数据日志

系统会自动记录所有交易数据，便于后续分析和策略优化。

### 日志文件

| 文件 | 描述 | 格式 |
|:---|:---|:---:|
| `logs/trades_YYYYMMDD.csv` | 交易记录 | CSV |
| `logs/bbo_YYYYMMDD.csv` | BBO 数据 | CSV |
| `logs/snapshots_YYYYMMDD.csv` | 策略快照 | CSV |
| `logs/summary_YYYYMMDD.json` | 每日汇总 | JSON |

### 交易记录字段

```csv
timestamp,direction,edgex_price,lighter_price,quantity,spread,latency_ms,pnl,edgex_position,lighter_position
```

### 用于 Claude Code 分析

导出 JSON 格式数据供 AI 分析：

```python
data_logger.export_for_analysis(days=7)  # 导出最近 7 天数据
```

---

## 🛡️ 风控机制

### 风控规则

| 规则 | 参数 | 动作 |
|:---|:---:|:---|
| **最大持仓** | 0.01 BTC | 超过限制不开新仓 |
| **仓位平衡** | 差异 < 0.005 | 差异过大告警 |
| **日亏损限制** | -$500 | 触发暂停交易 |
| **延迟监控** | > 500ms | 暂停交易 |
| **错误率监控** | > 10% | 触发熔断 |

### 熔断机制

```
错误次数 > 阈值 (60秒内 5 次)
    │
    ▼
触发熔断 ──▶ 暂停策略 ──▶ Telegram 告警
    │
    ▼
5 分钟后自动恢复
```

### 紧急处理

| 场景 | 自动动作 |
|:---|:---|
| 前端断连 | 暂停策略，等待重连 |
| 仓位不平衡 | Telegram 告警 |
| Ctrl+C 退出 | 双边紧急平仓 |

---

## 🔧 前端调试命令

在浏览器 Console 中执行：

```javascript
// 获取当前 BBO
window.edgexArbitrage.getBBO()

// 获取系统状态
window.edgexArbitrage.getStatus()

// 获取连接状态
window.edgexArbitrage.getConnectionStatus()

// 手动测试下单 (谨慎使用!)
window.edgexArbitrage.testOrder('buy', 0.001, 42000)
```

---

## 📁 项目结构

```
edgex-lighter-arbitrage/
├── 📂 frontend/
│   └── edgex_arbitrage.js        # 浏览器 Console 脚本
│
├── 📂 backend/
│   ├── main.py                   # 主入口
│   ├── config.py                 # 配置管理
│   ├── requirements.txt          # Python 依赖
│   │
│   ├── 📂 server/
│   │   └── websocket_server.py   # WebSocket 服务器
│   │
│   ├── 📂 exchanges/
│   │   ├── base.py               # 交易所基类
│   │   └── lighter_client.py     # Lighter 客户端
│   │
│   ├── 📂 strategy/
│   │   ├── arbitrage_engine.py   # 套利策略引擎
│   │   ├── order_book_manager.py # 订单簿管理
│   │   └── position_manager.py   # 仓位管理
│   │
│   ├── 📂 risk/
│   │   ├── risk_manager.py       # 风控管理
│   │   └── latency_monitor.py    # 延迟监控
│   │
│   └── 📂 utils/
│       ├── logger.py             # 日志配置
│       ├── helpers.py            # 工具函数
│       ├── data_logger.py        # 数据记录
│       └── telegram_bot.py       # Telegram 机器人
│
├── 📂 logs/                      # 运行日志目录
├── .env.example                  # 环境变量示例
└── README.md                     # 本文件
```

---

## ⚠️ 注意事项

### 安全提示

> ⚠️ **私钥安全**: 不要在公共场合或截图中暴露你的私钥!

- 私钥仅存储在本地 `.env` 文件和浏览器中
- 不要将包含私钥的文件提交到 Git
- 建议使用专用的交易账户

### 运行建议

| 建议 | 说明 |
|:---|:---|
| 🖥️ **专用浏览器** | 保持 EdgeX 标签页活跃，建议使用独立浏览器窗口 |
| 💰 **小额测试** | 先用小额测试系统稳定性 |
| 📊 **监控日志** | 关注后端日志中的异常信息 |
| 🔌 **网络稳定** | 确保网络连接稳定 |

### 已知限制

- 浏览器最小化可能导致 JS 执行变慢
- 前端延迟波动可能影响套利效果
- 依赖 EdgeX 网页保持登录状态

---

## 📈 性能优化

### 延迟分解

| 阶段 | 预估延迟 |
|:---|:---:|
| 信号检测 | ~5ms |
| 后端→前端通信 | ~5ms |
| 前端处理+签名 | ~50ms |
| EdgeX API 调用 | ~50-150ms |
| **总计** | **~100-200ms** |

### 优化建议

1. **使用有线网络** - 减少网络延迟
2. **靠近服务器** - 选择低延迟区域
3. **高性能设备** - 确保 JS 执行流畅
4. **调整阈值** - 根据实际延迟调整 offset

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request!

---

## 📄 License

本项目采用 [MIT License](LICENSE) 开源协议。

---

<div align="center">

**⭐ 如果这个项目对你有帮助，请给个 Star!**

Made with ❤️ by [wuyutanhongyuxin-cell](https://github.com/wuyutanhongyuxin-cell)

</div>
