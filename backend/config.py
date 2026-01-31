"""
配置管理模块
"""
import os
from decimal import Decimal
from typing import Dict, Any
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class ServerConfig:
    """WebSocket 服务器配置"""
    host: str = 'localhost'
    port: int = 8765


@dataclass
class LighterConfig:
    """Lighter 交易所配置"""
    base_url: str = 'https://mainnet.zklighter.elliot.ai'
    ws_url: str = 'wss://mainnet.zklighter.elliot.ai/stream'
    api_key_private_key: str = ''
    account_index: int = 0
    api_key_index: int = 0
    market_index: int = 0  # 0 = BTC
    base_amount_multiplier: Decimal = Decimal('1e8')
    price_multiplier: Decimal = Decimal('1e8')
    tick_size: Decimal = Decimal('0.1')


@dataclass
class StrategyConfig:
    """策略配置"""
    ticker: str = 'BTC'
    order_quantity: Decimal = Decimal('0.001')
    max_position: Decimal = Decimal('0.01')
    long_threshold: Decimal = Decimal('10')
    short_threshold: Decimal = Decimal('10')
    threshold_offset: Decimal = Decimal('10')
    min_samples: int = 100
    min_signal_interval: float = 1.0
    frontend_latency_ms: int = 100
    price_buffer: Decimal = Decimal('0.5')
    tick_size: Decimal = Decimal('0.1')


@dataclass
class RiskConfig:
    """风控配置"""
    max_position: Decimal = Decimal('0.01')
    max_position_imbalance: Decimal = Decimal('0.005')
    max_daily_loss: Decimal = Decimal('100')
    max_latency_ms: int = 500
    max_error_rate: float = 0.1
    min_balance: Decimal = Decimal('10')


@dataclass
class TelegramConfig:
    """Telegram 通知配置"""
    enabled: bool = False
    bot_token: str = ''
    group_id: str = ''
    account_label: str = 'A1'


@dataclass
class Config:
    """总配置"""
    server: ServerConfig = field(default_factory=ServerConfig)
    lighter: LighterConfig = field(default_factory=LighterConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    log_level: str = 'INFO'
    log_dir: str = 'logs'


def load_config(env_file: str = '.env') -> Config:
    """
    从环境变量加载配置

    Args:
        env_file: 环境变量文件路径

    Returns:
        Config 对象
    """
    load_dotenv(env_file)

    config = Config()

    # Server
    config.server.host = os.getenv('WS_SERVER_HOST', 'localhost')
    config.server.port = int(os.getenv('WS_SERVER_PORT', '8765'))

    # Lighter
    config.lighter.api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY', '')
    config.lighter.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
    config.lighter.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
    config.lighter.market_index = int(os.getenv('LIGHTER_MARKET_INDEX', '0'))

    # Strategy
    config.strategy.ticker = os.getenv('TICKER', 'BTC')
    config.strategy.order_quantity = Decimal(os.getenv('ORDER_QUANTITY', '0.001'))
    config.strategy.max_position = Decimal(os.getenv('MAX_POSITION', '0.01'))
    config.strategy.threshold_offset = Decimal(os.getenv('THRESHOLD_OFFSET', '10'))
    config.strategy.min_samples = int(os.getenv('MIN_SAMPLES', '100'))

    # Risk
    config.risk.max_position = config.strategy.max_position
    config.risk.max_daily_loss = Decimal(os.getenv('MAX_DAILY_LOSS', '100'))
    config.risk.max_latency_ms = int(os.getenv('MAX_LATENCY_MS', '500'))
    config.risk.min_balance = Decimal(os.getenv('MIN_BALANCE', '10'))

    # Telegram
    config.telegram.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    config.telegram.group_id = os.getenv('TELEGRAM_GROUP_ID', '')
    config.telegram.account_label = os.getenv('ACCOUNT_LABEL', 'A1')
    config.telegram.enabled = bool(config.telegram.bot_token and config.telegram.group_id)

    # Logging
    config.log_level = os.getenv('LOG_LEVEL', 'INFO')
    config.log_dir = os.getenv('LOG_DIR', 'logs')

    return config


def config_to_dict(config: Config) -> Dict[str, Any]:
    """将配置转换为字典"""
    return {
        'server': {
            'host': config.server.host,
            'port': config.server.port
        },
        'lighter': {
            'base_url': config.lighter.base_url,
            'account_index': config.lighter.account_index,
            'market_index': config.lighter.market_index
        },
        'strategy': {
            'ticker': config.strategy.ticker,
            'order_quantity': str(config.strategy.order_quantity),
            'max_position': str(config.strategy.max_position),
            'threshold_offset': str(config.strategy.threshold_offset),
            'min_samples': config.strategy.min_samples
        },
        'risk': {
            'max_position': str(config.risk.max_position),
            'max_daily_loss': str(config.risk.max_daily_loss),
            'max_latency_ms': config.risk.max_latency_ms
        },
        'telegram': {
            'enabled': config.telegram.enabled,
            'account_label': config.telegram.account_label
        }
    }
