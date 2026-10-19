/**
 * Ankidex - Core Logic
 */

// State Management
const state = {
  allPokemon: [], // Raw data from pokedex.json
  speciesMap: {}, // actual_id -> species object
  collection: {
    owned: new Set(),
    shinies: new Set(),
    seen: new Set(),
    encounterable: new Set(),
  },
  collectionHash: "",
  ui: {
    currentScope: "national", // 'national' or number for generation
    activeFilter: "all", // 'all', 'caught', 'uncaught', 'shiny'
    selectedTypes: new Set(),
    searchQuery: "",
    sortMode: "id-asc",
    viewMode: "grid",
    spriteMode: "static", // 'static' or 'animated'
    selectedPokemonId: null,
    activeMode: "collection", // 'collection' or 'discovery'
    panelMode: "briefing", // 'details' or 'briefing'
    zoomLevel: 1, // Map zoom level
  },
  visibleIds: [], // Currently filtered and sorted IDs
  counts: {
    total: 0,
    caught: 0,
  },
  evolutionNote: "", // Note from Python
  abilities: {}, // ability_id -> description
  prerequisites: {}, // target_id -> [required_ids]
  regionalData: {
    boosts: {},
    forms: {},
  },
  fullSpeciesMap: {}, // actual_id -> all species objects
  mapDirty: false, // Flag to force map refresh
  isInitializing: false, // Guard against concurrent initializations
  mapRendered: false, // Track if map has been rendered at least once
};

// Configuration
const GEN_RANGES = [
  { id: 1, name: "Gen I", start: 1, end: 151 },
  { id: 2, name: "Gen II", start: 152, end: 251 },
  { id: 3, name: "Gen III", start: 252, end: 386 },
  { id: 4, name: "Gen IV", start: 387, end: 493 },
  { id: 5, name: "Gen V", start: 494, end: 649 },
  { id: 6, name: "Gen VI", start: 650, end: 721 },
  { id: 7, name: "Gen VII", start: 722, end: 809 },
  { id: 8, name: "Gen VIII", start: 810, end: 905 },
  { id: 9, name: "Gen IX", start: 906, end: 1025 },
];

const TYPES = [
  "Normal",
  "Fire",
  "Water",
  "Grass",
  "Electric",
  "Ice",
  "Fighting",
  "Poison",
  "Ground",
  "Flying",
  "Psychic",
  "Bug",
  "Rock",
  "Ghost",
  "Dragon",
  "Dark",
  "Steel",
  "Fairy",
];

// Entry point called from Python
window.initializeAnkidex = async function (data) {
  state.collection.owned = new Set(data.owned);
  state.collection.shinies = new Set(data.shinies);
  state.collection.seen = new Set(data.seen);
  state.collection.encounterable = new Set(data.encounterable || []);
  state.evolutionNote = data.evolutionNote || "";
  state.prerequisites = data.prerequisites || {};
  state.regionalData = data.regional_data || { boosts: {}, forms: {} };
  // Apply Preferences
  if (data.prefs) {
    state.ui.viewMode = data.prefs.viewMode || "grid";
    state.ui.sortMode = data.prefs.sortMode || "id-asc";
    state.ui.spriteMode = data.prefs.spriteMode || "static";
  }

  // Update collection hash to detect status changes
  const newHash = `${data.owned.length}-${data.seen.length}-${data.shinies.length}-${data.owned.slice(0, 10).join(",")}`;
  const collectionChanged = state.collectionHash !== newHash;
  state.collectionHash = newHash;

  // A concurrent push during the async first-load has now been applied above;
  // the in-flight initialization will render the latest collection when it
  // resumes, so skip re-entering the load/render path.
  if (state.isInitializing) {
    return;
  }
  state.isInitializing = true;

  if (state.allPokemon.length === 0) {
    await loadSpeciesData();
    await loadFlavorData();
    await loadAbilityData();
    setupEventListeners();
    applyInitialUIState();
  }

  renderSidebarFilters();
  updateGlobalProgress();
  applyFiltersAndRender(collectionChanged);
  state.isInitializing = false;
};

function formatLoreName(name) {
  if (!name || typeof name !== "string") return name;

  if (name.includes("-Mega-X"))
    return "Mega " + name.replace("-Mega-X", "") + " X";
  if (name.includes("-Mega-Y"))
    return "Mega " + name.replace("-Mega-Y", "") + " Y";
  if (name.includes("-Mega-Z"))
    return "Mega " + name.replace("-Mega-Z", "") + " Z";

  const replacements = {
    "-Mega": "Mega ",
    "-Gmax": "Gigantamax ",
    "-Alola": "Alolan ",
    "-Galar": "Galarian ",
    "-Paldea": "Paldean ",
    "-Hisui": "Hisuian ",
    "-Primal": "Primal ",
    "-Origin": "Origin ",
    "-Therian": "Therian ",
    "-Attack": "Attack ",
    "-Defense": "Defense ",
    "-Speed": "Speed ",
    "-Sky": "Sky ",
    "-Pirouette": "Pirouette ",
    "-Resolute": "Resolute ",
    "-Black": "Black ",
    "-White": "White ",
    "-Crowned": "Crowned ",
    "-Ice": "Ice Rider ",
    "-Shadow": "Shadow Rider ",
    "-Terastal": "Terastal ",
    "-Stellar": "Stellar ",
    "-Dusk-Mane": "Dusk Mane ",
    "-Dawn-Wings": "Dawn Wings ",
    "-Ultra": "Ultra ",
    "-Unbound": "Unbound ",
    "-Original": "Original Color ",
    "-Rapid-Strike": "Rapid Strike ",
    "-10%": "10% ",
    "-Complete": "Complete ",
  };

  for (const [suffix, prefix] of Object.entries(replacements)) {
    if (name.includes(suffix)) {
      return prefix + name.replace(suffix, "");
    }
  }

  return name;
}

function resolveActualId(id) {
  return id === 718 ? 10119 : id;
}

function getVisibilityState(id) {
  const actualId = resolveActualId(id);
  if (state.collection.owned.has(actualId)) return 2; // CAUGHT
  if (state.collection.seen.has(actualId)) return 1; // SEEN
  return 0; // NOT SEEN
}


async function loadAbilityData() {
  try {
    const response = await fetch("abilities.json");
    state.abilities = await response.json();
  } catch (err) {
    console.warn("Ability data not found.");
  }
}

async function loadFlavorData() {
  try {
    const response = await fetch("pokedex_flavor.json");
    state.flavor = await response.json();
  } catch (err) {
    console.warn("Flavor text not found, continuing without lore.");
  }
}

function applyInitialUIState() {
  // Sync View Mode
  const grid = document.getElementById("pokemon-grid");
  const viewBtn = document.querySelector(
    `.view-btn[data-view="${state.ui.viewMode}"]`,
  );
  if (viewBtn) {
    document
      .querySelectorAll(".view-btn")
      .forEach((b) => b.classList.remove("active"));
    viewBtn.classList.add("active");
    if (state.ui.viewMode === "list") grid.classList.add("list-view");
    else grid.classList.remove("list-view");
  }

  // Sync Sort Mode
  document.getElementById("sort-mode").value = state.ui.sortMode;

  // Sync Sprite Mode
  const spriteToggleText = document.getElementById("sprite-toggle-text");
  if (spriteToggleText) {
    spriteToggleText.textContent =
      state.ui.spriteMode === "static" ? "Static" : "Animated";
  }
}

window.getAnkidexState = function () {
  return {
    viewMode: state.ui.viewMode,
    sortMode: state.ui.sortMode,
    spriteMode: state.ui.spriteMode,
  };
};

async function loadSpeciesData() {
  try {
    const response = await fetch("../data_files/pokedex.json");
    const rawData = await response.json();

    // Normalize and store
    const rawList = Object.values(rawData).filter((p) => p.species_id);

    const uniqueActual = new Map();
    const fullMap = {};
    rawList.forEach((p) => {
      const id = p.actual_id;

      // Prioritize base forms in fullMap (prevents forms like Arceus-Water from overwriting the base entry)
      const existing = fullMap[id];
      const isBase =
        !p.forme ||
        ["base", "normal", "land", "altered", "50%"].includes(
          p.forme.toLowerCase(),
        );
      if (!existing || isBase) {
        fullMap[id] = p;
      }

      // Map species_id to handle legendaries like Zygarde (718)
      // CRITICAL: Only do this for base forms to avoid overwriting (e.g. Mewtwo Mega Y overwriting Mewtwo)
      if (p.species_id && isBase) {
        fullMap[p.species_id] = p;
      }

      const existingUnique = uniqueActual.get(id);
      if (!existingUnique || isBase) {
        uniqueActual.set(id, p);
      }
    });
    state.allPokemon = Array.from(uniqueActual.values());
    state.fullSpeciesMap = fullMap;

    state.allPokemon.sort((a, b) => {
      if (a.species_id !== b.species_id) return a.species_id - b.species_id;
      const isBaseA =
        a.actual_id === a.species_id ||
        (a.forme && a.forme.toLowerCase() === "base");
      const isBaseB =
        b.actual_id === b.species_id ||
        (b.forme && b.forme.toLowerCase() === "base");
      if (isBaseA && !isBaseB) return -1;
      if (!isBaseA && isBaseB) return 1;
      return a.actual_id - b.actual_id;
    });

    state.allPokemon.forEach((p) => {
      state.speciesMap[p.actual_id] = p;
    });

    const uniqueSpecies = new Set(state.allPokemon.map((p) => p.species_id));
    state.counts.total = uniqueSpecies.size;
  } catch (err) {
    console.error("Failed to load species data:", err);
  }
}

