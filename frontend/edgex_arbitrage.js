/**
 * EdgeX 套利前端脚本
 * 在 EdgeX 网页 (https://pro.edgex.exchange) 的浏览器 console 中执行
 *
 * 使用方法:
 * 1. 打开 EdgeX 网页并登录
 * 2. 打开浏览器开发者工具 (F12)
 * 3. 在 Console 标签页中粘贴此脚本
 * 4. 修改 CONFIG 中的配置
 * 5. 按回车执行
 */

(async function() {
    'use strict';

    // ===============================================
    // 配置区域 - 请根据实际情况修改
    // ===============================================
    const CONFIG = {
        // EdgeX 配置 (从 EdgeX 账户获取)
        edgex: {
            accountId: 'YOUR_ACCOUNT_ID',           // 账户 ID
            starkPrivateKey: 'YOUR_STARK_PRIVATE_KEY', // STARK 私钥
            contractId: '10002',                    // 合约 ID (BTC-USD)
            ticker: 'BTC'
        },
        // 后端 WebSocket 配置
        backend: {
            serverUrl: 'ws://localhost:8765'
        },
        // 交易配置
        trading: {
            maxRetries: 15,      // 最大重试次数
            retryDelay: 100      // 重试延迟 (ms)
        }
    };

    // ===============================================
    // 工具函数
    // ===============================================
    const log = (level, ...args) => {
        const timestamp = new Date().toISOString().substr(11, 12);
        const prefix = `[${timestamp}][EdgeX]`;

        switch(level) {
            case 'info':
                console.log(`%c${prefix}`, 'color: #2196F3', ...args);
                break;
            case 'warn':
                console.warn(`${prefix}`, ...args);
                break;
            case 'error':
                console.error(`${prefix}`, ...args);
                break;
            case 'success':
                console.log(`%c${prefix}`, 'color: #4CAF50', ...args);
                break;
            default:
                console.log(`${prefix}`, ...args);
        }
    };

    const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    // ===============================================
    // EdgeX 客户端
    // ===============================================
    class EdgeXClient {
        constructor(config) {
            this.baseUrl = 'https://pro.edgex.exchange';
            this.wsUrl = 'wss://spot-quote.edgex.exchange/api/v1/public/ws';
            this.accountId = config.accountId;
            this.starkPrivateKey = config.starkPrivateKey;
            this.contractId = config.contractId;
            this.ticker = config.ticker;

            // WebSocket 连接
            this.publicWs = null;
            this.privateWs = null;

            // 订单簿
            this.orderBook = { bids: new Map(), asks: new Map() };
            this.bestBid = null;
            this.bestAsk = null;
            this.tickSize = 0.1;  // 默认值,会从 API 获取

            // 回调
            this.onOrderUpdate = null;
            this.onMarketData = null;

            // 状态
            this._publicConnected = false;
            this._privateConnected = false;
        }

        async initialize() {
            log('info', 'Initializing EdgeX client...');

            // 尝试从页面获取已有的 WebSocket 或使用 API
            try {
                // 连接公共 WebSocket (订单簿)
                await this.connectPublicWs();

                // 连接私有 WebSocket (订单更新) - 可选
                // await this.connectPrivateWs();

                log('success', 'EdgeX client initialized');
            } catch (error) {
                log('error', 'Failed to initialize EdgeX client:', error);
                throw error;
            }
        }

        async connectPublicWs() {
            return new Promise((resolve, reject) => {
                try {
                    this.publicWs = new WebSocket(this.wsUrl);

                    this.publicWs.onopen = () => {
                        log('success', 'Public WebSocket connected');
                        this._publicConnected = true;

                        // 订阅订单簿
                        this.subscribeDepth();
                        resolve();
                    };

                    this.publicWs.onmessage = (event) => {
                        this.handlePublicMessage(JSON.parse(event.data));
                    };

                    this.publicWs.onerror = (error) => {
                        log('error', 'Public WebSocket error:', error);
                    };

                    this.publicWs.onclose = () => {
                        log('warn', 'Public WebSocket closed');
                        this._publicConnected = false;
                        // 自动重连
                        setTimeout(() => this.connectPublicWs(), 2000);
                    };

                    // 超时处理
                    setTimeout(() => {
                        if (!this._publicConnected) {
                            reject(new Error('WebSocket connection timeout'));
                        }
                    }, 10000);

                } catch (error) {
                    reject(error);
                }
            });
        }

        subscribeDepth() {
            if (this.publicWs && this.publicWs.readyState === WebSocket.OPEN) {
                const msg = {
                    type: 'subscribe',
                    channel: `depth.${this.contractId}.15`
                };
                this.publicWs.send(JSON.stringify(msg));
                log('info', `Subscribed to depth.${this.contractId}.15`);
            }
        }

        handlePublicMessage(message) {
            try {
                // 处理订单簿更新
                if (message.type === 'quote-event' && message.channel?.startsWith('depth.')) {
                    const data = message.content?.data?.[0];
                    if (data) {
                        const isSnapshot = data.depthType === 'SNAPSHOT';
                        this.updateOrderBook(data.bids || [], data.asks || [], isSnapshot);

                        // 触发回调
                        if (this.onMarketData) {
                            this.onMarketData({
                                bestBid: this.bestBid,
                                bestAsk: this.bestAsk,
                                timestamp: Date.now()
                            });
                        }
                    }
                }
            } catch (error) {
                log('error', 'Error handling public message:', error);
            }
        }

        updateOrderBook(bids, asks, isSnapshot = false) {
            if (isSnapshot) {
                this.orderBook.bids.clear();
                this.orderBook.asks.clear();
            }

            // 更新 bids
            for (const bid of bids) {
                const price = parseFloat(bid.price || bid[0]);
                const size = parseFloat(bid.size || bid[1]);
                if (size > 0) {
                    this.orderBook.bids.set(price, size);
                } else {
                    this.orderBook.bids.delete(price);
                }
            }

            // 更新 asks
            for (const ask of asks) {
                const price = parseFloat(ask.price || ask[0]);
                const size = parseFloat(ask.size || ask[1]);
                if (size > 0) {
                    this.orderBook.asks.set(price, size);
                } else {
                    this.orderBook.asks.delete(price);
                }
            }

            // 计算 BBO
            if (this.orderBook.bids.size > 0) {
                this.bestBid = Math.max(...this.orderBook.bids.keys());
            }
            if (this.orderBook.asks.size > 0) {
                this.bestAsk = Math.min(...this.orderBook.asks.keys());
            }
        }

        getBBO() {
            return {
                bestBid: this.bestBid,
                bestAsk: this.bestAsk
            };
        }

        roundToTick(price) {
            return Math.round(price / this.tickSize) * this.tickSize;
        }

        isConnected() {
            return this._publicConnected;
        }

        /**
         * 下单 - 使用页面已有的交易功能
         * 注意: 这需要根据 EdgeX 网页的实际 API 调整
         */
        async placeOrder(side, quantity, price, postOnly = true) {
            const startTime = Date.now();

            try {
                // 方案 1: 尝试使用 window 上暴露的交易对象
                if (window.__EDGEX_TRADE_API__) {
                    return await window.__EDGEX_TRADE_API__.placeOrder({
                        contractId: this.contractId,
                        side: side.toUpperCase(),
                        size: quantity.toString(),
                        price: this.roundToTick(price).toString(),
                        type: 'LIMIT',
                        postOnly: postOnly
                    });
                }

                // 方案 2: 直接调用 REST API (需要签名)
                // 这需要 starkware 库来生成签名
                log('warn', 'Direct API call not implemented. Please use page trading interface.');

                // 方案 3: 模拟用户操作 (备用)
                // 这需要根据页面 DOM 结构来实现

                return {
                    success: false,
                    error: 'API not available',
                    latency: Date.now() - startTime
                };

            } catch (error) {
                log('error', 'Place order error:', error);
                return {
                    success: false,
                    error: error.message,
                    latency: Date.now() - startTime
                };
            }
        }

        async cancelOrder(orderId) {
            try {
                if (window.__EDGEX_TRADE_API__) {
                    return await window.__EDGEX_TRADE_API__.cancelOrder(orderId);
                }
                return { success: false, error: 'API not available' };
            } catch (error) {
                return { success: false, error: error.message };
            }
        }
    }

    // ===============================================
    // WebSocket 桥接 (与后端通信)
    // ===============================================
    class WebSocketBridge {
        constructor(config) {
            this.serverUrl = config.serverUrl;
            this.ws = null;
            this.isConnected = false;
            this.reconnectAttempts = 0;
            this.maxReconnectAttempts = 10;

            // 消息队列
            this.messageQueue = [];

            // 消息处理器
            this.messageHandlers = new Map();

            // 心跳
            this.heartbeatInterval = null;
            this.lastPong = Date.now();
        }

        async connect() {
            return new Promise((resolve, reject) => {
                try {
                    log('info', `Connecting to backend: ${this.serverUrl}`);
                    this.ws = new WebSocket(this.serverUrl);

                    this.ws.onopen = () => {
                        log('success', 'Connected to backend');
                        this.isConnected = true;
                        this.reconnectAttempts = 0;

                        // 发送缓存的消息
                        this.flushMessageQueue();

                        // 启动心跳
                        this.startHeartbeat();

                        resolve();
                    };

                    this.ws.onmessage = (event) => {
                        this.handleMessage(JSON.parse(event.data));
                    };

                    this.ws.onerror = (error) => {
                        log('error', 'Backend WebSocket error:', error);
                    };

                    this.ws.onclose = () => {
                        log('warn', 'Backend connection closed');
                        this.isConnected = false;
                        this.stopHeartbeat();
                        this.attemptReconnect();
                    };

                    // 超时
                    setTimeout(() => {
                        if (!this.isConnected) {
                            reject(new Error('Backend connection timeout'));
                        }
                    }, 10000);

                } catch (error) {
                    reject(error);
                }
            });
        }

        handleMessage(message) {
            // 心跳响应
            if (message.type === 'pong') {
                this.lastPong = Date.now();
                return;
            }

            // 调用注册的处理器
            const handler = this.messageHandlers.get(message.type);
            if (handler) {
                try {
                    handler(message.data);
                } catch (error) {
                    log('error', `Handler error for ${message.type}:`, error);
                }
            } else {
                log('warn', 'Unknown message type:', message.type);
            }
        }

        send(type, data) {
            const message = {
                type,
                data,
                timestamp: Date.now()
            };

            if (this.isConnected && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(message));
            } else {
                // 缓存消息
                this.messageQueue.push(message);
            }
        }

        on(type, handler) {
            this.messageHandlers.set(type, handler);
        }

        flushMessageQueue() {
            const now = Date.now();
            while (this.messageQueue.length > 0) {
                const message = this.messageQueue.shift();
                // 只发送 5 秒内的消息
                if (now - message.timestamp < 5000) {
                    this.ws.send(JSON.stringify(message));
                }
            }
        }

        attemptReconnect() {
            if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                log('error', 'Max reconnection attempts reached');
                return;
            }

            this.reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 30000);

            log('info', `Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

            setTimeout(() => {
                this.connect().catch(() => {});
            }, delay);
        }

        startHeartbeat() {
            this.heartbeatInterval = setInterval(() => {
                if (this.isConnected) {
                    this.ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));

                    // 检查 pong 超时
                    if (Date.now() - this.lastPong > 15000) {
                        log('warn', 'Heartbeat timeout, reconnecting...');
                        this.ws.close();
                    }
                }
            }, 5000);
        }

        stopHeartbeat() {
            if (this.heartbeatInterval) {
                clearInterval(this.heartbeatInterval);
                this.heartbeatInterval = null;
            }
        }
    }

    // ===============================================
    // 订单执行器
    // ===============================================
    class OrderExecutor {
        constructor(edgexClient, bridge, config) {
            this.edgex = edgexClient;
            this.bridge = bridge;
            this.config = config;

            // 活跃订单
            this.activeOrders = new Map();

            // 设置消息处理器
            this.setupMessageHandlers();
        }

        setupMessageHandlers() {
            // 处理下单指令
            this.bridge.on('execute_order', async (data) => {
                await this.executeOrder(data);
            });

            // 处理取消订单指令
            this.bridge.on('cancel_order', async (data) => {
                await this.cancelOrder(data.orderId);
            });

            // 处理状态查询
            this.bridge.on('query_status', () => {
                this.reportStatus();
            });

            // 处理紧急平仓
            this.bridge.on('emergency_close', async (data) => {
                await this.emergencyClose(data);
            });
        }

        async executeOrder(orderParams) {
            const { side, quantity, price, clientOrderId } = orderParams;
            const startTime = Date.now();

            log('info', `Executing order: ${side} ${quantity} @ ${price || 'market'}`);

            let retryCount = 0;
            const maxRetries = this.config.maxRetries || 15;

            while (retryCount < maxRetries) {
                try {
                    // 获取最新 BBO
                    const bbo = this.edgex.getBBO();

                    // 计算订单价格
                    let orderPrice = price ? parseFloat(price) : null;
                    if (!orderPrice) {
                        orderPrice = side === 'buy'
                            ? bbo.bestAsk - this.edgex.tickSize
                            : bbo.bestBid + this.edgex.tickSize;
                    }

                    // 下单
                    const result = await this.edgex.placeOrder(
                        side,
                        quantity,
                        orderPrice,
                        true  // post_only
                    );

                    const latency = Date.now() - startTime;

                    if (result.success) {
                        // 记录订单
                        this.activeOrders.set(result.orderId, {
                            clientOrderId,
                            side,
                            quantity: parseFloat(quantity),
                            price: orderPrice,
                            status: 'OPEN',
                            createTime: Date.now()
                        });

                        // 上报成功
                        this.bridge.send('order_placed', {
                            success: true,
                            orderId: result.orderId,
                            clientOrderId,
                            side,
                            quantity,
                            price: orderPrice,
                            latency
                        });

                        log('success', `Order placed: ${result.orderId} (${latency}ms)`);
                        return result;
                    }

                    // POST_ONLY 被拒绝,重试
                    log('warn', `Order rejected, retrying (${retryCount + 1}/${maxRetries})`);
                    retryCount++;
                    await delay(this.config.retryDelay || 100);

                } catch (error) {
                    log('error', 'Order execution error:', error);
                    retryCount++;
                    await delay(this.config.retryDelay || 100);
                }
            }

            // 所有重试失败
            const latency = Date.now() - startTime;
            this.bridge.send('order_placed', {
                success: false,
                clientOrderId,
                error: 'Max retries exceeded',
                latency
            });

            log('error', 'Order failed after max retries');
            return { success: false };
        }

        handleOrderUpdate(update) {
            const order = this.activeOrders.get(update.orderId);
            if (!order) return;

            // 更新本地状态
            order.status = update.status;
            order.filledSize = update.filledSize;

            // 上报到后端
            this.bridge.send('order_update', {
                orderId: update.orderId,
                clientOrderId: order.clientOrderId,
                status: update.status,
                side: order.side,
                price: order.price,
                filledSize: update.filledSize,
                timestamp: Date.now()
            });

            // 如果订单完成,移除
            if (update.status === 'FILLED' || update.status === 'CANCELED') {
                this.activeOrders.delete(update.orderId);
            }
        }

        async cancelOrder(orderId) {
            try {
                const result = await this.edgex.cancelOrder(orderId);
                this.bridge.send('order_canceled', {
                    success: result.success,
                    orderId
                });
                return result;
            } catch (error) {
                this.bridge.send('order_canceled', {
                    success: false,
                    orderId,
                    error: error.message
                });
            }
        }

        async emergencyClose(params) {
            const { side, quantity } = params;
            log('warn', `Emergency close: ${side} ${quantity}`);

            // 取消所有活跃订单
            for (const [orderId] of this.activeOrders) {
                try {
                    await this.edgex.cancelOrder(orderId);
                } catch (e) {
                    log('error', `Failed to cancel order: ${orderId}`);
                }
            }

            // 执行平仓 (使用激进价格)
            const bbo = this.edgex.getBBO();
            const aggressivePrice = side === 'buy'
                ? bbo.bestAsk * 1.002
                : bbo.bestBid * 0.998;

            await this.edgex.placeOrder(side, quantity, aggressivePrice, false);
        }

        reportStatus() {
            const bbo = this.edgex.getBBO();
            this.bridge.send('status_report', {
                connected: this.edgex.isConnected(),
                activeOrders: Array.from(this.activeOrders.entries()),
                bbo,
                timestamp: Date.now()
            });
        }
    }

    // ===============================================
    // 主程序
    // ===============================================
    log('info', '========================================');
    log('info', 'EdgeX Arbitrage Frontend Starting...');
    log('info', '========================================');

    try {
        // 创建 EdgeX 客户端
        const edgexClient = new EdgeXClient(CONFIG.edgex);
        await edgexClient.initialize();

        // 创建 WebSocket 桥接
        const bridge = new WebSocketBridge(CONFIG.backend);
        await bridge.connect();

        // 创建订单执行器
        const executor = new OrderExecutor(edgexClient, bridge, CONFIG.trading);

        // 设置 EdgeX 订单更新回调 (如果可用)
        edgexClient.onOrderUpdate = (update) => {
            executor.handleOrderUpdate(update);
        };

        // 设置市场数据回调 - 发送到后端
        edgexClient.onMarketData = (data) => {
            bridge.send('edgex_market_data', {
                bestBid: data.bestBid,
                bestAsk: data.bestAsk,
                timestamp: data.timestamp
            });
        };

        // 通知后端前端就绪
        bridge.send('frontend_ready', {
            exchange: 'edgex',
            contractId: CONFIG.edgex.contractId,
            ticker: CONFIG.edgex.ticker
        });

        log('success', '========================================');
        log('success', 'EdgeX Frontend Ready!');
        log('success', `Ticker: ${CONFIG.edgex.ticker}`);
        log('success', `Contract: ${CONFIG.edgex.contractId}`);
        log('success', `Backend: ${CONFIG.backend.serverUrl}`);
        log('success', '========================================');
        log('info', 'Waiting for trading signals from backend...');

        // 暴露到全局以便调试
        window.edgexArbitrage = {
            edgex: edgexClient,
            bridge,
            executor,
            config: CONFIG,
            // 调试方法
            getBBO: () => edgexClient.getBBO(),
            getStatus: () => ({
                edgexConnected: edgexClient.isConnected(),
                backendConnected: bridge.isConnected,
                activeOrders: executor.activeOrders.size
            }),
            // 手动测试
            testOrder: async (side, qty, price) => {
                return await edgexClient.placeOrder(side, qty, price);
            }
        };

        log('info', 'Debug: window.edgexArbitrage available');
        log('info', 'Commands: getBBO(), getStatus(), testOrder(side, qty, price)');

    } catch (error) {
        log('error', 'Failed to start:', error);
        throw error;
    }

})();
