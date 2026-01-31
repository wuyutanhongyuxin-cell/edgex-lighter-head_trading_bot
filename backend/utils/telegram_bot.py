"""
Telegram Bot 推送模块
实时推送交易通知、状态报告和告警信息
"""
import asyncio
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class TelegramConfig:
    """Telegram 配置"""
    bot_token: str
    chat_id: str
    account_label: str = 'A1'
    enabled: bool = True
    # 推送设置
    push_trades: bool = True
    push_errors: bool = True
    push_status: bool = True
    push_signals: bool = False  # 信号较多,默认关闭
    # 静默期 (避免刷屏)
    min_interval_seconds: int = 1
    status_interval_seconds: int = 1800  # 30分钟状态报告


class TelegramBot:
    """
    Telegram 机器人
    用于推送交易通知和系统状态
    """

    def __init__(self, config: TelegramConfig):
        self.config = config
        self.base_url = f"https://api.telegram.org/bot{config.bot_token}"

        # 消息队列
        self._message_queue: asyncio.Queue = asyncio.Queue()

        # 速率限制
        self._last_message_time = 0
        self._last_status_time = 0

        # 统计
        self.messages_sent = 0
        self.errors = 0

        # 后台任务
        self._sender_task: Optional[asyncio.Task] = None
        self._status_task: Optional[asyncio.Task] = None

        # 系统引用 (用于获取状态)
        self.system = None

    async def start(self):
        """启动机器人"""
        if not self.config.enabled:
            logger.info("Telegram bot disabled")
            return

        if not self.config.bot_token or not self.config.chat_id:
            logger.warning("Telegram bot not configured (missing token or chat_id)")
            self.config.enabled = False
            return

        # 启动消息发送任务
        self._sender_task = asyncio.create_task(self._message_sender())

        # 启动定时状态报告
        if self.config.push_status:
            self._status_task = asyncio.create_task(self._status_reporter())

        # 发送启动通知
        await self.send_startup_message()

        logger.info("Telegram bot started")

    async def stop(self):
        """停止机器人"""
        if not self.config.enabled:
            return

        # 发送关闭通知
        await self.send_shutdown_message()

        # 取消后台任务
        if self._sender_task:
            self._sender_task.cancel()
        if self._status_task:
            self._status_task.cancel()

        logger.info("Telegram bot stopped")

    async def _message_sender(self):
        """消息发送后台任务"""
        while True:
            try:
                message = await self._message_queue.get()

                # 速率限制
                now = time.time()
                if now - self._last_message_time < self.config.min_interval_seconds:
                    await asyncio.sleep(self.config.min_interval_seconds)

                await self._send_message(message)
                self._last_message_time = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Message sender error: {e}")
                self.errors += 1

    async def _status_reporter(self):
        """定时状态报告任务"""
        while True:
            try:
                await asyncio.sleep(self.config.status_interval_seconds)

                if self.system:
                    await self.send_status_report()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Status reporter error: {e}")

    async def _send_message(self, text: str, parse_mode: str = 'HTML'):
        """发送消息到 Telegram"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/sendMessage"
                data = {
                    'chat_id': self.config.chat_id,
                    'text': text,
                    'parse_mode': parse_mode,
                    'disable_web_page_preview': True
                }

                async with session.post(url, json=data) as resp:
                    if resp.status == 200:
                        self.messages_sent += 1
                        logger.debug(f"Telegram message sent")
                    else:
                        error = await resp.text()
                        logger.error(f"Telegram API error: {error}")
                        self.errors += 1

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            self.errors += 1

    def queue_message(self, text: str):
        """将消息加入队列"""
        if self.config.enabled:
            try:
                self._message_queue.put_nowait(text)
            except asyncio.QueueFull:
                logger.warning("Telegram message queue full")

    # ==================== 消息模板 ====================

    async def send_startup_message(self):
        """发送启动通知"""
        message = f"""
🚀 <b>套利系统启动</b>

📍 账户: <code>{self.config.account_label}</code>
⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📊 状态: 等待前端连接...

<i>EdgeX-Lighter 跨交易所套利</i>
"""
        self.queue_message(message.strip())

    async def send_shutdown_message(self):
        """发送关闭通知"""
        message = f"""
🛑 <b>套利系统关闭</b>

📍 账户: <code>{self.config.account_label}</code>
⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📨 本次推送: {self.messages_sent} 条

<i>系统已安全停止</i>
"""
        await self._send_message(message.strip())

    async def send_frontend_connected(self, ticker: str):
        """发送前端连接通知"""
        message = f"""
✅ <b>前端已连接</b>

📍 账户: <code>{self.config.account_label}</code>
💹 交易对: <code>{ticker}</code>
⏰ 时间: {datetime.now().strftime('%H:%M:%S')}

<i>开始采样,等待交易信号...</i>
"""
        self.queue_message(message.strip())

    async def send_sampling_complete(self, samples: int, long_threshold: float, short_threshold: float):
        """发送采样完成通知"""
        message = f"""
📊 <b>采样完成</b>

📍 账户: <code>{self.config.account_label}</code>
📈 样本数: {samples}
🎯 做多阈值: {long_threshold:.2f}
🎯 做空阈值: {short_threshold:.2f}
⏰ 时间: {datetime.now().strftime('%H:%M:%S')}

