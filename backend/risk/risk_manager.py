"""
风控管理模块
负责交易风险控制和紧急处理
"""
import logging
import time
from decimal import Decimal
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """风控配置"""
    max_position: Decimal = Decimal('0.01')
    max_position_imbalance: Decimal = Decimal('0.005')
    max_daily_loss: Decimal = Decimal('100')
    max_latency_ms: int = 500
    max_error_rate: float = 0.1
    min_balance: Decimal = Decimal('10')
    max_order_retry: int = 15
    circuit_breaker_threshold: int = 10
    circuit_breaker_window: int = 60


class RiskManager:
    """风控管理器"""

    def __init__(self, config: Dict[str, Any] = None):
        config = config or {}

        self.config = RiskConfig(
            max_position=Decimal(str(config.get('max_position', '0.01'))),
            max_position_imbalance=Decimal(str(config.get('max_position_imbalance', '0.005'))),
            max_daily_loss=Decimal(str(config.get('max_daily_loss', '100'))),
            max_latency_ms=config.get('max_latency_ms', 500),
            max_error_rate=config.get('max_error_rate', 0.1),
            min_balance=Decimal(str(config.get('min_balance', '10'))),
            max_order_retry=config.get('max_order_retry', 15),
            circuit_breaker_threshold=config.get('circuit_breaker_threshold', 10),
            circuit_breaker_window=config.get('circuit_breaker_window', 60)
        )

        # 状态追踪
        self.daily_pnl = Decimal('0')
        self.trade_count = 0
        self.error_count = 0
        self.last_error_time = 0

        # 错误记录 (用于熔断)
        self.error_history: deque = deque(maxlen=100)

        # 熔断状态
        self.circuit_breaker_triggered = False
        self.circuit_breaker_time = 0

        # 回调
        self.on_risk_alert: Optional[Callable] = None
        self.on_emergency: Optional[Callable] = None

    def check_signal(self, signal: Any, position_manager: Any = None) -> bool:
        """
        检查交易信号是否通过风控

        Args:
            signal: ArbitrageSignal 对象
            position_manager: PositionManager 对象

        Returns:
            bool: 是否通过风控检查
        """
        checks = []

        # 1. 检查熔断状态
        if self._check_circuit_breaker():
            logger.warning("Signal rejected: circuit breaker active")
            return False

        # 2. 检查仓位限制
        if position_manager:
            check, msg = self._check_position_limit(signal, position_manager)
            checks.append((check, msg))
            if not check:
                logger.warning(f"Signal rejected: {msg}")
                return False

        # 3. 检查仓位平衡
        if position_manager:
            check, msg = self._check_position_imbalance(position_manager)
            checks.append((check, msg))
            if not check:
                logger.warning(f"Signal rejected: {msg}")
                return False

        # 4. 检查日亏损限制
        check, msg = self._check_daily_loss_limit()
        checks.append((check, msg))
        if not check:
            logger.warning(f"Signal rejected: {msg}")
            return False

        # 5. 检查错误率
        check, msg = self._check_error_rate()
        checks.append((check, msg))
        if not check:
            logger.warning(f"Signal rejected: {msg}")
            return False

        return True

    def _check_circuit_breaker(self) -> bool:
        """检查熔断状态"""
        if self.circuit_breaker_triggered:
            # 检查是否可以恢复 (5分钟后自动恢复)
            if time.time() - self.circuit_breaker_time > 300:
                self.circuit_breaker_triggered = False
                logger.info("Circuit breaker reset")
                return False
            return True
        return False

    def _check_position_limit(self, signal: Any, position_manager: Any) -> tuple:
        """检查仓位限制"""
        current_position = position_manager.get_edgex_position()
        quantity = signal.quantity

        if signal.edgex_side == 'buy':
            new_position = current_position + quantity
            if new_position > self.config.max_position:
                return False, f"Would exceed max position: {new_position} > {self.config.max_position}"
        else:
            new_position = current_position - quantity
            if new_position < -self.config.max_position:
                return False, f"Would exceed max short position: {new_position} < -{self.config.max_position}"

        return True, "Position limit OK"

    def _check_position_imbalance(self, position_manager: Any) -> tuple:
        """检查仓位平衡"""
        imbalance = position_manager.get_position_imbalance()

        if imbalance > self.config.max_position_imbalance:
            return False, f"Position imbalance too high: {imbalance} > {self.config.max_position_imbalance}"

        return True, "Position balance OK"

    def _check_daily_loss_limit(self) -> tuple:
        """检查日亏损限制"""
        if self.daily_pnl < -self.config.max_daily_loss:
            return False, f"Daily loss limit exceeded: {self.daily_pnl} < -{self.config.max_daily_loss}"
        return True, "Daily loss OK"

    def _check_error_rate(self) -> tuple:
        """检查错误率"""
        if self.trade_count > 10:
            error_rate = self.error_count / self.trade_count
            if error_rate > self.config.max_error_rate:
                return False, f"Error rate too high: {error_rate:.2%} > {self.config.max_error_rate:.2%}"
        return True, "Error rate OK"

    def check_latency(self, latency_ms: int) -> bool:
        """检查延迟是否可接受"""
        if latency_ms > self.config.max_latency_ms:
            logger.warning(f"Latency too high: {latency_ms}ms > {self.config.max_latency_ms}ms")
            return False
        return True

    def check_balance(self, edgex_balance: Decimal, lighter_balance: Decimal) -> bool:
        """检查余额是否充足"""
        if edgex_balance < self.config.min_balance:
            logger.warning(f"EdgeX balance too low: {edgex_balance}")
            return False
        if lighter_balance < self.config.min_balance:
            logger.warning(f"Lighter balance too low: {lighter_balance}")
            return False
        return True

    def record_trade(self, success: bool, pnl: Decimal = Decimal('0')):
        """记录交易结果"""
        self.trade_count += 1
        self.daily_pnl += pnl

        if not success:
            self.error_count += 1
            self.error_history.append(time.time())
            self._check_for_circuit_breaker()

    def record_error(self, error_type: str = 'general'):
        """记录错误"""
        self.error_count += 1
        self.last_error_time = time.time()
        self.error_history.append(time.time())

        self._check_for_circuit_breaker()

    def _check_for_circuit_breaker(self):
        """检查是否需要触发熔断"""
        now = time.time()
        window_start = now - self.config.circuit_breaker_window

        # 统计窗口内的错误数
        recent_errors = sum(1 for t in self.error_history if t > window_start)

        if recent_errors >= self.config.circuit_breaker_threshold:
            self.circuit_breaker_triggered = True
            self.circuit_breaker_time = now
            logger.critical(f"Circuit breaker triggered! {recent_errors} errors in {self.config.circuit_breaker_window}s")

            if self.on_emergency:
                try:
                    self.on_emergency('circuit_breaker', {
                        'error_count': recent_errors,
                        'window': self.config.circuit_breaker_window
                    })
                except Exception as e:
                    logger.error(f"Error in emergency callback: {e}")

    def reset_daily_stats(self):
        """重置每日统计"""
        self.daily_pnl = Decimal('0')
        self.trade_count = 0
        self.error_count = 0
        self.error_history.clear()
        logger.info("Daily stats reset")

    def reset_circuit_breaker(self):
        """手动重置熔断器"""
        self.circuit_breaker_triggered = False
        self.circuit_breaker_time = 0
        self.error_history.clear()
        logger.info("Circuit breaker manually reset")

    def get_status(self) -> Dict[str, Any]:
        """获取风控状态"""
        now = time.time()
        window_start = now - self.config.circuit_breaker_window
        recent_errors = sum(1 for t in self.error_history if t > window_start)

        return {
            'circuit_breaker_triggered': self.circuit_breaker_triggered,
            'daily_pnl': float(self.daily_pnl),
            'trade_count': self.trade_count,
            'error_count': self.error_count,
            'error_rate': self.error_count / self.trade_count if self.trade_count > 0 else 0,
            'recent_errors': recent_errors,
            'config': {
                'max_position': float(self.config.max_position),
                'max_daily_loss': float(self.config.max_daily_loss),
                'max_latency_ms': self.config.max_latency_ms,
                'max_error_rate': self.config.max_error_rate
            }
        }
