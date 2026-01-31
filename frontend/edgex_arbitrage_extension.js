/**
 * EdgeX 套利前端脚本 (Chrome 扩展版本)
 * 通过 Chrome 扩展绕过 CSP 限制
 *
 * 使用方法:
 * 1. 安装 extension 文件夹中的 Chrome 扩展
 * 2. 打开 EdgeX 网页并登录
 * 3. 在 Console 中粘贴此脚本执行
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
    // 检查扩展是否已安装
    // ===============================================
    function checkExtension() {
        return new Promise((resolve) => {
            let resolved = false;

            const handler = (event) => {
                if (event.data && event.data.source === 'edgex-bridge') {
                    if (event.data.type === 'bridge_ready' || event.data.type === 'connection_status') {
                        if (!resolved) {
                            resolved = true;
                            window.removeEventListener('message', handler);
                            resolve(true);
                        }
                    }
                }
            };

            window.addEventListener('message', handler);

            // 请求连接状态
            window.postMessage({ source: 'edgex-page', type: 'check_connection' }, '*');

            // 超时后返回 false
            setTimeout(() => {
                if (!resolved) {
                    resolved = true;
                    window.removeEventListener('message', handler);
                    resolve(false);
                }
            }, 2000);
        });
    }

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

            this.publicWs = null;
            this.orderBook = { bids: new Map(), asks: new Map() };
            this.bestBid = null;
            this.bestAsk = null;
            this.tickSize = 0.1;

            this.onOrderUpdate = null;
            this.onMarketData = null;
            this._publicConnected = false;
        }

        async initialize() {
            log('info', 'Initializing EdgeX client...');
            await this.connectPublicWs();
            log('success', 'EdgeX client initialized');
        }

        async connectPublicWs() {
            return new Promise((resolve, reject) => {
                this.publicWs = new WebSocket(this.wsUrl);

                this.publicWs.onopen = () => {
                    log('success', 'Public WebSocket connected');
                    this._publicConnected = true;
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
                    setTimeout(() => this.connectPublicWs(), 2000);
                };

                setTimeout(() => {
                    if (!this._publicConnected) {
                        reject(new Error('WebSocket connection timeout'));
                    }
                }, 10000);
            });
        }

        subscribeDepth() {
            if (this.publicWs && this.publicWs.readyState === WebSocket.OPEN) {
                const msg = { type: 'subscribe', channel: `depth.${this.contractId}.15` };
                this.publicWs.send(JSON.stringify(msg));
                log('info', `Subscribed to depth.${this.contractId}.15`);
            }
        }

        handlePublicMessage(message) {
            try {
                if (message.type === 'quote-event' && message.channel?.startsWith('depth.')) {
                    const data = message.content?.data?.[0];
                    if (data) {
                        const isSnapshot = data.depthType === 'SNAPSHOT';
                        this.updateOrderBook(data.bids || [], data.asks || [], isSnapshot);

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

            for (const bid of bids) {
                const price = parseFloat(bid.price || bid[0]);
                const size = parseFloat(bid.size || bid[1]);
                if (size > 0) {
                    this.orderBook.bids.set(price, size);
                } else {
                    this.orderBook.bids.delete(price);
                }
            }

            for (const ask of asks) {
                const price = parseFloat(ask.price || ask[0]);
                const size = parseFloat(ask.size || ask[1]);
                if (size > 0) {
                    this.orderBook.asks.set(price, size);
                } else {
                    this.orderBook.asks.delete(price);
                }
            }

            if (this.orderBook.bids.size > 0) {
                this.bestBid = Math.max(...this.orderBook.bids.keys());
            }
            if (this.orderBook.asks.size > 0) {
                this.bestAsk = Math.min(...this.orderBook.asks.keys());
            }
        }

        getBBO() {
            return { bestBid: this.bestBid, bestAsk: this.bestAsk };
        }

        roundToTick(price) {
            return Math.round(price / this.tickSize) * this.tickSize;
        }

        isConnected() {
            return this._publicConnected;
        }

        async placeOrder(side, quantity, price, postOnly = true) {
            const startTime = Date.now();
            try {
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
                log('warn', 'Direct API call not implemented.');
                return { success: false, error: 'API not available', latency: Date.now() - startTime };
            } catch (error) {
                log('error', 'Place order error:', error);
                return { success: false, error: error.message, latency: Date.now() - startTime };
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
    // 扩展桥接 (通过 Chrome 扩展与后端通信)
    // ===============================================
    class ExtensionBridge {
        constructor() {
            this.isConnected = false;
            this.messageHandlers = new Map();
            this.setupMessageListener();
        }

        setupMessageListener() {
            window.addEventListener('message', (event) => {
                if (event.source !== window) return;
                if (!event.data || event.data.source !== 'edgex-bridge') return;

                const message = event.data;

                if (message.type === 'backend_connected') {
                    log('success', 'Backend connected (via extension)');
                    this.isConnected = true;
                } else if (message.type === 'backend_disconnected') {
                    log('warn', 'Backend disconnected');
                    this.isConnected = false;
                } else if (message.type === 'backend_message') {
                    this.handleBackendMessage(message.data);
                }
            });
        }

        handleBackendMessage(message) {
            if (message.type === 'pong') return;

            const handler = this.messageHandlers.get(message.type);
            if (handler) {
                try {
                    handler(message.data);
                } catch (error) {
                    log('error', `Handler error for ${message.type}:`, error);
                }
            }
        }

        async connect() {
            return new Promise((resolve, reject) => {
                log('info', 'Connecting to backend via extension...');

                window.postMessage({ source: 'edgex-page', type: 'connect_backend' }, '*');

                const checkConnection = () => {
                    if (this.isConnected) {
                        resolve();
                    } else {
                        setTimeout(checkConnection, 100);
                    }
                };

                setTimeout(() => {
                    if (!this.isConnected) {
                        reject(new Error('Backend connection timeout'));
                    }
                }, 10000);

                checkConnection();
            });
        }

        send(type, data) {
            const message = { type, data, timestamp: Date.now() };
            window.postMessage({ source: 'edgex-page', type: 'send_to_backend', data: message }, '*');
        }

        on(type, handler) {
            this.messageHandlers.set(type, handler);
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
            this.activeOrders = new Map();
            this.setupMessageHandlers();
        }

        setupMessageHandlers() {
            this.bridge.on('execute_order', async (data) => {
                await this.executeOrder(data);
            });

            this.bridge.on('cancel_order', async (data) => {
                await this.cancelOrder(data.orderId);
            });

            this.bridge.on('query_status', () => {
                this.reportStatus();
            });

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
                    const bbo = this.edgex.getBBO();
                    let orderPrice = price ? parseFloat(price) : null;
                    if (!orderPrice) {
                        orderPrice = side === 'buy'
                            ? bbo.bestAsk - this.edgex.tickSize
                            : bbo.bestBid + this.edgex.tickSize;
                    }

                    const result = await this.edgex.placeOrder(side, quantity, orderPrice, true);
                    const latency = Date.now() - startTime;

                    if (result.success) {
                        this.activeOrders.set(result.orderId, {
                            clientOrderId, side, quantity: parseFloat(quantity),
                            price: orderPrice, status: 'OPEN', createTime: Date.now()
                        });

                        this.bridge.send('order_placed', {
                            success: true, orderId: result.orderId, clientOrderId,
                            side, quantity, price: orderPrice, latency
                        });

                        log('success', `Order placed: ${result.orderId} (${latency}ms)`);
                        return result;
                    }

                    log('warn', `Order rejected, retrying (${retryCount + 1}/${maxRetries})`);
                    retryCount++;
                    await delay(this.config.retryDelay || 100);

                } catch (error) {
                    log('error', 'Order execution error:', error);
                    retryCount++;
                    await delay(this.config.retryDelay || 100);
                }
            }

            const latency = Date.now() - startTime;
            this.bridge.send('order_placed', {
                success: false, clientOrderId, error: 'Max retries exceeded', latency
            });

            log('error', 'Order failed after max retries');
            return { success: false };
        }

        handleOrderUpdate(update) {
            const order = this.activeOrders.get(update.orderId);
            if (!order) return;

            order.status = update.status;
            order.filledSize = update.filledSize;

            this.bridge.send('order_update', {
                orderId: update.orderId, clientOrderId: order.clientOrderId,
                status: update.status, side: order.side, price: order.price,
                filledSize: update.filledSize, timestamp: Date.now()
            });

            if (update.status === 'FILLED' || update.status === 'CANCELED') {
                this.activeOrders.delete(update.orderId);
            }
        }

        async cancelOrder(orderId) {
            try {
                const result = await this.edgex.cancelOrder(orderId);
                this.bridge.send('order_canceled', { success: result.success, orderId });
                return result;
            } catch (error) {
                this.bridge.send('order_canceled', { success: false, orderId, error: error.message });
            }
        }

        async emergencyClose(params) {
            const { side, quantity } = params;
            log('warn', `Emergency close: ${side} ${quantity}`);

            for (const [orderId] of this.activeOrders) {
                try {
                    await this.edgex.cancelOrder(orderId);
                } catch (e) {
                    log('error', `Failed to cancel order: ${orderId}`);
                }
            }

            const bbo = this.edgex.getBBO();
            const aggressivePrice = side === 'buy' ? bbo.bestAsk * 1.002 : bbo.bestBid * 0.998;
            await this.edgex.placeOrder(side, quantity, aggressivePrice, false);
        }

        reportStatus() {
            const bbo = this.edgex.getBBO();
            this.bridge.send('status_report', {
                connected: this.edgex.isConnected(),
                activeOrders: Array.from(this.activeOrders.entries()),
                bbo, timestamp: Date.now()
            });
        }
    }

    // ===============================================
    // 主程序
    // ===============================================
    log('info', '========================================');
    log('info', 'EdgeX Arbitrage Frontend Starting...');
    log('info', '(Chrome Extension Version)');
    log('info', '========================================');

    // 检查扩展是否已安装
    log('info', 'Checking for bridge extension...');
    const extensionInstalled = await checkExtension();

    if (!extensionInstalled) {
        log('error', '========================================');
        log('error', 'Bridge extension NOT FOUND!');
        log('error', '');
        log('error', 'Please install the extension first:');
        log('error', '1. Open chrome://extensions');
        log('error', '2. Enable "Developer mode"');
        log('error', '3. Click "Load unpacked"');
        log('error', '4. Select the extension folder');
        log('error', '5. Refresh this page');
        log('error', '========================================');
        throw new Error('Bridge extension not installed');
    }

    log('success', 'Bridge extension detected!');

    try {
        // 创建 EdgeX 客户端
        const edgexClient = new EdgeXClient(CONFIG.edgex);
        await edgexClient.initialize();

        // 创建扩展桥接
        const bridge = new ExtensionBridge();
        await bridge.connect();

        // 创建订单执行器
        const executor = new OrderExecutor(edgexClient, bridge, CONFIG.trading);

        // 设置回调
        edgexClient.onOrderUpdate = (update) => {
            executor.handleOrderUpdate(update);
        };

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
        log('success', 'Backend: Connected via extension');
        log('success', '========================================');
        log('info', 'Waiting for trading signals from backend...');

        // 暴露到全局以便调试
        window.edgexArbitrage = {
            edgex: edgexClient,
            bridge,
            executor,
            config: CONFIG,
            getBBO: () => edgexClient.getBBO(),
            getStatus: () => ({
                edgexConnected: edgexClient.isConnected(),
                backendConnected: bridge.isConnected,
                activeOrders: executor.activeOrders.size
            }),
            testOrder: async (side, qty, price) => {
                return await edgexClient.placeOrder(side, qty, price);
            }
        };

        log('info', 'Debug: window.edgexArbitrage available');

    } catch (error) {
        log('error', 'Failed to start:', error);
        throw error;
    }

})();