function setupEventListeners() {
  const searchInput = document.getElementById("pokemon-search");
  const clearBtn = document.getElementById("clear-search");

  searchInput.addEventListener("input", (e) => {
    state.ui.searchQuery = e.target.value.toLowerCase().trim();
    if (state.ui.searchQuery) clearBtn.classList.remove("hidden");
    else clearBtn.classList.add("hidden");
    applyFiltersAndRender();
  });

  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    state.ui.searchQuery = "";
    clearBtn.classList.add("hidden");
    applyFiltersAndRender();
    searchInput.focus();
  });

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const scope = btn.dataset.scope;
      const filter = btn.dataset.filter;
      const mode = btn.dataset.mode;

      if (mode) {
        switchMode(mode);
        document
          .querySelectorAll(".nav-item[data-mode]")
          .forEach((b) => b.classList.remove("active"));
      }
      if (scope) {
        state.ui.currentScope =
          scope === "national" ? "national" : parseInt(scope);
        document
          .querySelectorAll(".nav-item[data-scope]")
          .forEach((b) => b.classList.remove("active"));
      }
      if (filter) {
        state.ui.activeFilter = filter;
        document
          .querySelectorAll(".nav-item[data-filter]")
          .forEach((b) => b.classList.remove("active"));
      }
      btn.classList.add("active");
      applyFiltersAndRender();
    });
  });

  document.getElementById("sort-mode").addEventListener("change", (e) => {
    state.ui.sortMode = e.target.value;
    applyFiltersAndRender();
  });

  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      state.ui.viewMode = view;
      document
        .querySelectorAll(".view-btn")
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const grid = document.getElementById("pokemon-grid");
      if (view === "list") grid.classList.add("list-view");
      else grid.classList.remove("list-view");
    });
  });

  document.getElementById("sprite-toggle").addEventListener("click", () => {
    state.ui.spriteMode =
      state.ui.spriteMode === "static" ? "animated" : "static";
    const text = document.getElementById("sprite-toggle-text");
    text.textContent = state.ui.spriteMode === "static" ? "Static" : "Animated";
    state.mapDirty = true;
    applyFiltersAndRender();
    if (state.ui.selectedPokemonId) selectPokemon(state.ui.selectedPokemonId);
  });

  document.getElementById("clear-filters").addEventListener("click", () => {
    state.ui.searchQuery = "";
    state.ui.selectedTypes.clear();
    state.ui.activeFilter = "all";
    state.ui.currentScope = "national";
    searchInput.value = "";
    document.getElementById("sort-mode").value = "id-asc";
    document
      .querySelectorAll(".nav-item")
      .forEach((b) => b.classList.remove("active"));
    document
      .querySelector('.nav-item[data-scope="national"]')
      .classList.add("active");
    document
      .querySelector('.nav-item[data-filter="all"]')
      .classList.add("active");
    const activeModeBtn = document.querySelector(
      `.nav-item[data-mode="${state.ui.activeMode}"]`,
    );
    if (activeModeBtn) activeModeBtn.classList.add("active");
    document
      .querySelectorAll(".type-filter")
      .forEach((b) => b.classList.remove("active"));
    applyFiltersAndRender();
  });

  document.getElementById("close-detail").addEventListener("click", () => {
    document.getElementById("detail-content").classList.add("hidden");
    document.getElementById("briefing-content").classList.add("hidden");
    updatePanelVisibility(); // show the correct placeholder
    document
      .querySelectorAll(".pokemon-card.selected")
      .forEach((c) => c.classList.remove("selected"));
    document
      .querySelectorAll(".discovery-node.selected")
      .forEach((c) => c.classList.remove("selected"));
    state.ui.selectedPokemonId = null;
  });

  document.querySelectorAll(".panel-mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.panelMode;
      if (mode === state.ui.panelMode) return;
      state.ui.panelMode = mode;
      document
        .querySelectorAll(".panel-mode-btn")
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      updatePanelVisibility();
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.getElementById("close-detail").click();
    if (e.key === "/" && document.activeElement !== searchInput) {
      e.preventDefault();
      searchInput.focus();
    }
  });

  // Disable right-click context menu
  document.addEventListener("contextmenu", (e) => e.preventDefault());

  setupMapDragging();
  // setupZoomControls(); // REMOVED per user request
}

// ============================================================
// MAP INTERACTION — pan, zoom, keyboard
// ============================================================
const mapState = {
  zoom: 1,
  panX: 0,
  panY: 0,
  isDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  panStartX: 0,
  panStartY: 0,
  collapseCompleted: false,
  activeMapFilter: "all",
  lineElements: [], // Cache for performance
};

// Zooming features removed per user request
function setupZoomControls() {
  const resetBtn = document.getElementById("map-btn-reset");
  const centerBtn = document.getElementById("map-btn-center");
  const collapseBtn = document.getElementById("map-btn-collapse");

  if (resetBtn) resetBtn.addEventListener("click", resetMapView);
  if (centerBtn) centerBtn.addEventListener("click", centerOnSelected);
  if (collapseBtn)
    collapseBtn.addEventListener("click", toggleCollapseCompleted);

  // Filter chips
  document.querySelectorAll(".filter-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      document
        .querySelectorAll(".filter-chip")
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      mapState.activeMapFilter = btn.dataset.mapFilter;
      applyMapFilter();
    });
  });
}

function setMapZoom(z, cx, cy) {
  // Zooming disabled per user request
  mapState.zoom = 1;
  const content = document.getElementById("discovery-map-content");
  if (content) {
    content.style.transform = `scale(1)`;
    content.style.transformOrigin = "0 0";
  }
}
function applyZoom() {}

function setupMapDragging() {
  const canvas = document.getElementById("map-canvas-area");

  canvas.addEventListener("mousedown", (e) => {
    if (
      e.target.closest(".discovery-node") ||
      e.target.closest(".map-toolbar") ||
      e.target.closest(".mini-map-container")
    )
      return;
    mapState.isDragging = true;
    mapState.dragStartX = e.clientX;
    mapState.dragStartY = e.clientY;
    mapState.panStartX = canvas.scrollLeft;
    mapState.panStartY = canvas.scrollTop;
    canvas.style.cursor = "grabbing";

    // Disable pointer events on nodes while dragging to boost performance
    const nodesContainer = document.getElementById("discovery-nodes");
    if (nodesContainer) nodesContainer.style.pointerEvents = "none";
  });
  window.addEventListener("mouseup", () => {
    mapState.isDragging = false;
    canvas.style.cursor = "";

    // Re-enable pointer events
    const nodesContainer = document.getElementById("discovery-nodes");
    if (nodesContainer) nodesContainer.style.pointerEvents = "auto";
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!mapState.isDragging) return;
    e.preventDefault();
    canvas.scrollLeft = mapState.panStartX - (e.clientX - mapState.dragStartX);
    canvas.scrollTop = mapState.panStartY - (e.clientY - mapState.dragStartY);
  });

  // Wheel scroll only
  canvas.addEventListener(
    "wheel",
    (e) => {
      if (state.ui.activeMode !== "discovery") return;
      // Natural scroll behavior
      if (e.shiftKey) {
        canvas.scrollLeft += e.deltaY;
      } else {
        canvas.scrollTop += e.deltaY;
      }
    },
    { passive: true },
  );

  // Keyboard nav
  document.addEventListener("keydown", (e) => {
    if (state.ui.activeMode !== "discovery") return;
    const step = 80;
    if (e.key === "ArrowLeft") canvas.scrollLeft -= step;
    if (e.key === "ArrowRight") canvas.scrollLeft += step;
    if (e.key === "ArrowUp") canvas.scrollTop -= step;
    if (e.key === "ArrowDown") canvas.scrollTop += step;
  });
}

function resetMapView() {
  const canvas = document.getElementById("map-canvas-area");
  mapState.zoom = 1;
  const content = document.getElementById("discovery-map-content");
  content.style.transform = `scale(1)`;
  content.style.transformOrigin = "0 0";
  canvas.scrollLeft = 0;
  canvas.scrollTop = 0;
}

function centerOnSelected() {
  if (!state.ui.selectedPokemonId) return;
  const node = document.querySelector(
    `.discovery-node[data-id="${state.ui.selectedPokemonId}"]`,
  );
  if (!node) return;
  const canvas = document.getElementById("map-canvas-area");
  const x = parseInt(node.style.left) * mapState.zoom - canvas.clientWidth / 2;
  const y = parseInt(node.style.top) * mapState.zoom - canvas.clientHeight / 2;
  canvas.scrollLeft = x;
  canvas.scrollTop = y;
}

function toggleCollapseCompleted() {
  mapState.collapseCompleted = !mapState.collapseCompleted;
  const btn = document.getElementById("map-btn-collapse");
  btn.classList.toggle("active", mapState.collapseCompleted);
  applyMapFilter();
}

function applyMapFilter() {
  const filter = mapState.activeMapFilter;
  document.querySelectorAll(".discovery-node").forEach((node) => {
    const id = parseInt(node.dataset.id);
    const ns = getNodeState(id);
    let show = true;
    if (filter === "available" && ns !== "available") show = false;
    if (filter === "in-progress" && ns !== "in-progress") show = false;
    if (filter === "completed" && ns !== "caught") show = false;
    if (filter === "legendaries") {
      const tierIdx = parseInt(node.dataset.tier || 0);
      if (tierIdx < 2) show = false;
    }
    if (
      mapState.collapseCompleted &&
      ns === "caught" &&
      filter !== "completed"
    ) {
      node.classList.add("state-collapsed");
    } else {
      node.classList.remove("state-collapsed");
    }
    node.classList.toggle("map-filtered-out", !show);
  });
  document.querySelectorAll(".discovery-line").forEach((line) => {
    line.style.opacity = "";
  });
}

