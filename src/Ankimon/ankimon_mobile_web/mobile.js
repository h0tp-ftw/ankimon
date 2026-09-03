// State machine: "loading" → "empty" | "pending"

// Escape HTML metacharacters before interpolating user-controlled strings
// (Pokemon nicknames) into innerHTML — prevents stored-XSS from malicious names.
function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c];
  });
}

function showConfirm(message, onConfirm, onCancel = null) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.style.zIndex = "1000";

  const modal = document.createElement("div");
  modal.className = "modal";
  modal.style.maxWidth = "400px";

  const head = document.createElement("div");
  head.className = "modal-head";
  const title = document.createElement("h3");
  title.textContent = "Confirmation";
  head.appendChild(title);

  const body = document.createElement("div");
  body.className = "modal-body";
  body.style.padding = "18px";
  body.style.fontSize = "0.95rem";
  body.style.lineHeight = "1.4";
  body.style.color = "var(--text-main)";
  body.innerHTML = message;

  const foot = document.createElement("div");
  foot.className = "modal-foot";
  foot.style.gap = "10px";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-secondary";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = function () {
    document.body.removeChild(overlay);
    if (onCancel) onCancel();
  };

  const confirmBtn = document.createElement("button");
  confirmBtn.className = "btn btn-primary";
  confirmBtn.textContent = "Confirm";
  confirmBtn.onclick = function () {
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
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.style.zIndex = "1000";

  const modal = document.createElement("div");
  modal.className = "modal";
  modal.style.maxWidth = "400px";

  const head = document.createElement("div");
  head.className = "modal-head";
  const title = document.createElement("h3");
  title.textContent = "Notification";
  head.appendChild(title);

  const body = document.createElement("div");
  body.className = "modal-body";
  body.style.padding = "18px";
  body.style.fontSize = "0.95rem";
  body.style.lineHeight = "1.4";
  body.style.color = "var(--text-main)";
  body.textContent = message;

  const foot = document.createElement("div");
  foot.className = "modal-foot";

  const okBtn = document.createElement("button");
  okBtn.className = "btn btn-primary";
  okBtn.textContent = "OK";
  okBtn.onclick = function () {
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

let mobileBridge = null;
let nav = null;
let _currentMobileStatus = null;
let _replayCompanionOverride = null;

new QWebChannel(qt.webChannelTransport, function (channel) {
  window.parentChannel = channel; // Expose to iframe picker
  mobileBridge = channel.objects.mobile;
  nav = channel.objects && channel.objects.nav;
  window.nav = nav;
  loadStatus();
  if (window.wireNavSwitcher) {
    window.wireNavSwitcher(nav);
  }
});

function loadStatus() {
  if (!mobileBridge) return;
  mobileBridge.getMobileStatus(function (status) {
    render(status);
  });
}

function refreshStatus() {
  const icons = document.querySelectorAll(".spin-icon");
  icons.forEach((icon) => icon.classList.add("spinning"));
  if (!mobileBridge) {
    setTimeout(
      () => icons.forEach((icon) => icon.classList.remove("spinning")),
      500,
    );
    return;
  }
  mobileBridge.triggerAnkiSync(function (res) {
    mobileBridge.getMobileStatus(function (status) {
      render(status);
      setTimeout(
        () => icons.forEach((icon) => icon.classList.remove("spinning")),
        600,
      );
    });
  });
}

window.initializeMobile = function (status) {
  render(status);
};

window.liveRefreshMobile = function (status) {
  render(status);
};

window.updateMobileEstimates = function (estimates) {
  if (_currentMobileStatus) {
    _currentMobileStatus.estimates = estimates;
    _currentMobileStatus.estimates_loading = false;
    _currentMobileStatus.battle_count = estimates.encounters;

    // Update top badge count display with correct battle count
    const countDisplay = document.getElementById("top-count-display");
    if (countDisplay && _currentMobileStatus.pending_count > 0) {
      countDisplay.textContent = `${_currentMobileStatus.pending_count} / ${_currentMobileStatus.cap || 10000}`;
      countDisplay.classList.remove("hidden");
    }
  }

  const estXp = document.getElementById("est-xp");
  if (estXp) estXp.textContent = `+${estimates.xp || 0}`;
  const estEncounters = document.getElementById("est-encounters");
  if (estEncounters) estEncounters.textContent = estimates.encounters || 0;
  const estCatches = document.getElementById("est-catches");
  if (estCatches) estCatches.textContent = estimates.catches || 0;
  const estCashEl = document.getElementById("est-cash");
  if (estCashEl) {
    estCashEl.textContent = `+${estimates.cash || 0}`;
  }

  const caughtListContainer = document.getElementById("caught-list-container");
  const caughtListEl = document.getElementById("caught-list");

  if (caughtListEl) {
    caughtListEl.innerHTML = "";
    if (caughtListContainer) {
      const existingNote =
        caughtListContainer.querySelector(".truncation-note");
      if (existingNote) {
        existingNote.remove();
      }
    }
    const caughtList = estimates.caught_list || [];
    if (caughtList.length > 0) {
      if (caughtListContainer) caughtListContainer.style.display = "block";
      caughtList.forEach((pkmn) => {
        const item = document.createElement("div");
        item.className = "caught-pokemon-item";
        const shinyIcon = pkmn.shiny
          ? '<span class="shiny-badge">✨</span> '
          : "";
        item.innerHTML = `
                    <span class="caught-pokemon-name">${shinyIcon}${escapeHtml(pkmn.name)}</span>
                    <span class="caught-pokemon-lvl">Lv.${pkmn.level}</span>
                `;
        caughtListEl.appendChild(item);
      });
    } else {
      if (caughtListContainer) caughtListContainer.style.display = "block";
      caughtListEl.innerHTML = `
                <div style="font-size: 12px; color: var(--text-muted); font-style: italic; padding: 4px 0;">
                    No Pokémon will be caught.
                </div>
            `;
    }

    if (estimates.is_truncated && caughtListContainer) {
      const noteEl = document.createElement("div");
      noteEl.className = "truncation-note";
      noteEl.innerHTML = `
  ⚠ Preview shows first ${estimates.simulated_reviews} of ${estimates.total_reviews} reviews.
  XP and encounter counts include estimates for the remainder.
            `.trim();
      caughtListContainer.appendChild(noteEl);
    }
  }

  const loaderEl = document.getElementById("estimates-loader");
  if (loaderEl) {
    loaderEl.classList.add("hidden");
  }
};

window.onResolveNextReady = function (result) {
  if (!_replayRunning) return;
  if (result.done) {
    _replayRunning = false;
    loadStatus();
    return;
  }
  if (result.error) {
    _replayRunning = false;
    showAlert("Replay error: " + result.error);
    loadStatus();
    return;
  }
  renderReplayBattle(result);
};

function render(status) {
  _currentMobileStatus = status;
  const loadingEl = document.getElementById("loading");
  if (loadingEl) {
    loadingEl.style.display = "none";
  }

  const countDisplay = document.getElementById("top-count-display");
  if (countDisplay) {
    if (status.pending_count > 0) {
      countDisplay.textContent = `${status.pending_count} / ${status.cap || 10000}`;
      countDisplay.classList.remove("hidden");
    } else {
      countDisplay.classList.add("hidden");
    }
  }

  if (status.error) {
    document.getElementById("app").innerHTML =
      `<p style="color:var(--accent-red); text-align:center; padding: 40px;">Error loading status: ${status.error}</p>`;
    return;
  }

  if (_replayRunning) {
    return;
  }

  if (status.pending_count === 0) {
    showState("empty");
  } else {
    showState("pending", status);
  }
}

function showState(name, data) {
  document
    .querySelectorAll(
      "#state-empty, #state-pending, #state-summary, #state-replay",
    )
    .forEach((el) => el.classList.remove("active"));
  const el = document.getElementById(`state-${name}`);
  if (el) {
    el.classList.add("active");
    if (name === "pending" && data) fillPending(data);
  }

  // Toggle in-replay class on body for conditional styling
  document.body.classList.toggle("in-replay", name === "replay");
}

function fillPending(data) {
  // Count / cap
  const countEl =
    document.getElementById("count-display") ||
    document.getElementById("top-count-display");
  if (countEl) {
    countEl.textContent = `${data.pending_count} / ${data.cap}`;
  }

  // Ease breakdown
  const eb = data.ease_breakdown || {};
  const easeAgain = document.getElementById("ease-again");
  if (easeAgain) easeAgain.textContent = eb["1"] || 0;
  const easeHard = document.getElementById("ease-hard");
  if (easeHard) easeHard.textContent = eb["2"] || 0;
  const easeGood = document.getElementById("ease-good");
  if (easeGood) easeGood.textContent = eb["3"] || 0;
  const easeEasy = document.getElementById("ease-easy");
  if (easeEasy) easeEasy.textContent = eb["4"] || 0;

  // Show/hide estimates loader
  const loaderEl = document.getElementById("estimates-loader");
  if (loaderEl) {
    if (data.estimates_loading) {
      loaderEl.classList.remove("hidden");
    } else {
      loaderEl.classList.add("hidden");
    }
  }

  // Auto-resolve preview
  const est = data.estimates || {};
  const estXp = document.getElementById("est-xp");
  if (estXp) estXp.textContent = `+${est.xp || 0}`;
  const estEncounters = document.getElementById("est-encounters");
  if (estEncounters) estEncounters.textContent = est.encounters || 0;
  const estCatches = document.getElementById("est-catches");
  if (estCatches) estCatches.textContent = est.catches || 0;
  const estCashEl = document.getElementById("est-cash");
  if (estCashEl) {
    estCashEl.textContent = `+${est.cash || 0}`;
  }

  const autoBattleStatus = document.getElementById("auto-battle-status");
  if (autoBattleStatus) {
    autoBattleStatus.textContent = data.auto_battle_mode || "OFF";
  }

  const rareCatchEl = document.getElementById("rare-catch-status");
  if (rareCatchEl) {
    rareCatchEl.textContent = data.rare_catch_active ? "ON" : "OFF";
  }

  // Populate caught list
  const caughtListContainer = document.getElementById("caught-list-container");
  const caughtListEl = document.getElementById("caught-list");

  if (caughtListEl) {
    caughtListEl.innerHTML = "";
    if (caughtListContainer) {
      const existingNote =
        caughtListContainer.querySelector(".truncation-note");
      if (existingNote) {
        existingNote.remove();
      }
    }
    const caughtList = est.caught_list || [];
    if (caughtList.length > 0) {
      if (caughtListContainer) caughtListContainer.style.display = "block";
      caughtList.forEach((pkmn) => {
        const item = document.createElement("div");
        item.className = "caught-pokemon-item";

        const shinyIcon = pkmn.shiny
          ? '<span class="shiny-badge">✨</span> '
          : "";

        item.innerHTML = `
                    <span class="caught-pokemon-name">${shinyIcon}${escapeHtml(pkmn.name)}</span>
                    <span class="caught-pokemon-lvl">Lv.${pkmn.level}</span>
                `;
        caughtListEl.appendChild(item);
      });
    } else {
      if (caughtListContainer) caughtListContainer.style.display = "block";
      caughtListEl.innerHTML = `
                <div style="font-size: 12px; color: var(--text-muted); font-style: italic; padding: 4px 0;">
                    No Pokémon will be caught.
                </div>
            `;
    }

    if (est.is_truncated && caughtListContainer) {
      const noteEl = document.createElement("div");
      noteEl.className = "truncation-note";
      noteEl.innerHTML = `
  ⚠ Preview shows first ${est.simulated_reviews} of ${est.total_reviews} reviews.
  XP and encounter counts include estimates for the remainder.
            `.trim();
      caughtListContainer.appendChild(noteEl);
    }
  }

  // Render Team Grid
  const teamGrid = document.getElementById("team-grid");
  if (teamGrid && data.team_status && data.team_status.team) {
    teamGrid.innerHTML = "";
    data.team_status.team.forEach((member) => {
      const card = document.createElement("div");
      card.className = "team-card" + (member.inactive ? " inactive" : "");
      card.setAttribute("data-id", member.individual_id);
      card.onclick = function () {
        toggleMobileCompanion(member.individual_id);
      };

      const memberName = escapeHtml(member.name);
      card.innerHTML = `
                <div class="team-sprite-wrap">
                    <img class="team-sprite" src="${escapeHtml(member.sprite_path)}" alt="${memberName}"
                         onerror="if (this.src.indexOf('_gif') !== -1) { this.src = this.src.replace('_gif', '').replace('.gif', '.png'); } else { this.onerror=null; this.src='../user_files/sprites/front_default/0.png'; }">
                </div>
                <div class="team-name">${memberName}</div>
                <div class="team-level">Lv.${member.level}</div>
            `;
      teamGrid.appendChild(card);
    });
  }
}

function toggleMobileCompanion(individualId) {
  if (!mobileBridge) return;

  // Optimistic UI: toggle the active/inactive state immediately for instant feedback
  const cards = document.querySelectorAll(".team-card");
  cards.forEach((card) => {
    if (card.getAttribute("data-id") === individualId) {
      card.classList.toggle("inactive");
    }
  });

  // Show the loader immediately
  const loaderEl = document.getElementById("estimates-loader");
  if (loaderEl) {
    loaderEl.classList.remove("hidden");
  }

  mobileBridge.toggleMobileCompanion(individualId, function (res) {
    if (res.success) {
      loadStatus();
    } else {
      showAlert("Error toggling companion: " + res.error);
    }
  });
}

function dismissAll() {
  if (!mobileBridge) return;
  showConfirm(
    "Dismiss all pending battles? This cannot be undone.",
    function () {
      mobileBridge.dismissAll(function (result) {
        if (result.success) {
          loadStatus(); // Re-render (will show State 1)
        } else {
          showAlert("Error: " + result.error);
        }
      });
    },
  );
}

function resolveAll() {
  if (!mobileBridge) return;
  showConfirm(
    'Resolve all pending battles? This will apply catches and XP to your active companion.<br/><br/><strong style="color: #ff5555; display: block; text-align: center;">⚠️ WARNING: Do not review cards on desktop while auto-resolve is active or you risk losing your progress!</strong>',
    function () {
      const totalReviews = _currentMobileStatus
        ? _currentMobileStatus.pending_count
        : 0;
      runChunkedResolve(totalReviews);
    },
  );
}

let _isResolvePaused = false;

function togglePauseResolve() {
  if (!mobileBridge) return;
  const btn = document.getElementById("resolve-pause-btn");
  if (_isResolvePaused) {
    mobileBridge.resumeBulkResolve(function () {
      _isResolvePaused = false;
      if (btn) btn.textContent = "Pause";
      const headEl = document.querySelector("#resolve-progress-modal h3");
      if (headEl) headEl.textContent = "Auto-Resolving Battles";
    });
  } else {
    mobileBridge.pauseBulkResolve(function () {
      _isResolvePaused = true;
      if (btn) btn.textContent = "Resume";
      const headEl = document.querySelector("#resolve-progress-modal h3");
      if (headEl) headEl.textContent = "Auto-Resolve Paused";
    });
  }
}

function stopResolve() {
  if (!mobileBridge) return;
  showConfirm(
    "Stop auto-resolving? Any reviews processed so far will be saved.",
    function () {
      mobileBridge.stopBulkResolve(function () {
        // Background thread will exit, poller will pick up the completion.
      });
    },
  );
}

function runChunkedResolve(totalReviews) {
  if (totalReviews <= 0) return;
  if (!mobileBridge) return;

  _isResolvePaused = false;
  const btn = document.getElementById("resolve-pause-btn");
  if (btn) btn.textContent = "Pause";
  const headEl = document.querySelector("#resolve-progress-modal h3");
  if (headEl) headEl.textContent = "Auto-Resolving Battles";

  const progressModal = document.getElementById("resolve-progress-modal");
  const textEl = document.getElementById("resolve-progress-text");
  const encountersEl = document.getElementById("resolve-progress-encounters");
  const barEl = document.getElementById("resolve-progress-bar-fill");

  if (progressModal) {
    progressModal.classList.remove("hidden");
  }

  function updateProgress(proc, total, encs) {
    if (textEl) {
      textEl.textContent = `Processed reviews: ${proc} / ${total} (${Math.round((proc * 100) / (total || 1))}%);`;
    }
    if (encountersEl) {
      encountersEl.textContent = `Encounters resolved: ${encs}`;
    }
    if (barEl) {
      barEl.style.width = `${Math.min(100, Math.round((proc * 100) / (total || 1)))}%`;
    }
  }

  updateProgress(0, totalReviews, 0);

  // Start background thread execution
  mobileBridge.startBulkResolve(function () {
    // Poll for progress updates
    let poller = setInterval(function () {
      mobileBridge.getBulkResolveProgress(function (progress) {
        updateProgress(progress.processed, progress.total, progress.resolved);

        // Update pause state dynamically
        const btnVal = document.getElementById("resolve-pause-btn");
        const headVal = document.querySelector("#resolve-progress-modal h3");
        if (progress.paused) {
          _isResolvePaused = true;
          if (btnVal) btnVal.textContent = "Resume";
          if (headVal) headVal.textContent = "Auto-Resolve Paused";
        } else {
          _isResolvePaused = false;
          if (btnVal) btnVal.textContent = "Pause";
          if (headVal) headVal.textContent = "Auto-Resolving Battles";
        }

        if (progress.done) {
          clearInterval(poller);
          if (progressModal) progressModal.classList.add("hidden");
          if (progress.error) {
            showAlert("Error in bulk resolve:\n" + progress.error);
          } else {
            showSummaryModal(progress);
          }
          loadStatus();
        }
      });
    }, 150);
  });
}

function showSummaryModal(result) {
  // Fill summary data in modal
  document.getElementById("modal-summary-xp").textContent =
    `+${result.xp_gained || 0}`;
  document.getElementById("modal-summary-encounters").textContent =
    result.resolved || 0;
  document.getElementById("modal-summary-catches").textContent =
    result.catches || 0;
  document.getElementById("modal-summary-cash").textContent =
    `+${result.cash_gained || 0}¥`;
  document.getElementById("modal-summary-trainer-xp").textContent =
    `+${result.trainer_xp_gained || 0}`;

  // Populate caught list
  const listEl = document.getElementById("modal-summary-caught-list");
  listEl.innerHTML = "";
  const caught = result.caught_list || [];
  if (caught.length === 0) {
    listEl.innerHTML =
      '<div style="color:var(--text-muted); font-size: 13px; text-align: center; padding: 12px 0;">No Pokémon caught.</div>';
  } else {
    caught.forEach((p) => {
      const item = document.createElement("div");
      item.className = "caught-pokemon-item";
      const shiny = p.shiny ? '<span class="shiny-badge">✨</span> ' : "";
      const tier =
        p.tier && p.tier !== "Normal"
          ? `<span class="tier-badge">${p.tier}</span>`
          : "";
      item.innerHTML = `
                <span class="caught-pokemon-name">${shiny}${escapeHtml(p.name)} ${tier}</span>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span class="caught-pokemon-lvl">Lv.${p.level}</span>
                    <span class="caught-pokemon-cp">CP ${p.cp || 10}</span>
                </div>
            `;
      listEl.appendChild(item);
    });
  }

  // Show modal overlay
  const modal = document.getElementById("summary-modal");
  if (modal) {
    modal.classList.remove("hidden");
  }
}

function closeSummaryModal() {
  const modal = document.getElementById("summary-modal");
  if (modal) {
    modal.classList.add("hidden");
  }
  loadStatus(); // Re-render: will show State 1 (no more pending battles)
}

// ===== Replay State =====

let _replayRunning = false;
let _replayTotal = 0;
let _replayResolved = 0;
let _currentReplayResult = null;
let _replayTimeouts = [];

function clearReplayTimeouts() {
  _replayTimeouts.forEach(clearTimeout);
  _replayTimeouts = [];
}

// A battle is lost when the companion faints while the enemy is still standing.
// Mirrors mobile_sync's companion_fainted (comp_hp_after <= 0) resolution.
function replayCompanionFainted(result) {
  const turns = (result && result.turns) || [];
  if (turns.length === 0) return false;
  const last = turns[turns.length - 1];
  return last.comp_hp_pct <= 0 && last.enemy_hp_pct > 0;
}

function startReplay() {
  _replayCompanionOverride = null;
  if (!mobileBridge) return;
  _replayRunning = true;
  clearReplayTimeouts();
  // Get total count from current pending status before starting
  mobileBridge.getMobileStatus(function (status) {
    _replayTotal = status.battle_count || 0;
    _replayResolved = 0;
    showState("replay");
    updateReplayCounter();
    // Kick off the first battle
    loadNextBattle();
  });
}

function loadNextBattle() {
  if (!mobileBridge || !_replayRunning) return;
  // Disable button while loading
  const btn = document.getElementById("replay-next-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Resolving…";
  }

  const companionOverrideId = _replayCompanionOverride || "";
  _replayCompanionOverride = null;

  mobileBridge.resolveNext(companionOverrideId, function (result) {
    if (result && result.loading) {
      return;
    }
    if (result.done) {
      // All done — show summary equivalent
      _replayRunning = false;
      loadStatus(); // will show State 1 (empty)
      return;
    }
    if (result.error) {
      _replayRunning = false;
      showAlert("Replay error: " + result.error);
      loadStatus();
      return;
    }
    renderReplayBattle(result);
  });
}

function renderReplayBattle(result) {
  _currentReplayResult = result;
  clearReplayTimeouts();

  // Hide controls initially during animation sequence
  const nextBtn = document.getElementById("replay-next-btn");
  if (nextBtn) {
    nextBtn.classList.add("hidden");
    nextBtn.disabled = true;
  }
  const choiceBox = document.getElementById("replay-choice-controls");
  if (choiceBox) {
    choiceBox.classList.add("hidden");
  }

  // Update counter
  updateReplayCounter();

  // Reset HP bars and colors
  const enemyHpBar = document.getElementById("replay-enemy-hp");
  const playerHpBar = document.getElementById("replay-player-hp");
  if (enemyHpBar) {
    enemyHpBar.style.width = "100%";
    enemyHpBar.className = "hp-bar-fill hp-green";
  }
  if (playerHpBar) {
    playerHpBar.style.width = "100%";
    playerHpBar.className = "hp-bar-fill player-hp hp-blue";
  }

  // Reset sprites and names
  const enemySprite = document.getElementById("replay-enemy-sprite");
  enemySprite.src =
    result.enemy_sprite ||
    `../user_files/sprites/front_default/${result.enemy_id}.png`;
  enemySprite.className = "battle-sprite enemy-battle-sprite"; // remove caught/fainted fade classes

  const playerSprite = document.getElementById("replay-player-sprite");
  if (playerSprite && result.companion_sprite) {
    playerSprite.src = result.companion_sprite;
    playerSprite.className = "battle-sprite player-battle-sprite"; // remove caught/fainted fade classes
  }

  const caughtIcon = document.getElementById("replay-enemy-caught-icon");
  if (caughtIcon) {
    if (result.enemy_caught) {
      caughtIcon.style.display = "inline-block";
    } else {
      caughtIcon.style.display = "none";
    }
  }
  document.getElementById("replay-enemy-name").textContent =
    result.enemy_name || "???";
  document.getElementById("replay-enemy-level").textContent =
    `Lv.${result.enemy_level}`;
  document.getElementById("replay-player-name").textContent =
    result.companion_name || "Companion";
  document.getElementById("replay-player-level").textContent =
    `Lv.${result.companion_level}`;

  // Render the compact selector
  renderReplayTeamSelector(result.companion_id);

  // Tier badge
  const tierEl = document.getElementById("replay-enemy-tier");
  if (tierEl) {
    tierEl.textContent =
      result.enemy_tier && result.enemy_tier !== "Normal"
        ? result.enemy_tier
        : "";
    tierEl.style.display =
      result.enemy_tier && result.enemy_tier !== "Normal" ? "inline" : "none";
  }

  // Reset cards entry animations
  const enemyCard = document.querySelector(".enemy-side");
  const playerCard = document.querySelector(".player-side");
  enemyCard.classList.remove("slide-in", "damaged");
  playerCard.classList.remove("slide-in", "attack-dash");

  // Trigger slide-in entry path animations
  void enemyCard.offsetWidth; // Reflow to restart animations
  enemyCard.classList.add("slide-in");
  playerCard.classList.add("slide-in");

  // Setup catch flash
  const catchFlash = document.getElementById("replay-catch-flash");
  if (catchFlash) {
    catchFlash.classList.add("hidden");
  }

  // Set initial narration
  const narrateEl = document.getElementById("narration-text");
  if (narrateEl) {
    narrateEl.innerHTML = `<span class="narrate-encounter">Wild <strong>${escapeHtml(result.enemy_name)}</strong> appeared!</span>`;
  }

  // Timeline sequence: animate turns list sequentially
  const turns = result.turns || [];

  function animateTurn(idx) {
    if (!_replayRunning) return;
    if (idx >= turns.length) {
      const catchBtn = document.getElementById("replay-catch-btn");
      const defeatBtn = document.getElementById("replay-defeat-btn");
      if (replayCompanionFainted(result)) {
        // Companion fainted while the enemy is still standing — the battle
        // was lost, so there is nothing to catch. Only allow acknowledging.
        if (choiceBox) {
          choiceBox.classList.remove("hidden");
          if (catchBtn) catchBtn.disabled = true;
          if (defeatBtn) {
            defeatBtn.disabled = false;
            defeatBtn.textContent = "Continue";
          }
        }
        if (narrateEl) {
          narrateEl.innerHTML = `<strong>${escapeHtml(result.companion_name)}</strong> fainted! Wild <strong>${escapeHtml(result.enemy_name)}</strong> won the battle.`;
        }
        return;
      }
      // Battle finished, show choice controls
      if (choiceBox) {
        choiceBox.classList.remove("hidden");
        if (catchBtn) catchBtn.disabled = false;
        if (defeatBtn) {
          defeatBtn.disabled = false;
          defeatBtn.textContent = "Defeat";
        }
      }
      if (narrateEl) {
        narrateEl.innerHTML = `Wild <strong>${escapeHtml(result.enemy_name)}</strong> is vulnerable! What will you do?`;
      }
      return;
    }

    const turn = turns[idx];

    // 1. Player Attack
    playerCard.classList.add("attack-dash");
    if (narrateEl) {
      narrateEl.innerHTML = `<strong>${escapeHtml(result.companion_name)}</strong> used <strong>${escapeHtml(turn.user_attack)}</strong>!`;
    }

    const tPlayer = setTimeout(() => {
      playerCard.classList.remove("attack-dash");

      // Shake enemy and deplete enemy HP
      enemyCard.classList.add("damaged");
      if (enemyHpBar) {
        enemyHpBar.style.width = `${turn.enemy_hp_pct}%`;
        if (turn.enemy_hp_pct < 20) {
          enemyHpBar.className = "hp-bar-fill hp-red";
        } else if (turn.enemy_hp_pct < 50) {
          enemyHpBar.className = "hp-bar-fill hp-yellow";
        } else {
          enemyHpBar.className = "hp-bar-fill hp-green";
        }
      }

      const tEnemyHurt = setTimeout(() => {
        enemyCard.classList.remove("damaged");

        // If enemy fainted, skip enemy turn and finish battle
        if (turn.enemy_hp_pct <= 0) {
          animateTurn(idx + 1);
          return;
        }

        // 2. Enemy Attack
        enemyCard.classList.add("attack-dash");
        if (narrateEl) {
          narrateEl.innerHTML = `Wild <strong>${escapeHtml(result.enemy_name)}</strong> used <strong>${escapeHtml(turn.enemy_attack)}</strong>!`;
        }

        const tEnemyAttack = setTimeout(() => {
          enemyCard.classList.remove("attack-dash");
          playerCard.classList.add("damaged");

          if (playerHpBar) {
            playerHpBar.style.width = `${turn.comp_hp_pct}%`;
            if (turn.comp_hp_pct < 20) {
              playerHpBar.className = "hp-bar-fill player-hp hp-red";
            } else if (turn.comp_hp_pct < 50) {
              playerHpBar.className = "hp-bar-fill player-hp hp-yellow";
            } else {
              playerHpBar.className = "hp-bar-fill player-hp hp-blue";
            }
          }

          const tPlayerHurt = setTimeout(() => {
            playerCard.classList.remove("damaged");
            animateTurn(idx + 1);
          }, 240);
          _replayTimeouts.push(tPlayerHurt);
        }, 240);
        _replayTimeouts.push(tEnemyAttack);
      }, 240);
      _replayTimeouts.push(tEnemyHurt);
    }, 240);
    _replayTimeouts.push(tPlayer);
  }

  const tStart = setTimeout(() => {
    animateTurn(0);
  }, 100);
  _replayTimeouts.push(tStart);
}

function chooseOutcome(choice) {
  if (!mobileBridge || !_replayRunning || !_currentReplayResult) return;

  const catchBtn = document.getElementById("replay-catch-btn");
  const defeatBtn = document.getElementById("replay-defeat-btn");
  if (catchBtn) catchBtn.disabled = true;
  if (defeatBtn) defeatBtn.disabled = true;

  mobileBridge.commitReplayOutcome(choice, function (res) {
    if (res.error) {
      showAlert("Outcome error: " + res.error);
      if (catchBtn) catchBtn.disabled = false;
      if (defeatBtn) defeatBtn.disabled = false;
      return;
    }

    const choiceBox = document.getElementById("replay-choice-controls");
    if (choiceBox) {
      choiceBox.classList.add("hidden");
    }

    _replayResolved++;
    if (res && res.success && typeof res.cash_gained !== "undefined") {
      _currentReplayResult.cash_gained = res.cash_gained;
    }
    animateResolution(res.outcome, res.xp_gained, res.remaining);
  });
}

function animateResolution(outcome, xp_gained, remaining) {
  if (!_replayRunning || !_currentReplayResult) return;

  const result = _currentReplayResult;
  const enemySprite = document.getElementById("replay-enemy-sprite");
  const catchFlash = document.getElementById("replay-catch-flash");
  const narrateEl = document.getElementById("narration-text");
  const nextBtn = document.getElementById("replay-next-btn");

  if (outcome === "caught") {
    if (narrateEl) {
      narrateEl.innerHTML = `You caught <strong>${escapeHtml(result.enemy_name)}</strong>! <span style="color: var(--accent-green)">Success!</span>`;
    }
    if (catchFlash) {
      catchFlash.classList.remove("hidden");
      setTimeout(() => catchFlash.classList.add("hidden"), 300);
    }
    enemySprite.classList.add("caught-fade");
  } else if (replayCompanionFainted(result)) {
    // The companion fainted — this encounter was lost, not a victory.
    if (narrateEl) {
      narrateEl.innerHTML = `<strong>${escapeHtml(result.companion_name)}</strong> fainted! Wild <strong>${escapeHtml(result.enemy_name)}</strong> won the battle.`;
    }
    const playerSprite = document.getElementById("replay-player-sprite");
    if (playerSprite) playerSprite.classList.add("fainted-fade");
  } else {
    if (narrateEl) {
      narrateEl.innerHTML =
        `<strong>${escapeHtml(result.enemy_name)}</strong> was defeated! <span style="color: var(--accent-blue)">+${xp_gained} XP</span>` +
        (result.cash_gained > 0
          ? ` <span style="color: var(--accent-gold)">+${result.cash_gained}¥</span>`
          : "");
    }
    enemySprite.classList.add("fainted-fade");
  }

  if (nextBtn) {
    if (remaining === 0) {
      nextBtn.classList.remove("hidden");
      nextBtn.textContent = "Finish ✓";
      nextBtn.onclick = function () {
        _replayRunning = false;
        loadStatus();
      };
      nextBtn.disabled = false;
    } else {
      // Auto-advance
      const tNext = setTimeout(nextReplayBattle, 1200);
      _replayTimeouts.push(tNext);
    }
  }
}

function nextReplayBattle() {
  loadNextBattle();
}

function pauseReplay() {
  _replayRunning = false;
  clearReplayTimeouts();
  // Return to pending state (or empty if all resolved)
  loadStatus();
}

function updateReplayCounter() {
  const el = document.getElementById("replay-battle-counter");
  if (el && _currentReplayResult) {
    const remainingBattles = Math.max(0, _replayTotal - _replayResolved);
    el.textContent = `(${remainingBattles} encounters remaining)`;
  }
}

function renderReplayTeamSelector(activeCompanionId) {
  const container = document.getElementById("replay-team-selector");
  if (!container) return;

  if (
    !_currentMobileStatus ||
    !_currentMobileStatus.team_status ||
    !_currentMobileStatus.team_status.team
  ) {
    container.innerHTML = "";
    return;
  }

  const team = _currentMobileStatus.team_status.team;

  // Only build team selector DOM cards once
  if (container.children.length === 0) {
    team.forEach((member) => {
      const card = document.createElement("div");
      card.className = "replay-member-card";
      card.dataset.individualId = member.individual_id;
      if (member.inactive) {
        card.classList.add("inactive");
      }

      const memberName = escapeHtml(member.name);
      card.innerHTML = `
                <div class="replay-member-sprite-wrap">
                    <img class="replay-member-sprite" src="${escapeHtml(member.sprite_path)}" alt="${memberName}"
                         onerror="if (this.src.indexOf('_gif') !== -1) { this.src = this.src.replace('_gif', '').replace('.gif', '.png'); } else { this.onerror=null; this.src='../user_files/sprites/front_default/0.png'; }">
                </div>
                <div class="replay-member-name">${memberName}</div>
            `;

      card.onclick = function () {
        if (card.classList.contains("active-selected")) return;

        // Re-simulate with new companion
        _replayCompanionOverride = member.individual_id;
        mobileBridge.resolveNext(member.individual_id, function (result) {
          if (result && result.loading) {
            return;
          }
          if (result.error) {
            showAlert("Replay error: " + result.error);
            return;
          }
          renderReplayBattle(result);
        });
      };

      container.appendChild(card);
    });
  }

  // Update active selections dynamically to avoid complete DOM re-creations
  const cards = container.querySelectorAll(".replay-member-card");
  cards.forEach((card) => {
    const indId = card.dataset.individualId;
    const isSelected = _replayCompanionOverride
      ? _replayCompanionOverride === indId
      : activeCompanionId === indId;

    if (isSelected) {
      card.classList.add("active-selected");
    } else {
      card.classList.remove("active-selected");
    }
  });
}

function formatTime(timestamp) {
  if (!timestamp) return "";
  const ts = Number(timestamp);
  if (isNaN(ts) || ts <= 0) return "";
  const diffMs = Date.now() - ts;
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);

  if (diffSecs < 60) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;

  const d = new Date(ts);
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function goToMobileReviews() {
  // Already on Mobile Reviews tab
}

function goToHistory() {
  if (nav && typeof nav.openHistory === "function") {
    nav.openHistory();
  }
}

// Global Keydown Listeners for shortcuts
document.addEventListener("keydown", function (event) {
  if (event.key === "5") {
    const catchBtn = document.getElementById("replay-catch-btn");
    if (catchBtn && !catchBtn.disabled && !catchBtn.closest(".hidden")) {
      chooseOutcome("catch");
    }
  } else if (event.key === "6") {
    const defeatBtn = document.getElementById("replay-defeat-btn");
    if (defeatBtn && !defeatBtn.disabled && !defeatBtn.closest(".hidden")) {
      chooseOutcome("defeat");
    }
  } else if (event.key === "9") {
    if (!_replayRunning) return;
    const container = document.getElementById("replay-team-selector");
    if (!container || container.children.length === 0) return;

    let activeIdx = -1;
    const cards = Array.from(container.children);

    for (let i = 0; i < cards.length; i++) {
      if (cards[i].classList.contains("active")) {
        activeIdx = i;
        break;
      }
    }

    if (activeIdx !== -1) {
      // Find next active member
      let nextIdx = (activeIdx + 1) % cards.length;
      // Prevent infinite loop if all others are inactive
      let found = false;
      for (let j = 0; j < cards.length; j++) {
        if (!cards[nextIdx].classList.contains("inactive")) {
          found = true;
          break;
        }
        nextIdx = (nextIdx + 1) % cards.length;
      }

      if (found && nextIdx !== activeIdx) {
        const nextId = cards[nextIdx].dataset.individualId;
        _replayCompanionOverride = nextId;
        loadNextBattle();
      }
    }
  }
});
