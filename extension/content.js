/**
 * EdgeX Arbitrage Bridge - Content Script
 * 注入到 EdgeX 页面，与 background service worker 通信
 */

console.log('[Bridge Content] Content script loaded');

let port = null;
let backendConnected = false;
let portConnected = false;

// 连接到 background service worker
function connectPort() {
    try {
        port = chrome.runtime.connect({ name: 'edgex-bridge' });
        portConnected = true;
        console.log('[Bridge Content] Port connected to background');

        // 监听来自 background 的消息
        port.onMessage.addListener((message) => {
            console.log('[Bridge Content] Received from background:', message.type);
            if (message.type === 'backend_connected') {
                console.log('[Bridge Content] Backend connected, forwarding to page');
                backendConnected = true;
                window.postMessage({ source: 'edgex-bridge', type: 'backend_connected' }, '*');
            } else if (message.type === 'backend_disconnected') {
                console.log('[Bridge Content] Backend disconnected');
                backendConnected = false;
                window.postMessage({ source: 'edgex-bridge', type: 'backend_disconnected' }, '*');
            } else if (message.type === 'backend_message') {
                window.postMessage({ source: 'edgex-bridge', type: 'backend_message', data: message.data }, '*');
            }
        });

        // 监听 port 断开
        port.onDisconnect.addListener(() => {
            console.log('[Bridge Content] Port disconnected from background');
            portConnected = false;
            backendConnected = false;
            // 尝试重连
            setTimeout(connectPort, 1000);
        });

        // 请求后端连接状态
        setTimeout(() => {
            if (portConnected) {
                console.log('[Bridge Content] Requesting backend connection status');
                port.postMessage({ type: 'connect_backend' });
            }
        }, 100);

    } catch (e) {
        console.error('[Bridge Content] Failed to connect port:', e);
        portConnected = false;
    }
}

// 发送消息到 background（带重试）
function sendToBackground(message, retries = 3) {
    if (!portConnected || !port) {
        console.log('[Bridge Content] Port not connected, reconnecting...');
        connectPort();
        if (retries > 0) {
            setTimeout(() => sendToBackground(message, retries - 1), 200);
        }
        return;
    }

    try {
        console.log('[Bridge Content] Sending to background:', message.type);
        port.postMessage(message);
    } catch (e) {
        console.error('[Bridge Content] Failed to send message:', e);
        if (retries > 0) {
            portConnected = false;
            connectPort();
            setTimeout(() => sendToBackground(message, retries - 1), 200);
        }
    }
}

// 监听来自页面脚本的消息
window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== 'edgex-page') return;

    const message = event.data;

    if (message.type === 'connect_backend') {
        console.log('[Bridge Content] Page requested backend connection');
        sendToBackground({ type: 'connect_backend' });
    } else if (message.type === 'send_to_backend') {
        sendToBackground({ type: 'send_to_backend', data: message.data });
    } else if (message.type === 'check_connection') {
        window.postMessage({
            source: 'edgex-bridge',
            type: 'connection_status',
            connected: backendConnected
        }, '*');
    }
});

// 初始化连接
connectPort();

// 通知页面脚本 content script 已就绪
window.postMessage({ source: 'edgex-bridge', type: 'bridge_ready' }, '*');
