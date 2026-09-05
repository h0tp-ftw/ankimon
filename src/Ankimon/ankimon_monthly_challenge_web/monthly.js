// Monthly Challenge screen — unified Ankimon shell.
// Talks to Python via QWebChannel (window.monthly) for data and actions.

(function () {
    'use strict';

    let bridge = null;
    let nav = null;
    let pendingAction = null;
    let latest = null;
    let dataLoaded = false;
    let allChallenges = [];
    let currentMonth = '';
    let currentMonId = null;
    let currentStatus = 0;
    let currentMonData = null;
    let showSprites = true;

    // Type color mapping
    const TYPE_COLORS = {
        normal: '#A8A77A', fire: '#EE8130', water: '#6390F0',
        electric: '#F7D02C', grass: '#7AC74C', ice: '#96D9D6',
        fighting: '#C22E28', poison: '#A33EA1', ground: '#E2BF65',
        flying: '#A98FF3', psychic: '#F95587', bug: '#A6B91A',
        rock: '#B6A136', ghost: '#735797', dragon: '#6F35FC',
        dark: '#705746', steel: '#B7B7CE', fairy: '#D685AD',
        stellar: '#40C0E0', unknown: '#68A090'
    };

    function getTypeColor(type) {
        // Coerce type to string and handle various input types safely
        if (type === null || type === undefined) {
            return '#888888';
        }
        const typeStr = String(type).toLowerCase();
        return TYPE_COLORS[typeStr] || '#888888';
    }

    function initChannel(callback) {
        if (typeof qt === 'undefined' || !qt.webChannelTransport) {
            console.warn('qt.webChannelTransport unavailable — standalone mode');
            callback(null);
            return;
        }
        new QWebChannel(qt.webChannelTransport, function (channel) {
            bridge = channel.objects && channel.objects.monthly;
            nav = channel.objects && channel.objects.nav;
            window.bridge = bridge;
            window.nav = nav;
            callback(bridge);
        });
    }

    function statusLabel(value) {
        if (Number(value) === 1) return ['Accepted', 'accepted'];
        if (Number(value) === 2) return ['Rejected', 'rejected'];
        return ['Unknown', 'unknown'];
    }

    function escapeText(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function render(data) {
        latest = data || {};
        
        // Check if this is a loading state
        if (data && data.loading === true) {
            // Show skeleton - the HTML already has it, but we need to make sure it's visible
            const container = document.getElementById('monthly-content');
            container.innerHTML = `
                <div class="skeleton skeleton-hero" style="min-height: 400px; border-radius: 16px;"></div>
            `;
            return;
        }
        
        if (!latest.ok) {
            const container = document.getElementById('monthly-content');
            const errorMessage = latest.message || 'Monthly Challenge is unavailable.';
            // Use textContent to avoid XSS
            container.innerHTML = `
                <div class="monthly-error">
                    <div class="monthly-error-icon">⚠️</div>
                    <div class="monthly-error-text"></div>
                </div>
            `;
            const errorTextEl = container.querySelector('.monthly-error-text');
            if (errorTextEl) {
                errorTextEl.textContent = errorMessage;
            }
            showToast(errorMessage, true);
            // Clear any loading state on error path
            removeLoadingStates();
            return;
        }

        dataLoaded = true;
        allChallenges = latest.challenges || [];
        currentMonth = latest.current_month || '';
        currentMonId = latest.current_individual_id || null;
        currentStatus = latest.current_status || 0;
        showSprites = latest.show_sprites !== undefined ? latest.show_sprites : true;

        // Find current month's data for the sidebar box
        currentMonData = allChallenges.find(c => c.is_current) || null;

        // Update current month box (left sidebar - shows user's current Mon)
        updateCurrentMonBox(currentMonData);

        // Update stats summary (left sidebar - shows user's current Mon stats)
        updateStatsSummary(currentMonData);

        // Build the challenge list (right side - challenge history)
        const container = document.getElementById('monthly-content');
        container.innerHTML = '';
        container.appendChild(buildChallengeList(allChallenges));
        
        // Remove any loading states after render completes
        removeLoadingStates();
    }

    function updateCurrentMonBox(data) {
        const img = document.getElementById('current-mon-img');
        const container = document.getElementById('current-mon-sprite-container');
        const typeBadge = document.getElementById('current-mon-types');
        const levelBadge = document.getElementById('current-mon-level');
        const defeatedBadge = document.getElementById('current-mon-defeated');
        
        if (data) {
            // Check if the Pokémon is in the user's collection
            const inCollection = data.in_collection || false;
            
            // Get types - safely handle non-string, non-array values
            let types = ['Normal'];
            if (inCollection && data.collection_types) {
                if (Array.isArray(data.collection_types)) {
                    types = data.collection_types;
                } else if (typeof data.collection_types === 'string') {
                    types = [data.collection_types];
                }
            } else if (data.type) {
                if (Array.isArray(data.type)) {
                    types = data.type;
                } else if (typeof data.type === 'string') {
                    types = [data.type];
                }
            }
            
            // Update type badge
            if (typeBadge) {
                if (types && types.length > 0) {
                    const typeStr = types.join('/');
                    typeBadge.textContent = typeStr;
                    // Safely get color - coerce first type to string
                    const firstType = types[0];
                    const color = getTypeColor(firstType);
                    typeBadge.style.backgroundColor = color;
                    typeBadge.style.color = '#ffffff';
                } else {
                    typeBadge.textContent = '—';
                    typeBadge.style.backgroundColor = '#888888';
                    typeBadge.style.color = '#ffffff';
                }
            }
            
            // Update level badge
            if (levelBadge) {
                if (inCollection && data.collection_level) {
                    levelBadge.textContent = 'Lv.' + data.collection_level;
                } else {
                    levelBadge.textContent = 'Lv.—';
                }
            }
            
            // Update defeated badge
            if (defeatedBadge) {
                if (inCollection && data.collection_defeated !== undefined) {
                    defeatedBadge.textContent = 'Df. ' + data.collection_defeated;
                } else {
                    defeatedBadge.textContent = 'Df. —';
                }
            }
            
            // Update sprite - LEFT SIDE uses user's current Mon (evolved if applicable)
            if (showSprites) {
                container.classList.remove('sprites-hidden');
                img.style.display = 'block';
                
                let spriteSrc = null;
                // Use collection sprite (evolved) if available, otherwise base
                if (inCollection && data.collection_sprite_gif) {
                    spriteSrc = data.collection_sprite_gif;
                } else if (data.sprite_gif) {
                    spriteSrc = data.sprite_gif;
                } else if (data.sprite) {
                    spriteSrc = data.sprite;
                }
                
                if (spriteSrc) {
                    img.src = spriteSrc;
                } else {
                    img.src = '../user_files/sprites/front_default/0.png';
                }
                img.alt = data.name || 'Pokémon';
                img.onerror = function() {
                    this.src = '../user_files/sprites/front_default/0.png';
                };
            } else {
                container.classList.add('sprites-hidden');
                img.style.display = 'none';
            }
        } else {
            // No data - show dashes
            if (typeBadge) {
                typeBadge.textContent = '—';
                typeBadge.style.backgroundColor = '#888888';
                typeBadge.style.color = '#ffffff';
            }
            if (levelBadge) {
                levelBadge.textContent = 'Lv.—';
            }
            if (defeatedBadge) {
                defeatedBadge.textContent = 'Df. —';
            }
            img.src = '../user_files/sprites/front_default/0.png';
            if (!showSprites) {
                container.classList.add('sprites-hidden');
                img.style.display = 'none';
            } else {
                container.classList.remove('sprites-hidden');
                img.style.display = 'block';
            }
        }
    }

    function updateStatsSummary(data) {
        const container = document.getElementById('stats-summary-content');
        
        if (!data) {
            container.innerHTML = '<div class="stats-summary-empty">No stats available</div>';
            return;
        }

        // Use collection stats if available (evolved form), otherwise use base stats
        let stats;
        
        if (data.in_collection && data.collection_stats) {
            stats = data.collection_stats;
        } else {
            stats = data.stats || {};
        }

        const statKeys = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
        const statLabels = {
            'hp': 'HP',
            'atk': 'Atk',
            'def': 'Def',
            'spa': 'SpA',
            'spd': 'SpD',
            'spe': 'Spe'
        };

        // Coerce all values to safe numbers to prevent XSS
        const values = statKeys.map(key => {
            const val = stats[key];
            return typeof val === 'number' && !isNaN(val) ? val : 0;
        });
        const maxVal = Math.max(...values, 1);

        // Clear container
        container.innerHTML = '';

        statKeys.forEach((key, index) => {
            const val = values[index];
            const pct = Math.max(0, Math.min(100, (val / maxVal) * 100));
            
            const row = document.createElement('div');
            row.className = 'stats-row';
            
            const label = document.createElement('span');
            label.className = 'stats-label';
            label.textContent = statLabels[key];
            row.appendChild(label);
            
            const barBg = document.createElement('div');
            barBg.className = 'stats-bar-bg';
            
            const barFill = document.createElement('div');
            barFill.className = 'stats-bar-fill';
            barFill.style.width = pct + '%';
            barBg.appendChild(barFill);
            row.appendChild(barBg);
            
            const valueSpan = document.createElement('span');
            valueSpan.className = 'stats-value';
            valueSpan.textContent = val;
            row.appendChild(valueSpan);
            
            container.appendChild(row);
        });
    }

    function buildChallengeList(challenges) {
        const wrapper = document.createElement('div');
        wrapper.className = 'monthly-list';

        // Parse current month properly
        const currentDate = new Date(currentMonth);
        // If currentMonth is "September 2026", this will parse correctly
        
        const filteredChallenges = challenges.filter(c => {
            const challengeDate = new Date(c.month);
            // If c.month is like "September 2026", this works
            // Fallback: if invalid, check string comparison
            if (isNaN(challengeDate.getTime())) {
                // Fallback: compare month strings alphabetically
                return c.month <= currentMonth;
            }
            return challengeDate <= currentDate;
        });

        // Sort by month (newest first - reverse chronological)
        const sortedChallenges = [...filteredChallenges].sort((a, b) => {
            const dateA = new Date(a.month);
            const dateB = new Date(b.month);
            // If either date is invalid, fall back to string comparison
            if (isNaN(dateA.getTime()) || isNaN(dateB.getTime())) {
                return a.month.localeCompare(b.month);
            }
            return dateB - dateA;
        });

        if (sortedChallenges.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'monthly-empty';
            empty.textContent = 'No monthly challenges available yet. Check back next month!';
            wrapper.appendChild(empty);
            return wrapper;
        }

        const show = showSprites;

        // Build each challenge entry
        sortedChallenges.forEach((challenge, index) => {
            const entry = document.createElement('div');
            entry.className = 'monthly-entry';
            if (challenge.is_current) {
                entry.classList.add('is-current');
            }

            // Left: Sprite - ALWAYS use base sprite from challenge data (static)
            const spriteBox = document.createElement('div');
            spriteBox.className = 'monthly-entry-sprite';
            
            if (show) {
                const img = document.createElement('img');
                let spriteSrc = null;
                if (challenge.sprite_gif) {
                    spriteSrc = challenge.sprite_gif;
                } else if (challenge.sprite) {
                    spriteSrc = challenge.sprite;
                }
                
                if (spriteSrc) {
                    img.src = spriteSrc;
                } else {
                    img.src = '../user_files/sprites/front_default/0.png';
                }
                img.alt = challenge.name || 'Pokémon';
                img.onerror = function() {
                    this.src = '../user_files/sprites/front_default/0.png';
                };
                spriteBox.appendChild(img);
            } else {
                spriteBox.classList.add('sprites-hidden');
            }
            
            entry.appendChild(spriteBox);

            // Right: Info
            const infoBox = document.createElement('div');
            infoBox.className = 'monthly-entry-info';

            // Month/Year
            const monthLabel = document.createElement('div');
            monthLabel.className = 'monthly-entry-month';
            if (challenge.is_current) {
                monthLabel.textContent = challenge.month || 'Current Month';
                monthLabel.classList.add('current');
            } else {
                monthLabel.textContent = challenge.month || 'Unknown';
            }
            infoBox.appendChild(monthLabel);

            // Pokémon name - ALWAYS use base name from challenge JSON
            let displayName = challenge.name || 'Unknown Pokémon';
            
            // If evolved, add arrow indicator to the right
            if (challenge.is_current && challenge.has_evolved) {
                displayName += ' ⬆';
            }
            
            const nameLabel = document.createElement('div');
            nameLabel.className = 'monthly-entry-name';
            nameLabel.textContent = displayName;
            infoBox.appendChild(nameLabel);

            // Description
            if (challenge.description) {
                const descLabel = document.createElement('div');
                descLabel.className = 'monthly-entry-desc';
                descLabel.textContent = challenge.description;
                infoBox.appendChild(descLabel);
            }

            // Result / Status
            const resultBox = document.createElement('div');
            resultBox.className = 'monthly-entry-result';

            const isCurrent = challenge.is_current;

            if (isCurrent) {
                // STATUS for current month
                const status = document.createElement('span');
                status.className = 'monthly-entry-status';
                let statusText = '';
                if (challenge.status === 1) {
                    statusText = 'Status: Accepted';
                    status.classList.add('accepted');
                } else if (challenge.status === 2) {
                    statusText = 'Status: Rejected';
                    status.classList.add('rejected');
                } else {
                    statusText = 'Status: Unknown';
                    status.classList.add('unknown');
                }
                status.textContent = statusText;
                resultBox.appendChild(status);

                // Action buttons for current month
                // If status is 0 (Unknown): Show both Accept and Reject buttons
                // If status is 1 (Accepted): Show only Remove button
                // If status is 2 (Rejected): Show only Accept button
                const actionBtns = document.createElement('div');
                actionBtns.className = 'monthly-entry-actions';

                if (challenge.status === 0) {
                    // Unknown - show both Accept and Reject
                    const acceptBtn = document.createElement('button');
                    acceptBtn.className = 'button button-primary';
                    acceptBtn.textContent = 'Accept';
                    acceptBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        openConfirm('receive');
                    });
                    actionBtns.appendChild(acceptBtn);

                    const rejectBtn = document.createElement('button');
                    rejectBtn.className = 'button button-danger';
                    rejectBtn.textContent = 'Reject';
                    rejectBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        openConfirm('reject');
                    });
                    actionBtns.appendChild(rejectBtn);
                } else if (challenge.status === 1) {
                    // Accepted - show only Remove button
                    const removeBtn = document.createElement('button');
                    removeBtn.className = 'button button-danger';
                    removeBtn.textContent = 'Remove';
                    removeBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        openConfirm('reject');
                    });
                    actionBtns.appendChild(removeBtn);
                } else if (challenge.status === 2) {
                    // Rejected - show only Accept button
                    const acceptBtn = document.createElement('button');
                    acceptBtn.className = 'button button-primary';
                    acceptBtn.textContent = 'Accept';
                    acceptBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        openConfirm('receive');
                    });
                    actionBtns.appendChild(acceptBtn);
                }

                resultBox.appendChild(actionBtns);
            } else {
                // RESULT for past challenges
                const result = document.createElement('span');
                result.className = 'monthly-entry-result-label';
                const resultText = challenge.result || 'unknown';
                let displayText = '';
                if (resultText === 'success') {
                    displayText = 'Result: Succeeded!';
                    result.classList.add('success');
                } else if (resultText === 'next_time') {
                    displayText = 'Result: Next time\'s a charm!';
                    result.classList.add('next-time');
                } else if (resultText === 'pending_accepted') {
                    displayText = 'Pending: Accepted';
                    result.classList.add('pending');
                } else if (resultText === 'pending_rejected') {
                    displayText = 'Pending: Rejected';
                    result.classList.add('pending');
                } else if (resultText === 'accepted') {
                    displayText = 'Result: Accepted';
                    result.classList.add('accepted');
                } else if (resultText === 'rejected') {
                    displayText = 'Result: Not Joined';
                    result.classList.add('rejected');
                } else {
                    displayText = 'Result: Unknown';
                    result.classList.add('unknown');
                }
                result.textContent = displayText;
                resultBox.appendChild(result);
            }

            infoBox.appendChild(resultBox);
            entry.appendChild(infoBox);

            wrapper.appendChild(entry);

            // Divider (except after last)
            if (index < sortedChallenges.length - 1) {
                const divider = document.createElement('div');
                divider.className = 'monthly-divider';
                wrapper.appendChild(divider);
            }
        });

        return wrapper;
    }

    // --- Loading State Management ---
    let loadingButton = null;

    function setLoadingState(button) {
        if (!button) return;
        loadingButton = button;
        button._originalText = button.textContent;
        button.textContent = 'Processing...';
        button.disabled = true;
        button.classList.add('loading');
    }

    function removeLoadingStates() {
        if (loadingButton) {
            loadingButton.textContent = loadingButton._originalText || loadingButton.textContent;
            loadingButton.disabled = false;
            loadingButton.classList.remove('loading');
            loadingButton = null;
        }
    }

    // --- Modal Functions ---

    function getPokemonDisplayInfo() {
        let pokemonName = 'this Mon';
        let pokemonLevel = '';
        
        if (currentMonData) {
            // Check if the Pokémon is in the user's collection
            const inCollection = currentMonData.in_collection || false;
            
            if (inCollection) {
                // Use collection data (what the user actually has)
                pokemonName = currentMonData.collection_name || currentMonData.name || 'this Mon';
                pokemonLevel = currentMonData.collection_level || '';
            } else {
                // Fall back to template data if not in collection
                pokemonName = currentMonData.name || 'this Mon';
                pokemonLevel = currentMonData.level || '';
            }
        }
        
        return { name: pokemonName, level: pokemonLevel };
    }

    function openConfirm(action) {
        const info = getPokemonDisplayInfo();
        const levelText = info.level ? ` Lvl. ${info.level}` : '';
        
        if (action === 'reject') {
            // SHOW REMOVE MODAL
            const removeTitle = document.getElementById('remove-title');
            const removeCopy = document.getElementById('remove-copy');
            
            removeTitle.textContent = `Remove this month's Pokémon?`;
            // Use textContent for the display name to prevent XSS
            const displayName = info.name || 'this Mon';
            // Clear and rebuild with safe DOM nodes
            removeCopy.innerHTML = '';
            removeCopy.appendChild(document.createTextNode(`This removes ${displayName}${levelText} from your collection. `));
            const warningSpan = document.createElement('strong');
            warningSpan.textContent = 'All progress will be reset';
            removeCopy.appendChild(warningSpan);
            const suffixSpan = document.createTextNode(` — if you receive it again, its level and number of Pokémon defeated will return to their defaults.`);
            removeCopy.appendChild(suffixSpan);
            
            document.getElementById('remove-modal').classList.remove('hidden');
            return;
        }

        // SHOW RECEIVE MODAL (action === 'receive')
        pendingAction = action;
        document.getElementById('confirm-mark').textContent = '?';
        document.getElementById('confirm-title').textContent = 'Receive this month\'s Pokémon?';
        const displayName = info.name || 'this Mon';
        document.getElementById('confirm-copy').textContent = `Add ${displayName}${levelText} to your collection?`;
        
        const confirmBtn = document.getElementById('confirm-action');
        confirmBtn.textContent = 'Accept Challenge';
        confirmBtn.className = 'button button-primary';
        document.getElementById('confirm-modal').classList.remove('hidden');
    }

    function closeConfirm() {
        document.getElementById('confirm-modal').classList.add('hidden');
        pendingAction = null;
    }

    function closeRemoveModal() {
        document.getElementById('remove-modal').classList.add('hidden');
    }

    function runAction() {
        if (!bridge || !pendingAction) return;
        const action = pendingAction;
        
        // Set loading state on the confirm button
        const confirmBtn = document.getElementById('confirm-action');
        setLoadingState(confirmBtn);
        
        closeConfirm();

        const callback = function(result) {
            if (result && result.ok) {
                // Clear loading state before refresh so the button isn't stuck if the refresh fails
                removeLoadingStates();
                bridge.getMonthlyChallenge(render);
                showToast('Pokémon received!');
            } else {
                showToast((result && result.message) || 'The action could not be completed.', true);
                removeLoadingStates();
            }
        };

        if (action === 'receive') {
            bridge.receiveMon(callback);
        }
    }

    function confirmRemove() {
        if (!bridge) return;
        
        // Set loading state on the remove confirm button
        const removeBtn = document.getElementById('remove-confirm');
        setLoadingState(removeBtn);
        
        closeRemoveModal();
        bridge.removeMon(function(result) {
            if (result && result.ok) {
                // Clear loading state before refresh so the button isn't stuck if the refresh fails
                removeLoadingStates();
                bridge.getMonthlyChallenge(render);
                showToast('Monthly challenge removed.');
            } else {
                showToast((result && result.message) || 'Could not remove.', true);
                removeLoadingStates();
            }
        });
    }

    function showToast(text, isError) {
        if (!text) return;
        const toast = document.getElementById('toast');
        // Use textContent to prevent XSS
        toast.textContent = text;
        toast.classList.toggle('error', !!isError);
        toast.classList.add('visible');
        clearTimeout(toast._timer);
        toast._timer = setTimeout(function() {
            toast.classList.remove('visible');
        }, 2800);
    }

    // Live refresh support
    window.liveRefreshMonthly = function(data) {
        if (!data) return;
        render(data);
    };

    // Initialize from Python
    window.initializeMonthlyChallenge = function(data) {
        render(data);
    };

    // ----- UI Binding -----
    document.addEventListener('DOMContentLoaded', function() {
        document.getElementById('confirm-cancel').addEventListener('click', closeConfirm);
        document.getElementById('confirm-action').addEventListener('click', runAction);

        document.getElementById('remove-cancel').addEventListener('click', closeRemoveModal);
        document.getElementById('remove-confirm').addEventListener('click', confirmRemove);

        document.getElementById('confirm-modal').addEventListener('click', function(e) {
            if (e.target.classList.contains('modal-backdrop')) closeConfirm();
        });
        document.getElementById('remove-modal').addEventListener('click', function(e) {
            if (e.target.classList.contains('modal-backdrop')) closeRemoveModal();
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const confirmModal = document.getElementById('confirm-modal');
                const removeModal = document.getElementById('remove-modal');
                if (!confirmModal.classList.contains('hidden')) {
                    closeConfirm();
                } else if (!removeModal.classList.contains('hidden')) {
                    closeRemoveModal();
                }
            }
        });

        initChannel(function() {
            if (window.wireNavSwitcher) {
                window.wireNavSwitcher(nav);
            }
            if (bridge && bridge.getMonthlyChallenge) {
                bridge.getMonthlyChallenge(render);
            }
        });
    });
})();
