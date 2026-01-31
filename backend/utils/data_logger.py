"""
数据日志记录模块
记录详细的交易数据、BBO 历史、策略状态等，用于后续分析优化
"""
import os
import csv
import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
import threading
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """交易记录"""
    timestamp: float
    datetime_str: str
    direction: str  # long/short
    edgex_side: str
    lighter_side: str
    quantity: str
    edgex_price: str
    lighter_price: str
    spread: str
    threshold: str
    edgex_order_id: str
    lighter_order_id: str
    edgex_fill_time_ms: int
    lighter_fill_time_ms: int
    total_latency_ms: int
    pnl_estimate: str
    edgex_position_after: str
    lighter_position_after: str
    net_position_after: str
    status: str  # success/partial/failed


@dataclass
class BBORecord:
    """BBO 记录"""
    timestamp: float
    datetime_str: str
    edgex_bid: str
    edgex_ask: str
    lighter_bid: str
    lighter_ask: str
    long_spread: str
    short_spread: str
    long_threshold: str
    short_threshold: str


@dataclass
class StrategySnapshot:
    """策略快照"""
    timestamp: float
    datetime_str: str
    is_running: bool
    is_sampling: bool
    samples_collected: int
    long_threshold: str
    short_threshold: str
    current_long_spread: str
    current_short_spread: str
    edgex_position: str
    lighter_position: str
    net_position: str
    signal_count: int
    trade_count: int
    success_count: int
    error_count: int
    daily_pnl: str
    avg_latency_ms: float
    latency_p95_ms: float