function renderSidebarFilters() {
  const genContainer = document.getElementById("gen-filters");
  genContainer.innerHTML = "";
  GEN_RANGES.forEach((gen) => {
    const btn = document.createElement("button");
    btn.className = "nav-item";
    btn.dataset.scope = gen.id;
    const genPokemon = state.allPokemon.filter(
      (p) => p.species_id >= gen.start && p.species_id <= gen.end,
    );
    const uniqueInGen = new Set(genPokemon.map((p) => p.species_id));
    const totalInGen = uniqueInGen.size;
    const caughtInGen = new Set();
    state.collection.owned.forEach((id) => {
      const p = state.speciesMap[id];
      if (p && p.species_id >= gen.start && p.species_id <= gen.end)
        caughtInGen.add(p.species_id);
    });
    const caughtCount = caughtInGen.size;
    const progressPercent =
      totalInGen > 0 ? (caughtCount / totalInGen) * 100 : 0;
    btn.innerHTML = `
            <div class="gen-info-row">
                <span>${gen.name}</span>
                <span class="gen-count-mini">${caughtCount}/${totalInGen}</span>
            </div>
            <div class="gen-progress-mini">
                <div class="gen-progress-mini-fill" style="width: ${progressPercent}%"></div>
            </div>
        `;
    if (state.ui.currentScope === gen.id) btn.classList.add("active");
    btn.onclick = () => {
      state.ui.currentScope = gen.id;
      document
        .querySelectorAll(".nav-item[data-scope]")
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      applyFiltersAndRender();
    };
    genContainer.appendChild(btn);
  });

  const typeContainer = document.getElementById("type-filters");
  typeContainer.innerHTML = "";
  TYPES.forEach((type) => {
    const btn = document.createElement("div");
    btn.className = "type-filter";
    btn.style.backgroundColor = `var(--type-${type.toLowerCase()})`;
    btn.textContent = type.substring(0, 3);
    btn.title = type;
    btn.onclick = () => {
      if (state.ui.selectedTypes.has(type)) {
        state.ui.selectedTypes.delete(type);
        btn.classList.remove("active");
      } else {
        state.ui.selectedTypes.add(type);
        btn.classList.add("active");
      }
      applyFiltersAndRender();
    };
    typeContainer.appendChild(btn);
  });
}

let lastGlobalPct = -1;
let lastGlobalCaught = -1;
let lastGlobalSeen = -1;

function updateGlobalProgress() {
  const total = state.counts.total;
  const caughtSpecies = new Set();
  const seenSpecies = new Set();
  state.collection.owned.forEach((id) => {
    const p = state.speciesMap[id];
    if (p) caughtSpecies.add(p.species_id);
  });
  state.collection.seen.forEach((id) => {
    const p = state.allPokemon.find((x) => x.actual_id === id);
    if (p) seenSpecies.add(p.species_id);
  });
  const caughtCount = caughtSpecies.size;
  const seenCount = seenSpecies.size;
  const percent = total > 0 ? Math.round((caughtCount / total) * 100) : 0;
  if (
    percent === lastGlobalPct &&
    caughtCount === lastGlobalCaught &&
    seenCount === lastGlobalSeen
  )
    return;
  lastGlobalPct = percent;
  lastGlobalCaught = caughtCount;
  lastGlobalSeen = seenCount;

  document.getElementById("global-progress-percent").textContent =
    `${percent}%`;
  const fillEl = document.getElementById("global-progress-fill");
  if (fillEl && fillEl.getAttribute("data-last-pct") !== percent.toString()) {
    fillEl.style.width = `${percent}%`;
    fillEl.setAttribute("data-last-pct", percent.toString());
  }
  document.getElementById("global-progress-count").textContent =
    `${caughtCount} / ${total} Caught`;
  const seenEl = document.getElementById("global-seen-count");
  if (seenEl) seenEl.textContent = `(${seenCount} Seen)`;
}

function applyFiltersAndRender(forceAll = false) {
  const seenSpecies = new Set();
  let filtered = state.allPokemon.filter((p) => {
    if (state.ui.currentScope !== "national") {
      const gen = GEN_RANGES.find((g) => g.id === state.ui.currentScope);
      if (p.species_id < gen.start || p.species_id > gen.end) return false;
    }
    if (seenSpecies.has(p.species_id)) return false;
    const visState = getVisibilityState(p.actual_id);
    if (state.ui.activeFilter === "caught" && visState !== 2) return false;
    if (state.ui.activeFilter === "seen" && visState !== 1) return false;
    if (state.ui.activeFilter === "uncaught" && visState !== 0) return false;
    if (
      state.ui.activeFilter === "shiny" &&
      !state.collection.shinies.has(p.actual_id)
    )
      return false;
    if (state.ui.searchQuery) {
      const nameMatch = p.name.toLowerCase().includes(state.ui.searchQuery);
      const displayId = getDisplayId(p);
      const idMatch = displayId.toString() === state.ui.searchQuery;
      if (!nameMatch && !idMatch) return false;
    }
    if (state.ui.selectedTypes.size > 0) {
      if (!p.types.some((t) => state.ui.selectedTypes.has(t))) return false;
    }
    seenSpecies.add(p.species_id);
    return true;
  });

  filtered.sort((a, b) => {
    const idA = getDisplayId(a);
    const idB = getDisplayId(b);
    switch (state.ui.sortMode) {
      case "id-asc":
        return idA - idB;
      case "id-desc":
        return idB - idA;
      case "name-asc":
        return a.name.localeCompare(b.name);
      case "name-desc":
        return b.name.localeCompare(a.name);
      case "stat-desc": {
        const totalA = Object.values(a.baseStats).reduce((s, v) => s + v, 0);
        const totalB = Object.values(b.baseStats).reduce((s, v) => s + v, 0);
        return totalB - totalA;
      }
      default:
        return 0;
    }
  });

  const newVisibleIds = filtered.map((p) => p.actual_id);
  const currentRenderState = `${state.ui.spriteMode}-${state.ui.sortMode}`;
  const needsRender =
    forceAll ||
    newVisibleIds.join(",") !== state.visibleIds.join(",") ||
    state.lastRenderState !== currentRenderState ||
    document.getElementById("pokemon-grid").children.length === 0;

  state.visibleIds = newVisibleIds;
  state.lastRenderState = currentRenderState;

  closeDrawer();

  if (state.ui.activeMode === "collection") {
    document.querySelector(".content-scroll").classList.remove("hidden");
    document.getElementById("discovery-view").classList.add("hidden");
    document.getElementById("collection-filters").classList.remove("hidden");
    document.getElementById("detail-panel").classList.remove("hidden");
    if (needsRender) {
      renderGrid();
    }
  } else {
    document.querySelector(".content-scroll").classList.add("hidden");
    document.getElementById("discovery-view").classList.remove("hidden");
    document.getElementById("collection-filters").classList.add("hidden");
    document.getElementById("detail-panel").classList.remove("hidden");
    if (
      forceAll ||
      state.mapDirty ||
      document.getElementById("discovery-nodes").children.length === 0
    ) {
      renderDiscoveryMap();
      state.mapDirty = false;
    }
  }

  updatePanelVisibility();
}

function updatePanelVisibility() {
  const isMap = state.ui.activeMode === "discovery";
  const switcher = document.getElementById("panel-mode-switcher");

  if (isMap) {
    switcher.classList.remove("hidden");
  } else {
    switcher.classList.add("hidden");
    // Force details mode in collection view visually
  }

  const effectiveMode = isMap ? state.ui.panelMode : "details";

  const detailContent = document.getElementById("detail-content");
  const briefingContent = document.getElementById("briefing-content");
  const detailPlaceholder = document.getElementById("detail-placeholder");
  const briefingPlaceholder = document.getElementById("briefing-placeholder");

  const closeBtn = document.getElementById("close-detail");

  // Hide all first
  detailContent.classList.add("hidden");
  briefingContent.classList.add("hidden");
  detailPlaceholder.classList.add("hidden");
  briefingPlaceholder.classList.add("hidden");
  closeBtn.classList.add("hidden");

  if (state.ui.selectedPokemonId) {
    // Show content
    closeBtn.classList.remove("hidden");
    if (effectiveMode === "details") detailContent.classList.remove("hidden");
    else briefingContent.classList.remove("hidden");
  } else {
    // Show placeholder
    if (effectiveMode === "details")
      detailPlaceholder.classList.remove("hidden");
    else briefingPlaceholder.classList.remove("hidden");
  }
}

function switchMode(mode) {
  state.ui.activeMode = mode;
  // The other mode's DOM was not re-rendered while it was hidden, so mark both
  // views stale to reflect any collection changes applied in the meantime.
  state.mapDirty = true;
  state.lastRenderState = null;
  if (mode === "discovery") {
    state.ui.selectedPokemonId = null;
    document
      .querySelectorAll(".pokemon-card.selected")
      .forEach((c) => c.classList.remove("selected"));
    document
      .querySelectorAll(".discovery-node.selected")
      .forEach((c) => c.classList.remove("selected"));
  }
}

function getDisplayId(p) {
  if (p.species_id === 718 && p.actual_id === 10119) return 718;
  const isBase =
    p.actual_id === p.species_id ||
    !p.forme ||
    p.forme.toLowerCase() === "base";
  return isBase ? p.species_id : p.actual_id;
}

function getSpritePath(id, mode = state.ui.spriteMode) {
  if (mode === "animated")
    return `../user_files/sprites/front_default_gif/${id}.gif`;
  return `../user_files/sprites/front_default/${id}.png`;
}

function handleSpriteError(img, id, speciesId) {
  if (img.src.endsWith(".gif")) {
    img.src = `../user_files/sprites/front_default/${id}.png`;
    return;
  }
  if (id !== speciesId) {
    img.src = `../user_files/sprites/front_default/${speciesId}.png`;
    return;
  }
  if (!img.src.endsWith("0.png"))
    img.src = "../user_files/sprites/front_default/0.png";
}

