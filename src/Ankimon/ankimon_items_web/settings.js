// Settings screen — renders the group schema, tracks dirty state, persists
// via the QWebChannel `settings` bridge object.

(function () {
    'use strict';

    const state = {
        data: null,           // raw payload from Python
        edits: {},            // key → new value (only present if dirty)
        search: '',
        activeGroup: null,    // currently scrolled-into-view group label
    };

    let bridge = null;
    let nav = null;
    let unsavedGuardOpen = false;

    /**
     * Initializes the QWebChannel connection to the Python backend.
     * 
     * Establishes communication with the settings bridge object exposed
     * by the Python side. Falls back to standalone mode if the Qt
     * WebChannel transport is unavailable.
     * 
     * @param {Function} cb - Callback function invoked with the bridge object
     *                         or null if initialization fails.
     */
    function initChannel(cb) {
        if (typeof qt === 'undefined' || !qt.webChannelTransport) {
            console.warn('qt.webChannelTransport unavailable — standalone mode');
            cb(null);
            return;
        }
        new QWebChannel(qt.webChannelTransport, function (channel) {
            bridge = channel.objects && channel.objects.settings;
            nav = channel.objects && channel.objects.nav;
            window.bridge = bridge;
            window.nav = nav;
            cb(bridge);
        });
    }

    /**
     * Entry point for the Python backend to initialize the settings UI.
     * 
     * Receives the settings data payload, resets any pending edits, and
     * triggers a full UI re-render.
     * 
     * @param {Object} data - The settings data containing groups, settings,
     *                        and current values from the Python backend.
     */
    window.initializeSettings = function (data) {
        state.data = data || {groups: []};
        state.edits = {};  // fresh data resets dirty state
        renderAll();
    };

    // ---------- Link handling ----------
    /**
     * Sets up click handlers for all anchor tags within setting descriptions.
     * 
     * Intercepts link clicks to prevent default browser navigation and instead
     * routes them through the Qt bridge for opening in the system's default
     * browser. Falls back to window.open if the bridge is unavailable.
     */
    function setupDescriptionLinks() {
        document.querySelectorAll('.setting-description a').forEach(function(link) {
            // Remove old listener to prevent duplicates
            if (link._clickHandler) {
                link.removeEventListener('click', link._clickHandler);
            }
            
            link._clickHandler = function(e) {
                e.preventDefault();
                e.stopPropagation();
                const url = this.getAttribute('href');
                if (!url) return;
                
                // Try to use bridge if available
                if (bridge && typeof bridge.openUrl === 'function') {
                    bridge.openUrl(url);
                } else if (bridge && typeof bridge.openLink === 'function') {
                    bridge.openLink(url);
                } else {
                    // Fallback: open in new tab/window
                    window.open(url, '_blank');
                }
            };
            
            link.addEventListener('click', link._clickHandler);
        });
    }

    // ---------- Leaderboard toggle handling ----------
    /**
     * Manages the enabling/disabling of leaderboard credential fields.
     * 
     * When the "Enable Leaderboard Sync" toggle is toggled, this function
     * enables or disables the Username and API Key input fields accordingly.
     * The toggle's built-in click handler already triggers a full re-render,
     * so no additional event listeners are needed.
     */
    function setupLeaderboardToggle() {
        const toggleRow = document.querySelector('.setting-row[data-key="misc.leaderboard"]');
        if (!toggleRow) return;
        
        const usernameRow = document.querySelector('.setting-row[data-key="leaderboard.username"]');
        const apiKeyRow = document.querySelector('.setting-row[data-key="leaderboard.api_key"]');
        
        // Use the setting value directly instead of DOM text content
        // This is robust against translation/i18n changes
        const setting = findSetting('misc.leaderboard');
        const isEnabled = setting ? currentValue(setting) : false;
        
        function updateLeaderboardFields() {
            // Re-query the setting value each time to ensure fresh state
            const setting = findSetting('misc.leaderboard');
            const isEnabled = setting ? currentValue(setting) : false;
            
            [usernameRow, apiKeyRow].forEach(row => {
                if (!row) return;
                const inputs = row.querySelectorAll('.setting-input');
                inputs.forEach(input => {
                    input.disabled = !isEnabled;
                });
                row.classList.toggle('disabled', !isEnabled);
            });
        }
        
        // No redundant click listeners needed!
        // The toggle's click handler already calls renderAll() → setupLeaderboardToggle() → updateLeaderboardFields()
        updateLeaderboardFields();
    }

    // ---------- Validation helpers ----------
    /**
     * Removes validation error styling from all form elements.
     * 
     * Clears the 'validation-error' class from both setting rows and
     * individual input elements. Typically called when the user begins
     * typing in a previously invalid field.
     */
    function clearValidationErrors() {
        document.querySelectorAll('.validation-error').forEach(el => {
            el.classList.remove('validation-error');
        });
        document.querySelectorAll('.setting-input.validation-error').forEach(el => {
            el.classList.remove('validation-error');
        });
    }

    // ---------- Rendering ----------
    /**
     * Renders the entire settings UI from scratch.
     * 
     * Orchestrates the full rendering pipeline including sidebar navigation,
     * settings content, search filtering, dirty state indicators, and
     * scroll position tracking.
     */
    function renderAll() {
        renderGroupJumps();
        renderContent();
        applySearchFilter();
        updateDirtyUI();
        // Re-run the spy after render so the active button reflects the
        // post-render scroll position (in case the DOM rebuild moved things
        // around).
        updateActiveSection();
    }

    /**
     * Renders the sidebar navigation with group section links.
     * 
     * Creates clickable jump links for each settings group, displaying
     * the group name and the count of settings within it.
     */
    function renderGroupJumps() {
        const container = document.getElementById('group-jumps');
        // Build into a fragment then swap atomically to avoid a brief
        // empty-state on every save refresh.
        const frag = document.createDocumentFragment();
        (state.data.groups || []).forEach((g) => {
            const btn = document.createElement('button');
            btn.className = 'group-jump';
            btn.dataset.group = g.label;
            const label = document.createElement('span');
            label.textContent = g.label;
            const count = document.createElement('span');
            count.className = 'group-jump-count';
            count.textContent = countSettings(g);
            btn.appendChild(label);
            btn.appendChild(count);
            btn.addEventListener('click', () => scrollToGroup(g.label));
            frag.appendChild(btn);
        });
        container.replaceChildren(frag);
    }

    /**
     * Counts the total number of settings within a group including subgroups.
     * 
     * @param {Object} group - The settings group object.
     * @returns {number} The total count of settings in the group.
     */
    function countSettings(group) {
        let n = (group.settings || []).length;
        (group.subgroups || []).forEach((s) => { n += (s.settings || []).length; });
        return n;
    }

    /**
     * Renders the main settings content area.
     * 
     * Builds the complete settings tree from the data payload, including
     * all groups, subgroups, and individual setting rows. After rendering,
     * sets up link handlers and leaderboard toggle behavior.
     */
    function renderContent() {
        const root = document.getElementById('settings-content');
        // Build the whole settings tree into a fragment off-DOM, then swap
        // it in atomically. Without this, every save → re-fetch cycle
        // flashes an empty form area while ~60 rows rebuild sequentially.
        const frag = document.createDocumentFragment();
        (state.data.groups || []).forEach((group) => {
            const groupEl = document.createElement('section');
            groupEl.className = 'settings-group';
            groupEl.dataset.group = group.label;

            const header = document.createElement('div');
            header.className = 'settings-group-header';
            const title = document.createElement('h2');
            title.className = 'settings-group-title';
            title.textContent = group.label;
            const count = document.createElement('span');
            count.className = 'settings-group-count';
            count.textContent = countSettings(group) + ' settings';
            header.appendChild(title);
            header.appendChild(count);
            groupEl.appendChild(header);

            (group.settings || []).forEach((s) => {
                groupEl.appendChild(buildSettingRow(s));
            });

            (group.subgroups || []).forEach((sub) => {
                const subEl = document.createElement('div');
                subEl.className = 'settings-subgroup';
                subEl.dataset.subgroup = sub.label;
                const subTitle = document.createElement('div');
                subTitle.className = 'settings-subgroup-title';
                subTitle.textContent = sub.label;
                subEl.appendChild(subTitle);
                (sub.settings || []).forEach((s) => {
                    subEl.appendChild(buildSettingRow(s));
                });
                groupEl.appendChild(subEl);
            });

            frag.appendChild(groupEl);
        });
        root.replaceChildren(frag);
        
        // Setup description links after content is rendered
        setupDescriptionLinks();
        
        // Setup leaderboard toggle behavior
        setupLeaderboardToggle();
    }

    /**
     * Builds a single setting row DOM element.
     * 
     * Creates the DOM structure for an individual setting, including its
     * label, description (if any), and the appropriate input control.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The constructed setting row element.
     */
    function buildSettingRow(setting) {
        const row = document.createElement('div');
        row.className = 'setting-row';
        row.dataset.key = setting.key;
        row.dataset.label = (setting.label || '').toLowerCase();
        row.dataset.desc = (setting.description || '').toLowerCase();

        // Info column
        const info = document.createElement('div');
        info.className = 'setting-info';
        const label = document.createElement('span');
        label.className = 'setting-label';
        label.textContent = setting.label;
        info.appendChild(label);
        // Config key only surfaces in dev mode — irrelevant noise otherwise.
        if (state.data && state.data.dev_mode) {
            const key = document.createElement('span');
            key.className = 'setting-key';
            key.textContent = setting.key;
            info.appendChild(key);
        }
        if (setting.description) {
            const desc = document.createElement('div');
            desc.className = 'setting-description';
            desc.innerHTML = setting.description;
            info.appendChild(desc);
        }
        row.appendChild(info);

        // Control column
        const control = document.createElement('div');
        control.className = 'setting-control';
        control.appendChild(buildControl(setting));
        row.appendChild(control);

        return row;
    }

    /**
     * Builds the appropriate input control for a setting based on its type.
     * 
     * Routes to the appropriate builder function based on the setting's
     * declared type (boolean, select, int, float, chips, wishlist, password).
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The constructed input control element.
     */
    function buildControl(setting) {
        switch (setting.type) {
            case 'boolean':
                return buildToggle(setting);
            case 'select':
                return buildSelect(setting);
            case 'int':
            case 'float':
                return buildNumberInput(setting);
            case 'chips':
                return buildChipGroup(setting);
            case 'wishlist':
                return buildWishlistControl(setting);
            case 'password':
                return buildPasswordInput(setting);
            default:
                return buildTextInput(setting);
        }
    }

    /**
     * Builds a password input field for sensitive credential settings.
     * 
     * Creates a masked input field with placeholder text and change tracking.
     * Clears validation errors when the user begins typing.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The password input element.
     */
    function buildPasswordInput(setting) {
        const input = document.createElement('input');
        input.type = 'password';
        input.className = 'setting-input';
        input.value = currentValue(setting) ?? '';
        input.placeholder = 'Enter your API key';
        input.addEventListener('input', () => {
            setEdit(setting.key, input.value);
            updateDirtyUI();
            markRowDirty(setting.key);
            // Clear validation error when user types
            clearValidationErrors();
        });
        return input;
    }

    /**
     * Builds a wishlist control for managing catch wishlist Pokémon.
     * 
     * Creates a searchable input with dropdown suggestions and chip-based
     * display of currently selected Pokémon. Supports adding/removing
     * Pokémon via search or modal selection.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The wishlist control container.
     */
    function buildWishlistControl(setting) {
        if (setting.names) {
            state._wishlistNames = state._wishlistNames || {};
            for (const [id, name] of Object.entries(setting.names)) {
                state._wishlistNames[Number(id)] = name;
            }
        }

        const wrap = document.createElement('div');
        wrap.className = 'setting-wishlist';

        // Search row: text input + buttons + dropdown
        const searchRow = document.createElement('div');
        searchRow.className = 'wishlist-search-row';

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'setting-input';
        input.placeholder = 'Search Pokémon name…';

        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'wishlist-add-btn';
        addBtn.textContent = 'Add';
        addBtn.title = 'Add top search result';

        const chooseBtn = document.createElement('button');
        chooseBtn.type = 'button';
        chooseBtn.className = 'wishlist-choose-btn';
        chooseBtn.textContent = 'Choose from Caught…';
        chooseBtn.title = 'Open dialog to select from caught Pokémon';

        const dropdown = document.createElement('div');
        dropdown.className = 'wishlist-dropdown hidden';

        searchRow.append(input, addBtn, dropdown);

        // Action row for Choose from Caught button
        const actionRow = document.createElement('div');
        actionRow.className = 'wishlist-action-row';
        actionRow.append(chooseBtn);

        // Chip list showing currently added Pokémon
        const chipList = document.createElement('div');
        chipList.className = 'wishlist-chips';

        let activeModalBackdrop = null;
        let activeModalGrid = null;
        let caughtPokemon = [];

        function getIds() {
            return (currentValue(setting) || []).map(Number);
        }

        function togglePokemon(id, name) {
            const current = getIds();
            let next;
            if (current.includes(id)) {
                next = current.filter((v) => v !== id);
            } else {
                (state._wishlistNames = state._wishlistNames || {})[id] = name;
                next = [...current, id];
            }
            setEdit(setting.key, next);
            renderChips();
            updateDirtyUI();
            markRowDirty(setting.key);

            if (activeModalGrid) {
                renderModalGrid(activeModalGrid);
            }
        }

        function addPokemon(id, name) {
            const current = getIds();
            if (!current.includes(id)) {
                (state._wishlistNames = state._wishlistNames || {})[id] = name;
                const next = [...current, id];
                setEdit(setting.key, next);
                renderChips();
                updateDirtyUI();
                markRowDirty(setting.key);

                if (activeModalGrid) {
                    renderModalGrid(activeModalGrid);
                }
            }
            input.value = '';
            dropdown.innerHTML = '';
            dropdown.classList.add('hidden');
        }

        function renderChips() {
            chipList.innerHTML = '';
            const ids = getIds();
            if (ids.length === 0) {
                const empty = document.createElement('span');
                empty.className = 'wishlist-empty';
                empty.textContent = 'No Pokémon added yet.';
                chipList.appendChild(empty);
                return;
            }
            ids.forEach((id) => {
                const chip = document.createElement('span');
                chip.className = 'wishlist-chip';
                const cached = (state._wishlistNames = state._wishlistNames || {});
                chip.textContent = cached[id] ? `${cached[id]} (#${id})` : `#${id}`;
                const x = document.createElement('button');
                x.type = 'button';
                x.textContent = '×';
                x.className = 'wishlist-chip-remove';
                x.title = 'Remove';
                x.addEventListener('click', () => {
                    const next = getIds().filter((v) => v !== id);
                    setEdit(setting.key, next);
                    renderChips();
                    updateDirtyUI();
                    markRowDirty(setting.key);

                    if (activeModalGrid) {
                        renderModalGrid(activeModalGrid);
                    }
                });
                chip.appendChild(x);
                chipList.appendChild(chip);
            });
        }
        renderChips();

        let lastResults = [];
        let searchTimer = null;

        const performSearch = () => {
            const q = input.value.trim();
            if (q.length < 2) {
                dropdown.classList.add('hidden');
                dropdown.innerHTML = '';
                lastResults = [];
                return;
            }
            if (!bridge || !bridge.searchPokemon) return;
            bridge.searchPokemon(q, (res) => {
                dropdown.innerHTML = '';
                lastResults = (res && res.results) || [];
                if (lastResults.length === 0) {
                    dropdown.classList.add('hidden');
                    return;
                }
                dropdown.classList.remove('hidden');
                lastResults.forEach((r) => {
                    const opt = document.createElement('div');
                    opt.className = 'wishlist-option';
                    opt.textContent = `${r.name} (#${r.id})`;
                    opt.addEventListener('click', () => addPokemon(r.id, r.name));
                    dropdown.appendChild(opt);
                });
            });
        };

        const handleAddTopResult = () => {
            const q = input.value.trim();
            if (q.length < 2) return;

            if (lastResults.length > 0 && lastResults[0].name.toLowerCase().includes(q.toLowerCase())) {
                addPokemon(lastResults[0].id, lastResults[0].name);
                return;
            }

            if (!bridge || !bridge.searchPokemon) return;
            clearTimeout(searchTimer);
            bridge.searchPokemon(q, (res) => {
                const results = (res && res.results) || [];
                if (results.length > 0) {
                    addPokemon(results[0].id, results[0].name);
                }
            });
        };

        input.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(performSearch, 250);
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleAddTopResult();
            }
        });

        addBtn.addEventListener('click', () => {
            handleAddTopResult();
        });

        // Modal Suggestions logic
        function renderModalGrid(grid) {
            grid.innerHTML = '';
            if (caughtPokemon.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'wishlist-suggestions-empty';
                empty.textContent = 'No caught Pokémon found.';
                grid.appendChild(empty);
                return;
            }
            const activeIds = getIds();
            caughtPokemon.forEach((p) => {
                const isInWishlist = activeIds.includes(p.id);

                const card = document.createElement('div');
                card.className = `wishlist-suggestion-card${isInWishlist ? ' in-wishlist' : ''}`;
                card.title = `${p.name} (#${p.id})`;

                const img = document.createElement('img');
                img.className = 'wishlist-suggestion-sprite';
                img.src = `../user_files/sprites/front_default/${p.id}.png`;
                img.onerror = () => {
                    img.src = '../user_files/sprites/front_default/0.png';
                };

                const nameLbl = document.createElement('div');
                nameLbl.className = 'wishlist-suggestion-name';
                nameLbl.textContent = p.name;

                card.append(img, nameLbl);
                card.addEventListener('click', () => togglePokemon(p.id, p.name));
                grid.appendChild(card);
            });
        }

        function openCaughtModal() {
            if (activeModalBackdrop) return;

            const backdrop = document.createElement('div');
            backdrop.className = 'wishlist-modal-backdrop';
            activeModalBackdrop = backdrop;

            const content = document.createElement('div');
            content.className = 'wishlist-modal-content';

            const header = document.createElement('div');
            header.className = 'wishlist-modal-header';

            const title = document.createElement('h3');
            title.className = 'wishlist-modal-title';
            title.textContent = 'Select from Caught Pokémon';

            const closeBtn = document.createElement('button');
            closeBtn.type = 'button';
            closeBtn.className = 'wishlist-modal-close';
            closeBtn.textContent = '×';
            closeBtn.title = 'Close';

            const close = () => {
                backdrop.remove();
                activeModalBackdrop = null;
                activeModalGrid = null;
                document.removeEventListener('keydown', handleEsc);
            };

            const handleEsc = (e) => {
                if (e.key === 'Escape') {
                    close();
                }
            };

            closeBtn.addEventListener('click', close);
            backdrop.addEventListener('click', (e) => {
                if (e.target === backdrop) close();
            });
            document.addEventListener('keydown', handleEsc);

            header.append(title, closeBtn);

            const body = document.createElement('div');
            body.className = 'wishlist-modal-body';

            const subtitle = document.createElement('div');
            subtitle.className = 'wishlist-modal-subtitle';
            subtitle.textContent = 'Toggle Pokémon below to add them to your Always-Catch Wishlist:';

            const grid = document.createElement('div');
            grid.className = 'wishlist-suggestions-grid';
            activeModalGrid = grid;

            body.append(subtitle, grid);
            content.append(header, body);
            backdrop.append(content);
            document.body.appendChild(backdrop);

            if (bridge && bridge.getCaughtPokemon) {
                bridge.getCaughtPokemon((res) => {
                    caughtPokemon = (res && res.results) || [];
                    renderModalGrid(grid);
                });
            } else {
                renderModalGrid(grid);
            }
        }

        chooseBtn.addEventListener('click', openCaughtModal);

        // Close dropdown on outside click
        document.addEventListener('click', (e) => {
            if (!searchRow.contains(e.target)) {
                dropdown.classList.add('hidden');
            }
        });

        wrap.append(searchRow, actionRow, chipList);
        return wrap;
    }

    /**
     * Builds a chip group for multi-select boolean toggles.
     * 
     * Creates a group of toggle buttons where each chip represents a
     * boolean setting. Active chips are visually highlighted and tracked
     * in the edit state.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The chip group container.
     */
    function buildChipGroup(setting) {
        const wrap = document.createElement('div');
        wrap.className = 'setting-chips';
        (setting.chips || []).forEach((chip) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'setting-chip';
            btn.dataset.key = chip.key;
            const current = (Object.prototype.hasOwnProperty.call(state.edits, chip.key))
                ? state.edits[chip.key] : chip.value;
            if (current) btn.classList.add('active');
            btn.textContent = chip.label;
            btn.addEventListener('click', () => {
                const now = !btn.classList.contains('active');
                // Manually track edits since chips don't go through setEdit's
                // findSetting() lookup (each chip's "setting" is a sub-entry).
                if (chip.value === now) {
                    delete state.edits[chip.key];
                } else {
                    state.edits[chip.key] = now;
                }
                btn.classList.toggle('active', now);
                updateDirtyUI();
                markChipRowDirty(setting.key);
            });
            wrap.appendChild(btn);
        });
        return wrap;
    }

    /**
     * Marks a chip row as dirty if any of its chips have pending edits.
     * 
     * @param {string} rowKey - The data-key attribute of the row to check.
     */
    function markChipRowDirty(rowKey) {
        const row = document.querySelector(`.setting-row[data-key="${cssEscape(rowKey)}"]`);
        if (!row) return;
        const anyDirty = Array.from(row.querySelectorAll('.setting-chip')).some((c) =>
            Object.prototype.hasOwnProperty.call(state.edits, c.dataset.key));
        row.classList.toggle('dirty', anyDirty);
    }

    /**
     * Builds a boolean toggle control with Enabled/Disabled buttons.
     * 
     * Creates a segmented control with two buttons (Enabled/Disabled)
     * that act as a binary toggle. Clicking either button updates the
     * setting value and triggers a full re-render.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The toggle control container.
     */
    function buildToggle(setting) {
        const wrap = document.createElement('div');
        wrap.className = 'setting-toggle';
        const current = currentValue(setting);
        ['Enabled', 'Disabled'].forEach((label, i) => {
            const isOn = (i === 0 && current) || (i === 1 && !current);
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'setting-toggle-option' + (isOn ? ' active' : '') + (i === 1 ? ' off' : '');
            btn.textContent = label;
            btn.addEventListener('click', () => {
                setEdit(setting.key, i === 0);
                renderAll();  // refresh control + dirty pip
            });
            wrap.appendChild(btn);
        });
        return wrap;
    }

    /**
     * Builds a select dropdown for enumerated settings.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The select element.
     */
    function buildSelect(setting) {
        const sel = document.createElement('select');
        sel.className = 'setting-select';
        const current = currentValue(setting);
        (setting.options || []).forEach((opt, i) => {
            const o = document.createElement('option');
            o.value = (opt.value === null || opt.value === undefined) ? '__null__' : String(opt.value);
            o.textContent = opt.label;
            if (opt.value === current) o.selected = true;
            sel.appendChild(o);
        });
        sel.addEventListener('change', () => {
            const raw = sel.value === '__null__' ? null : sel.value;
            setEdit(setting.key, raw);
            renderAll();
        });
        return sel;
    }

    /**
     * Builds a numeric input with support for hyphenated ranges.
     * 
     * Allows numeric values and special range syntax (e.g., "1-3") for
     * settings like cards_per_round.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The numeric input element.
     */
    function buildNumberInput(setting) {
        const input = document.createElement('input');
        input.type = 'text';  // text so range strings like "1-3" work too
        input.className = 'setting-input numeric';
        input.value = currentValue(setting);
        input.addEventListener('input', () => {
            const raw = input.value.trim();
            // Allow ranges for cards_per_round; otherwise coerce to number.
            if (setting.key === 'battle.cards_per_round' && raw.includes('-')) {
                setEdit(setting.key, raw);
            } else if (raw === '') {
                setEdit(setting.key, raw);
            } else {
                const n = Number(raw);
                setEdit(setting.key, Number.isFinite(n) ? n : raw);
            }
            updateDirtyUI();
            markRowDirty(setting.key);
            // Clear validation error when user types
            clearValidationErrors();
        });
        return input;
    }

    /**
     * Builds a standard text input for string settings.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {HTMLElement} The text input element.
     */
    function buildTextInput(setting) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'setting-input';
        input.value = currentValue(setting) ?? '';
        input.addEventListener('input', () => {
            setEdit(setting.key, input.value);
            updateDirtyUI();
            markRowDirty(setting.key);
            // Clear validation error when user types
            clearValidationErrors();
        });
        return input;
    }

    /**
     * Gets the current value of a setting, checking pending edits first.
     * 
     * @param {Object} setting - The setting configuration object.
     * @returns {*} The current value from edits or the original value.
     */
    function currentValue(setting) {
        if (Object.prototype.hasOwnProperty.call(state.edits, setting.key)) {
            return state.edits[setting.key];
        }
        return setting.value;
    }

    // ---------- Edit tracking ----------
    /**
     * Sets a pending edit for a setting key.
     * 
     * Updates the edits state, tracking changes before they are saved.
     * If the new value equals the original, the edit is removed.
     * 
     * @param {string} key - The setting key.
     * @param {*} value - The new value.
     */
    function setEdit(key, value) {
        const original = findSetting(key);
        if (!original) return;
        if (deepEqual(original.value, value)) {
            delete state.edits[key];
        } else {
            state.edits[key] = value;
        }
    }

    /**
     * Finds a setting definition by its key.
     * 
     * Searches through all groups and subgroups for a setting with the
     * matching key.
     * 
     * @param {string} key - The setting key to find.
     * @returns {Object|null} The setting object or null if not found.
     */
    function findSetting(key) {
        for (const g of (state.data.groups || [])) {
            for (const s of (g.settings || [])) {
                if (s.key === key) return s;
            }
            for (const sub of (g.subgroups || [])) {
                for (const s of (sub.settings || [])) {
                    if (s.key === key) return s;
                }
            }
        }
        return null;
    }

    /**
     * Performs a deep equality comparison between two values.
     * 
     * Uses JSON serialization for reliable deep comparison of objects
     * and arrays.
     * 
     * @param {*} a - First value to compare.
     * @param {*} b - Second value to compare.
     * @returns {boolean} True if the values are deeply equal.
     */
    function deepEqual(a, b) {
        if (a === b) return true;
        if (typeof a !== typeof b) return false;
        return JSON.stringify(a) === JSON.stringify(b);
    }

    /**
     * Marks a specific setting row as dirty (has unsaved changes).
     * 
     * @param {string} key - The setting key to mark dirty.
     */
    function markRowDirty(key) {
        document.querySelectorAll('.setting-row').forEach((row) => {
            if (row.dataset.key !== key) return;
            row.classList.toggle('dirty',
                Object.prototype.hasOwnProperty.call(state.edits, key));
        });
    }

    /**
     * Updates the UI elements that display dirty state status.
     * 
     * Updates the save button state, dirty pill count, and status text
     * based on the number of pending edits.
     */
    function updateDirtyUI() {
        const n = Object.keys(state.edits).length;
        const dirty = n > 0;
        const saveBtn = document.getElementById('save-btn');
        const discardBtn = document.getElementById('discard-btn');
        const pill = document.getElementById('dirty-count');
        const status = document.getElementById('save-status');
        saveBtn.disabled = !dirty;
        saveBtn.classList.toggle('dirty', dirty);
        discardBtn.classList.toggle('hidden', !dirty);
        if (dirty) {
            pill.classList.remove('hidden');
            pill.textContent = n;
            status.textContent = n + ' unsaved';
            status.style.color = 'var(--accent-gold)';
        } else {
            pill.classList.add('hidden');
            status.textContent = 'All saved';
            status.style.color = 'var(--accent-green)';
        }
        // Sync the row-level dirty markers (in case render rebuilt them).
        // Chip rows key their edits under each chip's sub-key, not the row's
        // group key, so fall back to scanning the row's chips for those.
        document.querySelectorAll('.setting-row').forEach((row) => {
            let rowDirty = Object.prototype.hasOwnProperty.call(state.edits, row.dataset.key);
            if (!rowDirty) {
                rowDirty = Array.from(row.querySelectorAll('.setting-chip')).some((c) =>
                    Object.prototype.hasOwnProperty.call(state.edits, c.dataset.key));
            }
            row.classList.toggle('dirty', rowDirty);
        });
    }

    // ---------- Search ----------
    /**
     * Filters settings based on the current search query.
     * 
     * Hides groups, subgroups, and individual rows that don't match the
     * search term. Shows the empty state message when no matches are found.
     */
    function applySearchFilter() {
        const q = state.search;
        const empty = document.getElementById('settings-empty');
        let visible = 0;
        document.querySelectorAll('.settings-group').forEach((groupEl) => {
            let groupVisible = 0;
            groupEl.querySelectorAll('.setting-row').forEach((row) => {
                const hit = !q || row.dataset.label.includes(q) || row.dataset.desc.includes(q);
                row.classList.toggle('hidden', !hit);
                if (hit) groupVisible++;
            });
            // Hide empty subgroups
            groupEl.querySelectorAll('.settings-subgroup').forEach((sub) => {
                const anyHit = Array.from(sub.querySelectorAll('.setting-row'))
                    .some((r) => !r.classList.contains('hidden'));
                sub.classList.toggle('hidden', !anyHit);
            });
            groupEl.classList.toggle('hidden', groupVisible === 0);
            visible += groupVisible;
        });
        empty.classList.toggle('hidden', visible > 0);
    }

    // ---------- Save ----------
    /**
     * Saves all pending setting edits to the Python backend.
     * 
     * Performs validation on leaderboard settings before saving.
     * If leaderboard sync is enabled, ensures username and API key
     * are both filled. Shows validation errors if fields are missing.
     * 
     * @param {Function} onDone - Optional callback invoked after save completion.
     */
    function onSave(onDone) {
        // onDone (optional) fires only after a successful save/no-op, so
        // callers like the unsaved-changes guard can chain navigation.
        const done = typeof onDone === 'function' ? onDone : null;
        if (!bridge) { if (done) done(); return; }
        
        // ============================================================
        // LEADERBOARD VALIDATION
        // ============================================================
        const setting = findSetting('misc.leaderboard');
        const isEnabled = setting ? currentValue(setting) : false;
        
        if (isEnabled) {
            // Use currentValue() to get values from settings state
            // This is robust and independent of DOM structure
            const usernameSetting = findSetting('leaderboard.username');
            const apiKeySetting = findSetting('leaderboard.api_key');
            const username = usernameSetting ? (currentValue(usernameSetting) || '').trim() : '';
            const apiKey = apiKeySetting ? (currentValue(apiKeySetting) || '').trim() : '';
            
            // Still need DOM references for highlighting validation errors
            const usernameRow = document.querySelector('.setting-row[data-key="leaderboard.username"]');
            const apiKeyRow = document.querySelector('.setting-row[data-key="leaderboard.api_key"]');
            
            if (!username || !apiKey) {
                const missing = [];
                if (!username) missing.push('• Username');
                if (!apiKey) missing.push('• API Key');
                
                const msg = '⚠️ Leaderboard Sync is enabled but the following fields are empty:\n\n' + 
                           missing.join('\n') + 
                           '\n\nPlease fill in all fields before saving.';
                showToast(msg, true);
                
                if (!username && usernameRow) {
                    usernameRow.classList.add('validation-error');
                    const input = usernameRow.querySelector('.setting-input');
                    if (input) input.classList.add('validation-error');
                }
                if (!apiKey && apiKeyRow) {
                    apiKeyRow.classList.add('validation-error');
                    const input = apiKeyRow.querySelector('.setting-input');
                    if (input) input.classList.add('validation-error');
                }
                
                if (done) done();
                return;
            }
        }
        // ============================================================
        // END LEADERBOARD VALIDATION
        // ============================================================
        
        const payload = {...state.edits};
        if (Object.keys(payload).length === 0) { if (done) done(); return; }
        // Stringify so the bridge sees a stable `str` parameter — see the
        // SettingsBridge.saveSettings comment for why we avoid passing the
        // dict directly through QVariant.
        bridge.saveSettings(JSON.stringify(payload), function (result) {
            if (!result) { if (done) done(); return; }
            if (result.ok) {
                const adj = (result.adjustments || []).join('\n');
                const msg = adj ? result.message + '\n' + adj : result.message;
                showToast(msg);
                // Re-fetch fresh data so the UI reflects what was actually
                // persisted (including any clamping).
                bridge.getSettings(function (fresh) {
                    if (fresh) window.initializeSettings(fresh);
                    if (done) done();
                });
            } else {
                showToast(result.message || 'Save failed', true);
                // Save failed — do not run onDone; keep the user on the page.
            }
        });
    }

    // Unsaved-changes guard for navigation away from Settings. Returns true
    // when it takes ownership of navigation (a modal is shown); the shared
    // nav switcher then waits for the user's choice before leaving.
    function guardUnsavedNavigation(proceed) {
        if (Object.keys(state.edits).length === 0) return false;
        showUnsavedGuardModal(proceed);
        return true;
    }

    /**
     * Displays a modal dialog for unsaved changes when navigating away.
     * 
     * Shows a confirmation dialog with options to stay, discard changes,
     * or save and leave.
     * 
     * @param {Function} proceed - Callback to continue navigation after
     *                             the user makes a choice.
     */
    function showUnsavedGuardModal(proceed) {
        if (unsavedGuardOpen) return;
        unsavedGuardOpen = true;
        const n = Object.keys(state.edits).length;

        const backdrop = document.createElement('div');
        backdrop.className = 'wishlist-modal-backdrop';

        const content = document.createElement('div');
        content.className = 'wishlist-modal-content';
        content.style.maxWidth = '420px';

        const header = document.createElement('div');
        header.className = 'wishlist-modal-header';
        const title = document.createElement('h3');
        title.className = 'wishlist-modal-title';
        title.textContent = 'Unsaved Changes';
        header.appendChild(title);

        const body = document.createElement('div');
        body.className = 'wishlist-modal-body';
        const msg = document.createElement('div');
        msg.className = 'wishlist-modal-subtitle';
        msg.textContent = 'You have ' + n + ' unsaved ' +
            (n === 1 ? 'change' : 'changes') +
            '. Save before leaving, discard them, or stay on this page?';
        body.appendChild(msg);

        const close = () => {
            backdrop.remove();
            unsavedGuardOpen = false;
            document.removeEventListener('keydown', handleEsc);
        };
        const handleEsc = (e) => { if (e.key === 'Escape') close(); };

        const actions = document.createElement('div');
        actions.style.display = 'flex';
        actions.style.gap = '10px';
        actions.style.justifyContent = 'flex-end';

        const stayBtn = document.createElement('button');
        stayBtn.type = 'button';
        stayBtn.className = 'settings-discard-btn';
        stayBtn.textContent = 'Stay';
        stayBtn.addEventListener('click', close);

        const discardBtn = document.createElement('button');
        discardBtn.type = 'button';
        discardBtn.className = 'settings-discard-btn';
        discardBtn.textContent = 'Discard & Leave';
        discardBtn.addEventListener('click', () => {
            state.edits = {};
            renderAll();
            close();
            proceed();
        });

        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'settings-discard-btn';
        saveBtn.style.color = 'var(--accent-green)';
        saveBtn.style.borderColor = 'var(--accent-green)';
        saveBtn.textContent = 'Save & Leave';
        saveBtn.addEventListener('click', () => {
            close();
            onSave(proceed);
        });

        actions.append(stayBtn, discardBtn, saveBtn);
        body.appendChild(actions);
        content.append(header, body);
        backdrop.append(content);
        // Clicking the dim backdrop keeps the user on the page (safe default).
        backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
        document.addEventListener('keydown', handleEsc);
        document.body.appendChild(backdrop);
    }

    /**
     * Discards all pending edits and resets the UI.
     */
    function onDiscard() {
        if (Object.keys(state.edits).length === 0) return;
        state.edits = {};
        renderAll();
        showToast('Changes discarded');
    }

    /**
     * Displays a toast notification with optional error styling.
     * 
     * @param {string} message - The message to display.
     * @param {boolean} isError - Whether the message is an error.
     */
    function showToast(message, isError) {
        if (!message) return;
        const toast = document.getElementById('toast');
        toast.textContent = message;
        toast.classList.toggle('error', !!isError);
        toast.classList.add('visible');
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => toast.classList.remove('visible'), 2800);
    }

    // ---------- Scroll spy / jumps ----------
    //
    // Approach (after several iterations):
    // 1. Clicking a section in the sidebar SETS the active state directly
    //    and suppresses the scroll-spy for 1.5s. This means the user's
    //    explicit choice wins even when the scroll lands at max (e.g.
    //    Generations near the bottom of a short page).
    // 2. Otherwise the spy runs on scroll. The active section is the LAST
    //    one whose top has crossed a reading line near the top of the
    //    scroller — the classic "where am I" check.
    // 3. When the user is at max scroll AND later sections couldn't reach
    //    the line, fall back to whichever section's top is closest to
    //    (just below) the line. That's how Study/Generations become
    //    select-able via manual scrolling on short content.

    let suppressSpyUntil = 0;
    const READING_LINE_PX = 80;

    /**
     * Updates the active button in the sidebar navigation.
     * 
     * Highlights the group jump button corresponding to the currently
     * visible settings group.
     * 
     * @param {string} label - The label of the active group.
     */
    function setActiveButton(label) {
        if (label === state.activeGroup) return;
        state.activeGroup = label;
        document.querySelectorAll('.group-jump').forEach((b) => {
            b.classList.toggle('active', b.dataset.group === label);
        });
    }

    /**
     * Scrolls the settings content to a specific group section.
     * 
     * Smoothly scrolls to the target group, highlights it with a flash
     * animation, and updates the active sidebar button.
     * 
     * @param {string} label - The label of the group to scroll to.
     */
    function scrollToGroup(label) {
        const el = document.querySelector(`.settings-group[data-group="${cssEscape(label)}"]`);
        const scroller = document.querySelector('.content-scroll');
        if (!el || !scroller) return;
        const elTop = el.getBoundingClientRect().top;
        const scrollerTop = scroller.getBoundingClientRect().top;
        const offsetWithinScroller = elTop - scrollerTop + scroller.scrollTop;
        // 32px breathing room so the section header sits comfortably
        // below the top-bar instead of butting up against it.
        const target = Math.max(0, offsetWithinScroller - 32);
        scroller.scrollTo({top: target, behavior: 'smooth'});
        flashSection(el);
        // Win against the spy: the user's click wins even if the resulting
        // scroll position would normally activate a different section.
        setActiveButton(label);
        suppressSpyUntil = Date.now() + 1500;
    }

    /**
     * Triggers a brief highlight flash on a section element.
     * 
     * @param {HTMLElement} el - The section element to flash.
     */
    function flashSection(el) {
        // Brief tint so the user sees the jump landed on the right section.
        // Re-trigger the animation if the class is already present
        // (consecutive clicks on the same nav item).
        el.classList.remove('flash-highlight');
        // Force reflow so the animation restarts on re-add.
        void el.offsetWidth;
        el.classList.add('flash-highlight');
    }

    /**
     * Escapes double quotes in a string for CSS selector usage.
     * 
     * @param {string} s - The string to escape.
     * @returns {string} The escaped string.
     */
    function cssEscape(s) {
        return s.replace(/"/g, '\\"');
    }

    /**
     * Determines which settings group is currently visible and updates
     * the sidebar navigation accordingly. Handles edge cases when at
     * max scroll.
     */
    function updateActiveSection() {
        if (Date.now() < suppressSpyUntil) return;
        const scroller = document.querySelector('.content-scroll');
        if (!scroller) return;
        const sRect = scroller.getBoundingClientRect();
        const sections = Array.from(document.querySelectorAll('.settings-group'))
            .filter((g) => !g.classList.contains('hidden'));
        if (!sections.length) return;

        const readingLine = sRect.top + READING_LINE_PX;

        // Primary: last section whose top has crossed the reading line.
        let active = null;
        sections.forEach((g) => {
            if (g.getBoundingClientRect().top <= readingLine + 4) active = g;
        });

        // Fallback when we're at max scroll: short later sections that
        // couldn't physically be scrolled past the line still need to be
        // select-able. Pick whichever section's top is closest to (just
        // below) the line.
        const atMax = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 4;
        if (atMax) {
            let nearestBelow = null;
            let nearestDist = Infinity;
            sections.forEach((g) => {
                const dist = g.getBoundingClientRect().top - readingLine;
                if (dist > 0 && dist < nearestDist) {
                    nearestDist = dist;
                    nearestBelow = g;
                }
            });
            if (nearestBelow) active = nearestBelow;
        }

        if (!active) active = sections[0];
        setActiveButton(active.dataset.group);
    }

    // ---------- UI plumbing ----------
    /**
     * Binds UI event handlers to DOM elements.
     * 
     * Sets up search input, save button, discard button, scroll spy,
     * and keyboard shortcuts.
     */
    function bindUI() {
        const search = document.getElementById('settings-search');
        const clear = document.getElementById('clear-search');
        search.addEventListener('input', () => {
            state.search = search.value.trim().toLowerCase();
            clear.classList.toggle('hidden', !state.search);
            applySearchFilter();
        });
        clear.addEventListener('click', () => {
            search.value = '';
            state.search = '';
            clear.classList.add('hidden');
            applySearchFilter();
        });

        document.getElementById('save-btn').addEventListener('click', () => onSave());
        document.getElementById('discard-btn').addEventListener('click', onDiscard);
        // Let the shared nav switcher consult us before leaving with unsaved edits.
        window.navBeforeLeave = guardUnsavedNavigation;

        const scroller = document.querySelector('.content-scroll');
        if (scroller) scroller.addEventListener('scroll', updateActiveSection);

        document.addEventListener('keydown', (e) => {
            if (e.key === '/' && document.activeElement !== search) {
                e.preventDefault();
                search.focus();
            } else if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                onSave();
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        bindUI();
        // Dropdown nav: wire from the shared switcher once the channel resolves
        // so it has the live NavBridge. See ankimon_items_web/nav-switcher.js.
        initChannel(() => {
            if (window.wireNavSwitcher) window.wireNavSwitcher(nav);
        });
    });
})();
