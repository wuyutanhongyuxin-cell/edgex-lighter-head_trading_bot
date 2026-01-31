"""
仓位管理模块
跟踪双交易所仓位状态
"""
import logging
from decimal import Decimal
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import threading

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """仓位信息"""
    size: Decimal = Decimal('0')
    entry_price: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')
    last_update: float = 0


class PositionManager:
    """仓位管理器 - 跟踪双交易所仓位"""

    def __init__(self, ticker: str, lighter_client=None):
        self.ticker = ticker
        self.lighter_client = lighter_client

        # EdgeX 仓位 (正数为多头, 负数为空头)
        self._edgex_position = Position()

        # Lighter 仓位
        self._lighter_position = Position()

        # 余额
        self._edgex_balance = Decimal('0')
        self._lighter_balance = Decimal('0')

        # 线程锁
        self._lock = threading.Lock()

        # 仓位变动历史 (用于检测异常)
        self._position_history = []
        self._max_history = 100

    # ==================== EdgeX 仓位 ====================

    def get_edgex_position(self) -> Decimal:
        """获取 EdgeX 仓位"""
        with self._lock:
            return self._edgex_position.size

    def update_edgex_position(self, delta: Decimal):
        """更新 EdgeX 仓位 (增量更新)"""
        with self._lock:
            old_size = self._edgex_position.size
            self._edgex_position.size += Decimal(str(delta))
            self._edgex_position.last_update = datetime.now().timestamp()

            # 记录历史
            self._record_position_change('edgex', old_size, self._edgex_position.size)

            logger.info(f"EdgeX position updated: {old_size} -> {self._edgex_position.size} (delta: {delta})")

    def set_edgex_position(self, size: Decimal, entry_price: Decimal = None):
        """设置 EdgeX 仓位 (绝对值)"""
        with self._lock:
            old_size = self._edgex_position.size
            self._edgex_position.size = Decimal(str(size))
            if entry_price is not None:
                self._edgex_position.entry_price = Decimal(str(entry_price))
            self._edgex_position.last_update = datetime.now().timestamp()

            self._record_position_change('edgex', old_size, self._edgex_position.size)

    def get_edgex_balance(self) -> Decimal:
        """获取 EdgeX 可用余额"""
        with self._lock:
            return self._edgex_balance

    def set_edgex_balance(self, balance: Decimal):
        """设置 EdgeX 余额"""
        with self._lock:
            self._edgex_balance = Decimal(str(balance))

    # ==================== Lighter 仓位 ====================

    def get_lighter_position(self) -> Decimal:
        """获取 Lighter 仓位"""
        with self._lock:
            return self._lighter_position.size

    def update_lighter_position(self, delta: Decimal):
        """更新 Lighter 仓位 (增量更新)"""
        with self._lock:
            old_size = self._lighter_position.size
            self._lighter_position.size += Decimal(str(delta))
            self._lighter_position.last_update = datetime.now().timestamp()

            self._record_position_change('lighter', old_size, self._lighter_position.size)

            logger.info(f"Lighter position updated: {old_size} -> {self._lighter_position.size} (delta: {delta})")

    def set_lighter_position(self, size: Decimal, entry_price: Decimal = None):
        """设置 Lighter 仓位 (绝对值)"""
        with self._lock:
            old_size = self._lighter_position.size
            self._lighter_position.size = Decimal(str(size))
            if entry_price is not None:
                self._lighter_position.entry_price = Decimal(str(entry_price))
            self._lighter_position.last_update = datetime.now().timestamp()

            self._record_position_change('lighter', old_size, self._lighter_position.size)

    def get_lighter_balance(self) -> Decimal:
        """获取 Lighter 可用余额"""
        with self._lock:
            return self._lighter_balance

    def set_lighter_balance(self, balance: Decimal):
        """设置 Lighter 余额"""
        with self._lock:
            self._lighter_balance = Decimal(str(balance))

    async def sync_lighter_position(self):
        """从 Lighter API 同步仓位"""
        if self.lighter_client:
            try:
                position = await self.lighter_client.get_position()
                self.set_lighter_position(position)
                logger.info(f"Synced Lighter position: {position}")
            except Exception as e:
                logger.error(f"Failed to sync Lighter position: {e}")

    # ==================== 综合查询 ====================

    def get_net_position(self) -> Decimal:
        """获取净仓位 (两边仓位之和,理想情况下应该接近0)"""
        with self._lock:
            return self._edgex_position.size + self._lighter_position.size

    def get_position_imbalance(self) -> Decimal:
        """获取仓位不平衡程度 (绝对值)"""
        return abs(self.get_net_position())

    def is_position_balanced(self, threshold: Decimal = Decimal('0.001')) -> bool:
        """检查仓位是否平衡"""
        return self.get_position_imbalance() <= threshold

    def get_total_exposure(self) -> Decimal:
        """获取总敞口 (两边仓位绝对值之和的一半)"""
        with self._lock:
            return (abs(self._edgex_position.size) + abs(self._lighter_position.size)) / 2

    def get_status(self) -> Dict:
        """获取仓位状态"""
        with self._lock:
            return {
                'edgex': {
                    'size': float(self._edgex_position.size),
                    'entry_price': float(self._edgex_position.entry_price),
                    'balance': float(self._edgex_balance),
                    'last_update': self._edgex_position.last_update
                },
                'lighter': {
                    'size': float(self._lighter_position.size),
                    'entry_price': float(self._lighter_position.entry_price),
                    'balance': float(self._lighter_balance),
                    'last_update': self._lighter_position.last_update
                },
                'net_position': float(self.get_net_position()),
                'position_imbalance': float(self.get_position_imbalance()),
                'is_balanced': self.is_position_balanced()
            }

    # ==================== 辅助方法 ====================

    def _record_position_change(self, exchange: str, old_size: Decimal, new_size: Decimal):
        """记录仓位变动"""
        self._position_history.append({
            'exchange': exchange,
            'old_size': float(old_size),
            'new_size': float(new_size),
            'timestamp': datetime.now().timestamp()
        })

        # 保持历史记录在限制范围内
        if len(self._position_history) > self._max_history:
            self._position_history = self._position_history[-self._max_history:]

    def get_recent_changes(self, limit: int = 10) -> list:
        """获取最近的仓位变动"""
        return self._position_history[-limit:]

    def reset(self):
        """重置所有仓位 (用于紧急情况)"""
        with self._lock:
            self._edgex_position = Position()
            self._lighter_position = Position()
            self._position_history.clear()
            logger.warning("Position manager reset!")