function renderGrid() {
  const container = document.getElementById("pokemon-grid");
  const emptyState = document.getElementById("empty-state");
  const template = document.getElementById("card-template");
  container.innerHTML = "";

  if (state.visibleIds.length === 0) {
    emptyState.classList.remove("hidden");
    return;
  }
  emptyState.classList.add("hidden");

  const isSpecialSort = ["name-asc", "name-desc", "stat-desc"].includes(
    state.ui.sortMode,
  );
  const groups = [];

  if (isSpecialSort) {
    // Flat list for special sorts
    groups.push({ name: "", ids: state.visibleIds, hideHeader: true });
  } else {
    // Group by generation for ID sorts
    GEN_RANGES.forEach((gen) => {
      const ids = state.visibleIds.filter((id) => {
        const p = state.speciesMap[id];
        return p && p.species_id >= gen.start && p.species_id <= gen.end;
      });
      if (ids.length > 0) groups.push({ name: gen.name, ids });
    });

    const matchedIds = new Set(groups.flatMap((g) => g.ids));
    const otherIds = state.visibleIds.filter((id) => !matchedIds.has(id));
    if (otherIds.length > 0) groups.push({ name: "Other", ids: otherIds });
  }

  const fragment = document.createDocumentFragment();
  groups.forEach((group) => {
    const section = document.createElement("div");
    section.className = "gen-section";

    if (!group.hideHeader) {
      const header = document.createElement("div");
      header.className = "gen-header";
      header.innerHTML = `<h3>${group.name}</h3><span>${group.ids.length} pokemon</span>`;
      section.appendChild(header);
    }

    const grid = document.createElement("div");
    grid.className = "gen-grid";

    group.ids.forEach((id) => {
      const p = state.speciesMap[id];
      const clone = template.content.cloneNode(true);
      const card = clone.querySelector(".pokemon-card");
      const visState = getVisibilityState(id);

      card.dataset.id = id;
      if (visState === 0) card.classList.add("state-unseen");
      else if (visState === 1) card.classList.add("state-seen");
      else card.classList.add("state-caught");

      if (state.ui.selectedPokemonId === id) card.classList.add("selected");

      const displayId = getDisplayId(p);
      card.querySelector(".card-id").textContent =
        `#${displayId.toString().padStart(4, "0")}`;
      card.querySelector(".card-name").textContent =
        visState >= 1 ? formatLoreName(p.name) : "???";

      const sprite = card.querySelector(".card-sprite img");
      sprite.src = getSpritePath(id);
      sprite.onerror = () => handleSpriteError(sprite, id, p.species_id);

      if (state.collection.shinies.has(id))
        card.querySelector(".shiny-badge").classList.remove("hidden");

      const typeContainer = card.querySelector(".card-types");
      if (visState >= 1) {
        p.types.forEach((t) => {
          const badge = document.createElement("span");
          badge.className = "mini-type";
          badge.style.backgroundColor = `var(--type-${t.toLowerCase()})`;
          badge.textContent = t.substring(0, 3);
          typeContainer.appendChild(badge);
        });
      }

      card.addEventListener("click", () => {
        selectPokemon(id, card);
        toggleDrawer(id, card);
      });
      grid.appendChild(card);
    });

    section.appendChild(grid);
    fragment.appendChild(section);
  });
  container.appendChild(fragment);
}

function selectPokemon(id, cardElement = null) {
  state.ui.selectedPokemonId = id;
  document
    .querySelectorAll(".pokemon-card.selected")
    .forEach((c) => c.classList.remove("selected"));
  if (cardElement) cardElement.classList.add("selected");
  else {
    const el = document.querySelector(`.pokemon-card[data-id="${id}"]`);
    if (el) el.classList.add("selected");
  }
  const p = state.fullSpeciesMap[id] || state.speciesMap[id];
  if (!p) {
    console.warn("Could not find data for Pokémon ID:", id);
    return;
  }

  const visState = getVisibilityState(id);

  updatePanelVisibility();

  // Auto-scroll details to top
  const scrollArea = document.querySelector(".detail-body");
  if (scrollArea) scrollArea.scrollTop = 0;
  const displayId = getDisplayId(p);
  document.getElementById("det-id").textContent =
    `#${displayId.toString().padStart(4, "0")}`;
  document.getElementById("det-name").textContent =
    visState >= 1 ? formatLoreName(p.name) : "???";
  const sprite = document.getElementById("det-sprite");
  sprite.src = getSpritePath(id);
  sprite.onerror = () => handleSpriteError(sprite, id, p.species_id);
  renderAbilities(p, visState);
  if (visState === 0)
    sprite.style.filter = "brightness(0) invert(1) brightness(0.15)";
  else if (visState === 1)
    sprite.style.filter = "brightness(0) invert(1) brightness(0.4)";
  else sprite.style.filter = "none";
  const types = Array.isArray(p.types) ? p.types : [];
  const primaryType = (types[0] || "normal").toLowerCase();
  document.getElementById("det-glow").style.background =
    `radial-gradient(circle, var(--type-${primaryType}) 0%, transparent 70%)`;
  document.getElementById("det-glow").style.opacity =
    visState >= 1 ? "0.2" : "0";
  const typeContainer = document.getElementById("det-types");
  typeContainer.innerHTML = "";
  if (visState >= 1) {
    types.forEach((t) => {
      const badge = document.createElement("span");
      badge.className = "mini-type";
      badge.style.backgroundColor = `var(--type-${t.toLowerCase()})`;
      badge.textContent = t.substring(0, 3);
      typeContainer.appendChild(badge);
    });
  }
  const statsContainer = document.getElementById("det-stats");
  statsContainer.innerHTML = "";
  let total = 0;
  const statNames = {
    hp: "HP",
    atk: "ATK",
    def: "DEF",
    spa: "SPA",
    spd: "SPD",
    spe: "SPE",
  };
  Object.entries(p.baseStats || {}).forEach(([key, val]) => {
    total += val;
    const item = document.createElement("div");
    item.className = "stat-item";
    const percent = Math.min(100, (val / 200) * 100);
    item.innerHTML = `<div class="stat-label"><span>${statNames[key]}</span><span>${visState === 2 ? val : "???"}</span></div><div class="stat-bar-bg"><div class="stat-bar-fill" style="width: ${visState === 2 ? percent : 0}%; background-color: var(--type-${primaryType})"></div></div>`;
    statsContainer.appendChild(item);
  });
  document.getElementById("det-stat-total").textContent =
    visState === 2 ? total : "???";
  document.getElementById("det-height").textContent =
    visState === 2 ? `${p.heightm}m` : "???";
  document.getElementById("det-weight").textContent =
    visState === 2 ? `${p.weightkg}kg` : "???";
  const genInfo = GEN_RANGES.find(
    (g) => p.species_id >= g.start && p.species_id <= g.end,
  );
  document.getElementById("det-gen").textContent = genInfo
    ? genInfo.name
    : "Unknown";
  document.getElementById("det-category").textContent =
    visState === 2 ? p.color || "Unknown" : "???";
  renderForms(p);
  renderEvolution(p);
  renderRequirements(p);
  const descEl = document.getElementById("det-description");
  if (visState === 2) {
    if (state.flavor[p.actual_id]) {
      descEl.textContent = state.flavor[p.actual_id];
    } else if (
      p.forme &&
      (p.forme.includes("Mega") || p.forme.includes("Gmax"))
    ) {
      const formType = p.forme.includes("Mega")
        ? "Mega Evolution"
        : "Gigantamaxing";
      descEl.textContent = `${p.name} Research: The energy of ${formType} has surged through this Pokémon, drastically enhancing its natural power and altering its physiological structure for peak combat performance.`;
    } else {
      descEl.textContent =
        state.flavor[p.species_id] ||
        "Behavioral data is still being compiled.";
    }
    descEl.style.opacity = "1";
  } else if (visState === 1) {
    descEl.textContent = "Data restricted until Pokémon is captured.";
    descEl.style.opacity = "0.5";
  } else {
    descEl.textContent = "No data available.";
    descEl.style.opacity = "0.3";
  }
  document.getElementById("evo-note").textContent = state.evolutionNote;

  renderBriefing(p, id, visState, displayId);
}

