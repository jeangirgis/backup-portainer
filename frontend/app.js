document.addEventListener('htmx:configRequest', (event) => {
    const secretKey = localStorage.getItem('companion_secret_key') || '';
    event.detail.headers['Authorization'] = `Bearer ${secretKey}`;
});

// Helper to save secret key
function saveSecretKey(key) {
    localStorage.setItem('companion_secret_key', key);
    window.location.reload();
}

// Check if secret key is set, if not prompt for it (simplified for now)
if (!localStorage.getItem('companion_secret_key')) {
    const key = prompt('Please enter your Companion SECRET_KEY:');
    if (key) saveSecretKey(key);
}

document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.xhr.status === 401) {
        const key = prompt('Unauthorized. Please enter your Companion SECRET_KEY:');
        if (key) saveSecretKey(key);
    }
});