class DataLogger:
    """
    数据日志记录器
    记录所有交易数据、市场数据和策略状态
    用于后续 Claude Code 分析优化
    """

    def __init__(self, log_dir: str = 'logs', ticker: str = 'BTC'):
        self.log_dir = Path(log_dir)
        self.ticker = ticker
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 生成文件名前缀
        self.session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.file_prefix = f"{ticker}_{self.session_id}"

        # CSV 文件路径
        self.trades_file = self.log_dir / f"{self.file_prefix}_trades.csv"
        self.bbo_file = self.log_dir / f"{self.file_prefix}_bbo.csv"
        self.snapshots_file = self.log_dir / f"{self.file_prefix}_snapshots.csv"
        self.events_file = self.log_dir / f"{self.file_prefix}_events.jsonl"

        # 缓冲区 (减少 IO)
        self._trades_buffer: List[TradeRecord] = []
        self._bbo_buffer: List[BBORecord] = []
        self._snapshots_buffer: List[StrategySnapshot] = []
        self._buffer_size = 100
        self._flush_interval = 30  # 秒

        # 文件句柄
        self._trades_writer = None
        self._bbo_writer = None
        self._snapshots_writer = None
        self._events_file_handle = None

        # 线程锁
        self._lock = threading.Lock()

        # 统计
        self.total_trades = 0
        self.total_bbo_records = 0
        self.total_snapshots = 0

        # 最近数据缓存 (用于快速查询)
        self.recent_trades: deque = deque(maxlen=100)
        self.recent_bbo: deque = deque(maxlen=1000)

        # 初始化文件
        self._init_files()

        # 启动定时刷新
        self._flush_timer = None
        self._start_flush_timer()

        logger.info(f"DataLogger initialized: {self.log_dir}")

    def _init_files(self):
        """初始化 CSV 文件头"""
        # Trades CSV
        if not self.trades_file.exists():
            with open(self.trades_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'datetime', 'direction', 'edgex_side', 'lighter_side',
                    'quantity', 'edgex_price', 'lighter_price', 'spread', 'threshold',
                    'edgex_order_id', 'lighter_order_id', 'edgex_fill_time_ms',
                    'lighter_fill_time_ms', 'total_latency_ms', 'pnl_estimate',
                    'edgex_position_after', 'lighter_position_after', 'net_position_after', 'status'
                ])

        # BBO CSV
        if not self.bbo_file.exists():
            with open(self.bbo_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'datetime', 'edgex_bid', 'edgex_ask',
                    'lighter_bid', 'lighter_ask', 'long_spread', 'short_spread',
                    'long_threshold', 'short_threshold'
                ])

        # Snapshots CSV
        if not self.snapshots_file.exists():
            with open(self.snapshots_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'datetime', 'is_running', 'is_sampling', 'samples_collected',
                    'long_threshold', 'short_threshold', 'current_long_spread', 'current_short_spread',
                    'edgex_position', 'lighter_position', 'net_position',
                    'signal_count', 'trade_count', 'success_count', 'error_count',
                    'daily_pnl', 'avg_latency_ms', 'latency_p95_ms'
                ])

    def _start_flush_timer(self):
        """启动定时刷新"""
        def flush_loop():
            while True:
                time.sleep(self._flush_interval)
                self.flush()

        self._flush_timer = threading.Thread(target=flush_loop, daemon=True)
        self._flush_timer.start()

    def log_trade(
        self,
        direction: str,
        edgex_side: str,
        lighter_side: str,
        quantity: Decimal,
        edgex_price: Decimal,
        lighter_price: Decimal,
        spread: Decimal,
        threshold: Decimal,
        edgex_order_id: str = '',
        lighter_order_id: str = '',
        edgex_fill_time_ms: int = 0,
        lighter_fill_time_ms: int = 0,
        total_latency_ms: int = 0,
        pnl_estimate: Decimal = Decimal('0'),
        edgex_position_after: Decimal = Decimal('0'),
        lighter_position_after: Decimal = Decimal('0'),
        status: str = 'success'
    ):
        """记录交易"""
        now = time.time()
        record = TradeRecord(
            timestamp=now,
            datetime_str=datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            direction=direction,
            edgex_side=edgex_side,
            lighter_side=lighter_side,
            quantity=str(quantity),
            edgex_price=str(edgex_price),
            lighter_price=str(lighter_price),
            spread=str(spread),
            threshold=str(threshold),
            edgex_order_id=edgex_order_id,
            lighter_order_id=lighter_order_id,
            edgex_fill_time_ms=edgex_fill_time_ms,
            lighter_fill_time_ms=lighter_fill_time_ms,
            total_latency_ms=total_latency_ms,
            pnl_estimate=str(pnl_estimate),
            edgex_position_after=str(edgex_position_after),
            lighter_position_after=str(lighter_position_after),
            net_position_after=str(edgex_position_after + lighter_position_after),
            status=status
        )

        with self._lock:
            self._trades_buffer.append(record)
            self.recent_trades.append(record)
            self.total_trades += 1

        if len(self._trades_buffer) >= self._buffer_size:
            self.flush_trades()

        logger.info(f"Trade logged: {direction} {quantity} @ spread={spread}")

    def log_bbo(
        self,
        edgex_bid: Optional[Decimal],
        edgex_ask: Optional[Decimal],
        lighter_bid: Optional[Decimal],
        lighter_ask: Optional[Decimal],
        long_spread: Optional[Decimal] = None,
        short_spread: Optional[Decimal] = None,
        long_threshold: Decimal = Decimal('10'),
        short_threshold: Decimal = Decimal('10')
    ):
        """记录 BBO 数据"""
        now = time.time()

        # 计算价差
        if long_spread is None and lighter_bid and edgex_ask:
            long_spread = lighter_bid - edgex_ask
        if short_spread is None and edgex_bid and lighter_ask:
            short_spread = edgex_bid - lighter_ask

        record = BBORecord(
            timestamp=now,
            datetime_str=datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            edgex_bid=str(edgex_bid) if edgex_bid else '',
            edgex_ask=str(edgex_ask) if edgex_ask else '',
            lighter_bid=str(lighter_bid) if lighter_bid else '',
            lighter_ask=str(lighter_ask) if lighter_ask else '',
            long_spread=str(long_spread) if long_spread else '',
            short_spread=str(short_spread) if short_spread else '',
            long_threshold=str(long_threshold),
            short_threshold=str(short_threshold)
        )

        with self._lock:
            self._bbo_buffer.append(record)
            self.recent_bbo.append(record)
            self.total_bbo_records += 1

        if len(self._bbo_buffer) >= self._buffer_size:
            self.flush_bbo()

    def log_snapshot(
        self,
        engine_status: Dict[str, Any],
        position_status: Dict[str, Any],
        risk_status: Dict[str, Any],
        latency_status: Dict[str, Any]
    ):
        """记录策略快照"""
        now = time.time()

        # 获取延迟统计
        latency_stats = latency_status.get('stats', {})
        edgex_order_stats = latency_stats.get('edgex_order', {})

        record = StrategySnapshot(
            timestamp=now,
            datetime_str=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            is_running=engine_status.get('is_running', False),
            is_sampling=engine_status.get('is_sampling', True),
            samples_collected=engine_status.get('samples_collected', 0),
            long_threshold=str(engine_status.get('long_threshold', 10)),
            short_threshold=str(engine_status.get('short_threshold', 10)),
            current_long_spread=str(engine_status.get('current_long_spread', 0) or 0),
            current_short_spread=str(engine_status.get('current_short_spread', 0) or 0),
            edgex_position=str(position_status.get('edgex', {}).get('size', 0)),
            lighter_position=str(position_status.get('lighter', {}).get('size', 0)),
            net_position=str(position_status.get('net_position', 0)),
            signal_count=engine_status.get('signal_count', 0),
            trade_count=risk_status.get('trade_count', 0),
            success_count=risk_status.get('trade_count', 0) - risk_status.get('error_count', 0),
            error_count=risk_status.get('error_count', 0),
            daily_pnl=str(risk_status.get('daily_pnl', 0)),
            avg_latency_ms=edgex_order_stats.get('avg', 0),
            latency_p95_ms=edgex_order_stats.get('p95', 0)
        )

        with self._lock:
            self._snapshots_buffer.append(record)
            self.total_snapshots += 1

        if len(self._snapshots_buffer) >= 10:  # 快照频率较低
            self.flush_snapshots()

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """记录事件 (JSON Lines 格式)"""
        event = {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'type': event_type,
            'data': data
        }

        with self._lock:
            with open(self.events_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')

    def flush_trades(self):
        """刷新交易缓冲区"""
        with self._lock:
            if not self._trades_buffer:
                return

            with open(self.trades_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                for record in self._trades_buffer:
                    writer.writerow([
                        record.timestamp, record.datetime_str, record.direction,
                        record.edgex_side, record.lighter_side, record.quantity,
                        record.edgex_price, record.lighter_price, record.spread,
                        record.threshold, record.edgex_order_id, record.lighter_order_id,
                        record.edgex_fill_time_ms, record.lighter_fill_time_ms,
                        record.total_latency_ms, record.pnl_estimate,
                        record.edgex_position_after, record.lighter_position_after,
                        record.net_position_after, record.status
                    ])

            self._trades_buffer.clear()

    def flush_bbo(self):
        """刷新 BBO 缓冲区"""
        with self._lock:
            if not self._bbo_buffer:
                return

            with open(self.bbo_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                for record in self._bbo_buffer:
                    writer.writerow([
                        record.timestamp, record.datetime_str,
                        record.edgex_bid, record.edgex_ask,
                        record.lighter_bid, record.lighter_ask,
                        record.long_spread, record.short_spread,
                        record.long_threshold, record.short_threshold
                    ])

            self._bbo_buffer.clear()

    def flush_snapshots(self):
        """刷新快照缓冲区"""
        with self._lock:
            if not self._snapshots_buffer:
                return

            with open(self.snapshots_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                for record in self._snapshots_buffer:
                    writer.writerow([
                        record.timestamp, record.datetime_str, record.is_running,
                        record.is_sampling, record.samples_collected,
                        record.long_threshold, record.short_threshold,
                        record.current_long_spread, record.current_short_spread,
                        record.edgex_position, record.lighter_position, record.net_position,
                        record.signal_count, record.trade_count, record.success_count,
                        record.error_count, record.daily_pnl,
                        record.avg_latency_ms, record.latency_p95_ms
                    ])

            self._snapshots_buffer.clear()

    def flush(self):
        """刷新所有缓冲区"""
        self.flush_trades()
        self.flush_bbo()
        self.flush_snapshots()

    def get_summary(self) -> Dict[str, Any]:
        """获取数据摘要 (用于 Claude Code 分析)"""
        return {
            'session_id': self.session_id,
            'ticker': self.ticker,
            'log_dir': str(self.log_dir),
            'files': {
                'trades': str(self.trades_file),
                'bbo': str(self.bbo_file),
                'snapshots': str(self.snapshots_file),
                'events': str(self.events_file)
            },
            'statistics': {
                'total_trades': self.total_trades,
                'total_bbo_records': self.total_bbo_records,
                'total_snapshots': self.total_snapshots
            },
            'recent_trades': [asdict(t) for t in list(self.recent_trades)[-10:]]
        }

    def export_for_analysis(self) -> str:
        """导出数据用于 Claude Code 分析"""
        self.flush()

        export_data = {
            'summary': self.get_summary(),
            'sample_trades': [asdict(t) for t in list(self.recent_trades)[-50:]],
            'sample_bbo': [asdict(b) for b in list(self.recent_bbo)[-100:]],
            'analysis_hints': {
                'focus_areas': [
                    '延迟分布分析 (edgex_fill_time_ms, lighter_fill_time_ms)',
                    '价差分布分析 (spread vs threshold)',
                    '成功率分析 (status 字段)',
                    '仓位平衡分析 (net_position_after)',
                    '盈亏分析 (pnl_estimate)'
                ],
                'optimization_suggestions': [
                    '如果延迟过高,考虑调整 threshold_offset',
                    '如果成功率低,检查网络和 API 稳定性',
                    '如果仓位不平衡,检查对冲逻辑'
                ]
            }
        }

        export_file = self.log_dir / f"{self.file_prefix}_export.json"
        with open(export_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Data exported for analysis: {export_file}")
        return str(export_file)

    def close(self):
        """关闭日志记录器"""
        self.flush()
        logger.info(f"DataLogger closed. Total trades: {self.total_trades}")
