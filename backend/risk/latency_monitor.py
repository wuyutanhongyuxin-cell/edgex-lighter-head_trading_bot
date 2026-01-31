"""
延迟监控模块
监控和统计各环节延迟
"""
import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from collections import deque
import statistics

logger = logging.getLogger(__name__)


@dataclass
class LatencySample:
    """延迟样本"""
    category: str
    latency_ms: float
    timestamp: float


class LatencyMonitor:
    """延迟监控器"""

    def __init__(self, max_samples: int = 100):
        self.max_samples = max_samples

        # 各类延迟样本
        self.samples: Dict[str, deque] = {
            'frontend_ws': deque(maxlen=max_samples),      # 前端 WebSocket RTT
            'edgex_order': deque(maxlen=max_samples),      # EdgeX 下单延迟
            'lighter_order': deque(maxlen=max_samples),    # Lighter 下单延迟
            'signal_to_fill': deque(maxlen=max_samples),   # 信号到成交总延迟
            'market_data': deque(maxlen=max_samples),      # 市场数据延迟
        }

        # 时间戳记录 (用于计算延迟)
        self._timestamps: Dict[str, float] = {}

    def record(self, category: str, latency_ms: float):
        """记录延迟样本"""
        if category not in self.samples:
            self.samples[category] = deque(maxlen=self.max_samples)

        self.samples[category].append(LatencySample(
            category=category,
            latency_ms=latency_ms,
            timestamp=time.time()
        ))

    def start_timer(self, timer_id: str):
        """开始计时"""
        self._timestamps[timer_id] = time.time()

    def stop_timer(self, timer_id: str, category: str) -> Optional[float]:
        """停止计时并记录"""
        start_time = self._timestamps.pop(timer_id, None)
        if start_time is None:
            return None

        latency_ms = (time.time() - start_time) * 1000
        self.record(category, latency_ms)
        return latency_ms

    def get_stats(self, category: str) -> Dict[str, float]:
        """获取某类延迟的统计信息"""
        samples = self.samples.get(category, [])

        if not samples:
            return {'count': 0, 'avg': 0, 'min': 0, 'max': 0, 'p50': 0, 'p95': 0, 'p99': 0}

        latencies = [s.latency_ms for s in samples]
        sorted_latencies = sorted(latencies)
        n = len(sorted_latencies)

        return {
            'count': n,
            'avg': statistics.mean(latencies),
            'min': min(latencies),
            'max': max(latencies),
            'p50': sorted_latencies[n // 2],
            'p95': sorted_latencies[int(n * 0.95)] if n > 1 else sorted_latencies[-1],
            'p99': sorted_latencies[int(n * 0.99)] if n > 1 else sorted_latencies[-1]
        }

    def get_recent_avg(self, category: str, count: int = 10) -> float:
        """获取最近 N 个样本的平均延迟"""
        samples = self.samples.get(category, [])
        if not samples:
            return 0

        recent = list(samples)[-count:]
        return statistics.mean(s.latency_ms for s in recent)

    def get_recent_max(self, category: str, count: int = 10) -> float:
        """获取最近 N 个样本的最大延迟"""
        samples = self.samples.get(category, [])
        if not samples:
            return 0

        recent = list(samples)[-count:]
        return max(s.latency_ms for s in recent)

    def is_acceptable(self, max_latency_ms: int = 500) -> bool:
        """检查所有延迟是否在可接受范围内"""
        for category, samples in self.samples.items():
            if samples:
                recent = list(samples)[-10:]
                if recent and max(s.latency_ms for s in recent) > max_latency_ms:
                    logger.warning(f"High latency detected in {category}")
                    return False
        return True

    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """获取所有类别的统计信息"""
        return {category: self.get_stats(category) for category in self.samples}

    def get_status(self) -> Dict:
        """获取监控状态"""
        all_stats = self.get_all_stats()

        # 计算综合延迟评分 (0-100, 100最好)
        score = 100
        for category, stats in all_stats.items():
            if stats['count'] > 0:
                # p95 超过 200ms 扣分
                if stats['p95'] > 200:
                    score -= min(20, (stats['p95'] - 200) / 10)
                # 最大值超过 500ms 扣分
                if stats['max'] > 500:
                    score -= min(30, (stats['max'] - 500) / 20)

        return {
            'score': max(0, score),
            'is_acceptable': self.is_acceptable(),
            'stats': all_stats
        }

    def clear(self):
        """清空所有样本"""
        for samples in self.samples.values():
            samples.clear()
        self._timestamps.clear()

    def estimate_frontend_latency(self) -> int:
        """估算当前前端延迟 (毫秒)"""
        # 综合多个指标估算
        ws_avg = self.get_recent_avg('frontend_ws', 5)
        order_avg = self.get_recent_avg('edgex_order', 5)

        if order_avg > 0:
            return int(order_avg)
        elif ws_avg > 0:
            return int(ws_avg * 2)  # WebSocket RTT 的两倍作为估算
        else:
            return 100  # 默认 100ms
