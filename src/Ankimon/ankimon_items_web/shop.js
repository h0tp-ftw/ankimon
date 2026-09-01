// Ankimon Items — unified Mart + Bag web UI.
// Talks to Python via QWebChannel (window.bridge) for buy / reroll / use.

(function () {
    'use strict';

    const TYPE_COLORS = {
        normal: '#A8A77A', fire: '#EE8130', water: '#6390F0',
        electric: '#F7D02C', grass: '#7AC74C', ice: '#96D9D6',
        fighting: '#C22E28', poison: '#A33EA1', ground: '#E2BF65',
        flying: '#A98FF3', psychic: '#F95587', bug: '#A6B91A',
        rock: '#B6A136', ghost: '#735797', dragon: '#6F35FC',
        dark: '#705746', steel: '#B7B7CE', fairy: '#D685AD',
    };

    const CATEGORY_LABELS = {
        tm: 'TM',
        heal: 'Heal',
        pokeball: 'Ball',
        evolution: 'Evolution',
        fossil: 'Fossil',
        other: '',
    };

    const state = {
        data: null,
        filter: 'in_shop',     // in_shop | owned
        category: 'all',       // all | tm | heal | pokeball | evolution | fossil
        search: '',
        selected: null,        // item name
        picker: {
            open: false,
            item: null,        // item being applied
            choices: null,     // null = not yet loaded, [] = empty team, [...] = cached
            choicesContext: null, // Tracks item name or '__base__' to prevent stale stone data
            loading: false,
            search: '',
        },
    };

    let bridge = null;
    let nav = null;

    function initChannel(callback) {
        if (typeof qt === 'undefined' || !qt.webChannelTransport) {
            console.warn('qt.webChannelTransport unavailable — standalone mode');
            callback(null);
            return;
        }
        new QWebChannel(qt.webChannelTransport, function (channel) {
            bridge = channel.objects.bridge;
            nav = channel.objects && channel.objects.nav;
            window.bridge = bridge;
            window.nav = nav;
            callback(bridge);
        });
    }

    function preloadImages(urls, callback) {
        if (!urls || urls.length === 0) {
            callback();
            return;
        }
        let loaded = 0;
        const target = urls.length;
        urls.forEach((url) => {
            const img = new Image();
            img.onload = img.onerror = () => {
                loaded++;
                if (loaded === target) {
                    callback();
                }
            };
            img.src = url;
        });
    }

    // Switch the View filter (in_shop | owned) and sync the sidebar buttons.
    // Used both by the nav clicks and by a Python-forced initial view.
    function setView(filter) {
        if (filter !== 'in_shop' && filter !== 'owned') return;
        state.filter = filter;
        document.querySelectorAll('.nav-item[data-filter]').forEach((b) => {
            b.classList.toggle('active', b.dataset.filter === filter);
        });
    }

    // Entry from Python
    window.initializeItems = function (data) {
        state.data = data;
        // A menu entry (Mart vs Item Bag) can request a starting view. This
        // is a one-shot from Python — it's only present on the push that
        // follows a menu click, so it won't override the user's own filtering.
        if (data && data.initial_view) {
            setView(data.initial_view);
        }
        const urls = (data.items || []).map((i) => i.image_url).filter(Boolean);
        preloadImages(urls, function () {
            render();
        });
    };

    // ---------- Rendering ----------
    function render() {
        if (!state.data) return;
        const items = state.data.items || [];

        document.getElementById('currency-value').textContent =
            formatMoney(state.data.cash) + '¥';

        const rerollBtn = document.getElementById('reroll-btn');
        const rerollCost = state.data.reroll_cost || 0;
        document.getElementById('reroll-cost-label').textContent = rerollCost + '¥';
        rerollBtn.disabled = state.data.cash < rerollCost;

        const counts = countItems(items);
        document.getElementById('count-in-shop').textContent = counts.in_shop;
        document.getElementById('count-owned').textContent = counts.owned;
        document.getElementById('stat-shop').textContent = counts.in_shop;
        document.getElementById('stat-owned').textContent = counts.owned;

        const visible = items.filter(matchesFilters);
        const grid = document.getElementById('items-grid');
        const empty = document.getElementById('empty-state');

        if (visible.length === 0) {
            grid.replaceChildren();
            empty.classList.remove('hidden');
            refreshDetailPanel();
            return;
        }
        empty.classList.add('hidden');
        // Keyed DOM Reconciliation to completely eliminate visual flicker:
        // Instead of destroying and rebuilding all card DOM elements (which
        // destroys <img> instances and triggers asynchronous image-decoding layout passes),
        // we map existing cards by their item name and update/reorder them in-place.
        const existingCards = Array.from(grid.querySelectorAll('.shop-card'));
        const cardMap = new Map();
        existingCards.forEach(card => {
            const name = card.getAttribute('data-item-name');
            if (name) cardMap.set(name, card);
        });

        const itemEntries = visible.filter((i) => !i.is_tm);
        const tmEntries = visible.filter((i) => i.is_tm);
        const splitSections = state.category === 'all'
            && itemEntries.length > 0
            && tmEntries.length > 0;

        const targetElements = [];

        if (splitSections) {
            targetElements.push({type: 'header', kind: 'items', label: 'Items', count: itemEntries.length});
            itemEntries.forEach((item) => targetElements.push({type: 'card', item}));
            targetElements.push({type: 'header', kind: 'tms', label: 'TMs', count: tmEntries.length});
            tmEntries.forEach((item) => targetElements.push({type: 'card', item}));
        } else {
            visible.forEach((item) => targetElements.push({type: 'card', item}));
        }

        const elementsList = targetElements.map(target => {
            if (target.type === 'card') {
                const existing = cardMap.get(target.item.name);
                if (existing) {
                    updateCard(existing, target.item);
                    return existing;
                } else {
                    return buildCard(target.item);
                }
            } else {
                // Find existing header or create a new one
                let headerEl = grid.querySelector(`.shop-section-header .shop-section-title.${target.kind}`);
                if (headerEl) {
                    headerEl = headerEl.closest('.shop-section-header');
                    const countEl = headerEl.querySelector('.shop-section-sub');
                    if (countEl) {
                        countEl.textContent = target.count + ' ' + (target.count === 1 ? 'entry' : 'entries');
                    }
                    return headerEl;
                } else {
                    return buildSectionHeader(target.label, target.kind, target.count);
                }
            }
        });

        // Remove obsolete elements
        const activeSet = new Set(elementsList);
        Array.from(grid.childNodes).forEach(node => {
            if (!activeSet.has(node)) {
                node.remove();
            }
        });

        // Order elements in the grid in-place
        elementsList.forEach((el, index) => {
            if (grid.childNodes[index] !== el) {
                grid.insertBefore(el, grid.childNodes[index] || null);
            }
        });

        refreshDetailPanel();
    }

    function buildSectionHeader(label, kind, count) {
        const header = document.createElement('div');
        header.className = 'shop-section-header inline';
        header.innerHTML =
            '<span class="shop-section-title ' + kind + '">' + label + '</span>' +
            '<span class="shop-section-sub">' + count + ' ' +
                (count === 1 ? 'entry' : 'entries') + '</span>';
        return header;
    }

    function countItems(items) {
        const counts = {all: items.length, in_shop: 0, owned: 0};
        items.forEach((i) => {
            if (i.in_shop) counts.in_shop++;
            if ((i.owned_quantity || 0) > 0 || (i.equipped_instances && i.equipped_instances.length > 0)) counts.owned++;
        });
        return counts;
    }

    function matchesFilters(item) {
        if (state.filter === 'in_shop' && !item.in_shop) return false;
        if (state.filter === 'owned' && (item.owned_quantity || 0) <= 0 && (!item.equipped_instances || item.equipped_instances.length === 0)) return false;
        if (state.category !== 'all' && item.category !== state.category) return false;
        if (state.search) {
            const q = state.search.toLowerCase();
            const hay = [
                item.ui_name, item.name, item.move_type, item.category
            ].filter(Boolean).join(' ').toLowerCase();
            if (!hay.includes(q)) return false;
        }
        return true;
    }

    function cardStateClass(item) {
        const inShop = item.in_shop;
        const owned = (item.owned_quantity || 0) > 0 || (item.equipped_instances && item.equipped_instances.length > 0);
        if (inShop && owned) return 'in-shop-and-owned';
        if (inShop) return 'in-shop-only';
        if (owned) return 'owned-only';
        return '';
    }

    // Returns {label, cls} or null. Tag is suppressed when the filter context
    // already communicates everything (e.g. "OWNED" tag in the Bag filter).
    function cardTagFor(item) {
        if (item.is_tm) return {label: 'TM', cls: 'tm'};

        const owned = (item.owned_quantity || 0) > 0;
        const inShop = !!item.in_shop;

        if (inShop) {
            return {
                label: 'STOCK',
                cls: owned ? 'in-shop-and-owned' : 'in-shop-only'
            };
        }
        return null;
    }

    function updateCard(card, item) {
        // Reset and update class list
        card.className = 'shop-card pokemon-card-equivalent';
        const stateCls = cardStateClass(item);
        if (stateCls) card.classList.add(stateCls);
        if (item.is_tm && (item.owned_quantity || 0) > 0) card.classList.add('owned');
        if (item.in_shop && state.data.cash < (item.price || 0) &&
            (!item.is_tm || (item.owned_quantity || 0) === 0)) {
            card.classList.add('unaffordable');
        }
        if (item.equipped_instances && item.equipped_instances.length > 0) card.classList.add('equipped');
        if (state.selected === item.name) card.classList.add('selected');

        // Update tag
        let tag = card.querySelector('.shop-card-tag');
        const tagText = cardTagFor(item);
        if (tagText) {
            if (!tag) {
                tag = document.createElement('div');
                card.insertBefore(tag, card.firstChild);
            }
            tag.className = 'shop-card-tag ' + tagText.cls;
            tag.textContent = tagText.label;
        } else if (tag) {
            tag.remove();
        }

        // Update badges
        let badges = card.querySelector('.shop-card-badges');
        if (!badges) {
            badges = document.createElement('div');
            badges.className = 'shop-card-badges';
            const refNode = card.querySelector('.shop-card-tag') ? card.querySelector('.shop-card-tag').nextSibling : card.firstChild;
            card.insertBefore(badges, refNode);
        }
        badges.innerHTML = '';
        if ((item.owned_quantity || 0) > 0) {
            if (item.is_tm) {
                if (state.filter !== 'owned') {
                    const badge = document.createElement('span');
                    badge.className = 'shop-card-badge owned';
                    badge.textContent = 'OWNED';
                    badges.appendChild(badge);
                }
            } else {
                const badge = document.createElement('span');
                badge.className = 'shop-card-badge qty';
                badge.textContent = 'x' + item.owned_quantity;
                badges.appendChild(badge);
            }
        }
        if (item.equipped_instances && item.equipped_instances.length > 0) {
            const badge = document.createElement('span');
            badge.className = 'shop-card-badge equipped';
            badge.textContent = 'E x' + item.equipped_instances.length;
            badges.appendChild(badge);
        }

        // Update sprite src if different (preserves img DOM instance to avoid flickering)
        const img = card.querySelector('.shop-card-sprite img');
        if (img && img.getAttribute('src') !== (item.image_url || '')) {
            img.style.opacity = '1';
            img.onerror = () => {
                img.onerror = () => { img.style.opacity = '0.25'; };
                img.src = '../user_files/sprites/front_default/0.png';
                img.style.opacity = '0.25';
            };
            img.src = item.image_url || '';
        }

        // Update name
        const nameEl = card.querySelector('.shop-card-name');
        if (nameEl) {
            nameEl.textContent = item.ui_name || item.name;
        }

        // Update price pill
        let priceEl = card.querySelector('.shop-card-price-pill');
        if (item.in_shop) {
            if (!priceEl) {
                priceEl = document.createElement('div');
                priceEl.className = 'shop-card-price-pill';
                card.appendChild(priceEl);
            }
            priceEl.textContent = formatMoney(item.price || 0) + '¥';
        } else if (priceEl) {
            priceEl.remove();
        }
    }

    function buildCard(item) {
        const card = document.createElement('div');
        card.className = 'shop-card pokemon-card-equivalent';
        card.setAttribute('data-item-name', item.name);
        const stateCls = cardStateClass(item);
        if (stateCls) card.classList.add(stateCls);
        if (item.is_tm && (item.owned_quantity || 0) > 0) card.classList.add('owned');
        if (item.in_shop && state.data.cash < (item.price || 0) &&
            (!item.is_tm || (item.owned_quantity || 0) === 0)) {
            card.classList.add('unaffordable');
        }
        if (item.equipped_instances && item.equipped_instances.length > 0) card.classList.add('equipped');
        if (state.selected === item.name) card.classList.add('selected');

        // Top-left tag — only show information the filter doesn't already imply.
        const tag = document.createElement('div');
        const tagText = cardTagFor(item);
        if (tagText) {
            tag.className = 'shop-card-tag ' + tagText.cls;
            tag.textContent = tagText.label;
            card.appendChild(tag);
        }

        // Top-right badges. TMs are binary (owned or not), so we surface a
        // word "OWNED" instead of a redundant "x1" quantity — and suppress
        // it entirely in the Bag view where every visible card is owned.
        const badges = document.createElement('div');
        badges.className = 'shop-card-badges';
        if ((item.owned_quantity || 0) > 0) {
            if (item.is_tm) {
                if (state.filter !== 'owned') {
                    const badge = document.createElement('span');
                    badge.className = 'shop-card-badge owned';
                    badge.textContent = 'OWNED';
                    badges.appendChild(badge);
                }
            } else {
                const badge = document.createElement('span');
                badge.className = 'shop-card-badge qty';
                badge.textContent = 'x' + item.owned_quantity;
                badges.appendChild(badge);
            }
        }
        if (item.equipped_instances && item.equipped_instances.length > 0) {
            const badge = document.createElement('span');
            badge.className = 'shop-card-badge equipped';
            badge.textContent = 'E x' + item.equipped_instances.length;
            badges.appendChild(badge);
        }
        card.appendChild(badges);

        // Sprite
        const spriteWrap = document.createElement('div');
        spriteWrap.className = 'shop-card-sprite';
        const img = document.createElement('img');
        img.decoding = 'sync';
        img.src = item.image_url || '';
        img.alt = item.ui_name || item.name;
        img.onerror = () => {
            img.onerror = () => { img.style.opacity = '0.25'; };
            img.src = '../user_files/sprites/front_default/0.png';
            img.style.opacity = '0.25';
        };
        spriteWrap.appendChild(img);
        card.appendChild(spriteWrap);

        // Name
        const name = document.createElement('div');
        name.className = 'shop-card-name';
        name.textContent = item.ui_name || item.name;
        card.appendChild(name);

        // Tags row: TM type + category
        const tagsRow = document.createElement('div');
        tagsRow.className = 'shop-card-tags-row';
        if (item.is_tm && item.move_type) {
            const t = document.createElement('span');
            t.className = 'mini-type';
            t.textContent = item.move_type_label || item.move_type;
            t.style.background = TYPE_COLORS[item.move_type.toLowerCase()] || 'var(--border-main)';
            tagsRow.appendChild(t);
        } else if (CATEGORY_LABELS[item.category]) {
            const c = document.createElement('span');
            c.className = 'cat-pill';
            c.textContent = CATEGORY_LABELS[item.category];
            tagsRow.appendChild(c);
        }
        card.appendChild(tagsRow);

        // Price pill (only if in_shop)
        if (item.in_shop) {
            const price = document.createElement('div');
            price.className = 'shop-card-price-pill';
            price.textContent = formatMoney(item.price || 0) + '¥';
            card.appendChild(price);
        }

        card.addEventListener('click', () => selectItem(item.name));
        return card;
    }

    function formatMoney(n) {
        return (n || 0).toLocaleString();
    }

    // ---------- Detail panel ----------
    function selectItem(name) {
        state.selected = name;
        render();
    }

    function refreshDetailPanel() {
        const placeholder = document.getElementById('detail-placeholder');
        const content = document.getElementById('detail-content');
        const closeBtn = document.getElementById('close-detail');

        if (!state.selected) {
            placeholder.classList.remove('hidden');
            content.classList.add('hidden');
            closeBtn.classList.add('hidden');
            return;
        }

        const item = (state.data.items || []).find((i) => i.name === state.selected);
        if (!item) {
            state.selected = null;
            placeholder.classList.remove('hidden');
            content.classList.add('hidden');
            closeBtn.classList.add('hidden');
            return;
        }

        placeholder.classList.add('hidden');
        content.classList.remove('hidden');
        closeBtn.classList.remove('hidden');
        renderDetail(item);
    }

    function renderDetail(item) {
        document.getElementById('det-id').textContent =
            item.is_tm ? 'TM' : (CATEGORY_LABELS[item.category] || 'ITEM').toUpperCase();
        document.getElementById('det-name').textContent = item.ui_name || item.name;

        const statusRow = document.getElementById('det-status-row');
        statusRow.innerHTML = '';

        if (item.in_shop) {
            const p = document.createElement('span');
            p.className = 'det-pill price';
            p.textContent = formatMoney(item.price || 0) + '¥';
            statusRow.appendChild(p);
        }
        if ((item.owned_quantity || 0) > 0) {
            const o = document.createElement('span');
            o.className = 'det-pill owned';
            o.textContent = item.is_tm ? 'Owned' : 'Owned x' + item.owned_quantity;
            statusRow.appendChild(o);
        }
        if (item.is_tm && item.move_type) {
            const t = document.createElement('span');
            t.className = 'det-pill move-type';
            t.textContent = item.move_type_label || item.move_type;
            t.style.background = TYPE_COLORS[item.move_type.toLowerCase()] || 'var(--border-main)';
            statusRow.appendChild(t);
        }

        const sprite = document.getElementById('det-sprite');
        sprite.style.opacity = '1';
        sprite.src = item.image_url || '';
        sprite.alt = item.ui_name || item.name;
        sprite.onerror = () => {
            sprite.onerror = null;
            sprite.src = '../user_files/sprites/front_default/0.png';
            sprite.style.opacity = '0.25';
        };

        const glow = document.getElementById('det-glow');
        const glowColor = item.is_tm && item.move_type
            ? (TYPE_COLORS[item.move_type.toLowerCase()] || '#58a6ff')
            : (item.in_shop ? '#d29922' : '#3fb950');
        glow.style.background = `radial-gradient(circle, ${glowColor}55, transparent 70%)`;

        document.getElementById('det-description').textContent =
            item.description || 'No description available.';

        // Move stats (TMs)
        const moveSection = document.getElementById('det-move-section');
        const moveStats = document.getElementById('det-move-stats');
        moveStats.innerHTML = '';
        if (item.is_tm && (item.move_power || item.move_accuracy || item.move_pp)) {
            moveSection.classList.remove('hidden');
            const rows = [
                {label: 'Power', value: item.move_power, max: 250},
                {label: 'Accuracy', value: item.move_accuracy, max: 100},
                {label: 'PP', value: item.move_pp, max: 40},
            ];
            if (item.move_damage_class) {
                rows.push({label: 'Category', value: item.move_damage_class, max: null});
            }
            rows.forEach((row) => {
                if (row.value === null || row.value === undefined || row.value === '') return;
                moveStats.appendChild(buildStatRow(row));
            });
            setTimeout(() => {
                moveStats.querySelectorAll('.stat-bar-fill').forEach((el) => {
                    el.style.width = el.dataset.targetWidth || '0%';
                });
            }, 50);
        } else {
            moveSection.classList.add('hidden');
        }

        // Equipped section
        const equippedSection = document.getElementById('det-equipped-section');
        const equippedList = document.getElementById('det-equipped-list');
        equippedList.innerHTML = '';
        if (item.equipped_instances && item.equipped_instances.length > 0) {
            equippedSection.classList.remove('hidden');
            item.equipped_instances.forEach((inst) => {
                const row = document.createElement('div');
                row.className = 'equipped-row';
                
                const name = document.createElement('span');
                name.className = 'equipped-poke-name';
                name.textContent = inst.name;
                
                const btn = document.createElement('button');
                btn.className = 'equipped-unequip-btn';
                btn.textContent = 'Unequip';
                btn.onclick = () => onUnequip(inst.individual_id, item.name);
                
                row.appendChild(name);
                row.appendChild(btn);
                equippedList.appendChild(row);
            });
        } else {
            equippedSection.classList.add('hidden');
        }

        // Actions
        const actions = document.getElementById('det-actions');
        actions.innerHTML = '';
        const hint = document.getElementById('det-hint');
        hint.textContent = '';
        hint.className = 'affordability-hint';

        const ownedQty = item.owned_quantity || 0;
        const blockedTm = item.is_tm && ownedQty > 0;
        const unaffordable = state.data.cash < (item.price || 0);

        if (item.in_shop) {
            const buy = document.createElement('button');
            buy.className = 'det-action-btn buy';
            if (blockedTm) {
                buy.disabled = true;
                buy.innerHTML = '<span>Already Owned</span>';
            } else if (unaffordable) {
                buy.disabled = true;
                buy.innerHTML = '<span>Buy</span><span class="det-action-meta">Not Enough ¥</span>';
            } else {
                buy.innerHTML = '<span>Buy</span><span class="det-action-meta">' + formatMoney(item.price || 0) + '¥</span>';
                buy.onclick = () => onBuy(item);
            }
            actions.appendChild(buy);
        }

        if (ownedQty > 0 && !item.is_tm) {
            const use = document.createElement('button');
            use.className = 'det-action-btn use';
            const label = useLabelFor(item);
            use.innerHTML = '<span>' + label + '</span><span class="det-action-meta">x' + ownedQty + '</span>';
            use.onclick = () => onUse(item);
            actions.appendChild(use);
        }

        if (actions.childElementCount === 0) {
            hint.textContent = 'Nothing to do for this item right now.';
        } else if (item.in_shop && unaffordable && !blockedTm) {
            const shortBy = (item.price || 0) - state.data.cash;
            hint.textContent = `Need ${formatMoney(shortBy)}¥ more to buy.`;
            hint.classList.add('error');
        } else if (item.in_shop && !blockedTm) {
            const after = state.data.cash - (item.price || 0);
            hint.textContent = `Balance after buy: ${formatMoney(after)}¥`;
        }
    }

    function useLabelFor(item) {
        switch (item.category) {
            case 'heal': return 'Use on Active Pokémon';
            case 'fossil': return 'Revive Fossil';
            case 'pokeball': return 'Throw at Wild Pokémon';
            case 'evolution': return 'Evolve a Pokémon';
            default: return 'Give to a Pokémon';
        }
    }

    function buildStatRow({label, value, max}) {
        const row = document.createElement('div');
        row.className = 'stat-item';
        const labelEl = document.createElement('div');
        labelEl.className = 'stat-label';
        const lbl = document.createElement('span');
        lbl.textContent = label;
        const val = document.createElement('span');
        val.className = 'stat-val';
        val.textContent = value;
        labelEl.appendChild(lbl);
        labelEl.appendChild(val);
        row.appendChild(labelEl);

        if (max !== null) {
            const bg = document.createElement('div');
            bg.className = 'stat-bar-bg';
            const fill = document.createElement('div');
            fill.className = 'stat-bar-fill';
            const numVal = typeof value === 'number' ? value : 0;
            const pct = Math.max(0, Math.min(100, (numVal / max) * 100));
            fill.dataset.targetWidth = pct + '%';
            bg.appendChild(fill);
            row.appendChild(bg);
        }
        return row;
    }

    function onUnequip(individualId, itemName) {
        if (!bridge || !bridge.unequipItem) return;
        bridge.unequipItem(individualId, itemName, function (result) {
            if (!result) return;
            showToast(result.message || (result.ok ? 'Unequipped!' : 'Unequip failed.'), !result.ok);
            state.picker.choicesContext = null;
        });
    }

    // ---------- Actions ----------
    function onBuy(item) {
        if (!bridge) return;
        bridge.buy(item.name, !!item.is_tm, function (result) {
            if (!result) return;
            showToast(result.message || (result.ok ? 'Purchased!' : 'Purchase failed.'), !result.ok);
        });
    }

    function onUse(item) {
        if (!bridge) return;
        // Evolution items and held items need a Pokémon target — open the
        // in-shell picker instead of calling useItem (which would trigger
        // the legacy QInputDialog in Python).
        if (item.category === 'evolution' || item.category === 'other') {
            openPicker(item);
            return;
        }
        bridge.useItem(item.name, function (result) {
            if (!result) return;
            if (result.message) showToast(result.message, !result.ok);
        });
    }

    function onReroll() {
        if (!bridge) return;
        bridge.reroll(function (result) {
            if (!result) return;
            showToast(result.message || (result.ok ? 'Stock rerolled!' : 'Reroll failed.'), !result.ok);
        });
    }

    function showToast(message, isError) {
        if (!message) return;
        const toast = document.getElementById('toast');
        toast.textContent = message;
        toast.classList.toggle('error', !!isError);
        toast.classList.add('visible');
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => toast.classList.remove('visible'), 2400);
    }

    // ---------- UI plumbing ----------
    function bindUI() {
        document.querySelectorAll('.nav-item[data-filter]').forEach((btn) => {
            btn.addEventListener('click', () => {
                setView(btn.dataset.filter);
                render();
            });
        });

        document.querySelectorAll('.nav-item[data-category]').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.nav-item[data-category]').forEach((b) =>
                    b.classList.remove('active'));
                btn.classList.add('active');
                state.category = btn.dataset.category;
                render();
            });
        });

        const searchInput = document.getElementById('shop-search');
        const clearBtn = document.getElementById('clear-search');
        searchInput.addEventListener('input', (e) => {
            state.search = e.target.value.trim();
            clearBtn.classList.toggle('hidden', !state.search);
            render();
        });
        clearBtn.addEventListener('click', () => {
            searchInput.value = '';
            state.search = '';
            clearBtn.classList.add('hidden');
            render();
        });

        document.getElementById('reroll-btn').addEventListener('click', openRerollConfirm);
        document.getElementById('confirm-cancel').addEventListener('click', closeConfirm);
        document.getElementById('confirm-modal').addEventListener('click', (e) => {
            if (e.target.classList.contains('confirm-backdrop')) closeConfirm();
        });
        document.getElementById('confirm-ok').addEventListener('click', () => {
            const skip = document.getElementById('confirm-skip');
            if (skip && skip.checked && bridge && bridge.setSkipRerollConfirm) {
                bridge.setSkipRerollConfirm(true, function () {});
                // Optimistic — push_screen_data() will overwrite, but this
                // keeps the flag set if the next click happens to land before
                // the round-trip completes.
                if (state.data) state.data.skip_reroll_confirm = true;
            }
            closeConfirm();
            onReroll();
        });

        document.getElementById('close-detail').addEventListener('click', () => {
            state.selected = null;
            render();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const picker = document.getElementById('picker-modal');
                const modal = document.getElementById('confirm-modal');
                if (!picker.classList.contains('hidden')) {
                    closePicker();
                } else if (!modal.classList.contains('hidden')) {
                    closeConfirm();
                } else if (state.selected) {
                    state.selected = null;
                    render();
                }
            } else if (e.key === '/' && document.activeElement !== searchInput) {
                // Don't grab '/' while typing in the picker search box.
                const pickerSearch = document.getElementById('picker-search');
                if (document.activeElement === pickerSearch) return;
                e.preventDefault();
                searchInput.focus();
            } else if (e.key === 'Enter') {
                const modal = document.getElementById('confirm-modal');
                if (!modal.classList.contains('hidden')) {
                    const ok = document.getElementById('confirm-ok');
                    if (!ok.disabled) ok.click();
                }
            }
        });
    }

    function openRerollConfirm() {
        if (!state.data) return;
        const cost = state.data.reroll_cost || 0;
        const after = (state.data.cash || 0) - cost;
        const insufficient = after < 0;

        // "Don't ask again today" — skip the modal entirely when affordable.
        // The insufficient case still falls through so the user sees the
        // "Not Enough ¥" disabled-button feedback.
        if (state.data.skip_reroll_confirm && !insufficient) {
            onReroll();
            return;
        }

        document.getElementById('confirm-cost').textContent = formatMoney(cost) + '¥';
        const balance = document.getElementById('confirm-balance');
        document.getElementById('confirm-balance-value').textContent = formatMoney(after) + '¥';
        balance.classList.toggle('insufficient', insufficient);

        const okBtn = document.getElementById('confirm-ok');
        okBtn.disabled = insufficient;
        okBtn.textContent = insufficient ? 'Not Enough ¥' : 'Confirm Reroll';

        const skipCheckbox = document.getElementById('confirm-skip');
        if (skipCheckbox) skipCheckbox.checked = false;

        document.getElementById('confirm-modal').classList.remove('hidden');
    }

    function closeConfirm() {
        document.getElementById('confirm-modal').classList.add('hidden');
    }

    // ---------- Pokémon picker (evolution items + held items) ----------
    // Choices are lazy-fetched on first open (avoids shipping multi-MB
    // payloads with every inventory push) and cached in state.picker.choices
    // for the lifetime of the items window. Invalidated after team-mutating
    // actions (useItemOnPokemon) so the next open reflects the change.
    function openPicker(item) {
        const isEvo = item.category === 'evolution';
        const targetContext = isEvo ? item.name : '__base__';
        
        // If the current cache doesn't match the required context (specific stone
        // or base list), we must fetch fresh data.
        if (state.picker.choicesContext !== targetContext) {
            state.picker.choices = null;
        }
        
        state.picker.item = item;
        state.picker.search = '';
        state.picker.open = true;

        document.getElementById('picker-title').textContent =
            item.category === 'evolution' ? 'Evolve which Pokémon?' : 'Give to which Pokémon?';
        document.getElementById('picker-subtitle').textContent =
            (item.ui_name || item.name || '').toUpperCase();

        const searchEl = document.getElementById('picker-search');
        searchEl.value = '';

        if (state.picker.choices === null) {
            renderPickerLoading();
            document.getElementById('picker-modal').classList.remove('hidden');
            loadPickerChoices(item.name, targetContext, () => {
                // Don't render if user closed the modal during fetch.
                if (state.picker.open) renderPickerGrid();
            });
        } else {
            // Render grid *before* showing the modal to avoid flicker
            renderPickerGrid();
            document.getElementById('picker-modal').classList.remove('hidden');
        }
        
        // Auto-focus the search box so the user can type to filter.
        setTimeout(() => searchEl.focus(), 0);
    }

    function loadPickerChoices(itemName, context, done) {
        if (state.picker.loading || !bridge || !bridge.getPokemonChoices) {
            if (done) done();
            return;
        }
        state.picker.loading = true;
        bridge.getPokemonChoices(itemName || '', function (result) {
            state.picker.loading = false;
            state.picker.choices = (result && result.choices) || [];
            state.picker.choicesContext = context;
            if (done) done();
        });
    }

    function renderPickerLoading() {
        const grid = document.getElementById('picker-grid');
        const empty = document.getElementById('picker-empty');
        const countEl = document.getElementById('picker-count');
        empty.textContent = 'Loading your team…';
        empty.classList.remove('hidden');
        grid.replaceChildren();
        grid.style.display = 'none';
        countEl.textContent = '…';
        countEl.classList.remove('truncated');
    }

    function closePicker() {
        state.picker.open = false;
        state.picker.item = null;
        state.picker.search = '';
        document.getElementById('picker-modal').classList.add('hidden');
    }

    // Hard cap on rendered cards — long-time players can have 10k+
    // captured Pokémon, and rendering all of them blows out the DOM, the
    // lazy-image queue, and any hope of a smooth open. Past the cap we
    // require search to drill down (which is the natural UX for "pick
    // one of many" anyway).
    const PICKER_RENDER_CAP = 60;

    function renderPickerGrid() {
        const grid = document.getElementById('picker-grid');
        const empty = document.getElementById('picker-empty');
        const countEl = document.getElementById('picker-count');
        let choices = state.picker.choices || [];

        if (state.picker.choices === null) {
            renderPickerLoading();
            return;
        }

        // Only show eligible Pokémon for evolution items.
        if (state.picker.item && state.picker.item.category === 'evolution') {
            choices = choices.filter((c) => c.e === 1);
        }

        const q = state.picker.search.trim().toLowerCase();
        // Compact field names from Python — see get_pokemon_choices:
        //   id=individual_id, p=pokedex_id, n=name, l=level,
        //   s=shiny, m=is_main, h=held_item, nk=nickname (if differs)
        const matched = q
            ? choices.filter((c) => {
                const hay = [c.nk, c.n, c.h, String(c.l || ''), String(c.p || '')]
                    .filter(Boolean).join(' ').toLowerCase();
                return hay.includes(q);
            })
            : choices;

        const visible = matched.slice(0, PICKER_RENDER_CAP);

        // Count chip — show "60 / 16,626" when we capped, plain count otherwise.
        if (matched.length > visible.length) {
            countEl.textContent = `${visible.length} / ${matched.length.toLocaleString()}`;
            countEl.title = `Showing ${visible.length} of ${matched.length} — type to narrow down`;
            countEl.classList.add('truncated');
        } else {
            countEl.textContent = String(matched.length);
            countEl.title = '';
            countEl.classList.remove('truncated');
        }

        if (matched.length === 0) {
            grid.replaceChildren();
            // Three distinct empty states. The middle one matters because the
            // eligibility filter above can empty the list on its own, with the
            // search box untouched: a player whose only Kirlia is female sees
            // zero Dawn Stone candidates, and blaming a search they never typed
            // reads as a broken picker rather than an answer.
            const eligibilityEmptied = !q && choices.length === 0
                && state.picker.choices.length > 0;
            empty.textContent = state.picker.choices.length === 0
                ? "You don't have any Pokémon yet."
                : eligibilityEmptied
                    ? 'None of your Pokémon can use this item.'
                    : 'No Pokémon match that search.';
            empty.classList.remove('hidden');
            grid.style.display = 'none';
            return;
        }
        empty.classList.add('hidden');
        grid.style.display = '';

        const frag = document.createDocumentFragment();
        visible.forEach((choice) => frag.appendChild(buildPickerCard(choice)));

        // Footer row when truncated — gives the user an obvious "more
        // exist, refine to see them" cue instead of leaving them guessing.
        if (matched.length > visible.length) {
            const more = document.createElement('div');
            more.className = 'picker-more-hint';
            more.textContent =
                `+ ${(matched.length - visible.length).toLocaleString()} more — type a name or level to find a specific Pokémon`;
            frag.appendChild(more);
        }

        grid.replaceChildren(frag);
        grid.scrollTop = 0;
    }

    function buildPickerCard(choice) {
        // Compact fields from Python: id, p, b (base_id), n, l, cp, s, m, h, nk.
        // Match the Ankidex `.pokemon-card` shape so it picks up the same
        // hover/sprite/border treatment automatically. Team-specific bits
        // (level, CP, active/shiny marks, held item) layer on top.
        const isMain = !!choice.m;
        const isShiny = !!choice.s;
        const isEligible = !!choice.e;
        const heldItem = choice.h || '';
        const nickname = choice.nk || '';
        const name = choice.n || '';
        const displayName = nickname || name || 'Unknown';

        const card = document.createElement('div');
        card.className = 'pokemon-card team-card state-caught';
        if (isMain) card.classList.add('is-main');
        if (isEligible) card.classList.add('is-eligible');

        if (choice.l !== null && choice.l !== undefined) {
            const lvl = document.createElement('div');
            lvl.className = 'team-card-level';
            lvl.textContent = 'Lv ' + choice.l;
            card.appendChild(lvl);
        }

        if (choice.cp !== null && choice.cp !== undefined) {
            const cp = document.createElement('div');
            cp.className = 'team-card-cp';
            cp.textContent = 'CP ' + choice.cp.toLocaleString();
            card.appendChild(cp);
        }

        if (isMain || isShiny) {
            const marks = document.createElement('div');
            marks.className = 'team-card-marks';
            if (isMain) {
                const m = document.createElement('span');
                m.className = 'mark mark-main';
                m.textContent = '★';
                m.title = 'Active Pokémon';
                marks.appendChild(m);
            }
            if (isShiny) {
                const s = document.createElement('span');
                s.className = 'mark mark-shiny';
                s.textContent = '✦';
                s.title = 'Shiny';
                marks.appendChild(s);
            }
            card.appendChild(marks);
        }

        const spriteWrap = document.createElement('div');
        spriteWrap.className = 'card-sprite';
        const img = document.createElement('img');
        // Explicit width/height attrs lock layout space before the
        // sprite request fires — without them, the img collapses to 0
        // and the card's column-flex shrinks to just the level pill.
        img.width = 96;
        img.height = 96;
        // Reconstruct sprite path on the JS side — avoids shipping a
        // ~100-byte URL per Pokémon in the inventory payload.
        img.src = `../user_files/sprites/front_default/${choice.p || 0}.png`;
        img.alt = displayName;
        let baseFallbackTried = false;
        img.onerror = () => {
            // Stage 1 Fallback: Try the base species ID (choice.b) if different from choice.p.
            // This handles regional/mega forms that don't have their own sprites.
            if (!baseFallbackTried && choice.b && choice.b !== choice.p) {
                baseFallbackTried = true; // Prevent infinite loop if baseId also fails
                img.src = `../user_files/sprites/front_default/${choice.b}.png`;
            } else {
                // Stage 2 Fallback: Placeholder, then dim if even that fails.
                img.onerror = () => { img.style.opacity = '0.25'; };
                img.src = '../user_files/sprites/front_default/0.png';
            }
        };
        spriteWrap.appendChild(img);
        card.appendChild(spriteWrap);

        const info = document.createElement('div');
        info.className = 'card-info';
        const nameEl = document.createElement('div');
        nameEl.className = 'card-name';
        nameEl.textContent = displayName;
        info.appendChild(nameEl);

        // Second line: held item (or species when nickname differs).
        const metaParts = [];
        if (heldItem) {
            metaParts.push(`<span class="meta-held">Holds ${esc(titleCase(heldItem))}</span>`);
        } else if (nickname && name && nickname.toLowerCase() !== name.toLowerCase()) {
            metaParts.push(esc(titleCase(name)));
        }
        if (metaParts.length > 0) {
            const meta = document.createElement('div');
            meta.className = 'team-card-meta';
            meta.innerHTML = metaParts.join(' · ');
            info.appendChild(meta);
        }
        card.appendChild(info);

        card.addEventListener('click', () => onPickerChoose(choice));
        return card;
    }

    function titleCase(s) {
        return String(s || '').replace(/-/g, ' ').replace(
            /\w\S*/g, (t) => t.charAt(0).toUpperCase() + t.slice(1).toLowerCase()
        );
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function onPickerChoose(choice) {
        const item = state.picker.item;
        if (!item || !bridge || !bridge.useItemOnPokemon) {
            closePicker();
            return;
        }
        closePicker();
        // Held-item / evolution might change the team — invalidate so
        // the next open re-fetches fresh data.
        state.picker.choices = null;
        // choice.id == individual_id in the compact payload.
        bridge.useItemOnPokemon(item.name, choice.id, function (result) {
            if (!result) return;
            if (result.message) showToast(result.message, !result.ok);
        });
    }

    function bindPickerUI() {
        document.getElementById('picker-close').addEventListener('click', closePicker);
        document.getElementById('picker-cancel').addEventListener('click', closePicker);
        document.getElementById('picker-modal').addEventListener('click', (e) => {
            if (e.target.classList.contains('picker-backdrop')) closePicker();
        });
        // Debounce search re-renders — typing fast in a 16k-row list
        // shouldn't kick a full filter+render on every keystroke.
        let searchTimer = null;
        document.getElementById('picker-search').addEventListener('input', (e) => {
            state.picker.search = e.target.value || '';
            clearTimeout(searchTimer);
            searchTimer = setTimeout(renderPickerGrid, 80);
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        bindUI();
        bindPickerUI();
        // Dropdown nav: wire from the shared switcher once the channel resolves
        // so it has the live NavBridge. See ankimon_items_web/nav-switcher.js.
        initChannel(() => {
            if (window.wireNavSwitcher) window.wireNavSwitcher(window.nav);
        });
    });
})();