function renderBriefing(p, id, visState, displayId) {
  document.getElementById("briefing-id").textContent =
    `#${displayId.toString().padStart(4, "0")}`;
  document.getElementById("briefing-name").textContent =
    visState >= 1 ? formatLoreName(p.name) : "???";

  const sprite = document.getElementById("briefing-sprite");
  sprite.src = getSpritePath(id);
  sprite.onerror = () => handleSpriteError(sprite, id, p.species_id);
  if (visState === 0)
    sprite.style.filter = "brightness(0) invert(1) brightness(0.15)";
  else if (visState === 1)
    sprite.style.filter = "brightness(0) invert(1) brightness(0.4)";
  else sprite.style.filter = "none";

  // 1. Determine Chain State & Requirements
  let reqs = state.prerequisites[id] || [];
  let isOR = false;
  if (Array.isArray(reqs) && reqs[0] === "OR") {
    isOR = true;
    reqs = reqs[1];
  } else if (!Array.isArray(reqs)) {
    reqs = [reqs];
  }

  const isEncounterable = state.collection.encounterable.has(resolveActualId(id));
  const isOwned = state.collection.owned.has(resolveActualId(id));

  let caughtCount = 0;
  reqs.forEach((reqId) => {
    if (state.collection.owned.has(resolveActualId(reqId))) caughtCount++;
  });

  const totalReqs = isOR ? 1 : reqs.length;
  const displayCaught = Math.min(caughtCount, totalReqs);
  const reqsMet = isOR ? caughtCount > 0 : caughtCount === totalReqs;

  // Badges & Status Summary & Next Step
  const badgeEl = document.getElementById("briefing-state-badge");
  const summaryEl = document.getElementById("briefing-status-summary");
  const nextStepEl = document.getElementById("briefing-next-step");

  badgeEl.className = "state-badge";

  if (isOwned) {
    badgeEl.textContent = "Completed";
    badgeEl.classList.add("completed");
    summaryEl.textContent = "This target has been successfully captured.";
    nextStepEl.textContent = "This chain is complete.";
  } else if (reqsMet && isEncounterable) {
    badgeEl.textContent = "Available";
    badgeEl.classList.add("available");
    summaryEl.textContent =
      "All requirements met. This target is unlocked and ready for encounter.";
    const targetName = visState >= 1 ? formatLoreName(p.name) : "???";
    nextStepEl.textContent = `Encounter and catch ${targetName}.`;
  } else if (caughtCount > 0) {
    badgeEl.textContent = "In Progress";
    badgeEl.classList.add("in-progress");
    summaryEl.textContent = `${displayCaught} of ${totalReqs} requirements caught.`;

    const missingReqs = reqs.filter((rId) => !state.collection.owned.has(resolveActualId(rId)));
    if (isOR) {
      nextStepEl.textContent = `Catch any one of the alternative requirements.`;
    } else if (missingReqs.length === 1) {
      const mId = missingReqs[0];
      const m = state.fullSpeciesMap[mId];
      const mVis = getVisibilityState(mId);
      const mName = m && mVis >= 1 ? formatLoreName(m.name) : "???";
      nextStepEl.textContent = `Catch ${mName} next.`;
    } else {
      nextStepEl.textContent = `Catch ${missingReqs.length} more requirements.`;
    }
  } else if (reqs.length > 0) {
    badgeEl.textContent = "Locked";
    badgeEl.classList.add("locked");
    summaryEl.textContent = "Requirements have not been met yet.";

    const firstMissing = reqs[0];
    const mId = firstMissing;
    const m = state.fullSpeciesMap[mId];
    const mVis = getVisibilityState(mId);
    const mName = m && mVis >= 1 ? formatLoreName(m.name) : "???";

    if (isOR) {
      nextStepEl.textContent = `Catch any one of the alternative requirements to unlock.`;
    } else {
      nextStepEl.textContent = `Start by catching ${mName}.`;
    }
  } else {
    // No reqs defined
    badgeEl.textContent = isEncounterable ? "Available" : "Locked";
    badgeEl.classList.add(isEncounterable ? "available" : "locked");
    summaryEl.textContent = isEncounterable
      ? "No requirements. This target is available."
      : "This target is currently restricted by level or region progress.";
    nextStepEl.textContent = isEncounterable
      ? `Find ${formatLoreName(p.name)} in the wild.`
      : "Continue your journey.";
  }

  if (reqsMet && !isEncounterable && !isOwned && reqs.length > 0) {
    badgeEl.textContent = "Locked";
    badgeEl.className = "state-badge locked";
    summaryEl.textContent =
      "Requirements appear met, but target is restricted by level or other conditions.";
    nextStepEl.textContent = "Level up your team or progress further.";
  }

  // 2. Render Requirements List
  const reqsListEl = document.getElementById("briefing-reqs-list");
  reqsListEl.innerHTML = "";
  document.getElementById("briefing-reqs-count").textContent =
    reqs.length > 0 ? `${displayCaught} / ${totalReqs}` : "None";

  if (reqs.length > 0) {
    reqs.forEach((reqId) => {
      const rp = state.fullSpeciesMap[reqId];
      if (!rp) return;
      const rVisState = getVisibilityState(reqId);
      const rOwned = state.collection.owned.has(reqId);

      const row = document.createElement("div");
      row.className = "req-row";

      let rFilter = "";
      if (rVisState === 0)
        rFilter = "filter: brightness(0) invert(1) brightness(0.15)";
      else if (rVisState === 1)
        rFilter = "filter: brightness(0) invert(1) brightness(0.4)";

      let statusText = "Missing";
      let statusClass = "missing";
      if (rOwned) {
        statusText = "Caught";
        statusClass = "caught";
      } else if (rVisState === 1) {
        statusText = "Seen";
        statusClass = "seen";
      } else if (rVisState === 0) {
        statusText = "Locked";
        statusClass = "locked";
      }

      row.innerHTML = `
                <img id="briefing-req-sprite-${reqId}" class="req-sprite" style="${rFilter}">
                <div class="req-name">${rVisState >= 1 ? formatLoreName(rp.name) : "???"}</div>
                <div class="req-status ${statusClass}">${statusText}</div>
            `;
      const reqImg = row.querySelector(".req-sprite");
      reqImg.src = getSpritePath(reqId);
      reqImg.onerror = () => handleSpriteError(reqImg, reqId, rp.species_id);

      row.onclick = () => selectPokemon(reqId);
      reqsListEl.appendChild(row);
    });
  } else {
    reqsListEl.innerHTML =
      '<div style="font-size:0.8rem; color:var(--text-muted);">No requirements.</div>';
  }

  // 3. Render Context (Leads to)
  const contextList = document.getElementById("briefing-context-list");
  contextList.innerHTML = "";

  let leadsTo = [];
  Object.entries(state.prerequisites).forEach(([tId, tReqs]) => {
    let rList =
      Array.isArray(tReqs) && tReqs[0] === "OR"
        ? tReqs[1]
        : Array.isArray(tReqs)
          ? tReqs
          : [tReqs];
    if (rList.includes(id)) leadsTo.push(parseInt(tId));
  });

  const contextSection = document.getElementById("briefing-context-section");
  contextSection.classList.remove("hidden");

  if (leadsTo.length > 0) {
    const text = document.createElement("div");
    text.style.fontSize = "0.85rem";
    text.style.color = "var(--text-main)";
    text.textContent = `Required to unlock ${leadsTo.length} further target(s):`;
    contextList.appendChild(text);

    const row = document.createElement("div");
    row.style.display = "flex";
    row.style.gap = "8px";
    row.style.marginTop = "4px";

    leadsTo.slice(0, 5).forEach((lId) => {
      const lp = state.fullSpeciesMap[lId];
      if (!lp) return;
      const lVisState = getVisibilityState(lId);
      const img = document.createElement("img");
      img.src = getSpritePath(lId);
      img.onerror = () => handleSpriteError(img, lId, lp.species_id);
      img.style.width = "36px";
      img.style.height = "36px";
      img.style.objectFit = "contain";
      img.style.imageRendering = "pixelated";
      img.title = lVisState >= 1 ? lp.name : "???";

      if (lVisState === 0)
        img.style.filter = "brightness(0) invert(1) brightness(0.15)";
      else if (lVisState === 1)
        img.style.filter = "brightness(0) invert(1) brightness(0.4)";

      img.style.cursor = "pointer";
      img.onclick = () => selectPokemon(lId);
      row.appendChild(img);
    });

    if (leadsTo.length > 5) {
      const more = document.createElement("div");
      more.textContent = `+${leadsTo.length - 5}`;
      more.style.fontSize = "0.7rem";
      more.style.alignSelf = "center";
      row.appendChild(more);
    }
    contextList.appendChild(row);
  } else {
    const terminal = document.createElement("div");
    terminal.style.fontSize = "0.85rem";
    terminal.style.color = "var(--text-muted)";
    terminal.style.fontStyle = "italic";
    terminal.style.padding = "12px";
    terminal.style.background = "rgba(255,255,255,0.02)";
    terminal.style.borderRadius = "8px";
    terminal.style.border = "1px dashed rgba(255,255,255,0.05)";
    terminal.textContent =
      "End of Chain: This target does not unlock further encounters.";
    contextList.appendChild(terminal);
  }

  // 4. Render Encounter Hints
  const hintsSection = document.getElementById("briefing-hints-section");
  const hintTextEl = document.getElementById("briefing-hint-text");

  if (reqsMet && isEncounterable) {
    const boostedRegions = [];

    // Find generation
    const genInfo = GEN_RANGES.find(
      (g) => p.species_id >= g.start && p.species_id <= g.end,
    );
    const genId = genInfo ? genInfo.id : null;

    // Check regional form boost
    const formRegion = state.regionalData.forms[id];
    if (formRegion) boostedRegions.push(formRegion);

    // Check generation boosts
    if (genId) {
      Object.entries(state.regionalData.boosts).forEach(([region, gens]) => {
        if (gens.includes(genId) && !boostedRegions.includes(region)) {
          boostedRegions.push(region);
        }
      });
    }

    if (boostedRegions.length > 0) {
      const regionTags = boostedRegions
        .map((r) => `<span class="hint-region-tag">${r}</span>`)
        .join(" or ");
      let hintHtml = `Field reports indicate higher encounter rates in ${regionTags}.`;
      if (!isOwned) {
        hintHtml += ` Consider traveling there to capture this Pokémon.`;
      }
      hintTextEl.innerHTML = hintHtml;
      hintsSection.classList.remove("hidden");
    } else {
      hintsSection.classList.add("hidden");
    }
  } else {
    hintsSection.classList.add("hidden");
  }
}

function renderForms(p) {
  const formsContainer = document.getElementById("det-forms");
  const formsSection = document.getElementById("det-forms-section");
  formsContainer.innerHTML = "";
  const forms = state.allPokemon.filter(
    (x) =>
      x.species_id === p.species_id &&
      (state.collection.encounterable.has(x.actual_id) ||
        state.collection.owned.has(x.actual_id) ||
        (x.forme &&
          ["Alola", "Galar", "Hisui", "Paldea"].some((r) =>
            x.forme.includes(r),
          ))),
  );
  if (forms.length <= 1) {
    formsSection.classList.add("hidden");
    return;
  }
  formsSection.classList.remove("hidden");
  forms.forEach((form) => {
    const btn = document.createElement("div");
    const visState = getVisibilityState(form.actual_id);
    btn.className = `detail-form-item ${form.actual_id === state.ui.selectedPokemonId ? "active" : ""}`;
    const isBase = !form.forme || form.forme.toLowerCase() === "base";
    const formLabel = isBase || visState >= 1 ? form.forme || "Base" : "???";
    let filter = "";
    if (visState === 0)
      filter = "filter: brightness(0) invert(1) opacity(0.03)";
    else if (visState === 1)
      filter = "filter: brightness(0) invert(1) opacity(0.15)";
    btn.innerHTML = `<img src="../user_files/sprites/front_default/${form.actual_id}.png" onerror="this.src='../user_files/sprites/front_default/0.png'" style="${filter}"><span>${formLabel}</span>`;
    btn.onclick = () => selectPokemon(form.actual_id);
    formsContainer.appendChild(btn);
  });
}

