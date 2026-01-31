"""
Lighter 交易所客户端
基于 lighter-python-sdk 实现
"""
import asyncio
import json
import time
import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional, Callable
import aiohttp
import websockets

logger = logging.getLogger(__name__)

# 尝试导入 Lighter SDK
try:
    from lighter.lighter_client import Client as LighterSDKClient
    LIGHTER_SDK_AVAILABLE = True
except ImportError:
    LIGHTER_SDK_AVAILABLE = False
    logger.warning("Lighter SDK not available. Install with: pip install lighter-python")


class LighterClient:
    """Lighter 交易所客户端"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.base_url = config.get('base_url', 'https://mainnet.zklighter.elliot.ai')
        self.ws_url = config.get('ws_url', 'wss://mainnet.zklighter.elliot.ai/stream')

        # API 配置
        self.api_key_private_key = config.get('api_key_private_key')
        self.account_index = config.get('account_index', 0)
        self.api_key_index = config.get('api_key_index', 0)
        self.market_index = config.get('market_index', 0)  # 0 = BTC

        # 市场参数
        self.base_amount_multiplier = Decimal(str(config.get('base_amount_multiplier', 1e8)))
        self.price_multiplier = Decimal(str(config.get('price_multiplier', 1e8)))
        self.tick_size = Decimal(str(config.get('tick_size', '0.1')))

        # SDK 客户端
        self.sdk_client = None

        # WebSocket
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_task: Optional[asyncio.Task] = None
        self._ws_stop = False
        self._ws_connected = False

        # 订单簿
        self.order_book = {'bids': {}, 'asks': {}}
        self.best_bid: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.order_book_ready = False

        # 回调
        self.on_order_update: Optional[Callable] = None
        self.on_market_data: Optional[Callable] = None
        self.on_order_book_update: Optional[Callable] = None

    async def initialize(self):
        """初始化客户端"""
        if LIGHTER_SDK_AVAILABLE and self.api_key_private_key:
            try:
                self.sdk_client = LighterSDKClient(
                    private_key=self.api_key_private_key,
                    api_url=self.base_url
                )
                logger.info("Lighter SDK client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Lighter SDK: {e}")
                self.sdk_client = None
        else:
            logger.warning("Lighter SDK not configured, using REST API only")

        # 启动 WebSocket
        self.ws_task = asyncio.create_task(self._run_websocket())

        # 等待 WebSocket 连接
        for _ in range(50):  # 最多等 5 秒
            if self._ws_connected:
                break
            await asyncio.sleep(0.1)

        logger.info("Lighter client initialized")

    async def close(self):
        """关闭客户端"""
        self._ws_stop = True

        if self.ws and not self.ws.closed:
            await self.ws.close()

        if self.ws_task:
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass

        logger.info("Lighter client closed")

    async def _run_websocket(self):
        """WebSocket 主循环"""
        reconnect_count = 0
        max_reconnect_delay = 30

        while not self._ws_stop:
            try:
                # 使用较长的 ping 间隔和超时，让服务器主导心跳
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=30,  # 每 30 秒发送 ping
                    ping_timeout=60,   # 60 秒超时
                    close_timeout=5
                ) as ws:
                    self.ws = ws
                    self._ws_connected = True
                    reconnect_count = 0

                    # 订阅订单簿 - 尝试多种格式
                    subscription_formats = [
                        {'method': 'subscribe', 'params': [f'order_book/{self.market_index}']},
                        {'op': 'subscribe', 'channel': 'orderbook', 'market': self.market_index},
                        {'type': 'subscribe', 'channel': f'orderbook.{self.market_index}'},
                    ]

                    # 尝试第一种格式
                    await ws.send(json.dumps(subscription_formats[0]))
                    logger.info(f"Lighter WebSocket connected, subscribing to market {self.market_index}")

                    # 启动手动心跳任务
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    try:
                        async for message in ws:
                            if self._ws_stop:
                                break
                            try:
                                await self._handle_ws_message(json.loads(message))
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid JSON received: {message[:100]}")
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Lighter WebSocket closed: code={e.code}, reason={e.reason}")
                self._ws_connected = False
            except asyncio.TimeoutError:
                logger.warning("Lighter WebSocket connection timeout")
                self._ws_connected = False
            except Exception as e:
                logger.error(f"Lighter WebSocket error: {type(e).__name__}: {e}")
                self._ws_connected = False

            if not self._ws_stop:
                reconnect_count += 1
                delay = min(2 ** reconnect_count, max_reconnect_delay)
                logger.info(f"Reconnecting Lighter WebSocket in {delay}s... (attempt {reconnect_count})")
                await asyncio.sleep(delay)

    def _is_ws_open(self) -> bool:
        """检查 WebSocket 是否打开（兼容不同版本）"""
        if self.ws is None:
            return False
        # 兼容不同版本的 websockets 库
        if hasattr(self.ws, 'closed'):
            return not self.ws.closed
        elif hasattr(self.ws, 'close_code'):
            return self.ws.close_code is None
        return False

    async def _heartbeat_loop(self):
        """手动心跳循环"""
        while not self._ws_stop and self._is_ws_open():
            try:
                # 每 30 秒发送心跳
                await asyncio.sleep(30)
                if self._is_ws_open():
                    await self.ws.send(json.dumps({'method': 'ping'}))
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                break

    async def _handle_ws_message(self, data: Dict[str, Any]):
        """处理 WebSocket 消息"""
        try:
            # 调试：打印收到的消息类型
            msg_keys = list(data.keys())[:5] if isinstance(data, dict) else str(type(data))
            logger.debug(f"Lighter WS message keys: {msg_keys}")

            # 处理订单簿快照
            if 'order_book' in data:
                order_book = data.get('order_book', {})
                bids = order_book.get('bids', [])
                asks = order_book.get('asks', [])

                self._update_order_book(bids, asks, is_snapshot=True)
                self.order_book_ready = True
                logger.info(f"Lighter order book snapshot: {len(bids)} bids, {len(asks)} asks")

            # 处理订单簿增量更新
            elif data.get('type') == 'order_book_update':
                updates = data.get('data', {})
                bids = updates.get('bids', [])
                asks = updates.get('asks', [])
                self._update_order_book(bids, asks, is_snapshot=False)

            # 处理订单更新
            elif data.get('type') == 'order_update':
                if self.on_order_update:
                    await self.on_order_update({
                        'exchange': 'lighter',
                        **data.get('data', {})
                    })

            # 处理心跳
            elif data.get('method') == 'ping' or data.get('type') == 'ping':
                await self.ws.send(json.dumps({'method': 'pong'}))

        except Exception as e:
            logger.error(f"Error handling Lighter WS message: {e}")

    def _update_order_book(self, bids: List, asks: List, is_snapshot: bool = False):
        """更新订单簿"""
        if is_snapshot:
            self.order_book['bids'].clear()
            self.order_book['asks'].clear()

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
                self.order_book['bids'][price] = size
            else:
                self.order_book['bids'].pop(price, None)

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
                self.order_book['asks'][price] = size
            else:
                self.order_book['asks'].pop(price, None)

        # 更新 BBO
        self._update_bbo()

        # 触发回调
        if self.on_order_book_update:
            asyncio.create_task(self.on_order_book_update(self.get_bbo()))

        if self.on_market_data:
            asyncio.create_task(self.on_market_data('lighter', {
                'best_bid': float(self.best_bid) if self.best_bid else None,
                'best_ask': float(self.best_ask) if self.best_ask else None,
                'timestamp': time.time()
            }))

    def _update_bbo(self):
        """更新 BBO"""
        if self.order_book['bids']:
            self.best_bid = max(self.order_book['bids'].keys())
        else:
            self.best_bid = None

        if self.order_book['asks']:
            self.best_ask = min(self.order_book['asks'].keys())
        else:
            self.best_ask = None

    def get_bbo(self) -> Dict[str, Optional[Decimal]]:
        """获取 BBO"""
        return {
            'bid': self.best_bid,
            'ask': self.best_ask
        }

    async def place_market_order(
        self,
        side: str,
        quantity: Decimal,
        price: Decimal = None
    ) -> Dict[str, Any]:
        """
        下市价单 (使用激进限价单模拟)

        Args:
            side: 'buy' or 'sell'
            quantity: 数量
            price: 激进价格 (可选,会自动计算)
        """
        # 计算激进价格
        if price is None:
            bbo = self.get_bbo()
            if side.lower() == 'buy':
                if bbo['ask'] is None:
                    return {'success': False, 'error': 'No ask price available'}
                price = bbo['ask'] * Decimal('1.005')  # 高于卖一 0.5%
            else:
                if bbo['bid'] is None:
                    return {'success': False, 'error': 'No bid price available'}
                price = bbo['bid'] * Decimal('0.995')  # 低于买一 0.5%

        # 使用 SDK 下单
        if self.sdk_client:
            try:
                is_buy = side.lower() == 'buy'
                result = await self._place_order_via_sdk(
                    is_buy=is_buy,
                    quantity=quantity,
                    price=price
                )
                return result
            except Exception as e:
                logger.error(f"SDK order failed: {e}")
                return {'success': False, 'error': str(e)}
        else:
            # 使用 REST API
            return await self._place_order_via_rest(side, quantity, price)

    async def _place_order_via_sdk(
        self,
        is_buy: bool,
        quantity: Decimal,
        price: Decimal
    ) -> Dict[str, Any]:
        """通过 SDK 下单"""
        try:
            # 转换为 SDK 需要的格式
            base_amount = int(quantity * self.base_amount_multiplier)
            price_int = int(price * self.price_multiplier)

            # 调用 SDK
            result = self.sdk_client.create_order(
                market_index=self.market_index,
                is_buy=is_buy,
                base_amount=base_amount,
                price=price_int,
                order_type='limit',
                time_in_force='gtc'
            )

            return {
                'success': True,
                'order_id': result.get('order_index'),
                'tx_hash': result.get('tx_hash')
            }
        except Exception as e:
            logger.error(f"SDK order error: {e}")
            return {'success': False, 'error': str(e)}

    async def _place_order_via_rest(
        self,
        side: str,
        quantity: Decimal,
        price: Decimal
    ) -> Dict[str, Any]:
        """通过 REST API 下单 (备用方案)"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/api/v1/order"
                payload = {
                    'market_index': self.market_index,
                    'side': side,
                    'size': str(quantity),
                    'price': str(price),
                    'type': 'limit'
                }

                async with session.post(url, json=payload) as resp:
                    data = await resp.json()

                    if resp.status == 200:
                        return {
                            'success': True,
                            'order_id': data.get('order_index')
                        }
                    else:
                        return {
                            'success': False,
                            'error': data.get('error', 'Unknown error')
                        }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def get_position(self) -> Decimal:
        """获取当前仓位"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/api/v1/account"
                params = {'by': 'index', 'value': str(self.account_index)}

                async with session.get(url, params=params) as resp:
                    data = await resp.json()

            accounts = data.get('accounts', [])
            if accounts:
                positions = accounts[0].get('positions', [])
                for pos in positions:
                    if pos.get('market_index') == self.market_index:
                        size = Decimal(str(pos.get('size', 0)))
                        sign = 1 if pos.get('is_long', True) else -1
                        return size * sign

            return Decimal('0')
        except Exception as e:
            logger.error(f"Failed to get Lighter position: {e}")
            return Decimal('0')

    async def get_balance(self) -> Decimal:
        """获取可用余额"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/api/v1/account"
                params = {'by': 'index', 'value': str(self.account_index)}

                async with session.get(url, params=params) as resp:
                    data = await resp.json()

            accounts = data.get('accounts', [])
            if accounts:
                return Decimal(str(accounts[0].get('available_balance', 0)))

            return Decimal('0')
        except Exception as e:
            logger.error(f"Failed to get Lighter balance: {e}")
            return Decimal('0')

    async def flatten_position(self) -> Dict[str, Any]:
        """平掉所有仓位"""
        position = await self.get_position()

        if abs(position) < Decimal('0.0001'):
            return {'success': True, 'message': 'No position to flatten'}

        side = 'sell' if position > 0 else 'buy'
        quantity = abs(position)

        return await self.place_market_order(side, quantity)

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._ws_connected and self.order_book_ready

    def get_status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        bbo = self.get_bbo()
        return {
            'connected': self._ws_connected,
            'order_book_ready': self.order_book_ready,
            'sdk_available': self.sdk_client is not None,
            'best_bid': float(bbo['bid']) if bbo['bid'] else None,
            'best_ask': float(bbo['ask']) if bbo['ask'] else None,
            'order_book_depth': {
                'bids': len(self.order_book['bids']),
                'asks': len(self.order_book['asks'])
            }
        }
