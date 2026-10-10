// Shared dropdown nav switcher for the unified Ankimon shell.
//
// One implementation for all five screens (Items, Ankidex, Profile, Team,
// Settings) — open/close, click-outside, Escape, and routing menu clicks to
// the NavBridge via nav.open<Screen>().
//
// IMPORTANT: this never creates its own QWebChannel when wireNavSwitcher() is
// used. A page that already builds a channel must pass its `nav` to
// wireNavSwitcher(nav) — creating a SECOND channel over the same transport
// overwrites the first's `transport.onmessage`, so one of them never finishes
// initializing. Pages with NO channel of their own (Ankidex) call
// initNavSwitcher(), which builds the single channel and wires from it.

(function () {
    'use strict';

    function methodFor(screen) {
        if (!screen) return null;
        return 'open' + screen.split('_')
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join('');
    }

    window.updateNavSwitcherUnresolvedCount = function (count) {
        const trigger = document.getElementById('nav-trigger');
        const menu = document.getElementById('nav-menu');
        if (!trigger || !menu) return;
        if (count > 0) {
            trigger.classList.add('has-unresolved');
            const mobileItem = menu.querySelector('.nav-menu-item[data-screen="mobile"]');
            if (mobileItem) {
                mobileItem.classList.add('has-unresolved');
            }
        } else {
            trigger.classList.remove('has-unresolved');
            const mobileItem = menu.querySelector('.nav-menu-item[data-screen="mobile"]');
            if (mobileItem) {
                mobileItem.classList.remove('has-unresolved');
            }
        }
    };

    // Wire the dropdown to a NavBridge. Safe to call with a falsy nav
    // (standalone / no bridge) — it just leaves the switcher hidden via the CSS
    // body:not(.shell-mode) rule. The current screen's menu item carries the
    // `active` class, so clicking it is a no-op (no needless reload).
    window.wireNavSwitcher = function (nav) {
        if (!nav) return;
        document.body.classList.add('shell-mode');

        const trigger = document.getElementById('nav-trigger');
        const menu = document.getElementById('nav-menu');
        if (!trigger || !menu) return;

        const open = () => {
            menu.classList.remove('hidden');
            trigger.setAttribute('aria-expanded', 'true');
        };
        const close = () => {
            menu.classList.add('hidden');
            trigger.setAttribute('aria-expanded', 'false');
        };

        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.contains('hidden') ? open() : close();
        });
        document.addEventListener('click', (e) => {
            if (!menu.classList.contains('hidden') &&
                !menu.contains(e.target) && e.target !== trigger) {
                close();
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !menu.classList.contains('hidden')) close();
        });

        menu.querySelectorAll('.nav-menu-item[data-screen]').forEach((item) => {
            item.addEventListener('click', () => {
                const screen = item.dataset.screen;
                close();
                if (item.classList.contains('active')) return;
                const fn = methodFor(screen);
                const navigate = () => {
                    if (fn && typeof nav[fn] === 'function') nav[fn]();
                };
                // Give the active page a chance to intercept (e.g. Settings
                // guarding unsaved edits). A guard returns true when it takes
                // ownership of navigation and will call `navigate` itself.
                const guard = window.navBeforeLeave;
                if (typeof guard === 'function' && guard(navigate) === true) return;
                navigate();
            });
        });

        // Initialize notification dot count
        if (typeof nav.getPendingReviewsCount === 'function') {
            nav.getPendingReviewsCount(function (count) {
                window.updateNavSwitcherUnresolvedCount(count);
            });
        }
    };

    // For pages that have NO QWebChannel of their own (e.g. Ankidex): build the
    // single channel here and wire from it. Pages that create their own channel
    // must NOT call this — they call wireNavSwitcher(nav) from their callback.
    window.initNavSwitcher = function () {
        if (typeof qt === 'undefined' || !qt.webChannelTransport) return;
        new QWebChannel(qt.webChannelTransport, function (channel) {
            window.wireNavSwitcher(channel.objects && channel.objects.nav);
        });
    };
})();