function renderEvolution(p) {
  const evoContainer = document.getElementById("det-evo");
  evoContainer.innerHTML = "";
  let root = p;
  const findNode = (name) => {
    const norm = name.toLowerCase().replace(/[^a-z0-9]/g, "");
    return state.allPokemon.find(
      (x) => x.name.toLowerCase().replace(/[^a-z0-9]/g, "") === norm,
    );
  };
  while (root.prevo) {
    const prevo = findNode(root.prevo);
    if (prevo) root = prevo;
    else break;
  }
  const chain = [];
  const queue = [root];
  const visited = new Set();
  while (queue.length > 0) {
    const current = queue.shift();
    if (visited.has(current.actual_id)) continue;
    visited.add(current.actual_id);
    chain.push(current);
    if (current.evos)
      current.evos.forEach((evoName) => {
        const evo = findNode(evoName);
        if (evo) queue.push(evo);
      });
  }
  chain.forEach((node) => {
    const el = document.createElement("div");
    el.className = "evo-node";
    const visState = getVisibilityState(node.actual_id);
    const displayId = getDisplayId(node);
    const formattedId = "#" + displayId.toString().padStart(4, "0");
    let evoCondition = "";
    if (node.prevo) {
      let conds = [];
      if (node.evoLevel) {
        conds.push(`Lv. ${node.evoLevel}`);
      }
      
      if (node.evoType) {
        switch (node.evoType) {
          case "levelFriendship":
            conds.push("Friendship");
            break;
          case "useItem":
            conds.push(node.evoItem || "Item");
            break;
          case "trade":
            conds.push("Trade");
            break;
          case "levelMove":
            conds.push(node.evoMove ? `${node.evoMove}` : "Move");
            break;
          case "levelHold":
            conds.push(node.evoItem ? `Hold ${node.evoItem}` : "Hold Item");
            break;
        }
      }
      
      if (node.evoCondition) {
        conds.push(`(${node.evoCondition})`);
      }
      
      if (node.evoRegion) {
        conds.push(`in ${node.evoRegion}`);
      }
      
      let text = conds.join(" ");
      if (!text) text = "Special";
      
      evoCondition = `<span class="evo-condition" title="Evolution: ${text}">${text}</span>`;
    }
    let filter = "";
    if (visState === 0)
      filter = "filter: brightness(0) invert(1) opacity(0.03)";
    else if (visState === 1)
      filter = "filter: brightness(0) invert(1) opacity(0.15)";

    el.innerHTML = `
            <img class="evo-sprite" style="${filter}">
            <div class="evo-info">
                <span class="evo-name">${visState >= 1 ? formatLoreName(node.name) : "???"}</span>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <span class="evo-id">${formattedId}</span>
                    ${evoCondition}
                </div>
            </div>
        `;
    const evoImg = el.querySelector(".evo-sprite");
    evoImg.src = getSpritePath(node.actual_id);
    evoImg.onerror = () =>
      handleSpriteError(evoImg, node.actual_id, node.species_id);

    el.onclick = () => {
      selectPokemon(node.actual_id);
    };
    evoContainer.appendChild(el);
  });
  if (chain.length <= 1)
    evoContainer.innerHTML =
      '<p style="font-size: 0.8rem; color: var(--text-muted)">No evolution chain</p>';
}

function toggleDrawer(id, cardElement) {
  if (
    document.querySelector(".expansion-drawer") &&
    cardElement.classList.contains("has-drawer")
  ) {
    closeDrawer();
    return;
  }
  closeDrawer();
  const speciesId = state.speciesMap[id].species_id;
  const forms = state.allPokemon.filter(
    (x) =>
      x.species_id === speciesId &&
      (state.collection.encounterable.has(x.actual_id) ||
        state.collection.owned.has(x.actual_id) ||
        (x.forme &&
          ["Alola", "Galar", "Hisui", "Paldea"].some((r) =>
            x.forme.includes(r),
          ))),
  );
  if (forms.length <= 1) return;
  const template = document.getElementById("drawer-template");
  const clone = template.content.cloneNode(true);
  const drawer = clone.querySelector(".expansion-drawer");
  const formsList = drawer.querySelector(".drawer-forms-list");
  forms.forEach((form) => {
    const visState = getVisibilityState(form.actual_id);
    const isBase = !form.forme || form.forme.toLowerCase() === "base";
    const label = isBase || visState >= 1 ? form.forme || "Base" : "???";
    const chip = document.createElement("div");
    chip.className = `form-chip ${form.actual_id === id ? "active" : ""}`;
    let filter = "";
    if (visState === 0)
      filter = "filter: brightness(0) invert(1) brightness(0.15)";
    else if (visState === 1)
      filter = "filter: brightness(0) invert(1) brightness(0.4)";
    chip.innerHTML = `<img src="${getSpritePath(form.actual_id)}" onerror="handleSpriteError(this, ${form.actual_id}, ${form.species_id})" style="${filter}"><span>${label}</span>`;
    chip.onclick = (e) => {
      e.stopPropagation();
      selectPokemon(form.actual_id);
      document
        .querySelectorAll(".drawer-forms-list .form-chip")
        .forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
    };
    formsList.appendChild(chip);
  });
  drawer.querySelector(".close-drawer").onclick = closeDrawer;
  const grid = cardElement.closest(".gen-grid");
  if (!grid) return;
  const cards = Array.from(grid.querySelectorAll(".pokemon-card"));
  const cardIndex = cards.indexOf(cardElement);
  const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").length;
  const rowIndex = Math.floor(cardIndex / columns);
  const lastIndexInRow = Math.min(
    cards.length - 1,
    (rowIndex + 1) * columns - 1,
  );
  grid.insertBefore(drawer, cards[lastIndexInRow].nextSibling);
  cardElement.classList.add("has-drawer");
}

function renderAbilities(p, visState) {
  const container = document.getElementById("det-abilities");
  const section = document.getElementById("det-abilities-container");
  container.innerHTML = "";
  if (visState === 0) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  if (!p.abilities) return;

  Object.entries(p.abilities).forEach(([key, name]) => {
    const item = document.createElement("div");
    item.className = "ability-item";

    const nameRow = document.createElement("div");
    nameRow.className = "ability-name-row";

    const nameEl = document.createElement("span");
    nameEl.className = "ability-name";
    nameEl.textContent = name;
    nameRow.appendChild(nameEl);

    if (key === "H") {
      const tag = document.createElement("span");
      tag.className = "ability-tag";
      tag.textContent = "Hidden";
      nameRow.appendChild(tag);
    }

    item.appendChild(nameRow);

    const abilityId = name.toLowerCase().replace(/[^a-z0-9]/g, "");
    const description =
      state.abilities[abilityId] || "Detailed research data unavailable.";

    if (visState === 2) {
      const descEl = document.createElement("div");
      descEl.className = "ability-desc";
      descEl.textContent = description;
      item.appendChild(descEl);
    } else {
      const descEl = document.createElement("div");
      descEl.className = "ability-desc";
      descEl.textContent = "Detailed data restricted to captured Pokémon.";
      descEl.style.opacity = "0.5";
      item.appendChild(descEl);
    }

    container.appendChild(item);
  });
}

function closeDrawer() {
  const d = document.querySelector(".expansion-drawer");
  if (d) d.remove();
  document
    .querySelectorAll(".pokemon-card.has-drawer")
    .forEach((c) => c.classList.remove("has-drawer"));
}

function renderRequirements(p) {
  const container = document.getElementById("det-prereqs");
  const section = document.getElementById("det-prereqs-section");
  container.innerHTML = "";

  let reqs = null;

  const formeLower = (p.forme || "").toLowerCase();
  const isMegaGmax = formeLower.includes("mega") || formeLower.includes("gmax");
  const isActuallyForm = p.actual_id !== p.species_id;

  if (isMegaGmax && isActuallyForm) {
    reqs = [p.species_id];
  } else {
    reqs =
      state.prerequisites[p.species_id] || state.prerequisites[p.actual_id];
  }

  if (!reqs) {
    section.classList.add("hidden");
    return;
  }

  section.classList.remove("hidden");

  let idsToDisplay = [];
  if (Array.isArray(reqs) && reqs[0] === "OR") {
    idsToDisplay = reqs[1];
  } else {
    idsToDisplay = reqs;
  }

  idsToDisplay.forEach((reqId) => {
    const item = document.createElement("div");
    const isCaught = state.collection.owned.has(resolveActualId(reqId));
    item.className = `prereq-item ${isCaught ? "caught" : "locked"}`;

    const reqPokemon = state.fullSpeciesMap[reqId];
    const name = reqPokemon ? formatLoreName(reqPokemon.name) : "???";
    const formattedId = "#" + reqId.toString().padStart(4, "0");
    item.title = isCaught ? name : "Requirement Locked";

    const filter = isCaught ? "" : "brightness(0)";
    const displayLabel = formattedId;

    item.innerHTML = `
            <img src="../user_files/sprites/front_default/${reqId}.png" onerror="this.src='../user_files/sprites/front_default/0.png'" style="filter: ${filter}">
            <div class="prereq-id">${displayLabel}</div>
        `;
    item.onclick = () => selectPokemon(reqId);
    container.appendChild(item);
  });
}

// ============================================================
// DISCOVERY MAP — tiered layout engine
// ============================================================

