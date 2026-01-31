"""
套利策略引擎
核心决策模块 - 检测套利机会并生成交易信号
"""
import asyncio
import time
import logging
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum
from collections import deque

from .order_book_manager import OrderBookManager
from .position_manager import PositionManager

logger = logging.getLogger(__name__)


class ArbitrageDirection(Enum):
    """套利方向"""
    LONG = "long"    # EdgeX 买, Lighter 卖
    SHORT = "short"  # EdgeX 卖, Lighter 买
    NONE = "none"


@dataclass
class ArbitrageSignal:
    """套利信号"""
    direction: ArbitrageDirection
    edgex_side: str
    lighter_side: str
    edgex_price: Decimal
    lighter_price: Decimal
    spread: Decimal
    quantity: Decimal
    timestamp: float
    confidence: float = 1.0
    client_order_id: str = ''

    def to_dict(self) -> Dict:
        return {
            'direction': self.direction.value,
            'edgex_side': self.edgex_side,
            'lighter_side': self.lighter_side,
            'edgex_price': float(self.edgex_price),
            'lighter_price': float(self.lighter_price),
            'spread': float(self.spread),
            'quantity': float(self.quantity),
            'timestamp': self.timestamp,
            'confidence': self.confidence,
            'client_order_id': self.client_order_id
        }


