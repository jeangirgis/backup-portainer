// ============================================================
// Portainer Backup Companion — Frontend Logic v2.1
// ============================================================

// --- Auth Token Management ---
function getToken() {
    return localStorage.getItem('companion_secret_key') || '';
}

function saveSecretKey(key) {
    if (!key || !key.trim()) return;
    localStorage.setItem('companion_secret_key', key.trim());
    const modal = document.getElementById('login-modal');
    if (modal) modal.remove();
    window.location.reload();
}

function logout() {
    localStorage.removeItem('companion_secret_key');
    window.location.reload();
}

// --- HTMX Auth Header Injection ---
document.addEventListener('htmx:configRequest', (event) => {
    const token = getToken();
    if (token) {
        event.detail.headers['Authorization'] = `Bearer ${token}`;
    }
});

// --- Handle 401 responses ---
document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.xhr && evt.detail.xhr.status === 401) {
        showLoginModal('Your session has expired or the key is invalid.');
    }
});

// --- Login Modal ---
function showLoginModal(message) {
    if (document.getElementById('login-modal')) return;
    const overlay = document.createElement('div');
    overlay.id = 'login-modal';
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
        <div class="modal">
            <h2>🔒 Authentication Required</h2>
            <p>${message || 'Enter your SECRET_KEY to access the dashboard.'}</p>
            <input type="password" id="modal-key-input" class="form-input" 
                   placeholder="Enter SECRET_KEY..." autofocus
                   onkeydown="if(event.key==='Enter') saveSecretKey(this.value)">
            <button class="btn btn-primary" style="width: 100%;" 
                    onclick="saveSecretKey(document.getElementById('modal-key-input').value)">
                Unlock Dashboard
            </button>
        </div>
    `;
    document.body.appendChild(overlay);
    setTimeout(() => {
        const input = document.getElementById('modal-key-input');
        if (input) input.focus();
    }, 100);
}

// --- Check auth on page load ---
if (!getToken()) {
    setTimeout(() => showLoginModal(), 200);
}

// --- Auto-dismiss toasts after 8 seconds ---
const toastObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
            if (node.nodeType === 1 && node.classList && node.classList.contains('toast')) {
                setTimeout(() => {
                    node.style.transition = 'opacity 0.3s ease';
                    node.style.opacity = '0';
                    setTimeout(() => node.remove(), 300);
                }, 8000);
            }
        });
    });
});

document.addEventListener('DOMContentLoaded', () => {
    const toastTarget = document.getElementById('restore-toast');
    if (toastTarget) {
        toastObserver.observe(toastTarget, { childList: true, subtree: true });
    }
});

// ============================================================
// SETTINGS PAGE FUNCTIONS
// ============================================================

let selectedStorage = 'local';

// --- Helper: API call ---
async function apiCall(url, method = 'GET', body = null) {
    const opts = {
        method,
        headers: {
            'Authorization': `Bearer ${getToken()}`,
            'Content-Type': 'application/json',
        },
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(url, opts);
    return resp.json();
}

// --- Show result message ---
function showResult(targetId, status, message) {
    const el = document.getElementById(targetId);
    if (!el) return;
    el.innerHTML = `<div class="result-msg ${status === 'ok' ? 'success' : 'error'}">${message}</div>`;
    setTimeout(() => { el.innerHTML = ''; }, 8000);
}

// ═══════════════════════════════════════
// TABS
// ═══════════════════════════════════════

function selectTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const btn = document.getElementById('tab-' + tabName);
    const content = document.getElementById('content-' + tabName);
    if (btn) btn.classList.add('active');
    if (content) content.classList.add('active');

    // Load config when switching to storage or notifications tab
    if (tabName === 'storage') loadStorageConfig();
    if (tabName === 'notifications') loadNotificationConfig();
}

// ═══════════════════════════════════════
// STORAGE
// ═══════════════════════════════════════

function selectStorage(provider) {
    selectedStorage = provider;
    // Update cards
    document.querySelectorAll('.provider-card').forEach(c => c.classList.remove('active'));
    const card = document.getElementById('card-' + provider);
    if (card) card.classList.add('active');
    // Show/hide config panels
    ['local', 's3', 'sftp', 'gdrive'].forEach(p => {
        const panel = document.getElementById('config-' + p);
        if (panel) panel.classList.toggle('hidden', p !== provider);
    });
}

async function loadStorageConfig() {
    try {
        const data = await apiCall('/api/settings/storage/current');
        if (data.backend) {
            selectStorage(data.backend);
        }
        const configs = data.configs || {};
        // Populate Local
        if (configs.local) {
            setVal('local-backup-dir', configs.local.backup_dir);
        }
        // Populate S3
        if (configs.s3) {
            setVal('s3-bucket', configs.s3.bucket);
            setVal('s3-region', configs.s3.region);
            setVal('s3-access-key', configs.s3.access_key);
            setVal('s3-secret-key', configs.s3.secret_key);
            setVal('s3-endpoint', configs.s3.endpoint_url);
            setVal('s3-prefix', configs.s3.prefix);
        }
        // Populate SFTP
        if (configs.sftp) {
            setVal('sftp-host', configs.sftp.host);
            setVal('sftp-port', configs.sftp.port);
            setVal('sftp-user', configs.sftp.user);
            setVal('sftp-password', configs.sftp.password);
            setVal('sftp-key-path', configs.sftp.key_path);
            setVal('sftp-remote-dir', configs.sftp.remote_dir);
        }
        // Populate GDrive
        if (configs.gdrive) {
            setVal('gdrive-folder-id', configs.gdrive.folder_id);
        }
    } catch (e) {
        console.error('Failed to load storage config:', e);
    }
}

function getStorageConfig() {
    const configs = {
        local: { backup_dir: getVal('local-backup-dir') || '/backups' },
        s3: {
            bucket: getVal('s3-bucket'),
            region: getVal('s3-region') || 'us-east-1',
            access_key: getVal('s3-access-key'),
            secret_key: getVal('s3-secret-key'),
            endpoint_url: getVal('s3-endpoint'),
            prefix: getVal('s3-prefix') || 'backups/',
        },
        sftp: {
            host: getVal('sftp-host'),
            port: getVal('sftp-port') || '22',
            user: getVal('sftp-user'),
            password: getVal('sftp-password'),
            key_path: getVal('sftp-key-path'),
            remote_dir: getVal('sftp-remote-dir') || '/backups',
        },
        gdrive: {
            folder_id: getVal('gdrive-folder-id'),
            credentials_json: getVal('gdrive-credentials'),
        },
    };
    return configs[selectedStorage] || {};
}

async function saveStorage() {
    const btn = document.getElementById('btn-save-storage');
    btn.textContent = '⏳ Saving...';
    btn.disabled = true;
    try {
        const result = await apiCall('/api/settings/storage', 'POST', {
            backend: selectedStorage,
            config: getStorageConfig(),
        });
        showResult('storage-result', result.status, result.message || 'Storage configuration saved!');
    } catch (e) {
        showResult('storage-result', 'error', 'Failed to save: ' + e.message);
    } finally {
        btn.textContent = '💾 Save & Apply';
        btn.disabled = false;
    }
}

async function testStorage() {
    const btn = document.getElementById('btn-test-storage');
    btn.classList.add('loading');
    btn.textContent = '⏳ Testing...';
    try {
        const result = await apiCall('/api/settings/storage/test', 'POST', {
            backend: selectedStorage,
            config: getStorageConfig(),
        });
        showResult('storage-result', result.status, result.message);
    } catch (e) {
        showResult('storage-result', 'error', 'Test failed: ' + e.message);
    } finally {
        btn.classList.remove('loading');
        btn.textContent = '🔌 Test Connection';
    }
}

// ═══════════════════════════════════════
// NOTIFICATIONS
// ═══════════════════════════════════════

function toggleChannel(channel) {
    const el = document.getElementById('channel-' + channel);
    if (el) el.classList.toggle('expanded');
}

function updateChannelState(channel) {
    const checkbox = document.getElementById('notif-' + channel + '-enabled');
    const el = document.getElementById('channel-' + channel);
    if (checkbox && el) {
        el.classList.toggle('enabled', checkbox.checked);
        // Auto-expand when enabled
        if (checkbox.checked && !el.classList.contains('expanded')) {
            el.classList.add('expanded');
        }
    }
}

async function loadNotificationConfig() {
    try {
        const data = await apiCall('/api/settings/notifications/current');
        // Email
        if (data.email) {
            document.getElementById('notif-email-enabled').checked = data.email.enabled;
            setVal('smtp-host', data.email.smtp_host);
            setVal('smtp-port', data.email.smtp_port);
            setVal('smtp-user', data.email.smtp_user);
            setVal('smtp-password', data.email.smtp_password);
            setVal('email-from', data.email.from_address);
            setVal('email-to', data.email.to_address);
            if (data.email.smtp_use_tls !== undefined) {
                document.getElementById('smtp-tls').checked = data.email.smtp_use_tls;
            }
            updateChannelState('email');
        }
        // Telegram
        if (data.telegram) {
            document.getElementById('notif-telegram-enabled').checked = data.telegram.enabled;
            setVal('telegram-token', data.telegram.bot_token);
            setVal('telegram-chat-id', data.telegram.chat_id);
            updateChannelState('telegram');
        }
        // Slack
        if (data.slack) {
            document.getElementById('notif-slack-enabled').checked = data.slack.enabled;
            setVal('slack-webhook', data.slack.webhook_url);
            updateChannelState('slack');
        }
        // Webhook
        if (data.webhook) {
            document.getElementById('notif-webhook-enabled').checked = data.webhook.enabled;
            setVal('webhook-url', data.webhook.url);
            updateChannelState('webhook');
        }
    } catch (e) {
        console.error('Failed to load notification config:', e);
    }
}

function getNotificationPayload() {
    return {
        email: {
            enabled: document.getElementById('notif-email-enabled')?.checked || false,
            smtp_host: getVal('smtp-host'),
            smtp_port: parseInt(getVal('smtp-port')) || 587,
            smtp_user: getVal('smtp-user'),
            smtp_password: getVal('smtp-password'),
            smtp_use_tls: document.getElementById('smtp-tls')?.checked ?? true,
            from_address: getVal('email-from'),
            to_address: getVal('email-to'),
        },
        telegram: {
            enabled: document.getElementById('notif-telegram-enabled')?.checked || false,
            bot_token: getVal('telegram-token'),
            chat_id: getVal('telegram-chat-id'),
        },
        slack: {
            enabled: document.getElementById('notif-slack-enabled')?.checked || false,
            webhook_url: getVal('slack-webhook'),
        },
        webhook: {
            enabled: document.getElementById('notif-webhook-enabled')?.checked || false,
            url: getVal('webhook-url'),
        },
    };
}

async function saveNotifications() {
    const btn = document.getElementById('btn-save-notif');
    btn.textContent = '⏳ Saving...';
    btn.disabled = true;
    try {
        const result = await apiCall('/api/settings/notifications', 'POST', getNotificationPayload());
        showResult('notif-save-result', result.status, result.message || 'Notifications saved!');
    } catch (e) {
        showResult('notif-save-result', 'error', 'Failed: ' + e.message);
    } finally {
        btn.textContent = '💾 Save All Notifications';
        btn.disabled = false;
    }
}

async function testNotification(channel) {
    const payload = getNotificationPayload();
    const config = payload[channel] || {};
    const resultId = channel + '-test-result';
    
    showResult(resultId, 'ok', '⏳ Sending test...');
    try {
        const result = await apiCall('/api/settings/notifications/test', 'POST', {
            channel: channel,
            config: config,
        });
        showResult(resultId, result.status, result.message);
    } catch (e) {
        showResult(resultId, 'error', 'Test failed: ' + e.message);
    }
}

// ═══════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════

function getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value : '';
}

function setVal(id, value) {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) {
        el.value = value;
    }
}