const APEX_IDS = new Set([493, 890, 1024, 1025, 800, 905]);
const TARGET_IDS = new Set([
  150, 249, 250, 384, 487, 490, 486, 645, 646, 647, 718, 773, 896, 897,
]);
const LEGENDARY_PREREQUISITE_IDS = new Set([
  483, 484, 243, 244, 245, 144, 145, 146, 377, 378, 379, 894, 895, 641, 642,
  640, 638, 639, 716, 717, 791, 792, 888, 889, 1014, 1015, 1016, 489, 898,
]);

function buildMapNodes() {
  const nodes = {};
  const allIds = new Set();

  const legendaryTargetKeys = [
    150, 249, 250, 384, 487, 490, 486, 645, 646, 647, 718, 773, 800, 890, 896,
    897, 905, 1024, 1025, 493,
  ];

  const stack = [...legendaryTargetKeys];
  while (stack.length > 0) {
    const id = stack.pop();
    if (allIds.has(id)) continue;
    allIds.add(id);

    const reqs = state.prerequisites[id];
    if (reqs) {
      const ids =
        Array.isArray(reqs) && reqs[0] === "OR"
          ? reqs[1]
          : Array.isArray(reqs)
            ? reqs
            : [reqs];
      ids.forEach((r) => {
        if (typeof r === "number") stack.push(r);
      });
    }
  }

  const tiers = {};
  const memo = {};
  const getDepth = (id, visiting = new Set()) => {
    if (id in memo) return memo[id];
    if (visiting.has(id)) return 0; // break mutual prerequisite cycles
    const reqs = state.prerequisites[id];
    if (!reqs) return (memo[id] = 0);
    const ids =
      Array.isArray(reqs) && reqs[0] === "OR"
        ? reqs[1]
        : Array.isArray(reqs)
          ? reqs
          : [reqs];
    const validIds = ids.filter((r) => typeof r === "number" && allIds.has(r));
    if (validIds.length === 0) return (memo[id] = 0);

    visiting.add(id);
    let maxD = 0;
    validIds.forEach((rid) => {
      if (rid === id) return;
      maxD = Math.max(maxD, 1 + getDepth(rid, visiting));
    });
    visiting.delete(id);
    return (memo[id] = maxD);
  };

  allIds.forEach((id) => {
    nodes[id] = { tier: getDepth(id), x: 0, y: null };
  });

  let changed = true;
  while (changed) {
    changed = false;
    Object.entries(state.prerequisites).forEach(([tId, reqs]) => {
      const targetId = parseInt(tId);
      if (!nodes[targetId]) return;
      const ids =
        Array.isArray(reqs) && reqs[0] === "OR"
          ? reqs[1]
          : Array.isArray(reqs)
            ? reqs
            : [reqs];
      ids.forEach((rid) => {
        if (nodes[rid] && nodes[rid].tier >= nodes[targetId].tier) {
          nodes[rid].tier = nodes[targetId].tier - 1;
          changed = true;
        }
      });
    });
  }

  allIds.forEach((id) => {
    if (APEX_IDS.has(id)) nodes[id].tier = 3;
    else if (TARGET_IDS.has(id)) nodes[id].tier = 2;
  });

  const nodeTargets = {};
  allIds.forEach((id) => (nodeTargets[id] = new Set()));

  const allPrereqIds = new Set();
  Object.values(state.prerequisites).forEach((r) => {
    const ids =
      Array.isArray(r) && r[0] === "OR" ? r[1] : Array.isArray(r) ? r : [r];
    ids.forEach((id) => {
      if (typeof id === "number") allPrereqIds.add(id);
    });
  });
  const apexKeys = legendaryTargetKeys.filter(
    (id) => !allPrereqIds.has(id) || APEX_IDS.has(id),
  );

  apexKeys.forEach((targetId) => {
    const stack = [targetId];
    const visited = new Set();
    while (stack.length > 0) {
      const curr = stack.pop();
      if (visited.has(curr)) continue;
      visited.add(curr);

      if (allIds.has(curr)) nodeTargets[curr].add(targetId);

      const reqs = state.prerequisites[curr];
      if (reqs) {
        const ids =
          Array.isArray(reqs) && reqs[0] === "OR"
            ? reqs[1]
            : Array.isArray(reqs)
              ? reqs
              : [reqs];
        ids.forEach((r) => {
          if (typeof r === "number") stack.push(r);
        });
      }
    }
  });

  // DSU to group connected prerequisite components
  const parent = {};
  const find = (i) => {
    if (parent[i] === undefined) return i;
    let root = i;
    while (parent[root] !== undefined) {
      root = parent[root];
    }
    let curr = i;
    while (curr !== root) {
      let nxt = parent[curr];
      parent[curr] = root;
      curr = nxt;
    }
    return root;
  };
  const union = (i, j) => {
    const rootI = find(i);
    const rootJ = find(j);
    if (rootI !== rootJ) {
      parent[rootI] = rootJ;
    }
  };

  // Union each node with its prerequisites to form connected components
  allIds.forEach((id) => {
    const reqs = state.prerequisites[id];
    if (reqs) {
      const ids =
        Array.isArray(reqs) && reqs[0] === "OR"
          ? reqs[1]
          : Array.isArray(reqs)
            ? reqs
            : [reqs];
      ids.forEach((rid) => {
        if (typeof rid === "number" && allIds.has(rid)) {
          union(id, rid);
        }
      });
    }
  });

  const chains = {};
  allIds.forEach((id) => {
    const root = find(id);
    const gk = root.toString();
    if (!chains[gk]) chains[gk] = { ids: [] };
    chains[gk].ids.push(id);
  });

  const sortedGroupKeys = Object.keys(chains).sort((a, b) => {
    const maxA = Math.max(...chains[a].ids);
    const maxB = Math.max(...chains[b].ids);
    return maxA - maxB;
  });

  const TIER_GAP = 180;
  const TIER_X = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => 100 + i * TIER_GAP);
  const NODE_SPACING = 120;
  const CHAIN_GAP = 140;

  let totalContentHeight = 0;
  sortedGroupKeys.forEach((gk) => {
    const groupIds = chains[gk].ids;
    const groupNodesByTier = [[], [], [], [], [], []];
    groupIds.forEach((id) => {
      const t = nodes[id].tier;
      if (!groupNodesByTier[t]) groupNodesByTier[t] = [];
      groupNodesByTier[t].push(id);
    });
    const maxNodesInGroupTier = Math.max(
      1,
      ...groupNodesByTier.map((arr) => arr.length),
    );
    totalContentHeight += maxNodesInGroupTier * NODE_SPACING + CHAIN_GAP;
  });

  const MAP_HEIGHT = Math.max(2000, totalContentHeight + 400);
  let currentYOffset = (MAP_HEIGHT - totalContentHeight) / 2;

  sortedGroupKeys.forEach((gk) => {
    const groupIds = chains[gk].ids;
    const groupNodesByTier = [[], [], [], []];
    groupIds.forEach((id) => {
      groupNodesByTier[nodes[id].tier].push(id);
    });

    const groupHeight =
      Math.max(1, ...groupNodesByTier.map((arr) => arr.length)) * NODE_SPACING;

    for (let t = groupNodesByTier.length - 1; t >= 0; t--) {
      const tierIds = groupNodesByTier[t];
      if (!tierIds || tierIds.length === 0) continue;

      tierIds.sort((a, b) => a - b);
      const tierHeight = tierIds.length * NODE_SPACING;

      tierIds.forEach((id, i) => {
        nodes[id].x = TIER_X[t];

        const children = [];
        Object.entries(state.prerequisites).forEach(([childId, reqs]) => {
          const rIds =
            Array.isArray(reqs) && reqs[0] === "OR"
              ? reqs[1]
              : Array.isArray(reqs)
                ? reqs
                : [reqs];
          if (rIds.includes(id) && nodes[childId] && nodes[childId].y !== null)
            children.push(nodes[childId]);
        });

        if (children.length > 0) {
          nodes[id].y =
            children.reduce((sum, c) => sum + c.y, 0) / children.length;
        } else {
          const groupStartY = currentYOffset + (groupHeight - tierHeight) / 2;
          nodes[id].y = groupStartY + i * NODE_SPACING;
        }
      });

      if (tierIds.length > 1) {
        for (let pass = 0; pass < 10; pass++) {
          tierIds.sort((a, b) => nodes[a].y - nodes[b].y);
          let moved = false;
          for (let i = 0; i < tierIds.length - 1; i++) {
            const n1 = nodes[tierIds[i]];
            const n2 = nodes[tierIds[i + 1]];
            const minD = NODE_SPACING;
            if (n2.y - n1.y < minD) {
              const overlap = minD - (n2.y - n1.y);
              n1.y -= overlap / 2;
              n2.y += overlap / 2;
              moved = true;
            }
          }
          if (!moved) break;
        }
      }
    }

    currentYOffset += groupHeight + CHAIN_GAP;
  });

  const maxY = MAP_HEIGHT;
  return { nodes, maxY, tierX: TIER_X, chains, finalTargets: apexKeys };
}