<i>策略已激活,开始监控套利机会</i>
"""
        self.queue_message(message.strip())

    async def send_trade_notification(
        self,
        direction: str,
        quantity: str,
        edgex_price: str,
        lighter_price: str,
        spread: str,
        latency_ms: int,
        pnl_estimate: str = '0',
        edgex_position: str = '0',
        lighter_position: str = '0'
    ):
        """发送交易通知"""
        if not self.config.push_trades:
            return

        direction_emoji = '🟢' if direction == 'long' else '🔴'
        direction_text = '做多' if direction == 'long' else '做空'

        message = f"""
{direction_emoji} <b>交易成交 - {direction_text}</b>

📍 账户: <code>{self.config.account_label}</code>
📦 数量: <code>{quantity}</code>
💰 EdgeX: <code>{edgex_price}</code>
💰 Lighter: <code>{lighter_price}</code>
📊 价差: <code>{spread}</code>
⚡ 延迟: {latency_ms}ms

📈 EdgeX仓位: <code>{edgex_position}</code>
📉 Lighter仓位: <code>{lighter_position}</code>
💵 预估盈亏: <code>{pnl_estimate}</code>

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        self.queue_message(message.strip())

    async def send_error_alert(self, error_type: str, message: str, details: Dict[str, Any] = None):
        """发送错误告警"""
        if not self.config.push_errors:
            return

        details_text = ''
        if details:
            details_text = '\n'.join([f"  {k}: {v}" for k, v in details.items()])

        alert = f"""
⚠️ <b>错误告警</b>

📍 账户: <code>{self.config.account_label}</code>
❌ 类型: <code>{error_type}</code>
📝 信息: {message}
{f"📋 详情:\n{details_text}" if details_text else ""}
⏰ {datetime.now().strftime('%H:%M:%S')}

<i>请检查系统状态</i>
"""
        self.queue_message(alert.strip())

    async def send_circuit_breaker_alert(self, error_count: int, window_seconds: int):
        """发送熔断告警"""
        message = f"""
🚨 <b>熔断触发</b>

📍 账户: <code>{self.config.account_label}</code>
❌ 错误数: {error_count} 次 / {window_seconds}秒
⏰ 时间: {datetime.now().strftime('%H:%M:%S')}

<b>策略已暂停,5分钟后自动恢复</b>
<i>请检查网络和 API 状态</i>
"""
        self.queue_message(message.strip())

    async def send_position_imbalance_alert(self, edgex_pos: str, lighter_pos: str, net_pos: str):
        """发送仓位不平衡告警"""
        message = f"""
⚠️ <b>仓位不平衡告警</b>

📍 账户: <code>{self.config.account_label}</code>
📈 EdgeX: <code>{edgex_pos}</code>
📉 Lighter: <code>{lighter_pos}</code>
🔢 净仓位: <code>{net_pos}</code>
⏰ {datetime.now().strftime('%H:%M:%S')}

<i>请检查对冲执行状态</i>
"""
        self.queue_message(message.strip())

    async def send_status_report(self):
        """发送定时状态报告"""
        if not self.system:
            return

        try:
            engine_status = self.system.arbitrage_engine.get_status()
            position_status = self.system.position_manager.get_status()
            risk_status = self.system.risk_manager.get_status()
            latency_status = self.system.latency_monitor.get_status()

            # 计算运行时间
            # uptime = ...

            message = f"""
📊 <b>状态报告</b>

📍 账户: <code>{self.config.account_label}</code>
🔄 状态: {'运行中' if engine_status['is_running'] else '已暂停'}

<b>交易统计:</b>
  📈 信号数: {engine_status['signal_count']}
  ✅ 交易数: {risk_status['trade_count']}
  ❌ 错误数: {risk_status['error_count']}
  💵 日盈亏: {risk_status['daily_pnl']:.2f}

<b>仓位状态:</b>
  📊 EdgeX: {position_status['edgex']['size']:.6f}
  📊 Lighter: {position_status['lighter']['size']:.6f}
  🔢 净仓位: {position_status['net_position']:.6f}

<b>策略参数:</b>
  🎯 做多阈值: {engine_status['long_threshold']:.2f}
  🎯 做空阈值: {engine_status['short_threshold']:.2f}
  📊 当前价差: L={engine_status['current_long_spread'] or 0:.2f} / S={engine_status['current_short_spread'] or 0:.2f}

<b>延迟统计:</b>
  ⚡ 得分: {latency_status['score']:.0f}/100

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            self.queue_message(message.strip())

        except Exception as e:
            logger.error(f"Failed to send status report: {e}")

    async def send_daily_summary(
        self,
        trade_count: int,
        success_count: int,
        total_pnl: str,
        avg_latency: float,
        max_position: str
    ):
        """发送每日汇总"""
        success_rate = (success_count / trade_count * 100) if trade_count > 0 else 0

        message = f"""
📈 <b>每日交易汇总</b>

📍 账户: <code>{self.config.account_label}</code>
📅 日期: {datetime.now().strftime('%Y-%m-%d')}

<b>交易统计:</b>
  📊 总交易: {trade_count} 笔
  ✅ 成功率: {success_rate:.1f}%
  💵 总盈亏: <code>{total_pnl}</code>

<b>性能指标:</b>
  ⚡ 平均延迟: {avg_latency:.0f}ms
  📈 最大持仓: {max_position}

<i>祝交易顺利! 🎉</i>
"""
        self.queue_message(message.strip())

    def get_status(self) -> Dict[str, Any]:
        """获取机器人状态"""
        return {
            'enabled': self.config.enabled,
            'messages_sent': self.messages_sent,
            'errors': self.errors,
            'queue_size': self._message_queue.qsize() if self._message_queue else 0
        }