class ArbitrageEngine:
    """套利策略引擎 - 后端核心决策中心"""

    def __init__(
        self,
        order_book_manager: OrderBookManager,
        position_manager: PositionManager,
        config: Dict[str, Any]
    ):
        self.order_book_manager = order_book_manager
        self.position_manager = position_manager
        self.config = config

        # 策略参数
        self.order_quantity = Decimal(str(config.get('order_quantity', '0.001')))
        self.max_position = Decimal(str(config.get('max_position', '0.01')))
        self.tick_size = Decimal(str(config.get('tick_size', '0.1')))

        # 阈值参数 (动态计算)
        self.base_long_threshold = Decimal(str(config.get('long_threshold', '10')))
        self.base_short_threshold = Decimal(str(config.get('short_threshold', '10')))
        self.threshold_offset = Decimal(str(config.get('threshold_offset', '10')))

        # 当前动态阈值
        self.long_threshold = self.base_long_threshold
        self.short_threshold = self.base_short_threshold

        # 历史数据 (用于计算动态阈值)
        self.min_samples = config.get('min_samples', 100)
        self.history_long: deque = deque(maxlen=self.min_samples * 2)
        self.history_short: deque = deque(maxlen=self.min_samples * 2)

        # 状态
        self.is_running = False
        self.is_sampling = True  # 采样阶段
        self.last_signal_time = 0
        self.min_signal_interval = config.get('min_signal_interval', 1.0)

        # 延迟补偿参数
        self.frontend_latency_estimate = config.get('frontend_latency_ms', 100) / 1000
        self.price_buffer = Decimal(str(config.get('price_buffer', '0.5')))

        # 统计
        self.signal_count = 0
        self.sample_count = 0

        # 回调
        self.on_signal: Optional[callable] = None

    def start(self):
        """启动引擎"""
        self.is_running = True
        logger.info("Arbitrage engine started")

    def stop(self):
        """停止引擎"""
        self.is_running = False
        logger.info("Arbitrage engine stopped")

    def pause(self):
        """暂停引擎"""
        self.is_running = False
        logger.info("Arbitrage engine paused")

    def resume(self):
        """恢复引擎"""
        self.is_running = True
        logger.info("Arbitrage engine resumed")

    def sample_spread(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        采样价差数据

        Returns:
            (long_spread, short_spread)
        """
        long_spread, short_spread = self.order_book_manager.get_spread()

        if long_spread is not None and short_spread is not None:
            self.history_long.append(float(long_spread))
            self.history_short.append(float(short_spread))
            self.sample_count += 1

            # 检查是否完成采样阶段
            if self.is_sampling and len(self.history_long) >= self.min_samples:
                self.is_sampling = False
                self._update_thresholds()
                logger.info(f"Sampling complete. Thresholds: long={self.long_threshold}, short={self.short_threshold}")

        return long_spread, short_spread

    def _update_thresholds(self):
        """更新动态阈值 (基于历史数据均值 + 偏移量)"""
        if len(self.history_long) >= self.min_samples:
            avg_long = sum(self.history_long) / len(self.history_long)
            avg_short = sum(self.history_short) / len(self.history_short)

            self.long_threshold = Decimal(str(avg_long)) + self.threshold_offset
            self.short_threshold = Decimal(str(avg_short)) + self.threshold_offset

            logger.debug(f"Thresholds updated: long={self.long_threshold:.2f}, short={self.short_threshold:.2f}")

    def calculate_adaptive_threshold(
        self,
        base_threshold: Decimal,
        latency_ms: int
    ) -> Decimal:
        """
        计算自适应阈值 (延迟越高,阈值越高)

        延迟每增加 50ms, 阈值增加 1 个 tick
        """
        latency_penalty = Decimal(latency_ms // 50) * self.tick_size
        return base_threshold + latency_penalty

    def check_arbitrage_opportunity(self, latency_ms: int = 100) -> Optional[ArbitrageSignal]:
        """
        检测套利机会

        Args:
            latency_ms: 预估的前端延迟 (毫秒)

        Returns:
            ArbitrageSignal if opportunity exists, None otherwise
        """
        if not self.is_running:
            return None

        # 采样价差
        long_spread, short_spread = self.sample_spread()

        if long_spread is None or short_spread is None:
            return None

        # 仍在采样阶段
        if self.is_sampling:
            return None

        # 定期更新阈值
        if self.sample_count % 10 == 0:
            self._update_thresholds()

        # 检查信号间隔
        current_time = time.time()
        if current_time - self.last_signal_time < self.min_signal_interval:
            return None

        # 获取当前仓位
        current_position = self.position_manager.get_edgex_position()

        # 获取 BBO
        edgex_bbo = self.order_book_manager.get_edgex_bbo()
        lighter_bbo = self.order_book_manager.get_lighter_bbo()

        # 计算自适应阈值
        adaptive_long_threshold = self.calculate_adaptive_threshold(self.long_threshold, latency_ms)
        adaptive_short_threshold = self.calculate_adaptive_threshold(self.short_threshold, latency_ms)

        # 检查做多机会: EdgeX 买入
        if (long_spread > adaptive_long_threshold and
            current_position < self.max_position):

            # 计算考虑延迟的价格 (比卖一低一点,确保 post_only 成功)
            adjusted_price = edgex_bbo['ask'] - self.tick_size

            # 计算置信度 (价差越大越有信心)
            confidence = min(1.0, float(long_spread - adaptive_long_threshold) / 10)

            signal = ArbitrageSignal(
                direction=ArbitrageDirection.LONG,
                edgex_side='buy',
                lighter_side='sell',
                edgex_price=adjusted_price,
                lighter_price=lighter_bbo['bid'],
                spread=long_spread,
                quantity=self.order_quantity,
                timestamp=current_time,
                confidence=confidence,
                client_order_id=f"arb_long_{int(current_time * 1000)}"
            )

            self.last_signal_time = current_time
            self.signal_count += 1

            logger.info(f"LONG signal: spread={long_spread:.2f} > threshold={adaptive_long_threshold:.2f}, "
                       f"edgex_price={adjusted_price}, lighter_price={lighter_bbo['bid']}")

            return signal

        # 检查做空机会: EdgeX 卖出
        if (short_spread > adaptive_short_threshold and
            current_position > -self.max_position):

            # 计算考虑延迟的价格 (比买一高一点)
            adjusted_price = edgex_bbo['bid'] + self.tick_size

            confidence = min(1.0, float(short_spread - adaptive_short_threshold) / 10)

            signal = ArbitrageSignal(
                direction=ArbitrageDirection.SHORT,
                edgex_side='sell',
                lighter_side='buy',
                edgex_price=adjusted_price,
                lighter_price=lighter_bbo['ask'],
                spread=short_spread,
                quantity=self.order_quantity,
                timestamp=current_time,
                confidence=confidence,
                client_order_id=f"arb_short_{int(current_time * 1000)}"
            )

            self.last_signal_time = current_time
            self.signal_count += 1

            logger.info(f"SHORT signal: spread={short_spread:.2f} > threshold={adaptive_short_threshold:.2f}, "
                       f"edgex_price={adjusted_price}, lighter_price={lighter_bbo['ask']}")

            return signal

        return None

    def get_status(self) -> Dict[str, Any]:
        """获取引擎状态"""
        long_spread, short_spread = self.order_book_manager.get_spread()

        return {
            'is_running': self.is_running,
            'is_sampling': self.is_sampling,
            'samples_collected': len(self.history_long),
            'min_samples': self.min_samples,
            'long_threshold': float(self.long_threshold),
            'short_threshold': float(self.short_threshold),
            'current_long_spread': float(long_spread) if long_spread else None,
            'current_short_spread': float(short_spread) if short_spread else None,
            'signal_count': self.signal_count,
            'sample_count': self.sample_count,
            'order_quantity': float(self.order_quantity),
            'max_position': float(self.max_position),
            'current_position': float(self.position_manager.get_edgex_position()),
            'net_position': float(self.position_manager.get_net_position())
        }

    def reset_sampling(self):
        """重置采样 (用于策略参数调整后)"""
        self.history_long.clear()
        self.history_short.clear()
        self.is_sampling = True
        self.sample_count = 0
        self.long_threshold = self.base_long_threshold
        self.short_threshold = self.base_short_threshold
        logger.info("Sampling reset")

    def update_config(self, config: Dict[str, Any]):
        """更新配置"""
        if 'order_quantity' in config:
            self.order_quantity = Decimal(str(config['order_quantity']))
        if 'max_position' in config:
            self.max_position = Decimal(str(config['max_position']))
        if 'threshold_offset' in config:
            self.threshold_offset = Decimal(str(config['threshold_offset']))
            self._update_thresholds()
        if 'min_signal_interval' in config:
            self.min_signal_interval = config['min_signal_interval']

        logger.info(f"Config updated: {config}")
