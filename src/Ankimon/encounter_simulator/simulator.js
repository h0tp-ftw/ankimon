(function() {
    // Rarity Tier color mapping & configuration
    const TIERS_CONFIG = {
        "Normal": { name: "Normal", color: "#8a93a5", key: "Normal" },
        "Baby": { name: "Baby", color: "#ff7597", key: "Baby" },
        "Ultra": { name: "Ultra", color: "#00e5ff", key: "Ultra" },
        "Gmax": { name: "Gmax", color: "#bd00ff", key: "Gmax" },
        "Paradox": { name: "Paradox", color: "#e0ff00", key: "Paradox" },
        "Starter": { name: "Starter", color: "#00ff88", key: "Starter" },
        "Mega": { name: "Mega", color: "#ff0055", key: "Mega" },
        "Legendary": { name: "Legendary", color: "#ffaa00", key: "Legendary" },
        "Mythical": { name: "Mythical", color: "#7b2cff", key: "Mythical" }
    };

    // Exponential curve parameters (loaded dynamically from python OVERHAUL_TIER_PARAMS)
    let TIER_PARAMS = {
        "Normal": { base: 96.78, max_val: 82.70 },
        "Baby": { base: 2.30, max_val: 3.0 },
        "Ultra": { base: 0.35, max_val: 4.50 },
        "Paradox": { base: 0.20, max_val: 2.00 },
        "Gmax": { base: 0.15, max_val: 2.50 },
        "Starter": { base: 0.10, max_val: 1.80 },
        "Mega": { base: 0.05, max_val: 1.50 },
        "Legendary": { base: 0.05, max_val: 1.50 },
        "Mythical": { base: 0.02, max_val: 0.50 }
    };

    let LEVEL_THRESHOLDS = {
        "Starter": 80,
        "Ultra": 30,
        "Legendary": 50,
        "Paradox": 55,
        "Mega": 60,
        "Gmax": 65,
        "Mythical": 75
    };

    // EP mastery weights and caps (loaded dynamically from python)
    let EP_WEIGHTS = {
        trainerLevel: 0.25,
        dexCompletion: 0.25,
        reviewsDone: 0.25,
        avgCp: 0.25
    };

    let CAPS = {
        trainerLevel: 50.0,
        avgCp: 16000.0
    };

    // Pity dynamic config values (loaded dynamically from python)
    let TIER_PITY_THRESHOLDS = {
        "Ultra": 100,
        "Gmax": 150,
        "Starter": 175,
        "Paradox": 180,
        "Mega": 200,
        "Legendary": 200,
        "Mythical": 400
    };
    let PITY_DIVISOR = 50.0;

    // DOM Elements
    const sliders = {
        trainerLevel: document.getElementById('slide-trainer-level'),
        dexCompletion: document.getElementById('slide-dex-completion'),
        reviewsDone: document.getElementById('slide-reviews-done'),
        dailyGoal: document.getElementById('slide-daily-goal'),
        avgCp: document.getElementById('slide-avg-cp'),
        mainLevel: document.getElementById('slide-main-level')
    };

    const bubbles = {
        trainerLevel: document.getElementById('val-trainer-level'),
        dexCompletion: document.getElementById('val-dex-completion'),
        reviewsDone: document.getElementById('val-reviews-done'),
        dailyGoal: document.getElementById('val-daily-goal'),
        avgCp: document.getElementById('val-avg-cp'),
        mainLevel: document.getElementById('val-main-level')
    };

    let selectedPityTier = "Ultra";

    const pitySliders = {
        get tierValue() { return selectedPityTier; },
        dry: document.getElementById('slide-pity-dry')
    };

    const pityDisplays = {
        dryVal: document.getElementById('val-pity-dry'),
        threshold: document.getElementById('info-pity-threshold'),
        divisor: document.getElementById('info-pity-divisor'),
        multiplier: document.getElementById('pity-multiplier-value'),
        baseRate: document.getElementById('pity-base-rate'),
        boostedRate: document.getElementById('pity-boosted-rate')
    };

    const epValueDisplay = document.getElementById('ep-value');
    const gaugeFill = document.getElementById('gauge-fill');
    const matrixTbody = document.getElementById('matrix-tbody');
    const canvas = document.getElementById('curves-graph');
    const ctx = canvas.getContext('2d');
    const pityCanvas = document.getElementById('pity-graph');
    const pityCtx = pityCanvas ? pityCanvas.getContext('2d') : null;

    let currentEP = 0.0;
    let ratesData = null;
    let liveState = null;

    // Set up canvas sizing and high DPI support
    function resizeAllCanvases() {
        if (canvas) {
            const rect = canvas.parentElement.getBoundingClientRect();
            canvas.width = rect.width * window.devicePixelRatio;
            canvas.height = rect.height * window.devicePixelRatio;
            canvas.style.width = rect.width + 'px';
            canvas.style.height = rect.height + 'px';
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
            drawGraph();
        }

        if (pityCanvas) {
            const rect = pityCanvas.parentElement.getBoundingClientRect();
            pityCanvas.width = rect.width * window.devicePixelRatio;
            pityCanvas.height = rect.height * window.devicePixelRatio;
            pityCanvas.style.width = rect.width + 'px';
            pityCanvas.style.height = rect.height + 'px';
            pityCtx.setTransform(1, 0, 0, 1, 0, 0);
            pityCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
            drawPityGraph();
        }
    }

    // Set initial canvas dimension and bind window resize
    window.addEventListener('resize', resizeAllCanvases);
    setTimeout(resizeAllCanvases, 100);

    // Dynamic Graph Renderer
    function drawGraph() {
        if (!canvas.width || !canvas.height) return;
        
        const width = canvas.width / window.devicePixelRatio;
        const height = canvas.height / window.devicePixelRatio;
        
        ctx.clearRect(0, 0, width, height);

        // Chart Area Boundaries
        const padding = { top: 20, right: 30, bottom: 30, left: 40 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;

        // 1. Draw Grid Lines and Labels
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
        ctx.fillStyle = '#8f9cae';
        ctx.font = '10px "Outfit", sans-serif';
        ctx.lineWidth = 1;

        // X-Axis Grid & Labels (EP)
        for (let i = 0; i <= 10; i++) {
            const epX = i * 10;
            const x = padding.left + (i / 10) * chartWidth;
            
            ctx.beginPath();
            ctx.moveTo(x, padding.top);
            ctx.lineTo(x, padding.top + chartHeight);
            ctx.stroke();

            ctx.textAlign = 'center';
            ctx.fillText(epX, x, padding.top + chartHeight + 15);
        }

        // Define the tiers we want to focus on and scale on the graph
        const rareTiers = ["Baby", "Ultra", "Gmax", "Paradox", "Starter", "Mega", "Legendary", "Mythical"];

        // Get simulated levels for threshold checks
        const simulatedMainLevel = parseInt(sliders.mainLevel.value);

        // Helper: calculate percentages for all tiers at any given EP
        function getRatesAtEP(epVal) {
            const weights = {};
            for (const tier in TIER_PARAMS) {
                const param = TIER_PARAMS[tier];
                weights[tier] = param.base * Math.pow((param.max_val / param.base), (epVal / 100.0));
                
                // Gating threshold check
                if (LEVEL_THRESHOLDS[tier] && simulatedMainLevel < LEVEL_THRESHOLDS[tier]) {
                    weights[tier] = 0.0;
                }
            }

            const sum = Object.values(weights).reduce((a, b) => a + b, 0);
            const rates = {};
            for (const tier in weights) {
                rates[tier] = sum > 0 ? (weights[tier] / sum) * 100.0 : 0.0;
            }
            return rates;
        }

        // Calculate curves and find max percentage among active rare tiers to scale the chart dynamically
        const points = 100;
        const curvePaths = {};
        let maxPlottedPercent = 0.1; // Baseline safety floor

        for (const tier of rareTiers) {
            curvePaths[tier] = [];
        }

        for (let i = 0; i <= points; i++) {
            const epVal = (i / points) * 100;
            const rates = getRatesAtEP(epVal);
            const x = padding.left + (i / points) * chartWidth;

            for (const tier of rareTiers) {
                const pct = rates[tier] || 0.0;
                if (pct > maxPlottedPercent) {
                    maxPlottedPercent = pct;
                }
                curvePaths[tier].push({ x, rate: pct });
            }
        }

        // Determine Y-axis max with 15% headroom, capped at a minimum of 0.5% for visual scaling
        const yMax = Math.max(0.5, Math.ceil(maxPlottedPercent * 1.15 * 10) / 10);

        // Y-Axis Grid & Labels (Percentage dynamically scaled to yMax)
        const ySteps = 5;
        for (let i = 0; i <= ySteps; i++) {
            const pctY = (i / ySteps) * yMax;
            const y = padding.top + chartHeight - (i / ySteps) * chartHeight;

            ctx.beginPath();
            ctx.moveTo(padding.left, y);
            ctx.lineTo(padding.left + chartWidth, y);
            ctx.stroke();

            ctx.textAlign = 'right';
            ctx.fillText(pctY.toFixed(2) + '%', padding.left - 8, y + 3);
        }

        // Label axis names
        ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.font = '9px "Outfit", sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText("EP (Encounter Potential) →", padding.left + chartWidth, padding.top + chartHeight + 28);

        // 2. Stroke Curves (Rare encounters only)
        for (const tier of rareTiers) {
            const path = curvePaths[tier];
            // Check if this tier has any non-zero points plotted (skip fully gated curves)
            const isZero = path.every(pt => pt.rate === 0.0);
            if (isZero) continue;

            ctx.beginPath();
            ctx.strokeStyle = TIERS_CONFIG[tier].color;
            ctx.lineWidth = 2.5; // Thicker lines for clear rare visibility
            ctx.lineJoin = 'round';
            
            const startY = padding.top + chartHeight - (path[0].rate / yMax) * chartHeight;
            ctx.moveTo(path[0].x, startY);
            for (let i = 1; i < path.length; i++) {
                const y = padding.top + chartHeight - (path[i].rate / yMax) * chartHeight;
                ctx.lineTo(path[i].x, y);
            }
            ctx.stroke();
        }

        // 3. Mark Current EP Line
        const currentX = padding.left + (currentEP / 100.0) * chartWidth;
        
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(0, 229, 255, 0.4)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([4, 4]);
        ctx.moveTo(currentX, padding.top);
        ctx.lineTo(currentX, padding.top + chartHeight);
        ctx.stroke();
        ctx.setLineDash([]); // Reset dash

        // Draw text box indicator for current EP
        ctx.fillStyle = '#00e5ff';
        ctx.beginPath();
        ctx.arc(currentX, padding.top, 4, 0, 2 * Math.PI);
        ctx.fill();

        ctx.font = 'bold 9px "Outfit", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = '#00e5ff';
        ctx.fillText("EP: " + currentEP.toFixed(1), currentX, padding.top - 8);

        // 4. Draw Current Value Dot on each Curve
        const currentRates = getRatesAtEP(currentEP);
        for (const tier of rareTiers) {
            const pct = currentRates[tier] || 0.0;
            const y = padding.top + chartHeight - (pct / yMax) * chartHeight;
            
            // Only draw dots for tiers that have a positive presence
            if (pct > 0.0) {
                ctx.beginPath();
                ctx.fillStyle = TIERS_CONFIG[tier].color;
                ctx.arc(currentX, y, 4.5, 0, 2 * Math.PI);
                ctx.fill();
                
                ctx.beginPath();
                ctx.strokeStyle = '#ffffff';
                ctx.lineWidth = 1.2;
                ctx.arc(currentX, y, 4.5, 0, 2 * Math.PI);
                ctx.stroke();
            }
        }
    }

    // Update range bubbles locally for responsiveness
    function updateBubbles() {
        bubbles.trainerLevel.innerText = sliders.trainerLevel.value;
        bubbles.dexCompletion.innerText = sliders.dexCompletion.value + '%';
        bubbles.reviewsDone.innerText = sliders.reviewsDone.value;
        bubbles.dailyGoal.innerText = sliders.dailyGoal.value;
        bubbles.avgCp.innerText = sliders.avgCp.value;
        bubbles.mainLevel.innerText = sliders.mainLevel.value;
    }

    // Collect slider values to form the state packet
    function getSliderState() {
        return {
            trainer_level: parseInt(sliders.trainerLevel.value),
            dex_completion: parseFloat(sliders.dexCompletion.value),
            reviews_done: parseInt(sliders.reviewsDone.value),
            daily_goal: parseInt(sliders.dailyGoal.value),
            avg_cp: parseFloat(sliders.avgCp.value),
            main_level: parseInt(sliders.mainLevel.value)
        };
    }

    // Call Python backend and update page
    function triggerCalculation() {
        updateBubbles();
        const state = getSliderState();

        if (window.getRatesFromPython) {
            window.getRatesFromPython(state, function(result) {
                ratesData = result;
                currentEP = result.ep;
                updateUIElements(result);
            });
        } else {
            // Fallback for visual local prototyping in standard browser
            // Compute EP locally using dynamic weights and caps
            const t_norm = Math.min((state.trainer_level / CAPS.trainerLevel) * 100.0, 100.0);
            const d_norm = state.dex_completion;
            const s_norm = Math.min((state.reviews_done / state.daily_goal) * 100.0, 100.0);
            const c_norm = Math.min((state.avg_cp / CAPS.avgCp) * 100.0, 100.0);
            currentEP = (EP_WEIGHTS.trainerLevel * t_norm) + 
                        (EP_WEIGHTS.dexCompletion * d_norm) + 
                        (EP_WEIGHTS.reviewsDone * s_norm) + 
                        (EP_WEIGHTS.avgCp * c_norm);
            currentEP = Math.max(0.0, Math.min(currentEP, 100.0));
            
            const localLocks = {};
            for (const tier in LEVEL_THRESHOLDS) {
                localLocks[tier] = state.main_level < LEVEL_THRESHOLDS[tier];
            }
            
            updateUIElements({
                ep: currentEP,
                locks: localLocks,
                live_overhaul: { "Normal": 90, "Baby": 10, "Ultra": 0, "Gmax": 0, "Starter": 0, "Mega": 0, "Legendary": 0, "Paradox": 0, "Mythical": 0 },
                live_legacy: { "Normal": 91, "Baby": 9, "Ultra": 0, "Gmax": 0, "Starter": 0, "Mega": 0, "Legendary": 0, "Paradox": 0, "Mythical": 0 },
                overhaul: { "Normal": 85, "Baby": 12, "Ultra": 3, "Gmax": 0, "Starter": 0, "Mega": 0, "Legendary": 0, "Paradox": 0, "Mythical": 0 },
                legacy: { "Normal": 88, "Baby": 10, "Ultra": 2, "Gmax": 0, "Starter": 0, "Mega": 0, "Legendary": 0, "Paradox": 0, "Mythical": 0 }
            });
        }
    }

    // Refresh UI elements with backend calculation results
    function updateUIElements(data) {
        // 1. Update EP Value display and Gauge
        epValueDisplay.innerText = data.ep.toFixed(1);
        
        // Gauge stroke dashoffset math: circle perimeter is 2 * PI * r = 2 * 3.14159 * 70 = ~440px
        const perimeter = 440;
        const offset = perimeter - (data.ep / 100.0) * perimeter;
        gaugeFill.style.strokeDashoffset = offset;

        // 2. Clear and Render Table Matrix
        matrixTbody.innerHTML = '';
        
        for (const tier in TIERS_CONFIG) {
            const config = TIERS_CONFIG[tier];
            const liveOverhaulVal = data.live_overhaul[tier] !== undefined ? data.live_overhaul[tier].toFixed(3) + '%' : '0.000%';
            const liveLegacyVal = data.live_legacy[tier] !== undefined ? data.live_legacy[tier].toFixed(3) + '%' : '0.000%';
            const overhaulVal = data.overhaul[tier] !== undefined ? data.overhaul[tier].toFixed(3) + '%' : '0.000%';
            const legacyVal = data.legacy[tier] !== undefined ? data.legacy[tier].toFixed(3) + '%' : '0.000%';
            
            const isLocked = data.locks[tier] === true;
            const lockHtml = isLocked ? `<span class="lock-indicator" title="Locked: Main Pokémon below level threshold">🔒 Lvl ${LEVEL_THRESHOLDS[tier]}</span>` : '';

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>
                    <div class="tier-cell">
                        <span class="tier-indicator bg-${config.key.toLowerCase()}"></span>
                        <span class="color-${config.key.toLowerCase()}">${config.name}</span>
                        ${lockHtml}
                    </div>
                </td>
                <td style="text-align: right; color: #a5b4fc;" class="percentage-value">${liveOverhaulVal}</td>
                <td style="text-align: right; color: rgba(255,255,255,0.4);" class="percentage-value">${liveLegacyVal}</td>
                <td style="text-align: right; color: var(--accent-cyan);" class="percentage-value">${overhaulVal}</td>
                <td style="text-align: right; color: rgba(255,255,255,0.6);" class="percentage-value">${legacyVal}</td>
            `;
            matrixTbody.appendChild(tr);
        }

        // 3. Re-render graph curves and value markers
        drawGraph();

        // 4. Update Pity Simulator display
        updatePitySimulator();
    }

    // =========================================================================
    // Pity & Dry Spell Simulator Logic
    // =========================================================================

    function getOverhaulRatesWithPity(selectedPityTier, dryEncounters) {
        const weights = {};
        const simulatedMainLevel = parseInt(sliders.mainLevel.value);
        
        // 1. Calculate base weights
        for (const tier in TIER_PARAMS) {
            const param = TIER_PARAMS[tier];
            weights[tier] = param.base * Math.pow((param.max_val / param.base), (currentEP / 100.0));
            
            // Gating threshold check
            if (LEVEL_THRESHOLDS[tier] && simulatedMainLevel < LEVEL_THRESHOLDS[tier]) {
                weights[tier] = 0.0;
            }
        }

        // 2. Calculate pity multiplier for the selected tier
        let pityMultiplier = 1.0;
        if (selectedPityTier && TIER_PARAMS[selectedPityTier]) {
            const t_i = TIER_PITY_THRESHOLDS[selectedPityTier] || 100;
            const divisor = PITY_DIVISOR;
            pityMultiplier = 1.0 + Math.pow(Math.max(0, (dryEncounters - t_i) / divisor), 2);
            weights[selectedPityTier] *= pityMultiplier;
        }

        // 3. Re-normalize to get rates
        const sum = Object.values(weights).reduce((a, b) => a + b, 0);
        const rates = {};
        for (const tier in weights) {
            rates[tier] = sum > 0 ? (weights[tier] / sum) * 100.0 : 0.0;
        }
        return {
            rates: rates,
            multiplier: pityMultiplier
        };
    }

    function updatePitySimulator() {
        if (!pitySliders.dry) return;
        
        const tier = pitySliders.tierValue;
        const dry = parseInt(pitySliders.dry.value);
        
        // Update DOM labels
        pityDisplays.dryVal.innerText = dry;
        
        const threshold = TIER_PITY_THRESHOLDS[tier] || 100;
        pityDisplays.threshold.innerText = threshold;
        pityDisplays.divisor.innerText = PITY_DIVISOR.toFixed(1);
        
        // Calculate base rate (0 dry encounters)
        const baseResult = getOverhaulRatesWithPity(tier, 0);
        const baseRate = baseResult.rates[tier] || 0.0;
        pityDisplays.baseRate.innerText = baseRate.toFixed(3) + "%";
        
        // Calculate boosted rate
        const boostedResult = getOverhaulRatesWithPity(tier, dry);
        const boostedRate = boostedResult.rates[tier] || 0.0;
        pityDisplays.boostedRate.innerText = boostedRate.toFixed(3) + "%";
        pityDisplays.multiplier.innerText = boostedResult.multiplier.toFixed(2) + "x";
        
        // Draw the pity curve graph
        drawPityGraph();
    }

    function drawPityGraph() {
        if (!pityCanvas || !pityCtx) return;
        
        const width = pityCanvas.width / window.devicePixelRatio;
        const height = pityCanvas.height / window.devicePixelRatio;
        
        pityCtx.clearRect(0, 0, width, height);
        
        const padding = { top: 20, right: 30, bottom: 30, left: 40 };
        const chartWidth = width - padding.left - padding.right;
        const chartHeight = height - padding.top - padding.bottom;
        
        const tier = pitySliders.tierValue;
        const currentDry = parseInt(pitySliders.dry.value);
        const threshold = TIER_PITY_THRESHOLDS[tier] || 100;
        const divisor = PITY_DIVISOR;
        
        const tierColor = TIERS_CONFIG[tier]?.color || '#00ff88';
        
        // Scale X-axis from 0 to 1000
        const maxDry = 1000;
        const maxMult = 1.0 + Math.pow(Math.max(0, (maxDry - threshold) / divisor), 2);
        
        const yMin = 1.0;
        const yMax = Math.max(5.0, Math.ceil(maxMult * 1.05));
        
        // 1. Draw Grid Lines and Labels
        pityCtx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
        pityCtx.fillStyle = '#8f9cae';
        pityCtx.font = '9px "Outfit", sans-serif';
        pityCtx.lineWidth = 1;
        
        // X-Axis Grid & Labels (Dry encounters from 0 to 1000, step 200)
        for (let i = 0; i <= 5; i++) {
            const xVal = i * 200;
            const x = padding.left + (xVal / maxDry) * chartWidth;
            
            pityCtx.beginPath();
            pityCtx.moveTo(x, padding.top);
            pityCtx.lineTo(x, padding.top + chartHeight);
            pityCtx.stroke();
            
            pityCtx.textAlign = 'center';
            pityCtx.fillText(xVal, x, padding.top + chartHeight + 14);
        }
        
        // Y-Axis Grid & Labels (Multiplier, step 4)
        const ySteps = 4;
        for (let i = 0; i <= ySteps; i++) {
            const yVal = 1.0 + (i / ySteps) * (yMax - 1.0);
            const y = padding.top + chartHeight - (i / ySteps) * chartHeight;
            
            pityCtx.beginPath();
            pityCtx.moveTo(padding.left, y);
            pityCtx.lineTo(padding.left + chartWidth, y);
            pityCtx.stroke();
            
            pityCtx.textAlign = 'right';
            pityCtx.fillText(yVal.toFixed(0) + 'x', padding.left - 8, y + 3);
        }
        
        // Draw Axis Names
        pityCtx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        pityCtx.font = '9px "Outfit", sans-serif';
        pityCtx.textAlign = 'right';
        pityCtx.fillText("Dry Encounters →", padding.left + chartWidth, padding.top + chartHeight + 26);
        
        // 2. Stroke Pity Curve
        pityCtx.beginPath();
        pityCtx.strokeStyle = tierColor;
        pityCtx.lineWidth = 2.5;
        pityCtx.lineJoin = 'round';
        
        const points = 100;
        for (let i = 0; i <= points; i++) {
            const xVal = (i / points) * maxDry;
            const mult = 1.0 + Math.pow(Math.max(0, (xVal - threshold) / divisor), 2);
            
            const x = padding.left + (xVal / maxDry) * chartWidth;
            const y = padding.top + chartHeight - ((mult - yMin) / (yMax - yMin)) * chartHeight;
            
            if (i === 0) {
                pityCtx.moveTo(x, y);
            } else {
                pityCtx.lineTo(x, y);
            }
        }
        pityCtx.stroke();
        
        // 3. Draw Threshold Marker Line (dashed vertical)
        const threshX = padding.left + (threshold / maxDry) * chartWidth;
        pityCtx.beginPath();
        pityCtx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
        pityCtx.lineWidth = 1;
        pityCtx.setLineDash([3, 3]);
        pityCtx.moveTo(threshX, padding.top);
        pityCtx.lineTo(threshX, padding.top + chartHeight);
        pityCtx.stroke();
        pityCtx.setLineDash([]);
        
        // Add threshold text label
        pityCtx.fillStyle = 'rgba(255, 255, 255, 0.4)';
        pityCtx.font = '9px "Outfit", sans-serif';
        pityCtx.textAlign = 'center';
        pityCtx.fillText(`Threshold: ${threshold}`, threshX, padding.top - 6);
        
        // 4. Mark Current Selected Value Point on Curve
        const currentMult = 1.0 + Math.pow(Math.max(0, (currentDry - threshold) / divisor), 2);
        const currentX = padding.left + (currentDry / maxDry) * chartWidth;
        const currentY = padding.top + chartHeight - ((currentMult - yMin) / (yMax - yMin)) * chartHeight;
        
        // Draw vertical guide line for current value
        pityCtx.beginPath();
        pityCtx.strokeStyle = 'rgba(0, 255, 136, 0.25)';
        pityCtx.lineWidth = 1;
        pityCtx.setLineDash([2, 2]);
        pityCtx.moveTo(currentX, currentY);
        pityCtx.lineTo(currentX, padding.top + chartHeight);
        pityCtx.stroke();
        pityCtx.setLineDash([]);
        
        // Highlight active dot
        pityCtx.beginPath();
        pityCtx.fillStyle = '#00ff88';
        pityCtx.arc(currentX, currentY, 5, 0, 2 * Math.PI);
        pityCtx.fill();
        
        pityCtx.beginPath();
        pityCtx.strokeStyle = '#ffffff';
        pityCtx.lineWidth = 1.5;
        pityCtx.arc(currentX, currentY, 5, 0, 2 * Math.PI);
        pityCtx.stroke();
        
        // Draw bubble with current stats at active dot
        pityCtx.fillStyle = '#00ff88';
        pityCtx.font = 'bold 9px "Outfit", sans-serif';
        pityCtx.textAlign = 'center';
        pityCtx.fillText(`${currentMult.toFixed(2)}x`, currentX, currentY - 8);
    }

    let calcTimeout = null;

    function handleSliderInput() {
        // Instantly update local slider text bubbles for ultra-smooth feedback
        updateBubbles();
        
        // Debounce the heavy PyQt/WebEngine evaluation calculations to prevent flickering
        if (calcTimeout) {
            clearTimeout(calcTimeout);
        }
        calcTimeout = setTimeout(triggerCalculation, 50);
    }

    function handleSliderChange() {
        if (calcTimeout) {
            clearTimeout(calcTimeout);
        }
        triggerCalculation();
    }

    // Bind event listeners to all sliders
    for (const key in sliders) {
        sliders[key].addEventListener('input', handleSliderInput);
        sliders[key].addEventListener('change', handleSliderChange);
    }

    // Bind event listeners to pity simulator controls
    if (pitySliders.dry) {
        pitySliders.dry.addEventListener('input', function() {
            pityDisplays.dryVal.innerText = pitySliders.dry.value;
            updatePitySimulator();
        });
        pitySliders.dry.addEventListener('change', function() {
            updatePitySimulator();
        });
    }

    // Custom Styled Dropdown Click Interactivity (replaces native selects to prevent crashes)
    const dropdownTrigger = document.getElementById('pity-tier-trigger');
    const dropdownOptionsContainer = document.getElementById('pity-tier-options');
    const dropdownArrow = document.getElementById('dropdown-arrow');
    const selectedLabel = document.getElementById('selected-tier-label');
    const optionElements = document.querySelectorAll('.dropdown-option');

    if (dropdownTrigger && dropdownOptionsContainer) {
        dropdownTrigger.addEventListener('click', function(e) {
            e.stopPropagation();
            dropdownOptionsContainer.classList.toggle('active');
            if (dropdownArrow) {
                dropdownArrow.style.transform = dropdownOptionsContainer.classList.contains('active') ? 'rotate(180deg)' : 'rotate(0deg)';
            }
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', function() {
            dropdownOptionsContainer.classList.remove('active');
            if (dropdownArrow) {
                dropdownArrow.style.transform = 'rotate(0deg)';
            }
        });

        optionElements.forEach(option => {
            option.addEventListener('click', function(e) {
                e.stopPropagation();
                selectedPityTier = this.getAttribute('data-value');
                
                // Update active highlighted classes
                optionElements.forEach(opt => opt.classList.remove('selected'));
                this.classList.add('selected');
                
                // Update selection label text
                if (selectedLabel) {
                    selectedLabel.innerText = selectedPityTier;
                }
                
                // Collapse menu
                dropdownOptionsContainer.classList.remove('active');
                if (dropdownArrow) {
                    dropdownArrow.style.transform = 'rotate(0deg)';
                }
                
                // Trigger calculation / update
                updatePitySimulator();
            });
        });
        
        // Mark default selection on startup
        optionElements.forEach(option => {
            if (option.getAttribute('data-value') === selectedPityTier) {
                option.classList.add('selected');
            }
        });
    }

    // 5. PyQt6 QWebChannel Connection Wires
    document.addEventListener("DOMContentLoaded", function() {
        if (typeof qt !== "undefined" && qt.webChannelTransport) {
            new QWebChannel(qt.webChannelTransport, function(channel) {
                window.pyBridge = channel.objects.pyBridge;
                
                // Wire up the expected window helper
                window.getRatesFromPython = function(sliderState, callback) {
                    window.pyBridge.calculate_rates_js(JSON.stringify(sliderState)).then(function(resultJson) {
                        callback(JSON.parse(resultJson));
                    });
                };

                // Trigger initial load calculations from real python database values
                // Python can query active save settings and update sliders on load
                window.pyBridge.get_initial_state().then(function(stateJson) {
                    const state = JSON.parse(stateJson);
                    liveState = state;
                    
                    // Load dynamic config coefficients from Python dynamically!
                    if (state.config) {
                        const cfg = state.config;
                        EP_WEIGHTS.trainerLevel = cfg.ep_weight_trainer_level;
                        EP_WEIGHTS.dexCompletion = cfg.ep_weight_dex_completion;
                        EP_WEIGHTS.reviewsDone = cfg.ep_weight_session_progress;
                        EP_WEIGHTS.avgCp = cfg.ep_weight_core_team_power;
                        
                        CAPS.trainerLevel = cfg.trainer_level_cap;
                        CAPS.avgCp = cfg.core_team_power_cap;
                        
                        if (cfg.tier_params) {
                            for (const tier in cfg.tier_params) {
                                const val = cfg.tier_params[tier];
                                if (Array.isArray(val)) {
                                    TIER_PARAMS[tier] = { base: val[0], max_val: val[1] };
                                } else {
                                    TIER_PARAMS[tier] = val;
                                }
                            }
                        }
                        if (cfg.level_thresholds) LEVEL_THRESHOLDS = cfg.level_thresholds;
                        if (cfg.pity_thresholds) TIER_PITY_THRESHOLDS = cfg.pity_thresholds;
                        if (cfg.pity_divisor) PITY_DIVISOR = cfg.pity_divisor;
                    }

                    // Dynamically adjust slider limits based on dynamic config caps from Python
                    if (CAPS.trainerLevel) {
                        sliders.trainerLevel.max = CAPS.trainerLevel;
                    }
                    if (CAPS.avgCp) {
                        sliders.avgCp.min = 0; // Fix slider step alignment clamp bug!
                        sliders.avgCp.max = CAPS.avgCp;
                        // Set a step size that makes sense for the scale (e.g. 1/100 of the max, at least 50)
                        sliders.avgCp.step = Math.max(50, Math.floor(CAPS.avgCp / 100));
                    }

                    // Render active system badge dynamically
                    const systemBadge = document.getElementById('system-badge');
                    if (systemBadge && state.active_system) {
                        systemBadge.innerText = "Active System: " + state.active_system;
                        if (state.active_system === "Overhaul") {
                            systemBadge.style.borderColor = "rgba(0, 255, 136, 0.2)";
                            systemBadge.style.background = "rgba(0, 255, 136, 0.1)";
                            systemBadge.style.color = "#00ff88";
                        } else {
                            systemBadge.style.borderColor = "rgba(255, 170, 0, 0.2)";
                            systemBadge.style.background = "rgba(255, 170, 0, 0.1)";
                            systemBadge.style.color = "#ffaa00";
                        }
                    }
                    
                    sliders.trainerLevel.value = state.trainer_level;
                    sliders.dexCompletion.value = state.dex_completion;
                    sliders.reviewsDone.value = state.reviews_done;
                    sliders.dailyGoal.value = state.daily_goal;
                    sliders.avgCp.value = state.avg_cp;
                    sliders.mainLevel.value = state.main_level;
                    
                    // Wire up the Reset to Live button
                    const btnReset = document.getElementById("btn-reset");
                    if (btnReset) {
                        btnReset.addEventListener("click", function() {
                            if (liveState) {
                                sliders.trainerLevel.value = liveState.trainer_level;
                                sliders.dexCompletion.value = liveState.dex_completion;
                                sliders.reviewsDone.value = liveState.reviews_done;
                                sliders.dailyGoal.value = liveState.daily_goal;
                                sliders.avgCp.value = liveState.avg_cp;
                                sliders.mainLevel.value = liveState.main_level;
                                
                                if (pitySliders.dry) pitySliders.dry.value = 0;
                                selectedPityTier = "Ultra";
                                if (selectedLabel) selectedLabel.innerText = "Ultra";
                                optionElements.forEach(opt => {
                                    opt.classList.remove('selected');
                                    if (opt.getAttribute('data-value') === "Ultra") {
                                        opt.classList.add('selected');
                                    }
                                });
                                
                                triggerCalculation();
                            }
                        });
                    }
                    
                    triggerCalculation();
                });
            });
        } else {
            // Standard web prototyping load trigger
            triggerCalculation();
        }
    });

})();
