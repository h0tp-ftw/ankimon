function showConfirm(message, onConfirm, onCancel = null) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '1000';

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.maxWidth = '400px';

    const head = document.createElement('div');
    head.className = 'modal-head';
    const title = document.createElement('h3');
    title.textContent = 'Confirmation';
    head.appendChild(title);

    const body = document.createElement('div');
    body.className = 'modal-body';
    body.style.padding = '18px';
    body.style.fontSize = '0.95rem';
    body.style.lineHeight = '1.4';
    body.style.color = 'var(--text-main)';
    body.textContent = message;

    const foot = document.createElement('div');
    foot.className = 'modal-foot';
    foot.style.gap = '10px';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = function() {
        document.body.removeChild(overlay);
        if (onCancel) onCancel();
    };

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.textContent = 'Confirm';
    confirmBtn.onclick = function() {
        document.body.removeChild(overlay);
        onConfirm();
    };

    foot.appendChild(cancelBtn);
    foot.appendChild(confirmBtn);

    modal.appendChild(head);
    modal.appendChild(body);
    modal.appendChild(foot);
    overlay.appendChild(modal);

    document.body.appendChild(overlay);
}

function showAlert(message, onOk = null) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '1000';

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.maxWidth = '400px';

    const head = document.createElement('div');
    head.className = 'modal-head';
    const title = document.createElement('h3');
    title.textContent = 'Notification';
    head.appendChild(title);

    const body = document.createElement('div');
    body.className = 'modal-body';
    body.style.padding = '18px';
    body.style.fontSize = '0.95rem';
    body.style.lineHeight = '1.4';
    body.style.color = 'var(--text-main)';
    body.textContent = message;

    const foot = document.createElement('div');
    foot.className = 'modal-foot';

    const okBtn = document.createElement('button');
    okBtn.className = 'btn btn-primary';
    okBtn.textContent = 'OK';
    okBtn.onclick = function() {
        document.body.removeChild(overlay);
        if (onOk) onOk();
    };

    foot.appendChild(okBtn);

    modal.appendChild(head);
    modal.appendChild(body);
    modal.appendChild(foot);
    overlay.appendChild(modal);

    document.body.appendChild(overlay);
}

const OUTCOME_META = Object.freeze({
    caught: ['badge-caught', 'CAUGHT'],
    defeated: ['badge-defeated', 'DEFEATED'],
    lost: ['badge-lost', 'LOST'],
    escaped: ['badge-escaped', 'ESCAPED'],
});

let mobileBridge = null;
let nav = null;


new QWebChannel(qt.webChannelTransport, function(channel) {
    mobileBridge = channel.objects.mobile;
    nav = channel.objects && channel.objects.nav;
    window.nav = nav;
    loadHistory();
    if (window.wireNavSwitcher) {
        window.wireNavSwitcher(nav);
    }
});

function goToMobileReviews() {
    if (nav && typeof nav.openMobile === 'function') {
        nav.openMobile();
    }
}

function goToHistory() {
    // Already on History tab
}

function loadHistory() {
    if (!mobileBridge || typeof mobileBridge.getMobileHistory !== 'function') return;
    mobileBridge.getMobileHistory(function(historyList) {
        initializeHistory(historyList);
    });
}

window.initializeHistory = function(historyList) {
    const loadingEl = document.getElementById('loading');
    if (loadingEl) {
        loadingEl.style.display = 'none';
    }
    renderHistory(historyList);
};

window.liveRefreshHistory = function(historyList) {
    renderHistory(historyList);
};

function finiteNumber(value, fallback) {
    if (typeof value !== 'number' && typeof value !== 'string') return fallback;
    if (typeof value === 'string' && value.trim() === '') return fallback;
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function positiveNumber(value) {
    const number = finiteNumber(value, 0);
    return number > 0 ? number : 0;
}

function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
}

function appendText(element, text) {
    element.appendChild(document.createTextNode(String(text)));
}

function renderHistory(historyList) {
    const emptyEl = document.getElementById('history-empty');
    const listEl = document.getElementById('history-list');

    const entries = Array.isArray(historyList) ? historyList : [];
    if (entries.length === 0) {
        if (emptyEl) emptyEl.classList.remove('hidden');
        if (listEl) listEl.classList.add('hidden');
        return;
    }
    
    if (emptyEl) emptyEl.classList.add('hidden');
    if (listEl) listEl.classList.remove('hidden');
    
    listEl.replaceChildren();

    entries.forEach(entry => {
        if (!entry || typeof entry !== 'object') return;

        const outcome = Object.prototype.hasOwnProperty.call(OUTCOME_META, entry.outcome)
            ? String(entry.outcome)
            : '';
        const outcomeMeta = outcome ? OUTCOME_META[outcome] : null;
        const item = document.createElement('div');
        item.className = 'history-item' + (outcome ? ` outcome-${outcome}` : '');

        const main = makeElement('div', 'history-item-main');
        const left = makeElement('div', 'history-item-left');
        if (outcomeMeta) {
            left.appendChild(makeElement(
                'span',
                `outcome-badge ${outcomeMeta[0]}`,
                outcomeMeta[1]
            ));
        }

        const details = makeElement('span', 'history-item-details');
        appendText(details, 'Your ');
        details.appendChild(makeElement('strong', '', entry.companion_name || 'Companion'));
        appendText(details, ` (Lv.${finiteNumber(entry.companion_level, 5)}) vs wild `);
        details.appendChild(makeElement(
            'strong',
            '',
            (entry.enemy_shiny ? '✨ ' : '') + (entry.enemy_name || '???')
        ));
        appendText(details, ` (Lv.${finiteNumber(entry.enemy_level, 5)})`);
        left.appendChild(details);
        main.appendChild(left);
        main.appendChild(makeElement('span', 'history-item-time', formatTime(entry.timestamp)));
        item.appendChild(main);

        const rewardValues = [
            [entry.xp_gained, 'reward-xp', ' XP'],
            [entry.trainer_xp_gained, 'reward-txp', ' Trainer XP'],
            [entry.cash_gained, 'reward-cash', '¥'],
        ];
        const rewards = makeElement('div', 'history-item-rewards');
        rewardValues.forEach(([rawValue, rewardClass, suffix]) => {
            const value = positiveNumber(rawValue);
            if (!value) return;
            if (rewards.childNodes.length) appendText(rewards, ' ');
            rewards.appendChild(makeElement(
                'span',
                `reward-val ${rewardClass}`,
                `+${value}${suffix}`
            ));
        });
        if (rewards.childNodes.length) item.appendChild(rewards);

        listEl.appendChild(item);
    });
}

function clearHistory() {
    if (!mobileBridge || typeof mobileBridge.clearMobileHistory !== 'function') return;
    showConfirm("Are you sure you want to clear your mobile battle history?", function() {
        mobileBridge.clearMobileHistory(function(success) {
            if (success) {
                loadHistory();
            } else {
                showAlert("Failed to clear mobile history.");
            }
        });
    });
}

function formatTime(timestamp) {
    if (!timestamp) return '';
    try {
        // Timestamps arrive as epoch-ms numbers (or numeric strings after a
        // JSON round-trip); coerce and guard so an invalid value renders as
        // blank instead of "Invalid Date" — matches formatTime in mobile.js.
        const ts = Number(timestamp);
        if (isNaN(ts) || ts <= 0) return '';
        const date = new Date(ts);
        const now = new Date();
        
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return '';
    }
}
