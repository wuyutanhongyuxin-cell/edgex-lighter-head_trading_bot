"""
订单簿管理模块
维护 EdgeX 和 Lighter 的订单簿数据
"""
import logging
from decimal import Decimal
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import threading

logger = logging.getLogger(__name__)


@dataclass
class BBO:
    """Best Bid/Offer 数据"""
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    bid_size: Optional[Decimal] = None
    ask_size: Optional[Decimal] = None
    timestamp: float = 0


class OrderBookManager:
    """订单簿管理器 - 维护双交易所订单簿"""

    def __init__(self):
        # EdgeX 订单簿
        self._edgex_bbo = BBO()
        self._edgex_order_book: Dict[str, Dict[Decimal, Decimal]] = {
            'bids': {},
            'asks': {}
        }

        # Lighter 订单簿
        self._lighter_bbo = BBO()
        self._lighter_order_book: Dict[str, Dict[Decimal, Decimal]] = {
            'bids': {},
            'asks': {}
        }

        # 线程安全锁
        self._edgex_lock = threading.Lock()
        self._lighter_lock = threading.Lock()

        # 状态标记
        self.edgex_ready = False
        self.lighter_ready = False

    # ==================== EdgeX 订单簿 ====================

    def update_edgex_bbo(
        self,
        bid: Optional[Decimal],
        ask: Optional[Decimal],
        bid_size: Optional[Decimal] = None,
        ask_size: Optional[Decimal] = None
    ):
        """更新 EdgeX BBO (从前端 WebSocket 接收)"""
        with self._edgex_lock:
            if bid is not None:
                self._edgex_bbo.bid = Decimal(str(bid))
            if ask is not None:
                self._edgex_bbo.ask = Decimal(str(ask))
            if bid_size is not None:
                self._edgex_bbo.bid_size = Decimal(str(bid_size))
            if ask_size is not None:
                self._edgex_bbo.ask_size = Decimal(str(ask_size))
            self._edgex_bbo.timestamp = datetime.now().timestamp()
            self.edgex_ready = True

    def update_edgex_order_book(
        self,
        bids: list,
        asks: list,
        is_snapshot: bool = False
    ):
        """更新 EdgeX 完整订单簿"""
        with self._edgex_lock:
            if is_snapshot:
                self._edgex_order_book['bids'].clear()
                self._edgex_order_book['asks'].clear()

            # 更新 bids
            for bid in bids:
                price = Decimal(str(bid.get('price', bid[0]) if isinstance(bid, dict) else bid[0]))
                size = Decimal(str(bid.get('size', bid[1]) if isinstance(bid, dict) else bid[1]))
                if size > 0:
                    self._edgex_order_book['bids'][price] = size
                else:
                    self._edgex_order_book['bids'].pop(price, None)

            # 更新 asks
            for ask in asks:
                price = Decimal(str(ask.get('price', ask[0]) if isinstance(ask, dict) else ask[0]))
                size = Decimal(str(ask.get('size', ask[1]) if isinstance(ask, dict) else ask[1]))
                if size > 0:
                    self._edgex_order_book['asks'][price] = size
                else:
                    self._edgex_order_book['asks'].pop(price, None)

            # 更新 BBO
            self._update_edgex_bbo_from_book()
            self.edgex_ready = True

    def _update_edgex_bbo_from_book(self):
        """从订单簿更新 BBO"""
        if self._edgex_order_book['bids']:
            best_bid = max(self._edgex_order_book['bids'].keys())
            self._edgex_bbo.bid = best_bid
            self._edgex_bbo.bid_size = self._edgex_order_book['bids'][best_bid]

        if self._edgex_order_book['asks']:
            best_ask = min(self._edgex_order_book['asks'].keys())
            self._edgex_bbo.ask = best_ask
            self._edgex_bbo.ask_size = self._edgex_order_book['asks'][best_ask]

        self._edgex_bbo.timestamp = datetime.now().timestamp()

    def get_edgex_bbo(self) -> Dict[str, Optional[Decimal]]:
        """获取 EdgeX BBO"""
        with self._edgex_lock:
            return {
                'bid': self._edgex_bbo.bid,
                'ask': self._edgex_bbo.ask,
                'bid_size': self._edgex_bbo.bid_size,
                'ask_size': self._edgex_bbo.ask_size,
                'timestamp': self._edgex_bbo.timestamp
            }

    # ==================== Lighter 订单簿 ====================

    def update_lighter_bbo(
        self,
        bid: Optional[Decimal],
        ask: Optional[Decimal],
        bid_size: Optional[Decimal] = None,
        ask_size: Optional[Decimal] = None
    ):
        """更新 Lighter BBO"""
        with self._lighter_lock:
            if bid is not None:
                self._lighter_bbo.bid = Decimal(str(bid))
            if ask is not None:
                self._lighter_bbo.ask = Decimal(str(ask))
            if bid_size is not None:
                self._lighter_bbo.bid_size = Decimal(str(bid_size))
            if ask_size is not None:
                self._lighter_bbo.ask_size = Decimal(str(ask_size))
            self._lighter_bbo.timestamp = datetime.now().timestamp()
            self.lighter_ready = True

    def update_lighter_order_book(
        self,
        bids: list,
        asks: list,
        is_snapshot: bool = False
    ):
        """更新 Lighter 完整订单簿"""
        with self._lighter_lock:
            if is_snapshot:
                self._lighter_order_book['bids'].clear()
                self._lighter_order_book['asks'].clear()

            # 更新 bids
            for bid in bids:
                if isinstance(bid, (list, tuple)) and len(bid) >= 2:
                    price = Decimal(str(bid[0]))
                    size = Decimal(str(bid[1]))
                elif isinstance(bid, dict):
                    price = Decimal(str(bid.get('price', 0)))
                    size = Decimal(str(bid.get('size', 0)))
                else:
                    continue

                if size > 0:
                    self._lighter_order_book['bids'][price] = size
                else:
                    self._lighter_order_book['bids'].pop(price, None)

            # 更新 asks
            for ask in asks:
                if isinstance(ask, (list, tuple)) and len(ask) >= 2:
                    price = Decimal(str(ask[0]))
                    size = Decimal(str(ask[1]))
                elif isinstance(ask, dict):
                    price = Decimal(str(ask.get('price', 0)))
                    size = Decimal(str(ask.get('size', 0)))
                else:
                    continue

                if size > 0:
                    self._lighter_order_book['asks'][price] = size
                else:
                    self._lighter_order_book['asks'].pop(price, None)

            # 更新 BBO
            self._update_lighter_bbo_from_book()
            self.lighter_ready = True

    def _update_lighter_bbo_from_book(self):
        """从订单簿更新 BBO"""
        if self._lighter_order_book['bids']:
            best_bid = max(self._lighter_order_book['bids'].keys())
            self._lighter_bbo.bid = best_bid
            self._lighter_bbo.bid_size = self._lighter_order_book['bids'][best_bid]

        if self._lighter_order_book['asks']:
            best_ask = min(self._lighter_order_book['asks'].keys())
            self._lighter_bbo.ask = best_ask
            self._lighter_bbo.ask_size = self._lighter_order_book['asks'][best_ask]

        self._lighter_bbo.timestamp = datetime.now().timestamp()

    def get_lighter_bbo(self) -> Dict[str, Optional[Decimal]]:
        """获取 Lighter BBO"""
        with self._lighter_lock:
            return {
                'bid': self._lighter_bbo.bid,
                'ask': self._lighter_bbo.ask,
                'bid_size': self._lighter_bbo.bid_size,
                'ask_size': self._lighter_bbo.ask_size,
                'timestamp': self._lighter_bbo.timestamp
            }

    # ==================== 辅助方法 ====================

    def is_ready(self) -> bool:
        """检查两边订单簿是否都就绪"""
        return self.edgex_ready and self.lighter_ready

    def get_spread(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        计算跨交易所价差

        Returns:
            (long_spread, short_spread)
            long_spread: Lighter买一 - EdgeX卖一 (在EdgeX做多的收益)
            short_spread: EdgeX买一 - Lighter卖一 (在EdgeX做空的收益)
        """
        edgex_bbo = self.get_edgex_bbo()
        lighter_bbo = self.get_lighter_bbo()

        if not all([edgex_bbo['bid'], edgex_bbo['ask'],
                    lighter_bbo['bid'], lighter_bbo['ask']]):
            return None, None

        # 做多 EdgeX: 在 EdgeX 买入, 在 Lighter 卖出
        # 收益 = Lighter买一 - EdgeX卖一
        long_spread = lighter_bbo['bid'] - edgex_bbo['ask']

        # 做空 EdgeX: 在 EdgeX 卖出, 在 Lighter 买入
        # 收益 = EdgeX买一 - Lighter卖一
        short_spread = edgex_bbo['bid'] - lighter_bbo['ask']

        return long_spread, short_spread

    def get_status(self) -> Dict:
        """获取订单簿状态"""
        edgex_bbo = self.get_edgex_bbo()
        lighter_bbo = self.get_lighter_bbo()
        long_spread, short_spread = self.get_spread()

        return {
            'edgex_ready': self.edgex_ready,
            'lighter_ready': self.lighter_ready,
            'edgex_bbo': {
                'bid': float(edgex_bbo['bid']) if edgex_bbo['bid'] else None,
                'ask': float(edgex_bbo['ask']) if edgex_bbo['ask'] else None
            },
            'lighter_bbo': {
                'bid': float(lighter_bbo['bid']) if lighter_bbo['bid'] else None,
                'ask': float(lighter_bbo['ask']) if lighter_bbo['ask'] else None
            },
            'long_spread': float(long_spread) if long_spread else None,
            'short_spread': float(short_spread) if short_spread else None
        }
