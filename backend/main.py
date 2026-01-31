"""
跨交易所套利系统 - 后端主入口
EdgeX (前端 JS) + Lighter (后端 Python)

功能:
- 策略引擎和风控管理
- 与前端 WebSocket 通信
- Lighter 交易执行
- 数据日志记录 (用于 Claude Code 分析)
- Telegram 实时推送
"""
import asyncio
import argparse
import signal
import logging
import time
from decimal import Decimal
from typing import Dict, Any, Optional

from config import load_config, Config
from server.websocket_server import WebSocketServer
from exchanges.lighter_client import LighterClient
from strategy.arbitrage_engine import ArbitrageEngine, ArbitrageSignal
from strategy.order_book_manager import OrderBookManager
from strategy.position_manager import PositionManager
from risk.risk_manager import RiskManager
from risk.latency_monitor import LatencyMonitor
from utils.logger import setup_logging
from utils.data_logger import DataLogger
from utils.telegram_bot import TelegramBot, TelegramConfig

logger = logging.getLogger(__name__)


class ArbitrageSystem:
    """跨交易所套利系统主类"""

    def __init__(self, config: Config):
        self.config = config
        self.stop_flag = False

        # WebSocket 服务器 (与前端通信)
        self.ws_server = WebSocketServer(
            host=config.server.host,
            port=config.server.port
        )

        # Lighter 客户端
        self.lighter_client = LighterClient({
            'base_url': config.lighter.base_url,
            'ws_url': config.lighter.ws_url,
            'api_key_private_key': config.lighter.api_key_private_key,
            'account_index': config.lighter.account_index,
            'api_key_index': config.lighter.api_key_index,
            'market_index': config.lighter.market_index,
            'tick_size': config.lighter.tick_size
        })

        # 订单簿管理
        self.order_book_manager = OrderBookManager()

        # 仓位管理
        self.position_manager = PositionManager(
            config.strategy.ticker,
            self.lighter_client
        )

        # 策略引擎
        self.arbitrage_engine = ArbitrageEngine(
            self.order_book_manager,
            self.position_manager,
            {
                'order_quantity': str(config.strategy.order_quantity),
                'max_position': str(config.strategy.max_position),
                'long_threshold': str(config.strategy.long_threshold),
                'short_threshold': str(config.strategy.short_threshold),
                'threshold_offset': str(config.strategy.threshold_offset),
                'min_samples': config.strategy.min_samples,
                'min_signal_interval': config.strategy.min_signal_interval,
                'frontend_latency_ms': config.strategy.frontend_latency_ms,
                'tick_size': str(config.strategy.tick_size)
            }
        )

        # 风控管理
        self.risk_manager = RiskManager({
            'max_position': str(config.risk.max_position),
            'max_position_imbalance': str(config.risk.max_position_imbalance),
            'max_daily_loss': str(config.risk.max_daily_loss),
            'max_latency_ms': config.risk.max_latency_ms,
            'max_error_rate': config.risk.max_error_rate,
            'min_balance': str(config.risk.min_balance)
        })

        # 延迟监控
        self.latency_monitor = LatencyMonitor()

        # 数据日志记录器 (用于 Claude Code 分析)
        self.data_logger = DataLogger(
            log_dir=config.log_dir,
            ticker=config.strategy.ticker
        )

        # Telegram 机器人
        self.telegram_bot = TelegramBot(TelegramConfig(
            bot_token=config.telegram.bot_token,
            chat_id=config.telegram.group_id,
            account_label=config.telegram.account_label,
            enabled=config.telegram.enabled
        ))
        self.telegram_bot.system = self  # 设置系统引用

        # 设置风控熔断回调
        self.risk_manager.on_emergency = self._on_risk_emergency

        # 状态
        self.edgex_ready = False
        self.pending_orders: Dict[str, Dict] = {}
        self.last_status_log = 0
        self.last_bbo_log = 0
        self.sampling_notified = False

    async def start(self):
        """启动系统"""
        logger.info("=" * 60)
        logger.info("Starting EdgeX-Lighter Arbitrage System")
        logger.info("=" * 60)

        # 启动 Telegram 机器人
        await self.telegram_bot.start()

        # 启动 WebSocket 服务器
        await self.ws_server.start()

        # 设置消息回调
        self._setup_callbacks()

        # 初始化 Lighter 客户端
        logger.info("Initializing Lighter client...")
        await self.lighter_client.initialize()

        # 设置 Lighter 回调
        self.lighter_client.on_market_data = self._on_lighter_market_data
        self.lighter_client.on_order_update = self._on_lighter_order_update

        logger.info("Waiting for EdgeX frontend to connect...")
        logger.info(f"Frontend should connect to: ws://{self.config.server.host}:{self.config.server.port}")

        # 记录启动事件
        self.data_logger.log_event('system_start', {
            'ticker': self.config.strategy.ticker,
            'order_quantity': str(self.config.strategy.order_quantity),
            'max_position': str(self.config.strategy.max_position)
        })

        # 等待前端就绪
        while not self.edgex_ready and not self.stop_flag:
            await asyncio.sleep(1)

        if self.stop_flag:
            return

        # 同步 Lighter 仓位
        await self.position_manager.sync_lighter_position()

        # 启动策略引擎
        self.arbitrage_engine.start()

        logger.info("=" * 60)
        logger.info("System ready! Starting trading loop...")
        logger.info("=" * 60)

        # 启动交易循环
        await self._trading_loop()

    async def stop(self):
        """停止系统"""
        logger.info("Stopping arbitrage system...")
        self.stop_flag = True

        # 停止策略引擎
        self.arbitrage_engine.stop()

        # 导出数据用于分析
        export_file = self.data_logger.export_for_analysis()
        logger.info(f"Data exported: {export_file}")

        # 记录停止事件
        self.data_logger.log_event('system_stop', {
            'total_trades': self.data_logger.total_trades,
            'total_bbo_records': self.data_logger.total_bbo_records
        })

        # 尝试平仓
        await self._emergency_flatten()

        # 关闭数据日志
        self.data_logger.close()

        # 停止 Telegram 机器人
        await self.telegram_bot.stop()

        # 关闭 Lighter 客户端
        await self.lighter_client.close()

        # 停止 WebSocket 服务器
        await self.ws_server.stop()

        logger.info("System stopped")

    def _setup_callbacks(self):
        """设置消息回调"""
        self.ws_server.on_client_ready = self._on_frontend_ready
        self.ws_server.on_client_disconnect = self._on_frontend_disconnect
        self.ws_server.on_market_data = self._on_market_data
        self.ws_server.on_order_placed = self._on_order_placed
        self.ws_server.on_order_update = self._on_order_update

    async def _on_frontend_ready(self, client_id: str, data: Dict[str, Any]):
        """前端就绪回调"""
        exchange = data.get('exchange')
        if exchange == 'edgex':
            self.edgex_ready = True
            ticker = data.get('ticker', self.config.strategy.ticker)
            logger.info(f"EdgeX frontend connected: ticker={ticker}, contractId={data.get('contractId')}")

            # 记录事件
            self.data_logger.log_event('frontend_connected', data)

            # Telegram 通知
            await self.telegram_bot.send_frontend_connected(ticker)

    async def _on_frontend_disconnect(self, client_id: str, exchange: str):
        """前端断连回调"""
        if exchange == 'edgex':
            self.edgex_ready = False
            logger.warning("EdgeX frontend disconnected! Pausing strategy...")
            self.arbitrage_engine.pause()

            # 记录事件
            self.data_logger.log_event('frontend_disconnected', {'exchange': exchange})

            # 触发风控
            self.risk_manager.record_error('frontend_disconnect')

            # Telegram 告警
            await self.telegram_bot.send_error_alert(
                'frontend_disconnect',
                'EdgeX 前端断开连接，策略已暂停'
            )

    async def _on_market_data(self, exchange: str, data: Dict[str, Any]):
        """市场数据回调"""
        if exchange == 'edgex':
            best_bid = data.get('bestBid')
            best_ask = data.get('bestAsk')

            if best_bid is not None and best_ask is not None:
                self.order_book_manager.update_edgex_bbo(
                    Decimal(str(best_bid)),
                    Decimal(str(best_ask))
                )

    async def _on_lighter_market_data(self, exchange: str, data: Dict[str, Any]):
        """Lighter 市场数据回调"""
        best_bid = data.get('best_bid')
        best_ask = data.get('best_ask')

        if best_bid is not None and best_ask is not None:
            self.order_book_manager.update_lighter_bbo(
                Decimal(str(best_bid)),
                Decimal(str(best_ask))
            )

        # 定期记录 BBO (每秒)
        now = time.time()
        if now - self.last_bbo_log >= 1.0:
            edgex_bbo = self.order_book_manager.get_edgex_bbo()
            lighter_bbo = self.order_book_manager.get_lighter_bbo()
            engine_status = self.arbitrage_engine.get_status()

            self.data_logger.log_bbo(
                edgex_bid=edgex_bbo.get('bid'),
                edgex_ask=edgex_bbo.get('ask'),
                lighter_bid=lighter_bbo.get('bid'),
                lighter_ask=lighter_bbo.get('ask'),
                long_threshold=Decimal(str(engine_status['long_threshold'])),
                short_threshold=Decimal(str(engine_status['short_threshold']))
            )
            self.last_bbo_log = now

    async def _on_order_placed(self, data: Dict[str, Any]):
        """EdgeX 下单结果回调"""
        client_order_id = data.get('clientOrderId')
        success = data.get('success', False)
        latency = data.get('latency', 0)

        # 记录延迟
        self.latency_monitor.record('edgex_order', latency)

        if success:
            order_id = data.get('orderId')
            logger.info(f"EdgeX order placed: {order_id} (latency: {latency}ms)")

            # 更新待处理订单
            if client_order_id in self.pending_orders:
                self.pending_orders[client_order_id]['edgex_order_id'] = order_id
                self.pending_orders[client_order_id]['status'] = 'placed'
                self.pending_orders[client_order_id]['place_latency'] = latency
        else:
            error = data.get('error', 'Unknown error')
            logger.error(f"EdgeX order failed: {error}")

            # 记录错误
            self.risk_manager.record_error('order_failed')

            # 记录事件
            self.data_logger.log_event('order_failed', {
                'client_order_id': client_order_id,
                'error': error,
                'latency': latency
            })

            # 清理待处理订单
            if client_order_id in self.pending_orders:
                del self.pending_orders[client_order_id]

    async def _on_order_update(self, data: Dict[str, Any]):
        """EdgeX 订单状态更新回调"""
        client_order_id = data.get('clientOrderId')
        status = data.get('status')
        filled_size = data.get('filledSize', '0')
        side = data.get('side', '')
        price = data.get('price', '0')

        logger.info(f"EdgeX order update: {client_order_id} -> {status}, filled={filled_size}")

        if status == 'FILLED':
            # 获取待处理订单信息
            pending = self.pending_orders.get(client_order_id, {})
            signal = pending.get('signal')

            # EdgeX 订单成交,执行 Lighter 对冲
            filled_qty = Decimal(str(filled_size))

            # 更新 EdgeX 仓位
            delta = filled_qty if side == 'buy' else -filled_qty
            self.position_manager.update_edgex_position(delta)

            # 开始 Lighter 对冲计时
            hedge_start = time.time()

            # 执行 Lighter 对冲
            hedge_result = await self._execute_lighter_hedge(side, filled_qty, data)

            hedge_latency = int((time.time() - hedge_start) * 1000)

            # 记录交易到日志
            if signal:
                total_latency = pending.get('place_latency', 0) + hedge_latency

                self.data_logger.log_trade(
                    direction=signal.direction.value,
                    edgex_side=signal.edgex_side,
                    lighter_side=signal.lighter_side,
                    quantity=signal.quantity,
                    edgex_price=Decimal(str(price)),
                    lighter_price=signal.lighter_price,
                    spread=signal.spread,
                    threshold=Decimal(str(self.arbitrage_engine.long_threshold)),
                    edgex_order_id=pending.get('edgex_order_id', ''),
                    lighter_order_id=hedge_result.get('order_id', ''),
                    edgex_fill_time_ms=pending.get('place_latency', 0),
                    lighter_fill_time_ms=hedge_latency,
                    total_latency_ms=total_latency,
                    edgex_position_after=self.position_manager.get_edgex_position(),
                    lighter_position_after=self.position_manager.get_lighter_position(),
                    status='success' if hedge_result.get('success') else 'partial'
                )

                # Telegram 通知
                await self.telegram_bot.send_trade_notification(
                    direction=signal.direction.value,
                    quantity=str(signal.quantity),
                    edgex_price=str(price),
                    lighter_price=str(signal.lighter_price),
                    spread=str(signal.spread),
                    latency_ms=total_latency,
                    edgex_position=str(self.position_manager.get_edgex_position()),
                    lighter_position=str(self.position_manager.get_lighter_position())
                )

            # 记录成功交易
            self.risk_manager.record_trade(success=True)

            # 清理待处理订单
            if client_order_id in self.pending_orders:
                del self.pending_orders[client_order_id]

        elif status == 'CANCELED':
            logger.warning(f"EdgeX order canceled: {client_order_id}")

            # 记录事件
            self.data_logger.log_event('order_canceled', {'client_order_id': client_order_id})

            # 清理待处理订单
            if client_order_id in self.pending_orders:
                del self.pending_orders[client_order_id]

    async def _on_lighter_order_update(self, data: Dict[str, Any]):
        """Lighter 订单状态更新回调"""
        status = data.get('status')
        filled_size = data.get('filled_size', Decimal('0'))
        side = data.get('side', '')

        if status == 'FILLED':
            # 更新 Lighter 仓位
            delta = filled_size if side == 'buy' else -filled_size
            self.position_manager.update_lighter_position(delta)
            logger.info(f"Lighter order filled: {side} {filled_size}")

    async def _on_risk_emergency(self, emergency_type: str, data: Dict[str, Any]):
        """风控紧急回调"""
        if emergency_type == 'circuit_breaker':
            # 熔断触发
            logger.critical("Circuit breaker triggered!")

            self.data_logger.log_event('circuit_breaker', data)

            await self.telegram_bot.send_circuit_breaker_alert(
                error_count=data.get('error_count', 0),
                window_seconds=data.get('window', 60)
            )

    async def _execute_lighter_hedge(self, edgex_side: str, quantity: Decimal, order_data: Dict) -> Dict:
        """执行 Lighter 对冲订单"""
        lighter_bbo = self.lighter_client.get_bbo()

        if edgex_side == 'buy':
            # EdgeX 买入后, Lighter 卖出对冲
            hedge_side = 'sell'
            if lighter_bbo['bid'] is None:
                logger.error("Cannot hedge: no Lighter bid price")
                return {'success': False, 'error': 'No bid price'}
            # 用激进价格确保成交
            hedge_price = lighter_bbo['bid'] * Decimal('0.995')
        else:
            # EdgeX 卖出后, Lighter 买入对冲
            hedge_side = 'buy'
            if lighter_bbo['ask'] is None:
                logger.error("Cannot hedge: no Lighter ask price")
                return {'success': False, 'error': 'No ask price'}
            hedge_price = lighter_bbo['ask'] * Decimal('1.005')

        logger.info(f"Executing Lighter hedge: {hedge_side} {quantity} @ {hedge_price}")

        self.latency_monitor.start_timer('lighter_hedge')

        result = await self.lighter_client.place_market_order(
            hedge_side,
            quantity,
            hedge_price
        )

        latency = self.latency_monitor.stop_timer('lighter_hedge', 'lighter_order')

        if result.get('success'):
            logger.info(f"Lighter hedge success (latency: {latency:.0f}ms)")
        else:
            logger.error(f"Lighter hedge failed: {result.get('error')}")
            self.risk_manager.record_error('hedge_failed')

            # Telegram 告警
            await self.telegram_bot.send_error_alert(
                'hedge_failed',
                f'Lighter 对冲失败: {result.get("error")}',
                {'side': hedge_side, 'quantity': str(quantity)}
            )

        return result

    async def _trading_loop(self):
        """主交易循环"""
        cycle_interval = 1.0  # 1秒采样周期
        snapshot_interval = 60  # 60秒快照间隔
        last_snapshot = 0

        while not self.stop_flag:
            cycle_start = time.time()

            try:
                # 检查前端连接
                if not self.edgex_ready:
                    await asyncio.sleep(1)
                    continue

                # 检查订单簿是否就绪
                if not self.order_book_manager.is_ready():
                    await asyncio.sleep(0.5)
                    continue

                # 估算当前延迟
                estimated_latency = self.latency_monitor.estimate_frontend_latency()

                # 检查套利机会
                signal = self.arbitrage_engine.check_arbitrage_opportunity(
                    latency_ms=estimated_latency
                )

                # 检查采样是否完成
                engine_status = self.arbitrage_engine.get_status()
                if not engine_status['is_sampling'] and not self.sampling_notified:
                    self.sampling_notified = True
                    await self.telegram_bot.send_sampling_complete(
                        samples=engine_status['samples_collected'],
                        long_threshold=engine_status['long_threshold'],
                        short_threshold=engine_status['short_threshold']
                    )
                    self.data_logger.log_event('sampling_complete', engine_status)

                if signal:
                    # 风控检查
                    if self.risk_manager.check_signal(signal, self.position_manager):
                        await self._execute_signal(signal)
                    else:
                        logger.debug("Signal rejected by risk manager")

                # 定期记录快照
                if cycle_start - last_snapshot >= snapshot_interval:
                    self.data_logger.log_snapshot(
                        engine_status=engine_status,
                        position_status=self.position_manager.get_status(),
                        risk_status=self.risk_manager.get_status(),
                        latency_status=self.latency_monitor.get_status()
                    )
                    last_snapshot = cycle_start

                # 定期打印状态
                if cycle_start - self.last_status_log > 30:
                    self._log_status()
                    self.last_status_log = cycle_start

            except Exception as e:
                logger.error(f"Error in trading loop: {e}", exc_info=True)
                self.risk_manager.record_error('trading_loop')
                self.data_logger.log_event('error', {'type': 'trading_loop', 'error': str(e)})

            # 控制循环频率
            elapsed = time.time() - cycle_start
            await asyncio.sleep(max(0, cycle_interval - elapsed))

    async def _execute_signal(self, signal: ArbitrageSignal):
        """执行套利信号"""
        logger.info(f"Executing {signal.direction.value} signal: "
                   f"spread={signal.spread:.2f}, quantity={signal.quantity}")

        # 记录待处理订单
        self.pending_orders[signal.client_order_id] = {
            'signal': signal,
            'status': 'pending',
            'create_time': signal.timestamp
        }

        # 记录事件
        self.data_logger.log_event('signal_triggered', signal.to_dict())

        # 开始计时
        self.latency_monitor.start_timer(signal.client_order_id)

        # 发送下单指令到前端
        await self.ws_server.execute_order(
            side=signal.edgex_side,
            quantity=str(signal.quantity),
            price=str(signal.edgex_price),
            client_order_id=signal.client_order_id
        )

    async def _emergency_flatten(self):
        """紧急平仓"""
        logger.warning("Emergency flatten initiated")

        # 记录事件
        self.data_logger.log_event('emergency_flatten', {
            'edgex_position': str(self.position_manager.get_edgex_position()),
            'lighter_position': str(self.position_manager.get_lighter_position())
        })

        # 获取仓位
        edgex_pos = self.position_manager.get_edgex_position()
        lighter_pos = self.position_manager.get_lighter_position()

        # 通知前端平仓 EdgeX
        if abs(edgex_pos) > Decimal('0.0001'):
            side = 'sell' if edgex_pos > 0 else 'buy'
            await self.ws_server.emergency_close(side, str(abs(edgex_pos)))

        # 平仓 Lighter
        if abs(lighter_pos) > Decimal('0.0001'):
            await self.lighter_client.flatten_position()

        logger.info("Emergency flatten completed")

    def _log_status(self):
        """打印状态日志"""
        engine_status = self.arbitrage_engine.get_status()
        position_status = self.position_manager.get_status()
        risk_status = self.risk_manager.get_status()
        latency_status = self.latency_monitor.get_status()

        logger.info("-" * 40)
        logger.info(f"Samples: {engine_status['samples_collected']}/{engine_status['min_samples']}")
        logger.info(f"Thresholds: long={engine_status['long_threshold']:.2f}, short={engine_status['short_threshold']:.2f}")
        logger.info(f"Spreads: long={engine_status['current_long_spread']}, short={engine_status['current_short_spread']}")
        logger.info(f"Position: EdgeX={position_status['edgex']['size']:.6f}, Lighter={position_status['lighter']['size']:.6f}")
        logger.info(f"Net: {position_status['net_position']:.6f}, Signals: {engine_status['signal_count']}")
        logger.info(f"Latency score: {latency_status['score']:.0f}, Trades: {self.data_logger.total_trades}")
        logger.info("-" * 40)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='EdgeX-Lighter Arbitrage System')

    parser.add_argument('--ticker', type=str, default='BTC',
                       help='Trading pair (default: BTC)')
    parser.add_argument('--size', type=float, default=0.001,
                       help='Order quantity (default: 0.001)')
    parser.add_argument('--max-position', type=float, default=0.01,
                       help='Maximum position (default: 0.01)')
    parser.add_argument('--threshold-offset', type=float, default=10,
                       help='Threshold offset (default: 10)')
    parser.add_argument('--port', type=int, default=8765,
                       help='WebSocket server port (default: 8765)')
    parser.add_argument('--log-level', type=str, default='INFO',
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Log level (default: INFO)')

    return parser.parse_args()


async def main():
    """主函数"""
    args = parse_args()

    # 加载配置
    config = load_config()

    # 覆盖命令行参数
    config.strategy.ticker = args.ticker
    config.strategy.order_quantity = Decimal(str(args.size))
    config.strategy.max_position = Decimal(str(args.max_position))
    config.risk.max_position = config.strategy.max_position
    config.strategy.threshold_offset = Decimal(str(args.threshold_offset))
    config.server.port = args.port
    config.log_level = args.log_level

    # 配置日志
    setup_logging(level=config.log_level, log_dir=config.log_dir)

    # 创建系统实例
    system = ArbitrageSystem(config)

    # 信号处理
    loop = asyncio.get_event_loop()

    def handle_signal():
        logger.info("Received shutdown signal")
        asyncio.create_task(system.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    try:
        await system.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"System error: {e}", exc_info=True)
    finally:
        await system.stop()


if __name__ == '__main__':
    asyncio.run(main())