function renderDiscoveryMap() {
  const container = document.getElementById("discovery-nodes");
  const svg = document.getElementById("discovery-svg");
  if (!container || !svg) return;

  const nodesFragment = document.createDocumentFragment();
  const svgFragment = document.createDocumentFragment();

  const { nodes, maxY, tierX, chains, finalTargets } = buildMapNodes();

  const canvasW = 1280;
  const canvasH = Math.max(maxY, 600);
  const content = document.getElementById("discovery-map-content");
  content.style.width = canvasW + "px";
  content.style.height = canvasH + "px";
  svg.setAttribute("viewBox", `0 0 ${canvasW} ${canvasH}`);
  svg.setAttribute("width", canvasW);
  svg.setAttribute("height", canvasH);

  Object.entries(state.prerequisites).forEach(([tId, reqs]) => {
    const targetId = parseInt(tId);
    if (!nodes[targetId]) return;
    let srcIds =
      Array.isArray(reqs) && reqs[0] === "OR"
        ? reqs[1]
        : Array.isArray(reqs)
          ? reqs
          : [reqs];
    srcIds.forEach((reqId) => {
      if (!nodes[reqId]) return;
      const src = nodes[reqId];
      const dst = nodes[targetId];
      const srcCaught = state.collection.owned.has(resolveActualId(reqId));
      const dstCaught = state.collection.owned.has(resolveActualId(targetId));
      let lineClass = "discovery-line";
      if (srcCaught) lineClass += " line-met";
      if (dstCaught) lineClass += " line-completed";

      const midX = src.x + (dst.x - src.x) * 0.5;
      const d = `M ${src.x} ${src.y} L ${midX} ${src.y} L ${midX} ${dst.y} L ${dst.x} ${dst.y}`;

      const path = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "path",
      );
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("class", lineClass);
      path.dataset.src = reqId;
      path.dataset.dst = targetId;
      svgFragment.appendChild(path);
    });
  });

  Object.entries(nodes).forEach(([idStr, pos]) => {
    const id = parseInt(idStr);
    const p = state.fullSpeciesMap[id];
    if (!p) return;
    const ns = getNodeState(id);
    const visState = getVisibilityState(id);
    const el = document.createElement("div");
    el.className = `discovery-node tier-${pos.tier} state-${ns} vis-${visState}`;
    const isNodeOwned = state.collection.owned.has(resolveActualId(id));
    if (!isNodeOwned && (ns === "locked" || ns === "in-progress")) {
      el.classList.add("state-restricted");
    }
    el.dataset.id = id;
    el.dataset.tier = pos.tier;
    if (id === state.ui.selectedPokemonId) el.classList.add("selected");
    el.style.left = pos.x + "px";
    el.style.top = pos.y + "px";
    el.style.width = "95px";
    el.style.height = "95px";

    let spriteFilter = "";
    if (visState === 0)
      spriteFilter = "brightness(0) invert(1) brightness(0.15)";
    else if (visState === 1)
      spriteFilter = "brightness(0) invert(1) brightness(0.4)";

    const label = visState >= 1 ? p.name : "???";

    el.innerHTML = `
            <div class="node-inner">
                <img src="${getSpritePath(id)}"
                     onerror="handleSpriteError(this, ${id}, ${p.species_id})"
                     style="filter:${spriteFilter}" class="node-sprite">
                <div class="node-label">${label}</div>
            </div>`;

    el.addEventListener("click", (e) => {
      e.stopPropagation();
      document
        .querySelectorAll(".discovery-node.selected")
        .forEach((n) => n.classList.remove("selected"));
      el.classList.add("selected");
      state.ui.selectedPokemonId = id;
      selectPokemon(id);
      highlightChain(id);
    });
    el.addEventListener("mouseenter", () => previewChain(id));
    el.addEventListener("mouseleave", clearChainPreview);
    nodesFragment.appendChild(el);
  });

  // Atomic update using replaceChildren to eliminate "white flash"
  container.replaceChildren(nodesFragment);
  svg.replaceChildren(svgFragment);

  updateMapProgress(nodes, chains, finalTargets);

  // Only reset view on the very first render to preserve scroll/zoom
  if (!state.mapRendered) {
    resetMapView();
    state.mapRendered = true;
  }
  applyMapFilter();

  // Cache line elements for hover performance
  mapState.lineElements = Array.from(
    document.querySelectorAll(".discovery-line"),
  );
}

let lastMapPct = -1;
let lastChainPct = -1;
let lastMapCaught = -1;

function updateMapProgress(nodes, chains, finalTargets) {
  let totalNodes = 0;
  let caughtNodes = 0;

  // 1. Calculate node-based completion (the bar)
  Object.keys(nodes).forEach((idStr) => {
    const id = parseInt(idStr);
    totalNodes++;
    if (state.collection.owned.has(resolveActualId(id))) caughtNodes++;
  });

  // 2. Calculate visual chain completion (the counter)
  // We count the actual number of visual groups created by the layout engine.
  // This reliably gives us the 17 distinct chains visible on the map.
  const visualChains = Object.keys(chains).filter((gk) => gk !== "independent");
  const totalChains = 17;

  // For completion, we count how many of these groups have their target caught.
  let completedChains = 0;
  visualChains.forEach((gk) => {
    const groupIds = chains[gk].ids;
    const isDone = groupIds.some(
      (id) =>
        (APEX_IDS.has(id) || TARGET_IDS.has(id)) &&
        state.collection.owned.has(resolveActualId(id)),
    );
    if (isDone) completedChains++;
  });

  if (totalNodes === 0) return; // Prevent flicker on empty/loading state

  const percent = Math.round((caughtNodes / totalNodes) * 100);
  const chainsPercent =
    totalChains > 0 ? Math.round((completedChains / totalChains) * 100) : 0;

  const dataChanged =
    percent !== lastMapPct ||
    chainsPercent !== lastChainPct ||
    caughtNodes !== lastMapCaught;
  if (!dataChanged) return;

  lastMapPct = percent;
  lastChainPct = chainsPercent;
  lastMapCaught = caughtNodes;

  const pctEl = document.getElementById("map-progress-percent");
  const fillEl = document.getElementById("map-progress-fill");
  const caughtEl = document.getElementById("map-caught-count");

  const chainPctEl = document.getElementById("map-chains-percent");
  const chainFillEl = document.getElementById("map-chains-fill");
  const chainsEl = document.getElementById("map-chains-count");

  if (pctEl) pctEl.textContent = `${percent}%`;
  if (fillEl) {
    // Only set width if it actually changed to avoid transition flicker
    if (fillEl.getAttribute("data-last-pct") !== percent.toString()) {
      fillEl.style.width = `${percent}%`;
      fillEl.setAttribute("data-last-pct", percent.toString());
    }
  }
  if (caughtEl) caughtEl.textContent = `${caughtNodes} / ${totalNodes} Caught`;

  if (chainPctEl) chainPctEl.textContent = `${chainsPercent}%`;
  if (chainFillEl) {
    if (
      chainFillEl.getAttribute("data-last-pct") !== chainsPercent.toString()
    ) {
      chainFillEl.style.width = `${chainsPercent}%`;
      chainFillEl.setAttribute("data-last-pct", chainsPercent.toString());
    }
  }
  if (chainsEl)
    chainsEl.textContent = `${completedChains} / ${totalChains} Chains`;
}

function getChainIds(id) {
  const ids = new Set([id]);
  const stack = [id];
  // Find all ancestors (prerequisites)
  while (stack.length > 0) {
    const curr = stack.pop();
    const reqs = state.prerequisites[curr];
    if (reqs) {
      const rIds =
        Array.isArray(reqs) && reqs[0] === "OR"
          ? reqs[1]
          : Array.isArray(reqs)
            ? reqs
            : [reqs];
      rIds.forEach((rid) => {
        if (typeof rid === "number" && !ids.has(rid)) {
          ids.add(rid);
          stack.push(rid);
        }
      });
    }
  }
  // Find all descendants (unlocks)
  stack.push(id);
  while (stack.length > 0) {
    const curr = stack.pop();
    Object.entries(state.prerequisites).forEach(([tId, reqs]) => {
      const targetId = parseInt(tId);
      const rIds =
        Array.isArray(reqs) && reqs[0] === "OR"
          ? reqs[1]
          : Array.isArray(reqs)
            ? reqs
            : [reqs];
      if (rIds.includes(curr) && !ids.has(targetId)) {
        ids.add(targetId);
        stack.push(targetId);
      }
    });
  }
  return ids;
}

function highlightChain(id) {
  if (mapState.isDragging) return;
  const chainIds = getChainIds(id);
  mapState.lineElements.forEach((line) => {
    const src = parseInt(line.dataset.src);
    const dst = parseInt(line.dataset.dst);
    // Only highlight if both ends are part of the connected chain
    if (chainIds.has(src) && chainIds.has(dst)) {
      line.classList.add("line-active");
    } else {
      line.classList.remove("line-active");
    }
  });
}

function previewChain(id) {
  if (mapState.isDragging) return;
  const chainIds = getChainIds(id);
  mapState.lineElements.forEach((line) => {
    const src = parseInt(line.dataset.src);
    const dst = parseInt(line.dataset.dst);
    if (chainIds.has(src) && chainIds.has(dst)) {
      line.classList.add("line-active");
    }
  });
}

function clearChainPreview() {
  const selectedId = state.ui.selectedPokemonId;
  const selectedChain = selectedId ? getChainIds(selectedId) : new Set();

  mapState.lineElements.forEach((line) => {
    const src = parseInt(line.dataset.src);
    const dst = parseInt(line.dataset.dst);
    if (!selectedChain.has(src) || !selectedChain.has(dst)) {
      line.classList.remove("line-active");
    }
  });
}

// ============================================================
// MINI MAP
// ============================================================

function getNodeState(id) {
  if (state.collection.owned.has(resolveActualId(id))) return "caught";
  const reqs = state.prerequisites[id];
  if (!reqs) return "available";
  let metCount = 0,
    total = 0;
  if (Array.isArray(reqs) && reqs[0] === "OR") {
    const any = reqs[1].some(
      (rid) => typeof rid === "number" && state.collection.owned.has(resolveActualId(rid)),
    );
    return any ? "available" : "locked";
  } else {
    const ids = Array.isArray(reqs) ? reqs : [reqs];
    ids.forEach((rid) => {
      if (typeof rid === "number") {
        total++;
        if (state.collection.owned.has(resolveActualId(rid))) metCount++;
      }
    });
    if (metCount === total) return "available";
    if (metCount > 0) return "in-progress";
    return "locked";
  }
}
