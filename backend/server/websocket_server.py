"""
WebSocket 服务器模块
处理与前端 JS 的通信
"""
import asyncio
import json
import logging
from typing import Dict, Set, Callable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)


@dataclass
class ClientConnection:
    """客户端连接信息"""
    websocket: WebSocketServerProtocol
    connected_at: datetime
    last_heartbeat: datetime
    exchange: str = ''
    is_ready: bool = False
    contract_id: str = ''
    ticker: str = ''


class WebSocketServer:
    """WebSocket 服务器 - 与前端 JS 通信"""

    def __init__(self, host: str = 'localhost', port: int = 8765):
        self.host = host
        self.port = port
        self.clients: Dict[str, ClientConnection] = {}
        self.message_handlers: Dict[str, Callable] = {}
        self.server = None
        self._running = False

        # 回调函数
        self.on_client_ready: Optional[Callable] = None
        self.on_client_disconnect: Optional[Callable] = None
        self.on_market_data: Optional[Callable] = None
        self.on_order_update: Optional[Callable] = None
        self.on_order_placed: Optional[Callable] = None

    async def start(self):
        """启动 WebSocket 服务器"""
        self.server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=None,  # 禁用自动 ping，避免兼容性问题
            ping_timeout=None
        )
        self._running = True
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")

    async def stop(self):
        """停止服务器"""
        self._running = False

        # 关闭所有客户端连接
        for client_id, client in list(self.clients.items()):
            try:
                await client.websocket.close()
            except Exception:
                pass

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        logger.info("WebSocket server stopped")

    async def _handle_connection(self, websocket: WebSocketServerProtocol, path: str = '/'):
        """处理新连接"""
        client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

        self.clients[client_id] = ClientConnection(
            websocket=websocket,
            connected_at=datetime.now(),
            last_heartbeat=datetime.now()
        )

        logger.info(f"Client connected: {client_id}")

        # 发送连接确认消息
        try:
            await websocket.send(json.dumps({
                'type': 'welcome',
                'message': 'Connected to EdgeX-Lighter Arbitrage Backend',
                'timestamp': int(datetime.now().timestamp() * 1000)
            }))
        except Exception as e:
            logger.error(f"Failed to send welcome message: {e}")

        try:
            async for message in websocket:
                await self._handle_message(client_id, message)
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Client disconnected: {client_id} - {e}")
        except Exception as e:
            logger.error(f"Error handling client {client_id}: {e}")
        finally:
            if client_id in self.clients:
                client = self.clients[client_id]
                del self.clients[client_id]

                # 触发断连回调
                if self.on_client_disconnect and client.is_ready:
                    try:
                        await self.on_client_disconnect(client_id, client.exchange)
                    except Exception as e:
                        logger.error(f"Error in disconnect callback: {e}")

    async def _handle_message(self, client_id: str, message: str):
        """处理收到的消息"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            msg_data = data.get('data', {})
            request_id = data.get('requestId')
            timestamp = data.get('timestamp')

            client = self.clients.get(client_id)
            if not client:
                return

            # 心跳处理
            if msg_type == 'ping':
                client.last_heartbeat = datetime.now()
                await self._send(client_id, {'type': 'pong', 'timestamp': timestamp})
                return

            # 前端就绪通知
            if msg_type == 'frontend_ready':
                client.exchange = msg_data.get('exchange', '')
                client.contract_id = msg_data.get('contractId', '')
                client.ticker = msg_data.get('ticker', '')
                client.is_ready = True
                logger.info(f"Frontend ready: {client.exchange} - {client.ticker}")

                if self.on_client_ready:
                    try:
                        await self.on_client_ready(client_id, msg_data)
                    except Exception as e:
                        logger.error(f"Error in client ready callback: {e}")
                return

            # EdgeX 市场数据
            if msg_type == 'edgex_market_data':
                if self.on_market_data:
                    try:
                        await self.on_market_data('edgex', msg_data)
                    except Exception as e:
                        logger.error(f"Error in market data callback: {e}")
                return

            # 订单下单结果
            if msg_type == 'order_placed':
                logger.info(f"Order placed result: {msg_data}")
                if self.on_order_placed:
                    try:
                        await self.on_order_placed(msg_data)
                    except Exception as e:
                        logger.error(f"Error in order placed callback: {e}")
                return

            # 订单更新
            if msg_type == 'order_update':
                logger.info(f"Order update: {msg_data}")
                if self.on_order_update:
                    try:
                        await self.on_order_update(msg_data)
                    except Exception as e:
                        logger.error(f"Error in order update callback: {e}")
                return

            # 订单取消结果
            if msg_type == 'order_canceled':
                logger.info(f"Order canceled: {msg_data}")
                return

            # 状态报告
            if msg_type == 'status_report':
                logger.debug(f"Status report from {client_id}: {msg_data}")
                return

            # 调用注册的处理器
            handler = self.message_handlers.get(msg_type)
            if handler:
                try:
                    result = await handler(msg_data)
                    if request_id:
                        await self._send(client_id, {
                            'requestId': request_id,
                            'data': result
                        })
                except Exception as e:
                    logger.error(f"Error in handler for {msg_type}: {e}")
                    if request_id:
                        await self._send(client_id, {
                            'requestId': request_id,
                            'error': str(e)
                        })
            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    def _is_ws_open(self, websocket) -> bool:
        """检查 WebSocket 是否打开（兼容不同版本）"""
        if websocket is None:
            return False
        # 兼容不同版本的 websockets 库
        if hasattr(websocket, 'open'):
            return websocket.open
        elif hasattr(websocket, 'close_code'):
            return websocket.close_code is None
        return False

    async def _send(self, client_id: str, data: Dict[str, Any]):
        """发送消息到指定客户端"""
        client = self.clients.get(client_id)
        if client and self._is_ws_open(client.websocket):
            try:
                await client.websocket.send(json.dumps(data))
            except Exception as e:
                logger.error(f"Error sending to {client_id}: {e}")

    async def broadcast(self, data: Dict[str, Any], exchange: str = None):
        """广播消息到所有客户端"""
        for client_id, client in list(self.clients.items()):
            if client.is_ready:
                if exchange is None or client.exchange == exchange:
                    await self._send(client_id, data)

    async def send_to_edgex(self, msg_type: str, data: Dict[str, Any]):
        """发送消息到 EdgeX 前端"""
        message = {
            'type': msg_type,
            'data': data,
            'timestamp': int(datetime.now().timestamp() * 1000)
        }
        await self.broadcast(message, exchange='edgex')

    async def execute_order(
        self,
        side: str,
        quantity: str,
        price: str = None,
        client_order_id: str = None
    ):
        """发送下单指令到前端"""
        await self.send_to_edgex('execute_order', {
            'side': side,
            'quantity': quantity,
            'price': price,
            'clientOrderId': client_order_id
        })

    async def cancel_order(self, order_id: str):
        """发送取消订单指令到前端"""
        await self.send_to_edgex('cancel_order', {
            'orderId': order_id
        })

    async def emergency_close(self, side: str, quantity: str):
        """发送紧急平仓指令到前端"""
        await self.send_to_edgex('emergency_close', {
            'side': side,
            'quantity': quantity
        })

    async def request_status(self):
        """请求前端状态报告"""
        await self.send_to_edgex('query_status', {})

    def register_handler(self, msg_type: str, handler: Callable):
        """注册消息处理器"""
        self.message_handlers[msg_type] = handler

    def get_ready_clients(self) -> Dict[str, ClientConnection]:
        """获取就绪的客户端"""
        return {k: v for k, v in self.clients.items() if v.is_ready}

    def is_frontend_ready(self, exchange: str = 'edgex') -> bool:
        """检查前端是否就绪"""
        for client in self.clients.values():
            if client.is_ready and client.exchange == exchange:
                return True
        return False

    def get_client_count(self) -> int:
        """获取连接的客户端数量"""
        return len(self.clients)

    def get_ready_count(self) -> int:
        """获取就绪的客户端数量"""
        return sum(1 for c in self.clients.values() if c.is_ready)
