// static/js/settings.js
/* global io */
// Handles various settings forms: account risk, notifications, API keys, and data management

function saveAccountSettings() {
    const settings = {
        risk_per_trade: parseFloat(document.getElementById('risk-per-trade').value),
        max_positions: parseInt(document.getElementById('max-positions').value),
        loss_limit: parseFloat(document.getElementById('loss-limit').value),
        target_profit: parseFloat(document.getElementById('target-profit').value)
    };
    localStorage.setItem('account_settings', JSON.stringify(settings));
    alert('Account settings saved');
}

function attachAccountForm() {
    const form = document.getElementById('account-settings');
    if (!form) return;
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        saveAccountSettings();
    });
    // Load stored
    const stored = localStorage.getItem('account_settings');
    if (stored) {
        const s = JSON.parse(stored);
        document.getElementById('risk-per-trade').value = s.risk_per_trade;
        document.getElementById('max-positions').value = s.max_positions;
        document.getElementById('loss-limit').value = s.loss_limit;
        document.getElementById('target-profit').value = s.target_profit;
    }
}

// Notification toggles and credentials
function saveNotificationSettings() {
    const settings = {
        notify_orders: document.getElementById('notify-orders').checked,
        notify_alerts: document.getElementById('notify-alerts').checked,
        notify_signals: document.getElementById('notify-signals').checked,
        notify_pnl: document.getElementById('notify-pnl').checked,
        enable_telegram: document.getElementById('enable-telegram').checked,
        telegram_token: document.getElementById('telegram-token').value,
        telegram_chat_id: document.getElementById('telegram-chat-id').value,
        enable_email: document.getElementById('enable-email').checked,
        gmail_user: document.getElementById('gmail-user').value,
        gmail_pass: document.getElementById('gmail-pass').value
    };
    localStorage.setItem('notification_settings', JSON.stringify(settings));
    alert('Notification settings saved');
}

function attachNotificationButtons() {
    document.getElementById('saveNotificationSettings')?.addEventListener('click', saveNotificationSettings);
    // Load stored
    const stored = localStorage.getItem('notification_settings');
    if (!stored) return;
    const s = JSON.parse(stored);
    document.getElementById('notify-orders').checked = s.notify_orders;
    document.getElementById('notify-alerts').checked = s.notify_alerts;
    document.getElementById('notify-signals').checked = s.notify_signals;
    document.getElementById('notify-pnl').checked = s.notify_pnl;
    document.getElementById('enable-telegram').checked = s.enable_telegram;
    document.getElementById('telegram-token').value = s.telegram_token;
    document.getElementById('telegram-chat-id').value = s.telegram_chat_id;
    document.getElementById('enable-email').checked = s.enable_email;
    document.getElementById('gmail-user').value = s.gmail_user;
    document.getElementById('gmail-pass').value = s.gmail_pass;
}

// API settings save/load
function saveApiSettings() {
    const settings = {
        api_key: document.getElementById('api-key').value,
        secret_key: document.getElementById('secret-key').value,
        client_id: document.getElementById('client-id').value
    };
    localStorage.setItem('api_settings', JSON.stringify(settings));
    alert('API settings saved');
}

function attachApiForm() {
    const form = document.getElementById('api-settings');
    if (!form) return;
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        saveApiSettings();
    });
    const stored = localStorage.getItem('api_settings');
    if (stored) {
        const s = JSON.parse(stored);
        document.getElementById('api-key').value = s.api_key;
        document.getElementById('secret-key').value = s.secret_key;
        document.getElementById('client-id').value = s.client_id;
    }
}

// Test helpers
window.testTelegramNotification = async () => {
    const token = document.getElementById('telegram-token').value.trim();
    const chatId = document.getElementById('telegram-chat-id').value.trim();
    try {
        const res = await fetch('/test_telegram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, chat_id: chatId })
        });
        const data = await res.json();
        alert(data.success ? 'Telegram sent successfully' : `Telegram failed: ${data.error || 'unknown error'}`);
    } catch (e) {
        alert('Telegram error: ' + e.message);
    }
};
window.testEmailNotification = async () => {
    const email = document.getElementById('gmail-user').value.trim();
    const password = document.getElementById('gmail-pass').value.trim();
    try {
        const res = await fetch('/test_email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        const data = await res.json();
        alert(data.success ? 'Email sent successfully' : `Email failed: ${data.error || 'unknown error'}`);
    } catch (e) {
        alert('Email error: ' + e.message);
    }
};
window.exportTrades = () => alert('Trades exported (placeholder)');
window.exportPerformance = () => alert('Performance exported (placeholder)');
window.backupSettings = () => alert('Backup complete (placeholder)');
window.restoreSettings = () => alert('Restore complete (placeholder)');

// show/hide password
window.togglePassword = (id) => {
    const input = document.getElementById(id);
    if (!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
}

async function initDryRunToggle() {
    const toggle = document.getElementById('dry-run-toggle');
    if (!toggle) return;

    // initial fetch
    try {
        const res = await fetch('/api/dry-run');
        const data = await res.json();
        toggle.checked = data.dry_run;
    } catch (e) { console.error('Dry-run fetch error', e); }

    toggle.addEventListener('change', async () => {
        const state = toggle.checked;
        try {
            await fetch('/api/dry-run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dry_run: state })
            });
        } catch (e) { console.error('Dry-run update error', e); }
    });

    // live updates
    if (typeof io !== 'undefined') {
        const socket = io();
        socket.on('dry_run_change', (data) => {
            toggle.checked = data.dry_run;
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    attachAccountForm();
    attachNotificationButtons();
    attachApiForm();
    initDryRunToggle();
});
