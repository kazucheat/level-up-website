// NXC LEVEL UP — shared helpers

const API = '';

function getToken() { return localStorage.getItem('nxc_token'); }
function getUser() {
    try { return JSON.parse(localStorage.getItem('nxc_user') || '{}'); }
    catch { return {}; }
}

async function api(path, opts = {}) {
    const token = getToken();
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (token) headers['Authorization'] = 'Bearer ' + token;

    const res = await fetch(API + path, {
        ...opts,
        headers,
        body: opts.body ? JSON.stringify(opts.body) : undefined
    });
    if (res.status === 401) {
        localStorage.removeItem('nxc_token');
        localStorage.removeItem('nxc_user');
        location.href = '/login';
        return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    return data;
}

// logout
document.addEventListener('DOMContentLoaded', () => {
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            localStorage.removeItem('nxc_token');
            localStorage.removeItem('nxc_user');
            location.href = '/';
        });
    }

    // auth guard
    if (document.body.dataset.auth === 'true' && !getToken()) {
        location.href = '/login';
    }
});