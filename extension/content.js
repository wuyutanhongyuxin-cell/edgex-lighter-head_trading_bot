/**
 * EdgeX Arbitrage Bridge - Content Script
 * 注入到 EdgeX 页面，与 background service worker 通信
 */

console.log('[Bridge Content] Content script loaded');

// 连接到 background service worker
const port = chrome.runtime.connect({ name: 'edgex-bridge' });

let backendConnected = false;

// 监听来自 background 的消息
port.onMessage.addListener((message) => {
    if (message.type === 'backend_connected') {
        console.log('[Bridge Content] Backend connected');
        backendConnected = true;
        // 转发给页面脚本
        window.postMessage({ source: 'edgex-bridge', type: 'backend_connected' }, '*');
    } else if (message.type === 'backend_disconnected') {
        console.log('[Bridge Content] Backend disconnected');
        backendConnected = false;
        window.postMessage({ source: 'edgex-bridge', type: 'backend_disconnected' }, '*');
    } else if (message.type === 'backend_message') {
        // 转发后端消息给页面脚本
        window.postMessage({ source: 'edgex-bridge', type: 'backend_message', data: message.data }, '*');
    }
});

// 监听来自页面脚本的消息
window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== 'edgex-page') return;

    const message = event.data;

    if (message.type === 'connect_backend') {
        console.log('[Bridge Content] Page requested backend connection');
        port.postMessage({ type: 'connect_backend' });
    } else if (message.type === 'send_to_backend') {
        port.postMessage({ type: 'send_to_backend', data: message.data });
    } else if (message.type === 'check_connection') {
        window.postMessage({
            source: 'edgex-bridge',
            type: 'connection_status',
            connected: backendConnected
        }, '*');
    }
});

// 通知页面脚本 content script 已就绪
window.postMessage({ source: 'edgex-bridge', type: 'bridge_ready' }, '*');

// 请求连接后端
port.postMessage({ type: 'connect_backend' });
