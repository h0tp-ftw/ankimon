// Profile screen — trainer identity card + badge case, with the sprite picker
// folded in as an avatar-click modal. Python pushes data via
// window.initializeProfile(); the sprite list is lazy-loaded when the modal
// first opens. Opened standalone (no qt bridge) it renders mock data.

(function () {
    'use strict';

    const SPRITE_BASE = '../user_files/sprites/front_default';   // team strip
    const BADGE_BASE = '../user_files/sprites/badges';
    const TRAINER_BASE = '../addon_sprites/trainers';

    const state = {
        data: null,
        sprites: null,        // null = not loaded yet
        spriteGens: [],       // [{key,label}] generation filter chips
        spriteCats: [],       // [category] type filter chips
        spriteGenders: [],    // [{key,label}] sex filter chips
        spriteCurrent: '',
        spriteSearch: '',
        spriteGen: 'all',
        spriteCat: 'all',
        spriteSex: 'all',
    };
    let trainer = null;

    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }
    function num(n) { return (Number(n) || 0).toLocaleString(); }
    // Pokémon names arrive lowercase from the DB ("dragonite", "mr-mime");
    // capitalize each word segment for display.
    function capName(s) {
        return String(s == null ? '' : s).replace(
            /(^|[\s\-/])([a-z])/g,
            (_, sep, c) => sep + c.toUpperCase()
        );
    }

    function pkmnSprite(stub) {
        if (!stub || !stub.p) return SPRITE_BASE + '/0.png';
        return stub.s ? SPRITE_BASE + '/shiny/' + stub.p + '.png'
                      : SPRITE_BASE + '/' + stub.p + '.png';
    }

    // ---------------- Render ----------------
    function render(animateRecent) {
        const data = state.data;
        if (!data) return;
        const hr = new Date().getHours();
        const part = hr < 12 ? 'Good morning' : (hr < 18 ? 'Good afternoon' : 'Good evening');
        setText('pf-greeting', data.name ? `${part}, ${data.name}!` : `${part}!`);
        renderRail(data);
        renderStatTiles(data);
        renderTeamShowcase(data.team);
        renderRecent(data.recent, !!animateRecent);
        renderBadges(data.badge_grid);
    }

    function renderRail(data) {
        const avatar = document.getElementById('avatar-btn');
        avatar.innerHTML =
            (data.sprite_url
                ? `<img src="${esc(data.sprite_url)}" alt="${esc(data.name)}" onerror="this.onerror=null;this.src='${TRAINER_BASE}/red.png';">`
                : `<div class="no-sprite">No sprite</div>`)
            + `<span class="change-hint">✎ Change sprite</span>`;

        // Fallback icons (only shown when a Pokémon sprite can't be resolved).
        const ICON_FAV = '<svg viewBox="0 0 24 24" fill="none" stroke="#f85149" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 1 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78z"/></svg>';
        const ICON_HI = '<svg viewBox="0 0 24 24" fill="none" stroke="#d29922" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>';
        const ICON_FR = '<svg viewBox="0 0 24 24" fill="none" stroke="#3fb950" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>';
        const pokeItem = (stub, fallback, nameVal, label) => {
            const hero = (stub && stub.sprite)
                ? `<img src="${esc(stub.sprite)}" alt="" onerror="this.onerror=null;this.src='${SPRITE_BASE}/0.png';">`
                : fallback;
            return `<div class="rail-poke"><div class="rail-poke-sprite">${hero}</div>` +
                `<div class="rail-poke-name">${nameVal}</div>` +
                `<div class="rail-poke-label">${label}</div></div>`;
        };
        const fav = data.favorite;
        const hi = data.highest;
        const fr = data.friendship;
        const favVal = (fav && fav.n) ? esc(fav.n) : esc(data.favorite_pokemon);
        const hiVal = (hi && hi.n) ? esc(hi.n) : esc(data.highest_level_pokemon);
        const hiLabel = (hi && hi.l) ? `Highest · Lv ${esc(hi.l)}` : 'Highest Level';
        const frVal = (fr && fr.n) ? esc(fr.n) : '—';
        document.getElementById('rail-meta').innerHTML = `
            <div class="rail-name" id="rail-name" role="button" tabindex="0" title="Click to rename">${esc(data.name)}<span class="rename-hint" aria-hidden="true">✎</span></div>
            <span class="league-chip">★ ${esc(data.league)}</span>
            <div class="rail-divider"></div>
            <div class="rail-info">
                ${pokeItem(fav, ICON_FAV, favVal, 'Favorite Pokémon')}
                ${pokeItem(hi, ICON_HI, hiVal, hiLabel)}
                ${pokeItem(fr, ICON_FR, frVal, 'Best Friend')}
            </div>`;

        // Level/XP + Total XP live at the bottom of the rail as a progress footer.
        const xp = Number(data.xp) || 0;
        const xpNext = Number(data.xp_for_next_level) || 0;
        const pct = xpNext > 0 ? Math.min(100, Math.round((xp / xpNext) * 100)) : 0;
        const remaining = Math.max(0, xpNext - xp);
        document.getElementById('rail-xp').innerHTML = `
            <div class="rail-xp-head"><span class="rail-xp-level">Level ${esc(data.level)}</span></div>
            <div class="xp-track"><div class="xp-fill" style="width:${pct}%"></div></div>
            <div class="rail-xp-val">${num(xp)} / ${num(xpNext)} XP</div>
            ${xpNext > 0 ? `<div class="rail-xp-next">${num(remaining)} XP to next level</div>` : ''}`;
    }

    function renderTeamShowcase(team) {
        team = team || [];
        let html = '';
        for (let i = 0; i < 6; i++) {
            const m = team[i];
            if (m) {
                html += `<div class="team-mini">
                    <div class="tm-sprite"><img src="${m.sprite || pkmnSprite(m)}" alt="${esc(m.n)}" onerror="this.onerror=null;this.src='${SPRITE_BASE}/0.png';"></div>
                    <div class="tm-name">${esc(capName(m.n))}</div>
                    <div class="tm-lv">Lv ${esc(m.l)}</div>
                </div>`;
            } else {
                html += `<div class="team-mini empty"><div class="tm-empty">+</div></div>`;
            }
        }
        document.getElementById('team-showcase').innerHTML = html;
    }

    // Stable keys for the Recently-Caught cards so a live update can tell which
    // card is genuinely new (→ plays the enter animation) vs. already on screen.
    const shownRecentKeys = new Set();
    let shownRecentOrder = [];
    function recentKey(m) {
        if (!m) return '';
        return (m.id != null) ? 'id:' + m.id : [m.p, m.n, m.l].join('|');
    }
    function sameOrder(a, b) {
        if (a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
        return true;
    }

    function renderRecent(recent, animateNew) {
        recent = recent || [];
        const el = document.getElementById('recent-grid');
        if (!recent.length) {
            el.innerHTML = '<div style="color:var(--text-muted);padding:8px;">No Pokémon caught yet.</div>';
            shownRecentKeys.clear();
            shownRecentOrder = [];
            return;
        }
        const keys = recent.map(recentKey);
        // If the list is unchanged (same Pokémon, same order), leave the DOM
        // alone — a rebuild would interrupt in-flight animations like the
        // lingering "NEW" badge. Stat-only refreshes (cash/XP) land here.
        if (sameOrder(keys, shownRecentOrder)) return;
        el.innerHTML = recent.map((m, i) => {
            const isNew = animateNew && !shownRecentKeys.has(keys[i]);
            return `<div class="team-mini${isNew ? ' just-caught' : ''}">
                <div class="tm-sprite"><img src="${m.sprite || pkmnSprite(m)}" alt="${esc(m.n)}" onerror="this.onerror=null;this.src='${SPRITE_BASE}/0.png';"></div>
                <div class="tm-name">${esc(capName(m.n))}${m.s ? ' <span class="shiny-dot">★</span>' : ''}</div>
                <div class="tm-lv">Lv ${esc(m.l)}</div>
            </div>`;
        }).join('');
        shownRecentKeys.clear();
        keys.forEach((k) => shownRecentKeys.add(k));
        shownRecentOrder = keys;
    }

    function renderStatTiles(data) {
        const caught = Number(data.caught) || 0;
        const shinies = Number(data.shinies) || 0;
        const shinyRate = (caught > 0 && shinies > 0)
            ? '1 in ' + Math.round(caught / shinies).toLocaleString()
            : '—';
        const tiles = [
            ['Cash', num(data.cash) + ' ¥', 'var(--accent-gold)'],
            ['Caught', num(data.caught), 'var(--accent-green)'],
            ['Pokédex', num(data.dex_seen), 'var(--accent-blue)'],
            ['Shinies', num(data.shinies), 'var(--accent-gold)'],
            ['Total XP', num(data.total_xp), 'var(--accent-blue)'],
            ['Shiny Rate', shinyRate, 'var(--accent-red)'],
        ];
        document.getElementById('stat-tiles').innerHTML = tiles.map((t) =>
            `<div class="stat-tile" style="border-top:2px solid ${t[2]}">
                <div class="st-value">${t[1]}</div>
                <div class="st-label">${t[0]}</div>
            </div>`
        ).join('');
    }

    function renderBadges(grid) {
        grid = grid || [];
        const el = document.getElementById('badge-grid');
        const unlocked = grid.filter((b) => b.unlocked).length;
        setText('badge-summary', grid.length ? `${unlocked} / ${grid.length} earned` : '');
        if (!grid.length) {
            el.innerHTML = '<div style="color:var(--text-muted);">No badges defined.</div>';
            return;
        }
        const frag = document.createDocumentFragment();
        grid.forEach((b) => {
            const cell = document.createElement('div');
            cell.className = 'badge-cell' + (b.unlocked ? ' unlocked' : ' locked');
            cell.innerHTML = `
                <img src="${BADGE_BASE}/${b.id}.png" alt="${esc(b.name)}"
                     onerror="this.onerror=null;this.src='${BADGE_BASE}/default.png';">
                <span class="badge-tip">${esc(b.name)}</span>`;
            frag.appendChild(cell);
        });
        el.replaceChildren(frag);
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    // ---------------- Sprite picker modal ----------------
    function openSpriteModal() {
        document.getElementById('sprite-modal').classList.remove('hidden');
        const search = document.getElementById('sprite-search');
        search.value = '';
        state.spriteSearch = '';
        state.spriteGen = 'all';
        state.spriteCat = 'all';
        state.spriteSex = 'all';

        if (state.sprites === null) {
            renderSpriteGrid('Loading…');
            if (trainer && trainer.getSprites) {
                trainer.getSprites().then((res) => {
                    state.sprites = (res && res.sprites) || [];
                    state.spriteGens = (res && res.generations) || [];
                    state.spriteCats = (res && res.categories) || [];
                    state.spriteGenders = (res && res.genders) || [];
                    state.spriteCurrent = (res && res.current) || state.spriteCurrent;
                    buildSpriteToolbar();
                    renderSpriteGrid();
                });
            } else {
                state.sprites = [];
                buildSpriteToolbar();
                renderSpriteGrid();
            }
        } else {
            buildSpriteToolbar();
            renderSpriteGrid();
        }
        setTimeout(() => search.focus(), 0);
    }

    // Condensed filters: Type / Gen / Sex each become a compact dropdown that
    // sits inline with the search bar. Built from the lists the bridge ships.
    function buildSpriteToolbar() {
        const cats = (state.spriteCats || []).map((c) => ({ key: c, label: c }));
        renderFilterSelect('fs-cat', 'cat', 'Type', cats, state.spriteCat);
        renderFilterSelect('fs-gen', 'gen', 'Gen', state.spriteGens || [], state.spriteGen);
        renderFilterSelect('fs-sex', 'sex', 'Sex', state.spriteGenders || [], state.spriteSex);
    }

    function closeAllFilterMenus() {
        document.querySelectorAll('#sprite-toolbar .filter-select.open').forEach((s) => {
            s.classList.remove('open');
            const menu = s.querySelector('.fs-menu');
            if (menu) menu.classList.add('hidden');
            const t = s.querySelector('.fs-trigger');
            if (t) t.setAttribute('aria-expanded', 'false');
        });
    }

    // One dropdown: a trigger showing the dimension name (when "All") or the
    // chosen value, plus a themed menu of options.
    function renderFilterSelect(hostId, which, dimLabel, items, selected) {
        const host = document.getElementById(hostId);
        if (!host) return;
        if (!items.length) { host.className = 'filter-select hidden'; host.innerHTML = ''; return; }
        const curItem = (selected !== 'all') ? items.find((it) => it.key === selected) : null;
        const triggerText = curItem ? curItem.label : dimLabel;
        const active = selected !== 'all';
        const opt = (label, val, on) =>
            `<button class="fs-option${on ? ' active' : ''}" type="button" role="menuitem" data-val="${esc(val)}">${esc(label)}</button>`;
        host.className = 'filter-select' + (active ? ' active' : '');
        host.innerHTML = `
            <button class="fs-trigger" type="button" aria-haspopup="true" aria-expanded="false">
                <span class="fs-value">${esc(triggerText)}</span>
                <span class="fs-chevron">▾</span>
            </button>
            <div class="fs-menu hidden" role="menu">
                ${opt('All', 'all', selected === 'all')}
                ${items.map((it) => opt(it.label, it.key, selected === it.key)).join('')}
            </div>`;
        const trigger = host.querySelector('.fs-trigger');
        const menu = host.querySelector('.fs-menu');
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = menu.classList.contains('hidden');
            closeAllFilterMenus();
            if (willOpen) {
                menu.classList.remove('hidden');
                host.classList.add('open');
                trigger.setAttribute('aria-expanded', 'true');
            }
        });
        menu.querySelectorAll('.fs-option').forEach((o) => {
            o.addEventListener('click', (e) => {
                e.stopPropagation();
                const val = o.getAttribute('data-val');
                if (which === 'cat') state.spriteCat = val;
                else if (which === 'gen') state.spriteGen = val;
                else state.spriteSex = val;
                buildSpriteToolbar();
                renderSpriteGrid();
            });
        });
    }

    function closeSpriteModal() {
        document.getElementById('sprite-modal').classList.add('hidden');
    }

    function renderSpriteGrid(placeholder) {
        const grid = document.getElementById('sprite-grid');
        const empty = document.getElementById('sprite-empty');
        if (placeholder) {
            if (empty) empty.classList.add('hidden');
            grid.classList.remove('hidden');
            grid.innerHTML = `<div style="color:var(--text-muted);padding:12px;">${esc(placeholder)}</div>`;
            return;
        }
        const q = state.spriteSearch.toLowerCase();
        const gen = state.spriteGen || 'all';
        const cat = state.spriteCat || 'all';
        const sex = state.spriteSex || 'all';
        const list = (state.sprites || []).filter((s) => {
            if (gen !== 'all' && (s.gen || 'other') !== gen) return false;
            if (cat !== 'all' && s.category !== cat) return false;
            if (sex !== 'all' && (s.gender || '') !== sex) return false;
            if (q && !((s.label || '').toLowerCase().includes(q)
                     || (s.name || '').toLowerCase().includes(q)
                     || (s.sublabel || '').toLowerCase().includes(q))) return false;
            return true;
        });
        const countEl = document.getElementById('sprite-count');
        if (countEl) countEl.textContent = list.length + (list.length === 1 ? ' sprite' : ' sprites');
        if (!list.length) {
            grid.classList.add('hidden');
            grid.innerHTML = '';
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');
        grid.classList.remove('hidden');
        const frag = document.createDocumentFragment();
        list.forEach((s) => {
            const card = document.createElement('div');
            card.className = 'sprite-card' + (s.name === state.spriteCurrent ? ' selected' : '');
            card.innerHTML = `
                <img src="${esc(s.url)}" alt="${esc(s.label)}" onerror="this.style.visibility='hidden';">
                <span class="sprite-label">${esc(s.label)}</span>
                ${s.sublabel ? `<span class="sprite-sub">${esc(s.sublabel)}</span>` : ''}`;
            card.addEventListener('click', () => chooseSprite(s));
            frag.appendChild(card);
        });
        grid.replaceChildren(frag);
    }

    function chooseSprite(sprite) {
        const prev = state.spriteCurrent;
        const prevUrl = state.data ? state.data.sprite_url : null;
        state.spriteCurrent = sprite.name;
        // Optimistically update the avatar + close the modal.
        if (state.data) state.data.sprite_url = sprite.url;
        render();
        closeSpriteModal();
        if (trainer && trainer.setSprite) {
            trainer.setSprite(sprite.name).then((res) => {
                if (res && res.ok) {
                    toast(res.message || 'Trainer sprite updated.', 'success');
                } else {
                    // Backend rejected it — roll the optimistic avatar back.
                    state.spriteCurrent = prev;
                    if (state.data) state.data.sprite_url = prevUrl;
                    render();
                    toast((res && res.message) || 'Failed to set sprite.', 'error');
                }
            });
        } else {
            toast('Selected (preview — no backend).', 'success');
        }
    }

    let toastTimer = null;
    function toast(msg, kind) {
        const el = document.getElementById('toast');
        el.textContent = msg;
        el.className = 'toast show' + (kind ? ' ' + kind : '');
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { el.className = 'toast'; }, 2400);
    }

    // ---------------- Rename trainer ----------------
    // Inline edit on the rail name: click (or Enter/Space when focused) swaps it
    // for an input; Enter commits, Escape OR blur (clicking away) cancels — so
    // clicking elsewhere never silently saves a half-typed name. Mirrors the
    // sprite flow: optimistic update + bridge call + toast, reverting on failure.
    let renaming = false;
    function startRename() {
        if (renaming || !state.data) return;
        const el = document.getElementById('rail-name');
        if (!el) return;
        renaming = true;
        const current = state.data.name || '';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'rail-name-input';
        input.value = current;
        input.maxLength = 24;
        input.setAttribute('aria-label', 'Trainer name');
        el.replaceWith(input);
        input.focus();
        input.select();
        let done = false;
        const finish = (commit) => {
            if (done) return;
            done = true;
            renaming = false;
            const val = input.value.trim();
            if (commit && val && val !== current) saveName(val);
            else render();
        };
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); finish(true); }
            else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
        });
        input.addEventListener('blur', () => finish(false));
    }
    function saveName(name) {
        const prev = state.data.name;
        state.data.name = name;     // optimistic
        render();
        if (trainer && trainer.setName) {
            trainer.setName(name).then((res) => {
                if (res && res.ok) {
                    state.data.name = res.name || name;
                    toast(res.message || 'Trainer name updated.', 'success');
                } else {
                    state.data.name = prev;
                    toast((res && res.message) || 'Failed to update name.', 'error');
                }
                render();
            });
        } else {
            toast('Renamed (preview — no backend).', 'success');
        }
    }

    // ---------------- Init ----------------
    function handleAction(action) {
        if (action === 'sprite') openSpriteModal();
        else if (action === 'badges') {
            const sec = document.getElementById('badges-section');
            if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    window.initializeProfile = function (data) {
        state.data = data || {};
        render();
        handleAction(state.data.action);
    };

    // Live refresh pushed by Python after a gameplay event (catch, defeat XP,
    // cash reward, level-up) while this screen is open — re-renders every stat.
    // Newly caught Pokémon animate into Recently Caught; renderRecent's id-diff
    // means unchanged cards don't re-animate, so cash/XP-only changes are quiet.
    window.liveRefreshProfile = function (data) {
        if (!data) return;
        state.data = data;
        render(true);
    };

    function wireStaticControls() {
        // The hero avatar persists across renders, so wire it once here.
        document.getElementById('avatar-btn').addEventListener('click', openSpriteModal);
        // Inline rename: #rail-meta persists across renders (its children are
        // rebuilt), so delegate the click/keydown here rather than on .rail-name.
        const railMeta = document.getElementById('rail-meta');
        if (railMeta) {
            railMeta.addEventListener('click', (e) => {
                if (e.target.closest('.rail-name')) startRename();
            });
            railMeta.addEventListener('keydown', (e) => {
                const t = e.target;
                if ((e.key === 'Enter' || e.key === ' ') && t.classList && t.classList.contains('rail-name')) {
                    e.preventDefault();
                    startRename();
                }
            });
        }
        const manage = document.getElementById('manage-team');
        if (manage) {
            manage.addEventListener('click', () => {
                if (window.nav && window.nav.openTeam) window.nav.openTeam();
            });
        }
        document.getElementById('sprite-close').addEventListener('click', closeSpriteModal);
        document.getElementById('sprite-search').addEventListener('input', (e) => {
            state.spriteSearch = e.target.value || '';
            renderSpriteGrid();
        });
        const clearBtn = document.getElementById('sprite-clear-filters');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                state.spriteSearch = '';
                state.spriteGen = 'all';
                state.spriteCat = 'all';
                state.spriteSex = 'all';
                const s = document.getElementById('sprite-search');
                if (s) s.value = '';
                buildSpriteToolbar();
                renderSpriteGrid();
            });
        }
        document.getElementById('sprite-modal').addEventListener('click', (e) => {
            if (e.target.id === 'sprite-modal') closeSpriteModal();
        });
        // Clicking anywhere else closes an open filter dropdown (trigger/option
        // clicks stopPropagation, so they don't trigger this).
        document.addEventListener('click', closeAllFilterMenus);
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (document.querySelector('#sprite-toolbar .filter-select.open')) {
                closeAllFilterMenus();
            } else {
                closeSpriteModal();
            }
        });
    }

    function init() {
        wireStaticControls();

        if (typeof qt === 'undefined' || !qt.webChannelTransport) {
            state.spriteCurrent = 'red';
            state.sprites = [
                { name: 'red', label: 'Red', sublabel: 'Gen 1', url: TRAINER_BASE + '/red.png', gen: '1', category: 'Characters', gender: '' },
                { name: 'blue', label: 'Blue', sublabel: 'Gen 1 · RB Champion', url: TRAINER_BASE + '/blue.png', gen: '1', category: 'Characters', gender: '' },
                { name: 'ethan', label: 'Ethan', sublabel: 'Gen 2', url: TRAINER_BASE + '/ethan.png', gen: '2', category: 'Characters', gender: '' },
                { name: 'leaf', label: 'Leaf', sublabel: 'Gen 3', url: TRAINER_BASE + '/leaf.png', gen: '3', category: 'Characters', gender: '' },
                { name: 'acetrainer', label: 'Ace Trainer', sublabel: 'Gen 6 · XY', url: TRAINER_BASE + '/ethan.png', gen: '6', category: 'Trainer', gender: 'm' },
                { name: 'acetrainerf', label: 'Ace Trainer', sublabel: 'Gen 3', url: TRAINER_BASE + '/leaf.png', gen: '3', category: 'Trainer', gender: 'f' },
                { name: 'acetrainercouple', label: 'Ace Trainer Couple', sublabel: 'Gen 3', url: TRAINER_BASE + '/red.png', gen: '3', category: 'Trainer', gender: '' },
                { name: 'swimmerf', label: 'Swimmer', sublabel: 'Gen 3', url: TRAINER_BASE + '/leaf.png', gen: '3', category: 'Athletic', gender: 'f' },
                { name: 'nurse', label: 'Nurse', sublabel: 'Gen 4', url: TRAINER_BASE + '/leaf.png', gen: '4', category: 'Medical', gender: 'f' },
                { name: 'scientist', label: 'Scientist', sublabel: 'Gen 1', url: TRAINER_BASE + '/red.png', gen: '1', category: 'Science', gender: 'm' },
                { name: 'youngster', label: 'Youngster', sublabel: 'Gen 2', url: TRAINER_BASE + '/ethan.png', gen: '2', category: 'Youth', gender: 'm' },
                { name: 'gentleman', label: 'Gentleman', sublabel: 'Gen 1', url: TRAINER_BASE + '/red.png', gen: '1', category: 'Elegant', gender: 'm' },
                { name: 'psychic', label: 'Psychic', sublabel: 'Gen 1', url: TRAINER_BASE + '/blue.png', gen: '1', category: 'Mystic', gender: 'm' },
                { name: 'guitarist', label: 'Guitarist', sublabel: 'Gen 3', url: TRAINER_BASE + '/ethan.png', gen: '3', category: 'Performer', gender: 'm' },
                { name: 'aaron', label: 'Aaron', sublabel: '', url: TRAINER_BASE + '/blue.png', gen: 'other', category: 'Characters', gender: '' },
            ];
            state.spriteGens = [
                { key: '1', label: 'Gen 1' }, { key: '2', label: 'Gen 2' },
                { key: '3', label: 'Gen 3' }, { key: '4', label: 'Gen 4' },
                { key: '6', label: 'Gen 6' }, { key: 'other', label: 'Other' },
            ];
            state.spriteCats = ['Characters', 'Trainer', 'Athletic', 'Youth', 'Elegant', 'Mystic', 'Science', 'Performer', 'Medical'];
            state.spriteGenders = [{ key: 'm', label: '♂ Male' }, { key: 'f', label: '♀ Female' }];
            window.initializeProfile({
                name: 'Red', trainer_id: 'PKMN-0001', sprite_url: TRAINER_BASE + '/red.png',
                level: 7, xp: 320, total_xp: 5400, xp_for_next_level: 900,
                badges: 2, cash: 12500, favorite_pokemon: 'Pikachu',
                highest_level_pokemon: 'Charizard (Level 52)', league: 'Ace',
                caught: 142, dex_seen: 89, shinies: 5, highest_level: 52,
                favorite: { n: 'Pikachu', sprite: SPRITE_BASE + '/25.png' },
                highest: { n: 'Charizard', l: 52, sprite: SPRITE_BASE + '/6.png' },
                friendship: { n: 'Snorlax', fr: 220, sprite: SPRITE_BASE + '/143.png' },
                recent: [
                    { id: 'r1', p: 130, n: 'gyarados', l: 39 }, { id: 'r2', p: 94, n: 'gengar', l: 36 },
                    { id: 'r3', p: 6, n: 'charizard', l: 52, s: 1 }, { id: 'r4', p: 3, n: 'venusaur', l: 41 },
                    { id: 'r5', p: 143, n: 'snorlax', l: 30 }, { id: 'r6', p: 9, n: 'blastoise', l: 44 },
                ],
                team: [
                    { id: '1', p: 25, n: 'Pikachu', l: 18 },
                    { id: '2', p: 6, n: 'Charizard', l: 52, s: 1 },
                    { id: '3', p: 9, n: 'Blastoise', l: 44 },
                ],
                badge_grid: [
                    { id: 1, name: 'Boulder Badge', unlocked: true },
                    { id: 2, name: 'Cascade Badge', unlocked: true },
                    { id: 3, name: 'Thunder Badge', unlocked: false },
                    { id: 4, name: 'Rainbow Badge', unlocked: false },
                    { id: 5, name: 'Soul Badge', unlocked: false },
                ],
            });
            return;
        }

        new QWebChannel(qt.webChannelTransport, function (channel) {
            trainer = channel.objects && channel.objects.trainer;
            const nav = channel.objects && channel.objects.nav;
            window.trainer = trainer;
            window.nav = nav;
            // Wire the dropdown from THIS channel's nav — see ankimon_items_web/nav-switcher.js
            // for why we don't open a second channel.
            if (window.wireNavSwitcher) window.wireNavSwitcher(nav);
            if (trainer && trainer.getProfile) {
                trainer.getProfile().then(window.initializeProfile);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
