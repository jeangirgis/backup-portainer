// ============================================================
// Portainer Backup Companion — Frontend Logic v2.0
// ============================================================

// --- Auth Token Management ---
function getToken() {
    return localStorage.getItem('companion_secret_key') || '';
}

function saveSecretKey(key) {
    if (!key || !key.trim()) return;
    localStorage.setItem('companion_secret_key', key.trim());
    // Remove login modal if present
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
    // Don't show multiple modals
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

    // Focus the input after animation
    setTimeout(() => {
        const input = document.getElementById('modal-key-input');
        if (input) input.focus();
    }, 100);
}

// --- Check auth on page load ---
if (!getToken()) {
    // Delay slightly so the page renders first
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

// Observe toast containers
document.addEventListener('DOMContentLoaded', () => {
    const toastTarget = document.getElementById('restore-toast');
    if (toastTarget) {
        toastObserver.observe(toastTarget, { childList: true, subtree: true });
    }
});
