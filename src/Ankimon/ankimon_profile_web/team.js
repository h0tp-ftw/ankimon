// Team builder — assign up to 6 Pokémon + an XP Share target, then Save.
//
// Initial push (window.initializeTeam) carries only the current team + xp_share
// (small). The full roster is lazy-loaded via team.getRoster() the first time a
// slot picker opens, then cached. Per-slot CP is fetched on demand with
// team.getCp() since roster stubs omit it for performance.

(function () {
    'use strict';

    const SPRITE_BASE = '../user_files/sprites/front_default';
    const FALLBACK = SPRITE_BASE + '/0.png';

    const state = {
        team: [null, null, null, null, null, null],
        xpShare: null,         // individual_id of the XP Share holder (any owned Pokémon)
        xpShareInfo: null,     // display stub for the holder (may be benched)
        maxSize: 6,
        teamCycleCount: 3,     // limit for hotkey 9 rotation
        roster: null,          // null = not loaded yet
        dirty: false,
        pickerSlot: null,
        pickerMode: 'slot',    // 'slot' (fill a team slot) | 'xpshare' (pick XP Share)
        rosterType: 'all',     // picker Type filter
        rosterSort: 'cp',      // picker Sort: 'cp' | 'level' | 'name'
        spriteMode: 'static',
    };

    // NOTE: the "Active Companion" picker (an iframe mode="companion-picker"
    // flow) was never finished — no page hosts the iframe and its helpers were
    // undefined — so that dead path is removed here. The Python read/write seam
    // (get_team_data.companion / handle_save_team companion_id) is retained for
    // whenever the picker UI is actually built; saveTeam sends '' until then.
    let teamBridge = null;

    function spriteUrl(stub, mode = state.spriteMode) {
        if (!stub) return FALLBACK;
        let base = stub.sprite;
        if (!base) {
            const isShiny = !!stub.s;
            const id = stub.p;
            if (!id) return FALLBACK;
            if (mode === 'animated') {
                return isShiny
                    ? `../user_files/sprites/front_default_gif/shiny/${id}.gif`
                    : `../user_files/sprites/front_default_gif/${id}.gif`;
            }
            return isShiny
                ? `../user_files/sprites/front_default/shiny/${id}.png`
                : `../user_files/sprites/front_default/${id}.png`;
        }
        if (mode === 'animated') {
            return base.replace('/front_default/', '/front_default_gif/').replace('.png', '.gif');
        }
        return base;
    }

    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }
    function num(n) { return (Number(n) || 0).toLocaleString(); }
    function setText(id, t) { const el = document.getElementById(id); if (el) el.textContent = t; }

    // Standard Pokémon type colours for the badges + coverage chips.
    const TYPE_COLORS = {
        Normal: '#9099a1', Fire: '#ff9d55', Water: '#4d90d5', Electric: '#f3d23b',
        Grass: '#63bc5a', Ice: '#74cec0', Fighting: '#ce4069', Poison: '#ab6ac8',
        Ground: '#d97845', Flying: '#8fa9de', Psychic: '#fa7179', Bug: '#90c12c',
        Rock: '#c7b78b', Ghost: '#5269ad', Dragon: '#0c6ac8', Dark: '#5a5366',
        Steel: '#5a8ea1', Fairy: '#ec8fe6',
    };
    function typeBadge(t, label) {
        // Colour + effectiveness stay keyed on the English type name `t`;
        // `label` (when supplied) is the localized text shown on the badge.
        const c = TYPE_COLORS[t] || '#6b727c';
        return `<span class="type-badge" style="background:${c};">${esc(label || t)}</span>`;
    }
    function typeBadges(m) {
        const ti = m.ti || [];
        return (m.types || []).map((t, i) => typeBadge(t, ti[i])).join('');
    }

    // Type effectiveness chart (attacking -> defending), only the non-1× cells.
    // Used to compute the team's collective defensive weaknesses. This is the
    // modern (Gen 6+) chart and is intentionally INDEPENDENT of the battle
    // engine's addon_files/eff_chart.json, which holds older/simplified values
    // (e.g. Ghost→Psychic 0, Dark→Fighting 2×) for a rough Present-Power
    // heuristic — do not "unify" them or the team analysis would regress.
    const ALL_TYPES = [
        'Normal', 'Fire', 'Water', 'Electric', 'Grass', 'Ice', 'Fighting', 'Poison',
        'Ground', 'Flying', 'Psychic', 'Bug', 'Rock', 'Ghost', 'Dragon', 'Dark', 'Steel', 'Fairy',
    ];
    const TYPE_FX = {
        Normal: { Rock: .5, Ghost: 0, Steel: .5 },
        Fire: { Fire: .5, Water: .5, Grass: 2, Ice: 2, Bug: 2, Rock: .5, Dragon: .5, Steel: 2 },
        Water: { Fire: 2, Water: .5, Grass: .5, Ground: 2, Rock: 2, Dragon: .5 },
        Electric: { Water: 2, Electric: .5, Grass: .5, Ground: 0, Flying: 2, Dragon: .5 },
        Grass: { Fire: .5, Water: 2, Grass: .5, Poison: .5, Ground: 2, Flying: .5, Bug: .5, Rock: 2, Dragon: .5, Steel: .5 },
        Ice: { Fire: .5, Water: .5, Grass: 2, Ice: .5, Ground: 2, Flying: 2, Dragon: 2, Steel: .5 },
        Fighting: { Normal: 2, Ice: 2, Poison: .5, Flying: .5, Psychic: .5, Bug: .5, Rock: 2, Ghost: 0, Dark: 2, Steel: 2, Fairy: .5 },
        Poison: { Grass: 2, Poison: .5, Ground: .5, Rock: .5, Ghost: .5, Steel: 0, Fairy: 2 },
        Ground: { Fire: 2, Electric: 2, Grass: .5, Poison: 2, Flying: 0, Bug: .5, Rock: 2, Steel: 2 },
        Flying: { Electric: .5, Grass: 2, Fighting: 2, Bug: 2, Rock: .5, Steel: .5 },
        Psychic: { Fighting: 2, Poison: 2, Psychic: .5, Dark: 0, Steel: .5 },
        Bug: { Fire: .5, Grass: 2, Fighting: .5, Poison: .5, Flying: .5, Psychic: 2, Ghost: .5, Dark: 2, Steel: .5, Fairy: .5 },
        Rock: { Fire: 2, Ice: 2, Fighting: .5, Ground: .5, Flying: 2, Bug: 2, Steel: .5 },
        Ghost: { Normal: 0, Psychic: 2, Ghost: 2, Dark: .5 },
        Dragon: { Dragon: 2, Steel: .5, Fairy: 0 },
        Dark: { Fighting: .5, Psychic: 2, Ghost: 2, Dark: .5, Fairy: .5 },
        Steel: { Fire: .5, Water: .5, Electric: .5, Ice: 2, Rock: 2, Steel: .5, Fairy: 2 },
        Fairy: { Fire: .5, Fighting: 2, Poison: .5, Dragon: 2, Dark: 2, Steel: .5 },
    };
    function defMultiplier(attacking, defendingTypes) {
        const row = TYPE_FX[attacking] || {};
        let m = 1;
        defendingTypes.forEach((d) => { m *= (d in row ? row[d] : 1); });
        return m;
    }
    // {attackingType: count} across the filled team, for a given test on the
    // defensive multiplier (>1 = weak, <1 = resists/immune).
    function teamDefense(filled, test) {
        const counts = {};
        filled.forEach((m) => {
            const types = (m.types || []).filter(Boolean);
            if (!types.length) return;
            ALL_TYPES.forEach((atk) => {
                if (test(defMultiplier(atk, types))) counts[atk] = (counts[atk] || 0) + 1;
            });
        });
        return counts;
    }
    // {opposingType: number-of-members-that-hit-it-super-effectively} — STAB-based
    // offensive coverage (the types the team is strong against).
    function teamStrengths(filled) {
        const counts = {};
        filled.forEach((m) => {
            const types = (m.types || []).filter(Boolean);
            if (!types.length) return;
            ALL_TYPES.forEach((def) => {
                if (types.some((t) => (TYPE_FX[t] || {})[def] > 1)) {
                    counts[def] = (counts[def] || 0) + 1;
                }
            });
        });
        return counts;
    }
    function weakBadge(t, count) {
        const c = TYPE_COLORS[t] || '#6b727c';
        const cnt = count > 1 ? `<span class="wk-count">${count}</span>` : '';
        return `<span class="type-badge" style="background:${c};">${esc(t)}${cnt}</span>`;
    }
    // Render a {type: count} map into a container, sorted by count desc.
    function renderTypeCounts(elId, counts, emptyMsg) {
        const el = document.getElementById(elId);
        if (!el) return;
        const order = Object.keys(counts).sort((a, b) =>
            (counts[b] - counts[a]) || (ALL_TYPES.indexOf(a) - ALL_TYPES.indexOf(b)));
        el.innerHTML = order.length
            ? order.map((t) => weakBadge(t, counts[t])).join('')
            : `<span class="coverage-empty">${esc(emptyMsg)}</span>`;
    }

    // ---------------- Render ----------------
    function renderTeam() {
        const grid = document.getElementById('team-grid');
        const frag = document.createDocumentFragment();

        for (let i = 0; i < state.maxSize; i++) {
            const m = state.team[i];
            const slot = document.createElement('div');
            const isXp = !!(m && state.xpShare && String(m.id) === String(state.xpShare));
            slot.className = 'slot ' + (m ? 'filled' : 'empty') + (isXp ? ' xp-share' : '');

            if (m) {
                const cp = (m.cp === undefined || m.cp === null) ? '—' : num(m.cp);
                const types = typeBadges(m);
                const hasCycle = state.teamCycleCount > 1 && i < state.teamCycleCount;
                const cycleBadge = hasCycle ? '<span class="slot-rotation-badge" title="Cycled via Hotkey 9">↻</span>' : '';

                slot.title = 'Click to switch';
                slot.innerHTML = `
                    <span class="slot-num">${i + 1}${cycleBadge}</span>
                    <button class="slot-corner slot-star${isXp ? ' on' : ''}" data-act="xp" data-slot="${i}"
                            title="${isXp ? 'Remove XP Share' : 'Set as XP Share'}">★</button>
                    <button class="slot-corner slot-remove" data-act="remove" data-slot="${i}" title="Remove">✕</button>
                    <div class="slot-sprite-wrap"><img src="${spriteUrl(m)}" alt="${esc(m.n)}"
                         onerror="if (this.src.indexOf('_gif') !== -1) { this.src = this.src.replace('_gif', '').replace('.gif', '.png'); } else { this.onerror=null; this.src='${FALLBACK}'; }"></div>
                    <div class="slot-name">${esc(m.n)}${m.s ? ' <span class="shiny-dot">★</span>' : ''}</div>
                    ${types ? `<div class="slot-types">${types}</div>` : ''}
                    <div class="slot-stats">
                        <div class="slot-stat"><span class="v">${esc(m.l)}</span><span class="k">Level</span></div>
                        <div class="slot-stat"><span class="v">${cp}</span><span class="k">CP</span></div>
                    </div>
                    <div class="slot-switch-hint">⇄ Switch</div>`;
                slot.addEventListener('click', () => openPicker(i));
            } else {
                slot.innerHTML = `
                    <span class="slot-num">${i + 1}</span>
                    <div class="slot-empty-icon">+</div>
                    <div class="slot-empty-label">Add Pokémon</div>
                    <div class="slot-empty-sub">Slot ${i + 1}</div>`;
                slot.addEventListener('click', () => openPicker(i));
            }
            frag.appendChild(slot);
        }
        grid.replaceChildren(frag);

        // Corner buttons (★ XP Share, ✕ remove) override the card's click-to-switch.
        grid.querySelectorAll('[data-act]').forEach((btn) => {
            const i = Number(btn.dataset.slot);
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (btn.dataset.act === 'remove') removeSlot(i);
                else if (btn.dataset.act === 'xp') toggleXp(i);
            });
        });

        renderSummary();
        renderXpShare();
    }

    function toggleXp(slot) {
        const m = state.team[slot];
        if (!m) return;
        if (state.xpShare && String(m.id) === String(state.xpShare)) setXpShare(null);
        else setXpShare(m);
    }

    // Sidebar team overview: filled count, average level, total CP, type coverage.
    function renderSummary() {
        const filled = state.team.filter(Boolean);
        setText('ts-count', filled.length + ' / ' + state.maxSize);
        const avg = filled.length
            ? Math.round(filled.reduce((s, m) => s + (Number(m.l) || 0), 0) / filled.length)
            : null;
        setText('ts-avg', avg == null ? '—' : String(avg));
        const cps = filled.map((m) => Number(m.cp)).filter((v) => !isNaN(v));
        setText('ts-cp', cps.length ? num(cps.reduce((s, v) => s + v, 0)) : '—');

        // Matchup analysis (counts = how many members are involved):
        //  Strengths  = opposing types the team hits super-effectively (offense)
        //  Weaknesses = types that hit the team super-effectively (defense)
        //  Resistances = types the team resists / is immune to (defense)
        renderTypeCounts('strengths', teamStrengths(filled), 'Add Pokémon to see strengths');
        renderTypeCounts('weaknesses', teamDefense(filled, (mult) => mult > 1), 'Add Pokémon to see weaknesses');
        renderTypeCounts('resistances', teamDefense(filled, (mult) => mult < 1), 'Add Pokémon to see resistances');
    }

    function renderXpShare() {
        const holder = document.getElementById('xpshare-holder');
        if (!holder) return;
        // The holder may be benched, so prefer the resolved stub; fall back to a
        // team member if it happens to be on the team.
        let m = state.xpShareInfo;
        if (!m && state.xpShare) {
            m = state.team.find((p) => p && String(p.id) === String(state.xpShare)) || null;
        }
        if (m) {
            holder.innerHTML = `
                <div class="xps-card" title="Change XP Share">
                    <img class="xps-sprite" src="${spriteUrl(m)}" alt="${esc(m.n)}"
                         onerror="if (this.src.indexOf('_gif') !== -1) { this.src = this.src.replace('_gif', '').replace('.gif', '.png'); } else { this.onerror=null; this.src='${FALLBACK}'; }">
                    <div class="xps-name">${esc(m.n)}${m.s ? ' <span class="shiny-dot">★</span>' : ''}</div>
                    <div class="xps-lv">★ Lv ${esc(m.l)}</div>
                    <div class="xps-change-hint">⇄ Change</div>
                </div>`;
        } else {
            holder.innerHTML = '<button class="xps-empty" type="button">+ Set XP Share</button>';
        }
        // Whole card is the trigger (like a team slot) — opens the XP Share picker.
        const trigger = holder.querySelector('.xps-card, .xps-empty');
        if (trigger) trigger.addEventListener('click', openXpSharePicker);
    }



    // ---------------- Slot actions ----------------
    function removeSlot(slot) {
        // XP Share is independent of team membership now — removing a Pokémon
        // from a slot leaves it owned, so we keep it as the XP Share holder.
        state.team[slot] = null;
        markDirty();
        renderTeam();
    }

    function assignSlot(slot, stub) {
        // Copy so editing one slot can't alias another / the roster entry.
        const member = { id: stub.id, p: stub.p, n: stub.n, l: stub.l };
        if (stub.s) member.s = 1;
        if (stub.sprite) member.sprite = stub.sprite;   // carry the resolved forme/mega sprite (else it'd 404 to 0.png)
        if (stub.types) member.types = stub.types;
        if (stub.ti) member.ti = stub.ti;
        if (stub.cp != null) member.cp = stub.cp;   // stored CP from the roster (shown instantly)
        state.team[slot] = member;
        markDirty();
        renderTeam();
        // Refresh CP/types from the live data (recomputed) for the added Pokémon.
        if (teamBridge && teamBridge.getMemberStats) {
            teamBridge.getMemberStats(String(stub.id)).then((res) => {
                const cur = state.team[slot];
                if (cur && String(cur.id) === String(stub.id) && res) {
                    if (res.cp != null) cur.cp = res.cp;   // don't clobber the shown CP with a missing value
                    cur.types = res.types || cur.types || [];
                    cur.ti = res.ti || cur.ti || [];
                    renderTeam();
                }
            });
        }
    }

    // ---------------- XP Share ----------------
    function setXpShare(stub) {
        if (stub) {
            state.xpShare = String(stub.id);
            state.xpShareInfo = { id: stub.id, p: stub.p, n: stub.n, l: stub.l };
            if (stub.s) state.xpShareInfo.s = 1;
            if (stub.sprite) state.xpShareInfo.sprite = stub.sprite;
        } else {
            state.xpShare = null;
            state.xpShareInfo = null;
        }
        markDirty();
        renderTeam();
    }

    // ---------------- Roster picker ----------------
    function setPickerTitle(text) {
        const el = document.getElementById('picker-title');
        if (el) el.textContent = text;
    }

    function ensureRoster(done) {
        if (state.roster !== null) { done(); return; }
        renderRosterList('Loading…');
        if (teamBridge && teamBridge.getRoster) {
            teamBridge.getRoster().then((res) => {
                state.roster = (res && res.choices) || [];
                done();
            });
        } else {
            state.roster = [];
            done();
        }
    }

    function openPicker(slot) {
        state.pickerMode = 'slot';
        state.pickerSlot = slot;
        setPickerTitle('Choose a Pokémon');
        resetPickerControls();
        showPicker(true);
        ensureRoster(() => { buildPickerFilters(); renderRosterList(); });
    }

    function openXpSharePicker() {
        state.pickerMode = 'xpshare';
        state.pickerSlot = null;
        setPickerTitle('Choose XP Share Pokémon');
        resetPickerControls();
        showPicker(true);
        ensureRoster(() => { buildPickerFilters(); renderRosterList(); });
    }

    function resetPickerControls() {
        const s = document.getElementById('picker-search');
        if (s) s.value = '';
        state.rosterType = 'all';
        state.rosterSort = 'cp';
    }

    function showPicker(show) {
        document.getElementById('picker').classList.toggle('hidden', !show);
        if (!show) closePickerMenus();
        if (show) {
            const s = document.getElementById('picker-search');
            setTimeout(() => s.focus(), 0);
        }
    }

    // ---- Picker filter dropdowns (Type + Sort) — mirrors the sprite picker ----
    function closePickerMenus() {
        document.querySelectorAll('#picker-toolbar .filter-select.open').forEach((s) => {
            s.classList.remove('open');
            const menu = s.querySelector('.fs-menu');
            if (menu) menu.classList.add('hidden');
            const t = s.querySelector('.fs-trigger');
            if (t) t.setAttribute('aria-expanded', 'false');
        });
    }

    function renderPickerSelect(hostId, which, opts) {
        const host = document.getElementById(hostId);
        if (!host) return;
        const items = opts.items || [];
        const sel = opts.selected;
        const hasAll = !!opts.allLabel;
        if (hasAll && !items.length) { host.className = 'filter-select hidden'; host.innerHTML = ''; return; }
        const cur = items.find((it) => it.key === sel);
        const triggerText = (hasAll && sel === 'all')
            ? opts.allLabel
            : (opts.prefix || '') + (cur ? cur.label : (opts.allLabel || ''));
        const active = hasAll && sel !== 'all';
        const opt = (label, val, on) =>
            `<button class="fs-option${on ? ' active' : ''}" type="button" data-val="${esc(val)}">${esc(label)}</button>`;
        host.className = 'filter-select' + (active ? ' active' : '');
        const optsHtml = (hasAll ? opt('All', 'all', sel === 'all') : '')
            + items.map((it) => opt(it.label, it.key, sel === it.key)).join('');
        host.innerHTML = `
            <button class="fs-trigger" type="button" aria-haspopup="true" aria-expanded="false">
                <span class="fs-value">${esc(triggerText)}</span>
                <span class="fs-chevron">▾</span>
            </button>
            <div class="fs-menu hidden" role="menu">${optsHtml}</div>`;
        const trigger = host.querySelector('.fs-trigger');
        const menu = host.querySelector('.fs-menu');
        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            const willOpen = menu.classList.contains('hidden');
            closePickerMenus();
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
                if (which === 'type') state.rosterType = val;
                else state.rosterSort = val;
                buildPickerFilters();
                renderRosterList();
            });
        });
    }

    function buildPickerFilters() {
        const present = new Set();
        (state.roster || []).forEach((c) => (c.types || []).forEach((t) => present.add(t)));
        const typeItems = ALL_TYPES.filter((t) => present.has(t)).map((t) => ({ key: t, label: t }));
        renderPickerSelect('pf-type', 'type', { items: typeItems, selected: state.rosterType, allLabel: 'Type' });
        renderPickerSelect('pf-sort', 'sort', {
            items: [{ key: 'cp', label: 'CP' }, { key: 'level', label: 'Level' }, { key: 'name', label: 'Name' }],
            selected: state.rosterSort, prefix: '↕ ',
        });
    }

    function renderRosterList(placeholder) {
        const list = document.getElementById('roster-list');
        const empty = document.getElementById('picker-empty');
        if (placeholder) {
            if (empty) empty.classList.add('hidden');
            list.classList.remove('hidden');
            list.innerHTML = `<div style="color:var(--text-muted);padding:12px;">${esc(placeholder)}</div>`;
            return;
        }
        const xpMode = state.pickerMode === 'xpshare';
        const q = (document.getElementById('picker-search').value || '').toLowerCase();
        const typeF = state.rosterType || 'all';
        const sort = state.rosterSort || 'cp';
        // In slot mode, Pokémon already in OTHER slots can't be added twice.
        const taken = xpMode ? new Set() : new Set(
            state.team.map((m, i) => (m && i !== state.pickerSlot ? String(m.id) : null)).filter(Boolean)
        );
        let choices = (state.roster || []).filter((c) => {
            if (typeF !== 'all' && (c.types || []).indexOf(typeF) < 0) return false;
            if (q && (c.n || '').toLowerCase().indexOf(q) < 0) return false;
            return true;
        });
        choices = choices.slice().sort((a, b) => {
            if (sort === 'name') return (a.n || '').localeCompare(b.n || '');
            if (sort === 'level') return (b.l || 0) - (a.l || 0) || (a.n || '').localeCompare(b.n || '');
            return (b.cp || 0) - (a.cp || 0) || (a.n || '').localeCompare(b.n || '');   // CP (default)
        });

        const countEl = document.getElementById('picker-count');
        if (countEl) countEl.textContent = choices.length + ' Pokémon';

        if (!choices.length && !xpMode) {
            list.classList.add('hidden');
            list.innerHTML = '';
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');
        list.classList.remove('hidden');

        const frag = document.createDocumentFragment();
        // XP Share mode: a "No XP Share" clear card pinned first.
        if (xpMode) {
            const none = document.createElement('div');
            none.className = 'roster-card' + (!state.xpShare ? ' current' : '');
            none.innerHTML = `
                <div class="rc-sprite" style="display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:1.7rem;">✕</div>
                <div class="rc-name" style="color:var(--text-muted);">No XP Share</div>`;
            none.addEventListener('click', () => { setXpShare(null); showPicker(false); });
            frag.appendChild(none);
        }
        const CAP = 200;
        choices.slice(0, CAP).forEach((c) => {
            const inTeam = !xpMode && taken.has(String(c.id));
            const isCurrent = xpMode && String(state.xpShare) === String(c.id);
            const card = document.createElement('div');
            card.className = 'roster-card' + (inTeam ? ' in-team' : '') + (isCurrent ? ' current' : '');
            const types = typeBadges(c);
            const cp = (c.cp != null && c.cp > 0) ? num(c.cp) : '—';
            card.innerHTML = `
                <img class="rc-sprite" src="${spriteUrl(c)}" alt="${esc(c.n)}" onerror="if (this.src.indexOf('_gif') !== -1) { this.src = this.src.replace('_gif', '').replace('.gif', '.png'); } else { this.onerror=null; this.src='${FALLBACK}'; }">
                <div class="rc-name">${esc(c.n)}${c.s ? ' <span class="shiny-dot">★</span>' : ''}</div>
                ${types ? `<div class="rc-types">${types}</div>` : ''}
                <div class="rc-stats"><span>Lv ${esc(c.l)}</span><span>·</span><span class="rc-cp">${cp}</span><span>CP</span></div>
                ${inTeam ? '<div class="rc-tag">On team</div>' : (isCurrent ? '<div class="rc-tag">Current</div>' : '')}`;
            if (!inTeam) {
                card.addEventListener('click', () => {
                    if (xpMode) {
                        setXpShare(c);
                    } else {
                        assignSlot(state.pickerSlot, c);
                    }
                    showPicker(false);
                });
            }
            frag.appendChild(card);
        });
        list.replaceChildren(frag);
        if (choices.length > CAP) {
            const more = document.createElement('div');
            more.style.cssText = 'grid-column:1/-1;color:var(--text-muted);padding:10px;text-align:center;font-size:.78rem;';
            more.textContent = `Showing top ${CAP} of ${choices.length}. Refine your search or filters.`;
            list.appendChild(more);
        }
    }

    // ---------------- Save / dirty ----------------
    function markDirty() {
        state.dirty = true;
        updateSaveUI();
    }

    function updateSaveUI() {
        const btn = document.getElementById('save-btn');
        const status = document.getElementById('save-status');
        btn.disabled = !state.dirty;
        status.textContent = state.dirty ? 'Unsaved changes' : 'All saved';
    }

    function saveTeam() {
        if (!state.dirty) return Promise.resolve({ ok: true });
        const ids = state.team.filter(Boolean).map((m) => String(m.id));
        if (!teamBridge || !teamBridge.saveTeam) {
            toast('Saved (preview — no backend).', 'success');
            state.dirty = false;
            updateSaveUI();
            return Promise.resolve({ ok: true });
        }
        // Third arg is companion_id — always '' (no shipped companion picker; the
        // Python seam accepts it for when that UI is built).
        return teamBridge.saveTeam(JSON.stringify(ids), state.xpShare || '', '').then((res) => {
            if (res && res.ok) {
                state.dirty = false;
                updateSaveUI();
                toast(res.message || 'Team saved.', 'success');
                return res;
            } else {
                toast((res && res.message) || 'Save failed.', 'error');
                throw new Error((res && res.message) || 'Save failed');
            }
        });
    }

    let toastTimer = null;
    function toast(msg, kind) {
        const el = document.getElementById('toast');
        el.textContent = msg;
        el.className = 'toast show' + (kind ? ' ' + kind : '');
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { el.className = 'toast'; }, 2600);
    }

    // ---------------- Init ----------------
    function applyData(data) {
        data = data || {};
        state.maxSize = data.max_size || 6;
        const team = (data.team || []).slice(0, state.maxSize);
        state.team = [];
        for (let i = 0; i < state.maxSize; i++) state.team.push(team[i] || null);
        state.xpShare = data.xp_share ? String(data.xp_share) : null;
        state.xpShareInfo = data.xp_share_info || null;
        state.spriteMode = data.sprite_mode || 'static';
        state.teamCycleCount = data.team_cycle_count || 3;

        const cycleSelect = document.getElementById('cycle-select');
        if (cycleSelect) cycleSelect.value = state.teamCycleCount;

        const text = document.getElementById('sprite-toggle-text');
        if (text) {
            text.textContent = state.spriteMode === 'static' ? 'Static' : 'Animated';
        }
        state.dirty = false;
        // Membership changed underneath us → force a roster reload next open.
        state.roster = null;
        renderTeam();
        updateSaveUI();
    }

    window.initializeTeam = applyData;

    function wireStaticControls() {
        document.getElementById('save-btn').addEventListener('click', saveTeam);
        
        const cycleSelect = document.getElementById('cycle-select');
        if (cycleSelect) {
            cycleSelect.addEventListener('change', (e) => {
                const count = parseInt(e.target.value, 10) || 3;
                state.teamCycleCount = count;
                renderTeam();
                if (teamBridge && teamBridge.saveCycleCount) {
                    teamBridge.saveCycleCount(count);
                }
            });
        }

        // XP Share button is rendered dynamically inside #xpshare-holder (see
        // renderXpShare), so it's wired there, not here.
        const spriteBtn = document.getElementById('sprite-toggle');
        if (spriteBtn) {
            spriteBtn.addEventListener('click', () => {
                state.spriteMode = state.spriteMode === 'static' ? 'animated' : 'static';
                const text = document.getElementById('sprite-toggle-text');
                if (text) {
                    text.textContent = state.spriteMode === 'static' ? 'Static' : 'Animated';
                }
                renderTeam();
                renderRosterList();
                if (teamBridge && teamBridge.saveSpriteMode) {
                    teamBridge.saveSpriteMode(state.spriteMode);
                }
            });
        }
        document.getElementById('picker-close').addEventListener('click', () => {
            showPicker(false);
        });
        document.getElementById('picker-search').addEventListener('input', () => renderRosterList());
        document.getElementById('picker').addEventListener('click', (e) => {
            if (e.target.id === 'picker') {
                showPicker(false);
            }
        });
        const pickerClear = document.getElementById('picker-clear');
        if (pickerClear) {
            pickerClear.addEventListener('click', () => {
                resetPickerControls();
                buildPickerFilters();
                renderRosterList();
            });
        }
        // Clicking elsewhere closes an open picker filter dropdown (trigger/option
        // clicks stopPropagation, so they don't trigger this).
        document.addEventListener('click', closePickerMenus);
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (document.querySelector('#picker-toolbar .filter-select.open')) { closePickerMenus(); return; }
                showPicker(false);
                return;
            }
            // Cmd/Ctrl+S saves the team (matches the Settings screen).
            if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
                e.preventDefault();
                saveTeam();
            }
        });
    }

    function init() {
        wireStaticControls();

        if (typeof qt === 'undefined' || !qt.webChannelTransport) {
            // Standalone preview or iframe picker mode reusing parent channel
            if (window.self !== window.top && window.parent && window.parent.parentChannel) {
                teamBridge = window.parent.parentChannel.objects.team;
                const nav = window.parent.parentChannel.objects.nav;
                window.team = teamBridge;
                window.nav = nav;
                if (window.wireNavSwitcher) window.wireNavSwitcher(nav);
                if (teamBridge && teamBridge.getTeam) {
                    teamBridge.getTeam().then(applyData);
                }
                return;
            }

            // Standalone preview. applyData() resets state.roster to null
            // (production reloads it from the bridge), so seed the mock roster
            // *after* it for the no-bridge picker to have something to show.
            applyData({
                max_size: 6,
                xp_share: '2',
                xp_share_info: { id: '2', p: 6, n: 'Charizard', l: 52, s: 1 },
                team: [
                    { id: '1', p: 25, n: 'Pikachu', l: 18, cp: 820, types: ['Electric'] },
                    { id: '2', p: 6, n: 'Charizard', l: 52, s: 1, cp: 2410, types: ['Fire', 'Flying'] },
                    { id: '3', p: 9, n: 'Blastoise', l: 44, cp: 2180, types: ['Water'] },
                    { id: '4', p: 3, n: 'Venusaur', l: 41, cp: 2090, types: ['Grass', 'Poison'] },
                    { id: '5', p: 94, n: 'Gengar', l: 36, cp: 1760, types: ['Ghost', 'Poison'] },
                    { id: '6', p: 143, n: 'Snorlax', l: 30, cp: 1980, types: ['Normal'] },
                ],
            });
            state.roster = [
                { id: '1', p: 25, n: 'Pikachu', l: 18, cp: 820, types: ['Electric'] },
                { id: '2', p: 6, n: 'Charizard', l: 52, s: 1, cp: 2410, types: ['Fire', 'Flying'] },
                { id: '3', p: 9, n: 'Blastoise', l: 44, cp: 2180, types: ['Water'] },
                { id: '4', p: 3, n: 'Venusaur', l: 41, cp: 2090, types: ['Grass', 'Poison'] },
                { id: '5', p: 94, n: 'Gengar', l: 36, cp: 1760, types: ['Ghost', 'Poison'] },
                { id: '6', p: 130, n: 'Gyarados', l: 39, cp: 2230, types: ['Water', 'Flying'] },
                { id: '7', p: 143, n: 'Snorlax', l: 30, cp: 1980, types: ['Normal'] },
                // Forme id with no front_default/<id>.png; resolved sprite falls back
                // to the base species (998) — dev demo of the mega sprite fix.
                { id: '8', p: 10325, n: 'Mega Baxcalibur', l: 60, cp: 3050, types: ['Dragon', 'Ice'], sprite: SPRITE_BASE + '/998.png' },
            ];
            return;
        }

        new QWebChannel(qt.webChannelTransport, function (channel) {
            teamBridge = channel.objects && channel.objects.team;
            const nav = channel.objects && channel.objects.nav;
            window.team = teamBridge;
            window.nav = nav;
            // Wire the dropdown from THIS channel's nav — see ankimon_items_web/nav-switcher.js
            // for why we don't open a second channel.
            if (window.wireNavSwitcher) window.wireNavSwitcher(nav);
            if (teamBridge && teamBridge.getTeam) {
                teamBridge.getTeam().then(applyData);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
