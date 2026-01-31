/**
 * EdgeX Arbitrage Bridge - Background Service Worker
 * 负责与本地后端 WebSocket 通信
 */

let ws = null;
let isConnected = false;
let reconnectAttempts = 0;
const maxReconnectAttempts = 10;
const backendUrl = 'ws://localhost:8765';

// 存储连接的 content script 端口
let contentPorts = new Map();

// 连接到本地后端
function connectToBackend() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        return;
    }

    console.log('[Bridge] Connecting to backend:', backendUrl);

    try {
        ws = new WebSocket(backendUrl);

        ws.onopen = () => {
            console.log('[Bridge] Connected to backend');
            isConnected = true;
            reconnectAttempts = 0;

            // 通知所有 content scripts
            broadcastToContent({ type: 'backend_connected' });
        };

        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                // 转发给所有 content scripts
                broadcastToContent({ type: 'backend_message', data: message });
            } catch (e) {
                console.error('[Bridge] Failed to parse message:', e);
            }
        };

        ws.onerror = (error) => {
            console.error('[Bridge] WebSocket error:', error);
        };

        ws.onclose = () => {
            console.log('[Bridge] Connection closed');
            isConnected = false;
            ws = null;

            // 通知 content scripts
            broadcastToContent({ type: 'backend_disconnected' });

            // 尝试重连
            attemptReconnect();
        };

    } catch (error) {
        console.error('[Bridge] Failed to connect:', error);
        attemptReconnect();
    }
}

function attemptReconnect() {
    if (reconnectAttempts >= maxReconnectAttempts) {
        console.log('[Bridge] Max reconnect attempts reached');
        return;
    }

    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);

    console.log(`[Bridge] Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);

    setTimeout(() => {
        connectToBackend();
    }, delay);
}

// 发送消息到后端
function sendToBackend(message) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(message));
        return true;
    }
    return false;
}

// 广播消息给所有 content scripts
function broadcastToContent(message) {
    contentPorts.forEach((port, tabId) => {
        try {
            port.postMessage(message);
        } catch (e) {
            console.error(`[Bridge] Failed to send to tab ${tabId}:`, e);
            contentPorts.delete(tabId);
        }
    });
}

// 监听来自 content scripts 的连接
chrome.runtime.onConnect.addListener((port) => {
    if (port.name !== 'edgex-bridge') return;

    const tabId = port.sender?.tab?.id;
    console.log('[Bridge] Content script connected, tab:', tabId);

    contentPorts.set(tabId, port);

    // 如果已经连接到后端，通知新连接的 content script
    if (isConnected) {
        port.postMessage({ type: 'backend_connected' });
    }

    // 监听来自 content script 的消息
    port.onMessage.addListener((message) => {
        if (message.type === 'connect_backend') {
            // 如果已经连接，立即通知
            if (isConnected) {
                port.postMessage({ type: 'backend_connected' });
            } else {
                connectToBackend();
            }
        } else if (message.type === 'send_to_backend') {
            sendToBackend(message.data);
        }
    });

    port.onDisconnect.addListener(() => {
        console.log('[Bridge] Content script disconnected, tab:', tabId);
        contentPorts.delete(tabId);
    });
});

// 启动时尝试连接
connectToBackend();

console.log('[Bridge] Background service worker started');
