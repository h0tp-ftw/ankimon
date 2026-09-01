from __future__ import annotations
import random
import math
import uuid
import copy
import json
import time
from datetime import datetime
import threading

_mobile_sync_lock = threading.Lock()

_desktop_session_revlog_ids: set[int] = set()
_desktop_session_card_ids: set[int] = set()
MOBILE_QUEUE_CAP = 10_000

def _mobile_sync_configured(settings) -> bool:
    """Whether the durable desktop-processed dedup record must be written.

    This MUST match the condition under which mobile-review detection actually
    runs. Detection is registered unconditionally (setup_ankimon_sync_hooks) and
    self-gates only on ``mobile.enabled`` — it is driven by Anki's native AnkiWeb
    sync, which is independent of the legacy ``misc.ankiweb_sync`` file-sync
    toggle. Gating this durable write on ``misc.ankiweb_sync`` (default False)
    while detection ignores it meant that, in the default config, a restart lost
    the in-memory desktop-session set and detection re-queued already-battled
    desktop reviews as phantom mobile battles (double XP/catches). So gate the
    record on the same flag detection uses: ``mobile.enabled``."""
    if settings is None:
        # Unknown config: keep the safe de-dupe record rather than risk a mobile
        # double-process on the next sync.
        return True
    try:
        return bool(settings.get("mobile.enabled", True))
    except Exception:
        return True

def record_desktop_review(revlog_id: int, card_id: int = None) -> None:
    """Record a revlog.id that Ankimon handled on desktop this inter-sync interval.

    The durable record (NOT a watermark advance) keeps a mid-session restart from
    re-exposing this id as a mobile review, while an OLDER not-yet-synced mobile
    review with a lower revlog id stays detectable (advancing the watermark here
    would permanently skip that older review).

    The durable write is INLINE on purpose. Under the default single-file DB config
    (rollback journal + ``synchronous=FULL``) the INSERT+commit fsyncs, but that
    costs ~0.6 ms/answer — below per-card perceptibility, and the review-loop lag
    this repo chased (#589) was a GIL-yield issue, not this. It must NOT be deferred
    to a background daemon thread: daemon threads are killed at interpreter exit, so
    a close right after the last answer would lose those close-adjacent ids —
    exactly the ``> watermark`` reviews that would then be re-queued as phantom
    mobile battles on reopen (the very double-processing this record prevents)."""
    if revlog_id:
        _desktop_session_revlog_ids.add(revlog_id)
        try:
            from ..services import services
            if _mobile_sync_configured(services.settings):
                db = services.db
                if db is not None:
                    db.record_desktop_processed_review(revlog_id, card_id)
        except Exception:
            pass
    if card_id is not None:
        _desktop_session_card_ids.add(card_id)

def get_desktop_session_revlog_ids(col=None) -> frozenset[int]:
    ids = set(_desktop_session_revlog_ids)
    # Merge the durably-recorded desktop-processed ids so a restart that cleared
    # the in-memory set can't re-expose those reviews as mobile battles.
    try:
        from ..services import services
        db = services.db
        if db is not None:
            ids |= db.get_desktop_processed_revlog_ids()
    except Exception:
        pass
    if col and _desktop_session_card_ids:
        try:
            placeholders = ",".join("?" for _ in _desktop_session_card_ids)
            rows = col.db.list(
                f"SELECT id FROM revlog WHERE cid IN ({placeholders})",
                *list(_desktop_session_card_ids)
            )
            for r_id in rows:
                ids.add(r_id)
        except Exception:
            pass
    return frozenset(ids)

def clear_desktop_session() -> None:
    _desktop_session_revlog_ids.clear()
    _desktop_session_card_ids.clear()

class TempTracker:
    def __init__(self, total_reviews: int):
        self.total_reviews = total_reviews
        self.pokemon_encounter = 0
        self.cards_battle_round = 0

    def get_total_reviews(self) -> int:
        return self.total_reviews

def _get_team_max_level(team_clones: list, db, settings_obj, main_pokemon) -> int:
    """Get the maximum level of any companion in the team, including inactive ones.
    
    This ensures that activating or deactivating a companion does not shift the
    encounter generation level (and thus the seed/pool of valid wild species).
    """
    levels = []
    for c in team_clones:
        lvl = getattr(c, "level", None)
        if lvl is not None and isinstance(lvl, (int, float)):
            levels.append(int(lvl))
            
    inactive = settings_obj.get("mobile.inactive_companions", []) if settings_obj else []
    if inactive and db is not None:
        # Fast path: fetch every inactive companion in a single query. If the
        # batched read fails for any reason, fall through to per-id accessor reads
        # below so a partial failure never drops every level.
        use_fallback = False
        try:
            placeholders = ",".join("?" for _ in inactive)
            cursor = db.execute(
                f"SELECT data FROM captured_pokemon WHERE individual_id IN ({placeholders})",
                inactive
            )
            for row in cursor.fetchall():
                data = db._deobfuscate(row["data"])
                if data:
                    lvl = data.get("level")
                    if lvl is not None:
                        levels.append(int(lvl))
        except Exception:
            use_fallback = True

        if use_fallback or not levels:
            for ind_id in inactive:
                try:
                    pdata = db.get_pokemon(ind_id)
                    if pdata:
                        lvl = pdata.get("level")
                        if lvl is not None:
                            levels.append(int(lvl))
                except Exception:
                    pass
                
    if levels:
        return max(levels)
        
    if main_pokemon:
        lvl = getattr(main_pokemon, "level", 5)
        if isinstance(lvl, (int, float)):
            return int(lvl)
            
    return 5

def _parse_cards_per_round(settings_obj) -> tuple[int, int]:
    """Reads settings_obj.get('battle.cards_per_round', 2) and returns (cards_per_round, cpr_split)."""
    cards_per_round = 2
    if settings_obj:
        try:
            cpr = settings_obj.get("battle.cards_per_round", 2)
            if isinstance(cpr, int):
                cards_per_round = cpr
            elif isinstance(cpr, str):
                if "-" in cpr:
                    parts = cpr.split("-")
                    cards_per_round = int(sum(map(int, parts)) / len(parts))
                else:
                    try:
                        cards_per_round = int(cpr)
                    except ValueError:
                        cards_per_round = 2
        except Exception:
            cards_per_round = 2
    # Clamp to >= 1. The int-branch guard that both settings UIs apply
    # (settings_window.py / settings_schema._coerce_cards_per_round: `1 if
    # value == 0 else value`) only covers the plain-int case, so a range string
    # whose average truncates to 0 (e.g. "0-0" / "0-1") would otherwise leak a
    # cards_per_round of 0 here and later ZeroDivide (encounter_idx math,
    # avg_reviews_per_encounter, seed_idx) at the mobile-sync entry point.
    if not isinstance(cards_per_round, int) or cards_per_round < 1:
        cards_per_round = 1
    cpr_split = cards_per_round
    return cards_per_round, cpr_split

def _xp_share_split(earned_xp: int, earner_id, settings_obj, db=None) -> tuple[int, dict]:
    """Split one companion's battle XP under XP Share.

    Mirrors desktop's ``trainer_functions.xp_share_gain_exp`` — two modes,
    picked by ``trainer.xp_share_mode`` (default "classic"):

    * "classic": one chosen holder (``trainer.xp_share``) splits ``earned_xp``
      50/50 with the earner — both are reduced.
    * "oras": the earner keeps its FULL xp, and EVERY other Pokémon on the
      active team also earns that same full amount — no holder to choose.

    Returns ``(xp_kept_by_earner, {target_id: xp_for_target, ...})``. The
    targets dict is empty when there's nothing to share (XP-Share unset,
    no positive XP, or — classic only — the holder is the earner itself).
    """
    if earned_xp <= 0:
        return earned_xp, {}

    mode = settings_obj.get("trainer.xp_share_mode", "classic") if settings_obj else "classic"

    if mode == "oras":
        if db is None:
            return earned_xp, {}
        try:
            team_rows = db.get_team() or []
        except Exception:
            team_rows = []
        targets = {}
        for row in team_rows:
            ind_id = row.get("individual_id")
            if not ind_id or str(ind_id) == str(earner_id):
                continue
            targets[ind_id] = earned_xp
        return earned_xp, targets

    # --- classic mode ---
    xp_share_id = settings_obj.get("trainer.xp_share") if settings_obj else None
    if not xp_share_id or str(xp_share_id) == str(earner_id):
        return earned_xp, {}
    share_half = int(earned_xp * 0.5)
    return earned_xp - share_half, {xp_share_id: share_half}

def _compute_initial_reviews(db, tracker, day_cutoff: int) -> int:
    """Computes the adjusted total review count for encounter seeding based on day_cutoff."""
    initial_reviews = tracker.get_total_reviews() if tracker else 0
    try:
        if db:
            cutoff_ms = (day_cutoff - 86400) * 1000
            
            # Subtract all mobile reviews done today (both resolved and unresolved)
            # to get today's desktop-only baseline.
            cursor = db.execute(
                "SELECT COUNT(*) FROM pending_mobile_battles WHERE revlog_id >= ?",
                (cutoff_ms,)
            )
            row = cursor.fetchone()
            mobile_reviews_today = row[0] if row else 0
            
            initial_reviews = max(0, initial_reviews - mobile_reviews_today)
    except Exception:
        pass
    return initial_reviews

def _generate_encounter(level: int, tracker, collected_ids=None, settings_obj=None, pokedex_cache=None) -> dict | None:
    """Generates a random wild Pokémon encounter."""
    from .encounter_functions import generate_random_pokemon
    from .. import utils

    if collected_ids is None:
        try:
            collected_ids = set(utils.load_collected_pokemon_ids())
        except Exception:
            collected_ids = set()

    orig_load_ids = utils.load_collected_pokemon_ids
    utils.load_collected_pokemon_ids = lambda: collected_ids
    try:
        try:
            res = generate_random_pokemon(level, tracker, collected_ids=collected_ids)
        except TypeError:
            res = generate_random_pokemon(level, tracker)
        pkmn_name, pkmn_id, pkmn_lvl, ability, pkmn_type, base_stats, \
        enemy_attacks, base_exp, growth_rate, ev, iv, gender, \
        battle_status, battle_stats, pkmn_tier, ev_yield, pkmn_shiny, nature = res
    except Exception:
        pkmn_name = "Pikachu"
        pkmn_id = 25
        pkmn_lvl = level
        ability = "Run Away"
        pkmn_type = ["Electric"]
        base_stats = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}
        enemy_attacks = ["Thunderbolt"]
        base_exp = 112
        growth_rate = "Medium"
        ev = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        iv = {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15}
        gender = "M"
        battle_status = "Fighting"
        battle_stats = {}
        pkmn_tier = "Normal"
        ev_yield = {"speed": 2}
        pkmn_shiny = False
        nature = "serious"
    finally:
        utils.load_collected_pokemon_ids = orig_load_ids

    return {
        "name": pkmn_name,
        "id": pkmn_id,
        "level": pkmn_lvl,
        "ability": ability,
        "type": pkmn_type,
        "base_stats": base_stats,
        "attacks": enemy_attacks,
        "base_experience": base_exp,
        "growth_rate": growth_rate,
        "ev": ev,
        "iv": iv,
        "gender": gender,
        "battle_status": battle_status,
        "battle_stats": battle_stats,
        "tier": pkmn_tier,
        "ev_yield": ev_yield,
        "shiny": pkmn_shiny,
        "nature": nature
    }

def _normalize_ev_yield(raw: dict) -> dict:
    """Renames EV keys and returns the normalized dict."""
    if not raw:
        return {}
    mapping = {
        "attack": "atk",
        "defense": "def",
        "special-attack": "spa",
        "special-defense": "spd",
        "speed": "spe"
    }
    return {mapping.get(k.lower(), k.lower()): v for k, v in raw.items()}

def _heal_to_full(p) -> None:
    """Restore a companion clone to full HP and clear its battle bonuses.

    Run after every resolved encounter so each encounter begins with the
    companion at full health — exactly the way manual replay behaves (it reloads
    the team fresh on every call). Without this heal, damage carries across
    encounters, so battle length (and therefore the encounter count and the seed
    of every subsequent encounter) starts to depend on how many companions are
    active and on the odd/even fainting-revival cycle, and the preview /
    auto-resolve / manual-replay sequences drift apart.
    """
    if p is None:
        return
    max_hp_val = getattr(p, "max_hp", 100)
    if isinstance(max_hp_val, (int, float)):
        p.hp = max_hp_val
        if hasattr(p, "current_hp"):
            p.current_hp = max_hp_val
    try:
        p.reset_bonuses()
    except Exception:
        pass

def detect_mobile_reviews(col, watermark_ms: int, desktop_revlog_ids: frozenset[int]) -> list[dict]:
    """
    Returns revlog rows that are:
    - Newer than watermark_ms
    - type IN (0, 1, 2, 3) — learn, review, relearn, cram (matches desktop Ankimon behavior)
    - NOT in desktop_revlog_ids (i.e., NOT already handled by Ankimon on desktop)
    """
    rows = col.db.all(
        """
        SELECT id, cid, ease, time, type
        FROM revlog
        WHERE id > ?
          AND type IN (0, 1, 2, 3)
        ORDER BY id ASC
        """,
        watermark_ms
    )
    return [
        {"id": r[0], "cid": r[1], "ease": r[2], "time": r[3], "type": r[4]}
        for r in rows
        if r[0] not in desktop_revlog_ids
    ]


# Residual known edge (deliberately NOT auto-recovered): a mobile review can sync
# in with a revlog id OLDER than the desktop's latest review, so it lands at or
# below the monotonic watermark and the ``id > watermark`` detector above never
# sees it. Registering the post-sync hook (setup_ankimon_sync_hooks) makes the
# watermark advance AFTER a sync rather than speculatively, which shrinks this
# window; the remainder is benign (a few reviews just don't become battles).
# It is NOT closed by scanning below the watermark, because that window also
# contains ordinary desktop reviews Ankimon already battled and there is no
# durable record that reliably distinguishes them (desktop_processed_reviews is
# pruned as the watermark advances) — such a scan would re-queue already-credited
# desktop reviews as phantom mobile battles (double XP). A safe recovery would
# require a durable, age-pruned processed-review ledger; left as future work.


def process_mobile_reviews_after_sync(col, ankimon_db, settings_obj, logger) -> int:
    """
    Full post-sync pipeline:
    1. Read watermark from DB
    2. Diff revlog against session set
    3. Apply system cap (MOBILE_QUEUE_CAP)
    4. Queue new mobile battles (INSERT OR IGNORE)
    5. Advance watermark to max(revlog.id) right now
    6. Clear session set
    Returns count of newly queued battles.
    """
    if not settings_obj.get("mobile.enabled", True):
        return 0

    try:
        watermark = ankimon_db.get_mobile_watermark()
        desktop_ids = get_desktop_session_revlog_ids()

        all_mobile = detect_mobile_reviews(col, watermark, desktop_ids)

        # Apply cap — take the MOST RECENT N (highest revlog IDs). The dropped
        # oldest reviews are permanently discarded (the watermark advances past
        # them below), so this must NOT be silent: surface a user-visible
        # warning, not just an info log line.
        if len(all_mobile) > MOBILE_QUEUE_CAP:
            discarded = len(all_mobile) - MOBILE_QUEUE_CAP
            msg = (
                f"Mobile sync: {len(all_mobile)} new reviews exceed the "
                f"{MOBILE_QUEUE_CAP} system cap — the {discarded} oldest were "
                f"discarded and will not become mobile battles."
            )
            try:
                logger.log_and_showinfo("warning", msg)
            except Exception:
                logger.log("warning", msg)
            all_mobile = all_mobile[-MOBILE_QUEUE_CAP:]  # list is ASC, so take tail
        else:
            logger.log("info", f"Mobile sync: {len(all_mobile)} reviews found.")

        newly_queued = ankimon_db.queue_mobile_battles(all_mobile)

        # Advance watermark to max revlog.id in the collection right now
        new_watermark = col.db.scalar("SELECT MAX(id) FROM revlog") or watermark
        ankimon_db.set_mobile_watermark(new_watermark)

        # Clear session set — next inter-sync interval starts fresh
        clear_desktop_session()

        return newly_queued

    except Exception as e:
        logger.log("error", f"Mobile sync error: {e}")
        return 0

def load_active_team_clones(ankimon_db, settings_obj, main_pokemon_fallback) -> list:
    """
    Load the current team from DB, filter out inactive companions, and return
    a list of deep-cloned PokemonObject instances healed to full HP.

    Returns a non-empty list. Falls back to [clone(main_pokemon_fallback)] if:
    - The team table is empty
    - None of the team members can be hydrated into PokemonObjects
    - All team members are in the inactive list
    """
    from ..pyobj.pokemon_obj import PokemonObject

    clones = []
    if ankimon_db is not None:
        try:
            team_rows = ankimon_db.get_team()
            inactive = set(settings_obj.get("mobile.inactive_companions", [])) if settings_obj else set()
            active_ids = [t.get("individual_id") for t in team_rows if t.get("individual_id") and t.get("individual_id") not in inactive]
            if active_ids:
                # Active teams are small (a handful at most), so load each member
                # via the DB accessor rather than hand-rolling batched SQL —
                # get_pokemon performs the same deobfuscation internally.
                for ind_id in active_ids:
                    data = ankimon_db.get_pokemon(ind_id)
                    if not data:
                        continue
                    try:
                        clones.append(PokemonObject(**data))
                    except Exception as e:
                        try:
                            from ..services import services
                            if services.logger:
                                services.logger.log("warning", f"load_active_team_clones: skipping {ind_id}: {e}")
                        except Exception:
                            pass
        except Exception:
            pass

    def make_safe_clone(p):
        p_clone = copy.copy(p)
        if hasattr(p, "base_stats") and isinstance(p.base_stats, dict):
            p_clone.base_stats = copy.deepcopy(p.base_stats)
        if hasattr(p, "ev") and isinstance(p.ev, dict):
            p_clone.ev = copy.deepcopy(p.ev)
        if hasattr(p, "iv") and isinstance(p.iv, dict):
            p_clone.iv = copy.deepcopy(p.iv)
        if hasattr(p, "attacks") and isinstance(p.attacks, list):
            p_clone.attacks = copy.deepcopy(p.attacks)
        if hasattr(p, "stat_stages") and isinstance(p.stat_stages, dict):
            p_clone.stat_stages = copy.deepcopy(p.stat_stages)
        if hasattr(p, "volatile_status") and isinstance(p.volatile_status, (set, list)):
            p_clone.volatile_status = set(p.volatile_status)
        return p_clone

    def heal_clone(p):
        p_clone = make_safe_clone(p)
            
        max_hp_val = getattr(p_clone, "max_hp", 100)
        if isinstance(max_hp_val, (int, float)):
            p_clone.hp = max_hp_val
            if hasattr(p_clone, "current_hp"):
                p_clone.current_hp = max_hp_val
            if hasattr(p_clone, "reset_bonuses"):
                try:
                    p_clone.reset_bonuses()
                except Exception:
                    pass
        return p_clone

    if not clones and main_pokemon_fallback is not None:
        fb = main_pokemon_fallback
        fb_copy = make_safe_clone(fb)
        clones = [fb_copy]

    return [heal_clone(c) for c in clones]


def select_best_companion(team_clones: list, enemy_pokemon) -> object:
    """
    Pick the team member with the highest estimated damage output against this enemy.

    Per move: Base Power * Stat (Atk or Sp.Atk by category) * type effectiveness vs
    the enemy * STAB. EDO (Estimated Damage Output) is the average of those move
    scores. The final score is EDO * Speed -- HP is intentionally excluded so a
    low-HP-but-strong companion isn't passed over. Ties break on Speed, then level.
    Fainted members are skipped; if the whole team has fainted they are revived first.
    """
    from ..business import _load_type_chart
    from .pokedex_functions import _load_moves_cache

    if not team_clones:
        return None

    try:
        moves_data = _load_moves_cache() or {}
    except Exception:
        moves_data = {}

    def get_real_move_effectiveness(move_type: str, defender_types: list[str]) -> float:
        chart = _load_type_chart()
        if not chart:
            from ..business import type_compatibility_multiplier
            return type_compatibility_multiplier([move_type], defender_types)
        if not move_type or not defender_types:
            return 1.0
        mult = 1.0
        atk_row = chart.get(move_type.capitalize())
        if not atk_row:
            return 1.0
        for dfn in defender_types:
            val = atk_row.get(dfn.capitalize())
            if val is not None:
                mult *= float(val)
        return mult

    def get_hp_safe(c):
        val = getattr(c, "hp", 100)
        return float(val) if isinstance(val, (int, float)) else 100.0

    def get_max_hp_safe(c):
        val = getattr(c, "max_hp", None) or getattr(c, "hp", 100)
        return float(val) if isinstance(val, (int, float)) else 100.0

    # Revive all if the whole team has fainted
    all_fainted = all(get_hp_safe(c) <= 0 for c in team_clones)
    if all_fainted:
        for c in team_clones:
            max_hp = get_max_hp_safe(c)
            c.hp = max_hp
            if hasattr(c, "current_hp"):
                c.current_hp = max_hp
            if hasattr(c, "reset_bonuses"):
                try:
                    c.reset_bonuses()
                except Exception:
                    pass

    enemy_type = getattr(enemy_pokemon, "type", ["Normal"])

    best_clone = None
    best_score = -1.0

    for c in team_clones:
        hp = get_hp_safe(c)
        if hp <= 0:
            continue  # Skip fainted

        stats = getattr(c, "stats", {}) or {}
        atk = float(stats.get("atk", 10) or 10)
        spa = float(stats.get("spa", 10) or 10)
        spe = float(stats.get("spe", 10) or 10)

        c_type = getattr(c, "type", ["Normal"])

        # Retrieve moves
        moves = getattr(c, "attacks", None)
        if not moves and hasattr(c, "to_dict"):
            try:
                moves = c.to_dict().get("attacks", [])
            except Exception:
                moves = []
        if not moves:
            moves = getattr(c, "moves", [])
        if not moves:
            moves = ["Tackle"]

        move_scores = []
        for move_name in moves:
            if not move_name or not isinstance(move_name, str):
                continue
            
            # Retrieve move details from cache
            move = moves_data.get(move_name.lower())
            if not move:
                move = moves_data.get(move_name.replace(" ", "").lower())
            if not move:
                move = moves_data.get(move_name.replace("-", "").lower())
            if not move:
                move = moves_data.get("tackle") or {}

            bp = float(move.get("basePower", 0) or 0)
            category = move.get("category", "Physical")
            move_type = move.get("type", "Normal")

            if category == "Status" or bp == 0:
                move_scores.append(0.0)
                continue

            stat_val = spa if category == "Special" else atk

            # Move type compatibility against the enemy (multiplied for dual types, real multipliers)
            move_type_mult = get_real_move_effectiveness(move_type, enemy_type)

            # STAB (1.5x if move matches companion's type)
            stab = 1.5 if move_type in c_type else 1.0

            move_scores.append(bp * stat_val * move_type_mult * stab)

        # Average EDO culmination across all active moves
        if move_scores:
            culminated_edo = sum(move_scores) / len(move_scores)
        else:
            culminated_edo = max(atk, spa) * 40.0

        # Final score is EDO (tie breaks on Speed, then level)
        score = culminated_edo

        if score > best_score:
            best_score = score
            best_clone = c
        elif score == best_score and best_clone is not None:
            # Tie breaker: prefer higher speed, then higher level
            c_spe = float(stats.get("spe", 10) or 10)
            bc_stats = getattr(best_clone, "stats", {}) or {}
            bc_spe = float(bc_stats.get("spe", 10) or 10)
            if c_spe > bc_spe:
                best_clone = c
            elif c_spe == bc_spe:
                if getattr(c, "level", 0) > getattr(best_clone, "level", 0):
                    best_clone = c

    if best_clone is None:
        best_clone = team_clones[0]

    return best_clone


def _compute_encounter_idx(all_reviews: list[dict], db, settings_obj, tracker, trainer_card, main_pokemon, commit: bool = True) -> int:
    if not all_reviews:
        return 0

    if db is not None:
        try:
            # Try to get the cached count
            cursor = db.execute("SELECT value FROM metadata WHERE key = 'mobile_resolved_encounters_count'")
            row = cursor.fetchone()
            if row is not None:
                return int(row[0])
            
            # If missing, calculate starting index using resolved reviews (never use pruned history)
            cursor = db.execute("SELECT COUNT(*) FROM pending_mobile_battles WHERE resolved = 1")
            resolved_reviews = cursor.fetchone()[0]
            cards_per_round, _ = _parse_cards_per_round(settings_obj)
            approx_count = resolved_reviews // cards_per_round
            
            if commit:
                conn = db._get_connection()
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('mobile_resolved_encounters_count', ?)",
                        (str(approx_count),)
                    )
            return approx_count
        except Exception:
            pass

    # If DB is None or lookup fails, we fall back to approximating using all_reviews
    cards_per_round, _ = _parse_cards_per_round(settings_obj)
    resolved_count = sum(1 for r in all_reviews if r.get("resolved") == 1)
    return resolved_count // cards_per_round


def run_mobile_battles(
    reviews: list[dict] = None,
    *,
    commit: bool,
    db,
    settings_obj,
    tracker,
    trainer_card,
    main_pokemon=None,
    companion_override_id=None,
    logger=None,
    day_cutoff=0,
    limit=None,
    mode="all",
    progress_callback=None
) -> dict:
    with _mobile_sync_lock:
        return _run_mobile_battles_impl(
            reviews=reviews,
            commit=commit,
            db=db,
            settings_obj=settings_obj,
            tracker=tracker,
            trainer_card=trainer_card,
            main_pokemon=main_pokemon,
            companion_override_id=companion_override_id,
            logger=logger,
            day_cutoff=day_cutoff,
            limit=limit,
            mode=mode,
            progress_callback=progress_callback
        )


def _run_mobile_battles_impl(
    reviews: list[dict] = None,
    *,
    commit: bool,
    db,
    settings_obj,
    tracker,
    trainer_card,
    main_pokemon=None,
    companion_override_id=None,
    logger=None,
    day_cutoff=0,
    limit=None,
    mode="all",
    progress_callback=None
) -> dict:
    """
    Unified engine for:
    - Dry-run simulation of pending mobile battles (commit=False, mode="all")
    - Real auto-resolve of pending mobile battles (commit=True, mode="all")
    - Turn-by-turn manual battle simulation (commit=True/False, mode="next")
    """
    from aqt import mw
    if day_cutoff == 0:
        day_cutoff = mw.col.sched.day_cutoff if (mw and mw.col) else 0

    # Load all reviews from DB to construct a stable sequence for deterministic seeding
    all_reviews = []
    if db is not None:
        try:
            if hasattr(db, "execute") and callable(db.execute):
                all_rows = db.execute(
                    """SELECT id, revlog_id, card_id, ease, review_time, review_type, queued_at, resolved
                       FROM pending_mobile_battles
                       ORDER BY id ASC"""
                ).fetchall()
                all_reviews = [
                    {
                        "id": r[0],
                        "revlog_id": r[1],
                        "card_id": r[2],
                        "ease": r[3],
                        "review_time": r[4],
                        "review_type": r[5],
                        "queued_at": r[6],
                        "resolved": r[7],
                    }
                    for r in all_rows
                ]
        except Exception:
            pass

    if not all_reviews and reviews is not None:
        all_reviews = list(reviews)

    if mode == "next":
        # Dedicated manual replay simulation block
        unresolved_rows = db.execute(
            """SELECT id, revlog_id, card_id, ease, review_time, review_type, queued_at
               FROM pending_mobile_battles
               WHERE resolved = 0
               ORDER BY id ASC"""
        ).fetchall()
        if not unresolved_rows:
            return {"done": True}

        all_unresolved = [
            {
                "id": r[0],
                "revlog_id": r[1],
                "card_id": r[2],
                "ease": r[3],
                "review_time": r[4],
                "review_type": r[5],
                "queued_at": r[6],
            }
            for r in unresolved_rows
        ]

        # We will simulate turn-by-turn until enemy or companion faints
        from ..utils import load_collected_pokemon_ids
        collected_ids = set(load_collected_pokemon_ids())
        team_clones = load_active_team_clones(db, settings_obj, main_pokemon)
        stable_max_level = _get_team_max_level(team_clones, db, settings_obj, main_pokemon)
        
        # Calculate active_max_level (max level of active team clones only)
        active_levels = []
        for c in team_clones:
            lvl = getattr(c, "level", None)
            if lvl is not None and isinstance(lvl, (int, float)):
                active_levels.append(int(lvl))
        if active_levels:
            active_max_level = max(active_levels)
        elif main_pokemon:
            active_max_level = int(getattr(main_pokemon, "level", 5))
        else:
            active_max_level = 5

        from ..pyobj.pokemon_obj import PokemonObject
        from ..business import calc_experience
        from .ankimon_hooks_to_poke_engine import simulate_battle_with_poke_engine

        cards_per_round, _ = _parse_cards_per_round(settings_obj)

        # Initial seed of the encounter using stable index
        first_review = all_unresolved[0]
        resolved_count = sum(1 for r in all_reviews if r.get("resolved") == 1)
        encounter_idx = _compute_encounter_idx(all_reviews, db, settings_obj, tracker, trainer_card, main_pokemon, commit=commit)
        seed_idx = cards_per_round - 1  # default when all_reviews is empty
        if all_reviews:
            seed_idx = min(len(all_reviews) - 1, (encounter_idx + 1) * cards_per_round - 1)
            seed_review = all_reviews[seed_idx]
            enc_seed = seed_review.get("revlog_id") or seed_review.get("id") or 42
        else:
            enc_seed = 42
        random.seed(enc_seed)

        initial_reviews = _compute_initial_reviews(
            db,
            tracker,
            day_cutoff
        )
        cards_in_encounter = seed_idx + 1
        temp_tracker = TempTracker(initial_reviews + cards_in_encounter)

        enc_data = _generate_encounter(stable_max_level, temp_tracker, collected_ids, settings_obj, None)
        adjusted_level = max(1, active_max_level + (enc_data["level"] - stable_max_level))
        current_enemy_pokemon = PokemonObject(
            type=enc_data["type"], name=enc_data["name"], id=enc_data["id"], shiny=enc_data["shiny"],
            level=adjusted_level, ability=enc_data["ability"], gender=enc_data["gender"], growth_rate=enc_data["growth_rate"],
            captured_date=None, tier=enc_data["tier"], individual_id=str(uuid.uuid4()),
            base_stats=enc_data["base_stats"], attacks=enc_data["attacks"], base_experience=enc_data["base_experience"],
            ev=enc_data["ev"], iv=enc_data["iv"], battle_status=enc_data["battle_status"], ev_yield=enc_data["ev_yield"], nature=enc_data["nature"]
        )

        selected_override = None
        if companion_override_id:
            for tc in team_clones:
                if getattr(tc, "individual_id", None) == companion_override_id:
                    if getattr(tc, "hp", 0) <= 0:
                        max_hp_val = getattr(tc, "max_hp", 100)
                        tc.hp = max_hp_val
                        if hasattr(tc, "current_hp"):
                            tc.current_hp = max_hp_val
                    selected_override = tc
                    break
            if selected_override is None:
                try:
                    data = db.get_pokemon(companion_override_id)
                    if data:
                        from ..pyobj.pokemon_obj import PokemonObject
                        pkmn = PokemonObject(**data)
                        max_hp_val = getattr(pkmn, "max_hp", 100)
                        if isinstance(max_hp_val, (int, float)):
                            pkmn.hp = max_hp_val
                            if hasattr(pkmn, "current_hp"):
                                pkmn.current_hp = max_hp_val
                        if hasattr(pkmn, "reset_bonuses"):
                            try:
                                pkmn.reset_bonuses()
                            except Exception:
                                pass
                        selected_override = pkmn
                except Exception:
                    pass
        if selected_override is not None:
            main_pokemon_clone = selected_override
        else:
            main_pokemon_clone = select_best_companion(team_clones, current_enemy_pokemon)

        if main_pokemon_clone is None:
            # No active companion, no override, and no main-Pokemon fallback: without
            # a battler simulate_battle_with_poke_engine(None, ...) raises, is swallowed
            # into enemy.hp = 0, and every review is auto-won for free. Bail out the
            # same way the mode=="all" path does instead of proceeding to the loop.
            return {"success": False, "error": "No active companion or main Pokémon available to battle."}

        mutator_full_reset = 1
        engine_state = None
        
        reviews_list = []
        turns_log = []
        accumulated_evs = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}

        # Read multiplier/boosts settings
        xp_multiplier = 1.0
        choose_moves_penalty = 1.0
        if settings_obj:
            xp_multiplier = settings_obj.get("battle.xp_multiplier", 1.0)
            if settings_obj.get("controls.allow_to_choose_moves", False):
                choose_moves_penalty = 0.5
        lucky_egg_boost = 1.0
        if main_pokemon_clone and getattr(main_pokemon_clone, "held_item", None) == "lucky-egg":
            lucky_egg_boost = 1.5

        chunk_idx = 0
        while chunk_idx < len(all_unresolved):
            chunk = all_unresolved[chunk_idx : chunk_idx + cards_per_round]
            reviews_list.extend(chunk)
            chunk_idx += cards_per_round

            companion_max_hp = getattr(main_pokemon_clone, "max_hp", 100)
            enemy_max_hp = getattr(current_enemy_pokemon, "max_hp", 100)

            # Select moves
            main_attacks = getattr(main_pokemon_clone, "attacks", None)
            if isinstance(main_attacks, (list, tuple)) and len(main_attacks) > 0:
                user_attack = random.choice(main_attacks)
            else:
                user_attack = "splash"

            enemy_attacks_list = getattr(current_enemy_pokemon, "attacks", None)
            if isinstance(enemy_attacks_list, (list, tuple)) and len(enemy_attacks_list) > 0:
                enemy_attack = random.choice(enemy_attacks_list)
            else:
                enemy_attack = "splash"

            points_map = {1: 0, 2: 5, 3: 10, 4: 20}
            total_points = sum(points_map.get(r.get("ease") or 3, 10) for r in chunk)
            max_points = 10.0 * len(chunk)
            turn_multiplier = total_points / max_points if max_points > 0 else 1.0

            orig_multiplier = 1.0
            has_tracker = tracker and hasattr(tracker, "multiplier")
            if has_tracker:
                orig_multiplier = tracker.multiplier
                tracker.multiplier = turn_multiplier

            try:
                results = simulate_battle_with_poke_engine(
                    main_pokemon_clone, current_enemy_pokemon, user_attack, enemy_attack,
                    mutator_full_reset, engine_state
                )
                engine_state, mutator_full_reset = results[1], results[4]
            except Exception:
                current_enemy_pokemon.hp = 0
            finally:
                if has_tracker: tracker.multiplier = orig_multiplier

            comp_hp_val = getattr(main_pokemon_clone, "hp", 0)
            enemy_hp_val = getattr(current_enemy_pokemon, "hp", 0)
            comp_hp_after = max(0, comp_hp_val)
            enemy_hp_after = max(0, enemy_hp_val)

            turns_log.append({
                "user_attack": user_attack.title(),
                "enemy_attack": enemy_attack.title(),
                "comp_hp_pct": int((comp_hp_after * 100) / companion_max_hp),
                "enemy_hp_pct": int((enemy_hp_after * 100) / enemy_max_hp),
            })

            if comp_hp_after <= 0 or enemy_hp_after <= 0:
                break

        # Calculate rewards
        battle_xp = 0
        total_trainer_xp = 0
        gained_cash = 0
        if enemy_hp_after <= 0:
            exp = calc_experience(current_enemy_pokemon.base_experience, current_enemy_pokemon.level)
            try:
                exp = max(1, math.ceil(exp * choose_moves_penalty * lucky_egg_boost * xp_multiplier))
            except TypeError:
                exp = 100
            battle_xp = exp

            from ..pyobj.trainer_card import POKEMON_TIERS
            txp = POKEMON_TIERS.get(current_enemy_pokemon.tier.lower(), 10)
            allow_to_choose_move = settings_obj.get("controls.allow_to_choose_moves") if settings_obj else False
            if allow_to_choose_move: txp *= 0.5
            total_trainer_xp = int(txp)

            if current_enemy_pokemon.ev_yield:
                for sk, v in _normalize_ev_yield(current_enemy_pokemon.ev_yield).items():
                    if sk in accumulated_evs: accumulated_evs[sk] += v

            gained_cash = 0

        from .sprite_functions import get_relative_sprite_path
        last_result_data = {
            "done": False,
            "enemy_name": current_enemy_pokemon.display_name,
            "enemy_id": current_enemy_pokemon.id,
            "enemy_level": current_enemy_pokemon.level,
            "enemy_shiny": current_enemy_pokemon.shiny,
            "enemy_tier": current_enemy_pokemon.tier,
            "enemy_sprite": get_relative_sprite_path(
                current_enemy_pokemon.id,
                current_enemy_pokemon.shiny,
                getattr(current_enemy_pokemon, "gender", "N") or "N",
                current_enemy_pokemon.name,
                "gif"
            ),
            "ease": first_review.get("ease", 3),
            "companion_name": main_pokemon_clone.display_name if main_pokemon_clone else "Companion",
            "companion_level": main_pokemon_clone.level if main_pokemon_clone else 5,
            "companion_sprite": get_relative_sprite_path(main_pokemon_clone.id, main_pokemon_clone.shiny, (getattr(main_pokemon_clone, "gender", "N") or "N"), main_pokemon_clone.name, "gif") if main_pokemon_clone else "",
            "companion_id": getattr(main_pokemon_clone, "individual_id", ""),
            "xp_gained": battle_xp,
            "turns": turns_log,
        }

        # Return state to commit later
        current_pending_outcome = {
            "enemy_pokemon": current_enemy_pokemon,
            "battle_xp": battle_xp,
            "total_xp": battle_xp,
            "accumulated_evs": accumulated_evs,
            "total_trainer_xp": total_trainer_xp,
            "companion_id": getattr(main_pokemon_clone, "individual_id", ""),
            "companion_name": getattr(main_pokemon_clone, "display_name", "Companion"),
            "companion_level": getattr(main_pokemon_clone, "level", 5),
            "review_ids": [r["id"] for r in reviews_list],
            "companion_fainted": (comp_hp_after <= 0),
            "gained_cash": gained_cash,
        }

        pending_total_at_start = len(all_unresolved)
        remaining_reviews = pending_total_at_start - len(reviews_list)
        last_result_data.update({
            "remaining": remaining_reviews,
            "cash_gained": gained_cash,
            "trainer_xp_gained": total_trainer_xp,
        })

        # Re-calculate battle_number properly:
        resolved_count = db.execute("SELECT COUNT(*) FROM pending_mobile_battles WHERE resolved=1").fetchone()[0]
        # Total encounters
        total_resolved_encounters = _compute_encounter_idx(all_reviews, db, settings_obj, tracker, trainer_card, main_pokemon, commit=commit)
        last_result_data["battle_number"] = total_resolved_encounters
        # Total encounters overall
        total_all_count = db.execute("SELECT COUNT(*) FROM pending_mobile_battles").fetchone()[0]
        last_result_data["total_battles"] = math.ceil(total_all_count / cards_per_round)

        return {
            "result": last_result_data,
            "current_pending_outcome": current_pending_outcome
        }

    # Otherwise, mode == "all"
    if reviews is None:
        if limit is not None:
            reviews_rows = db.execute(
                """SELECT id, revlog_id, card_id, ease, review_time, review_type, queued_at
                   FROM pending_mobile_battles
                   WHERE resolved = 0
                   ORDER BY id ASC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
        else:
            reviews_rows = db.execute(
                """SELECT id, revlog_id, card_id, ease, review_time, review_type, queued_at
                   FROM pending_mobile_battles
                   WHERE resolved = 0
                   ORDER BY id ASC"""
            ).fetchall()

        if not reviews_rows:
            if commit:
                return {"success": True, "resolved": 0, "message": "No pending battles.", "done": True}
            else:
                return {
                    "xp": 0, "encounters": 0, "caught": [], "defeated": [],
                    "catches_count": 0, "is_truncated": False, "simulated_reviews": 0,
                    "total_reviews": 0, "cash": 0
                }

        reviews_list = [
            {
                "id": r[0],
                "revlog_id": r[1],
                "card_id": r[2],
                "ease": r[3],
                "review_time": r[4],
                "review_type": r[5],
                "queued_at": r[6],
            }
            for r in reviews_rows
        ]
    else:
        reviews_list = list(reviews)

    if not reviews_list:
        if commit:
            return {"success": True, "resolved": 0, "message": "No pending battles.", "done": True}
        else:
            return {
                "xp": 0, "encounters": 0, "caught": [], "defeated": [],
                "catches_count": 0, "is_truncated": False, "simulated_reviews": 0,
                "total_reviews": 0, "cash": 0
            }



    state = random.getstate()

    cards_per_round, _ = _parse_cards_per_round(settings_obj)

    # Unified deterministic seed for the first encounter using the stable index
    resolved_count = sum(1 for r in all_reviews if r.get("resolved") == 1)
    encounter_idx = _compute_encounter_idx(all_reviews, db, settings_obj, tracker, trainer_card, main_pokemon, commit=commit)
    if all_reviews:
        seed_idx = min(len(all_reviews) - 1, (encounter_idx + 1) * cards_per_round - 1)
        seed_review = all_reviews[seed_idx]
        enc_seed = seed_review.get("revlog_id") or seed_review.get("id") or 42
    else:
        enc_seed = 42
    random.seed(enc_seed)

    auto_battle_setting = 3
    if settings_obj:
        try:
            auto_battle_setting = int(settings_obj.get("battle.automatic_battle", 3))
        except Exception: pass
    if auto_battle_setting == 0: auto_battle_setting = 3

    wishlist = []
    auto_catch_legendary = True
    auto_catch_mythical = True
    auto_catch_ultra = True
    auto_catch_starter = True
    auto_catch_mega = True
    auto_catch_gmax = True
    auto_catch_regional = True
    xp_multiplier = 1.0
    choose_moves_penalty = 1.0

    if settings_obj:
        wishlist = settings_obj.get("battle.auto_catch_wishlist", [])
        auto_catch_legendary = settings_obj.get("battle.auto_catch_legendary", True)
        auto_catch_mythical = settings_obj.get("battle.auto_catch_mythical", True)
        auto_catch_ultra = settings_obj.get("battle.auto_catch_ultra", True)
        auto_catch_starter = settings_obj.get("battle.auto_catch_starter", True)
        auto_catch_mega = settings_obj.get("battle.auto_catch_mega", True)
        auto_catch_gmax = settings_obj.get("battle.auto_catch_gmax", True)
        auto_catch_regional = settings_obj.get("battle.auto_catch_regional", True)
        xp_multiplier = settings_obj.get("battle.xp_multiplier", 1.0)
        if settings_obj.get("controls.allow_to_choose_moves", False):
            choose_moves_penalty = 0.5

    lucky_egg_boost = 1.0
    if main_pokemon and getattr(main_pokemon, "held_item", None) == "lucky-egg":
        lucky_egg_boost = 1.5

    from ..utils import load_collected_pokemon_ids
    collected_ids = set(load_collected_pokemon_ids())

    from .encounter_functions import (
        generate_random_pokemon,
        save_caught_pokemon,
        save_main_pokemon_progress
    )
    from .encounter_data import MEGA, GMAX, REGIONAL_FORM_REGION
    from ..business import calc_experience, calculate_cp_from_dict
    from ..pyobj.pokemon_obj import PokemonObject
    from ..singletons import get_evo_window

    initial_reviews = _compute_initial_reviews(
        db,
        tracker,
        day_cutoff
    )
    temp_tracker = TempTracker(initial_reviews + resolved_count)
    team_clones = load_active_team_clones(db, settings_obj, main_pokemon)
    main_pokemon_clone = team_clones[0] if team_clones else None
    if not main_pokemon_clone:
        # No active companion and no main-Pokemon fallback: without a battler the
        # engine would call simulate_battle_with_poke_engine(None, ...), which
        # raises and is swallowed into enemy.hp = 0 while companion hp defaults to
        # 100 (getattr(None, "hp", 100)) -> the player auto-wins/catches every
        # review for free. Bail out with a benign empty result instead.
        if commit:
            return {"success": False, "error": "No active companion or main Pokémon available to battle."}
        else:
            return {
                "xp": 0, "encounters": 0, "caught": [], "defeated": [],
                "catches_count": 0, "is_truncated": False, "simulated_reviews": 0,
                "total_reviews": 0, "cash": 0
            }
    stable_max_level = _get_team_max_level(team_clones, db, settings_obj, main_pokemon)
    
    # Calculate active_max_level (max level of active team clones only)
    active_levels = []
    for c in team_clones:
        lvl = getattr(c, "level", None)
        if lvl is not None and isinstance(lvl, (int, float)):
            active_levels.append(int(lvl))
    if active_levels:
        active_max_level = max(active_levels)
    elif main_pokemon:
        active_max_level = int(getattr(main_pokemon, "level", 5))
    else:
        active_max_level = 5

    total_xp = 0
    total_trainer_xp = 0
    caught_count = 0
    caught_pokemon_list = []
    cards_battle_round = 0
    accumulated_evs = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
    defeated_encounters = []
    current_turn_reviews = []
    total_reviews_processed = 0
    current_battle_cash = 0
    ci = int(settings_obj.get("trainer.cash_reward_interval", 5)) if settings_obj else 5
    ca = int(settings_obj.get("trainer.cash_reward_amount", 10)) if settings_obj else 10
    # Amulet Coin / Lucky Incense — see battle_loop.py's identical check for
    # why both apply the same doubling here.
    if getattr(main_pokemon, "held_item", None) in ("amulet-coin", "luck-incense"):
        ca *= 2
    # Seed the per-encounter cash counter from the SAME persisted carryover the
    # final wallet credit uses (trainer.mobile_reviews_resolved_since_payout),
    # so the sum of history entries' cash_gained matches trainer.cash actually
    # credited below. Without this, cash payouts are counted from 0 each run and
    # the Mobile Battle History screen misattributes where the cash came from.
    payout_start = int(settings_obj.get("trainer.mobile_reviews_resolved_since_payout", 0)) if settings_obj else 0

    from datetime import date
    today_str = str(date.today())
    last_reward_date = settings_obj.get("trainer.last_mobile_cash_reward_date", "") if settings_obj else ""
    mobile_cash_earned_today = settings_obj.get("trainer.mobile_cash_earned_today", 0) if settings_obj else 0

    if last_reward_date != today_str:
        mobile_cash_earned_today = 0
        if settings_obj:
            settings_obj.set("trainer.last_mobile_cash_reward_date", today_str)
            settings_obj.set("trainer.mobile_cash_earned_today", 0)

    accumulated_cash_earned_this_batch = 0

    from .. import utils
    orig_load_ids = utils.load_collected_pokemon_ids
    utils.load_collected_pokemon_ids = lambda: collected_ids

    current_enemy_pokemon = None
    mutator_full_reset = 1
    engine_state = None
    from .ankimon_hooks_to_poke_engine import simulate_battle_with_poke_engine

    history_entries_to_add = []
    encounters_fought = 0
    reviews_spent_for_resolved = 0
    resolved_encounters = 0
    current_encounter_reviews = 0

    caught_pokemon = []
    defeated_pokemon = []
    if not commit:
        reviews_to_process = reviews_list[:100]
        extra_reviews = reviews_list[100:]
    else:
        reviews_to_process = reviews_list
        extra_reviews = []

    # GIL yield for background preview sims (Bug 4). The mobile tab's estimates
    # (getMobileStatus.run_sim -> simulate_pending_mobile_battles) and the manual
    # replay "next" preview both run this CPU-bound loop on a QueryOp background
    # thread with NO progress_callback; the pure-Python engine work holds the GIL
    # and starves the Qt GUI, making the tab and replay transitions feel sluggish.
    # Hand the GIL to the GUI periodically. Gated so it only fires on a real
    # background thread (never the synchronous post-sync auto-resolve, which runs
    # on the GUI thread) and only when nobody else is already throttling via a
    # progress_callback (the bulk-resolve worker does its own yield). is_main_thread
    # returns True headless, so the Tier-1 harness / tests are unaffected.
    from ..utils import is_main_thread
    _yield_bg = (progress_callback is None) and (not is_main_thread())
    _last_yield = time.monotonic()

    try:
        for review in reviews_to_process:
            temp_tracker.total_reviews += 1
            total_reviews_processed += 1
            if _yield_bg:
                _now = time.monotonic()
                if _now - _last_yield >= 0.02:
                    time.sleep(0.003)
                    _last_yield = time.monotonic()
            if progress_callback:
                try:
                    cb_res = progress_callback({
                        "processed": total_reviews_processed,
                        "total": len(reviews_to_process),
                        "resolved": resolved_encounters,
                        "catches": caught_count,
                        "xp_gained": total_xp
                    })
                    if cb_res is False:
                        break
                except TypeError:
                    try:
                        cb_res = progress_callback(total_reviews_processed, len(reviews_to_process))
                        if cb_res is False:
                            break
                    except Exception:
                        pass
                except Exception:
                    pass
            if commit and ci > 0 and (payout_start + total_reviews_processed) % ci == 0:
                if mobile_cash_earned_today + accumulated_cash_earned_this_batch < 400:
                    allowed = min(ca, 400 - (mobile_cash_earned_today + accumulated_cash_earned_this_batch))
                    current_battle_cash += allowed
                    accumulated_cash_earned_this_batch += allowed
            cards_battle_round += 1
            current_turn_reviews.append(review)
            
            if current_enemy_pokemon is not None:
                current_encounter_reviews += 1

            if cards_battle_round >= cards_per_round or review == reviews_to_process[-1]:
                cards_battle_round = 0

                if current_enemy_pokemon is None:
                    encounters_fought += 1
                    current_encounter_reviews = len(current_turn_reviews)
                    
                    # Stable seeding based on encounter index
                    if all_reviews:
                        seed_idx = min(len(all_reviews) - 1, (encounter_idx + 1) * cards_per_round - 1)
                        seed_review = all_reviews[seed_idx]
                        enc_seed = seed_review.get("revlog_id") or seed_review.get("id") or 42
                    else:
                        enc_seed = 42
                    random.seed(enc_seed)
                    encounter_idx += 1
                    
                    enc_data = _generate_encounter(stable_max_level, temp_tracker, collected_ids, settings_obj, None)
                    adjusted_level = max(1, active_max_level + (enc_data["level"] - stable_max_level))
                    current_enemy_pokemon = PokemonObject(
                        type=enc_data["type"], name=enc_data["name"], id=enc_data["id"], shiny=enc_data["shiny"],
                        level=adjusted_level, ability=enc_data["ability"], gender=enc_data["gender"], growth_rate=enc_data["growth_rate"],
                        captured_date=None, tier=enc_data["tier"], individual_id=str(uuid.uuid4()),
                        base_stats=enc_data["base_stats"], attacks=enc_data["attacks"], base_experience=enc_data["base_experience"],
                        ev=enc_data["ev"], iv=enc_data["iv"], battle_status=enc_data["battle_status"], ev_yield=enc_data["ev_yield"], nature=enc_data["nature"]
                    )
                    main_pokemon_clone = select_best_companion(team_clones, current_enemy_pokemon)
                    mutator_full_reset = 1
                    engine_state = None

                # Turn simulation
                main_attacks = getattr(main_pokemon_clone, "attacks", None)
                if isinstance(main_attacks, (list, tuple)) and len(main_attacks) > 0:
                    user_attack = random.choice(main_attacks)
                else:
                    user_attack = "splash"

                enemy_attacks_list = getattr(current_enemy_pokemon, "attacks", None)
                if isinstance(enemy_attacks_list, (list, tuple)) and len(enemy_attacks_list) > 0:
                    enemy_attack = random.choice(enemy_attacks_list)
                else:
                    enemy_attack = "splash"

                points_map = {1: 0, 2: 5, 3: 10, 4: 20}
                total_points = sum(points_map.get(r.get("ease") or 3, 10) for r in current_turn_reviews)
                max_points = 10.0 * len(current_turn_reviews)
                turn_multiplier = total_points / max_points if max_points > 0 else 1.0

                orig_multiplier = 1.0
                has_tracker = tracker and hasattr(tracker, "multiplier")
                if has_tracker:
                    orig_multiplier = tracker.multiplier
                    tracker.multiplier = turn_multiplier

                try:
                    results = simulate_battle_with_poke_engine(
                        main_pokemon_clone, current_enemy_pokemon, user_attack, enemy_attack,
                        mutator_full_reset, engine_state
                    )
                    engine_state, mutator_full_reset = results[1], results[4]
                except Exception:
                    current_enemy_pokemon.hp = 0
                finally:
                    if has_tracker: tracker.multiplier = orig_multiplier
                    current_turn_reviews = []

                enemy_hp = getattr(current_enemy_pokemon, "hp", 100)
                companion_hp = getattr(main_pokemon_clone, "hp", 100)

                if isinstance(enemy_hp, (int, float)) and enemy_hp <= 0:
                    is_mega = current_enemy_pokemon.id in MEGA
                    is_gmax = current_enemy_pokemon.id in GMAX
                    is_regional = current_enemy_pokemon.id in REGIONAL_FORM_REGION
                    should_catch_always = (
                        (current_enemy_pokemon.tier == "Legendary" and auto_catch_legendary)
                        or (current_enemy_pokemon.tier == "Mythical" and auto_catch_mythical)
                        or (current_enemy_pokemon.tier == "Ultra" and auto_catch_ultra)
                        or (current_enemy_pokemon.tier == "Starter" and auto_catch_starter)
                        or (is_mega and auto_catch_mega)
                        or (is_gmax and auto_catch_gmax)
                        or (is_regional and auto_catch_regional)
                        or (current_enemy_pokemon.id in wishlist)
                    )

                    caught = False
                    if auto_battle_setting == 1: caught = True
                    elif auto_battle_setting == 2: caught = (current_enemy_pokemon.shiny or should_catch_always)
                    elif auto_battle_setting == 3:
                        caught = (current_enemy_pokemon.id not in collected_ids or current_enemy_pokemon.shiny or should_catch_always)
                    
                    if caught:
                        collected_ids.add(current_enemy_pokemon.id)

                    enemy_dict = current_enemy_pokemon.to_dict()
                    enemy_dict.update({
                        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
                    })
                    cp_val = calculate_cp_from_dict(enemy_dict)

                    pkmn_info = {
                        "name": str(current_enemy_pokemon.display_name),
                        "id": int(current_enemy_pokemon.id),
                        "level": int(current_enemy_pokemon.level),
                        "shiny": bool(current_enemy_pokemon.shiny),
                        "tier": str(current_enemy_pokemon.tier),
                        "xp": 0,
                        "cp": cp_val
                    }

                    if caught:
                        if commit:
                            from ..services import services
                            capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            current_enemy_pokemon.captured_date = capture_time
                            save_caught_pokemon(current_enemy_pokemon, nickname=None, achievements=services.achievements)
                            try:
                                from ..reviewer_ui import _collected_pokemon_ids
                                if isinstance(_collected_pokemon_ids, set): _collected_pokemon_ids.add(current_enemy_pokemon.id)
                            except Exception: pass
                            caught_pokemon_list.append(pkmn_info)
                            caught_count += 1  # else resolveAll/the UI report "0 caught"
                            last_outcome = "caught"
                        else:
                            caught_pokemon.append(pkmn_info)
                    else:
                        exp = calc_experience(current_enemy_pokemon.base_experience, current_enemy_pokemon.level)
                        try:
                            exp = max(1, math.ceil(exp * choose_moves_penalty * lucky_egg_boost * xp_multiplier))
                        except TypeError:
                            exp = 100
                        battle_xp = exp
                        pkmn_info["xp"] = exp

                        if commit:
                            total_xp += exp
                            defeated_encounters.append({"tier": current_enemy_pokemon.tier})
                            if current_enemy_pokemon.ev_yield:
                                for sk, v in _normalize_ev_yield(current_enemy_pokemon.ev_yield).items():
                                    if sk in accumulated_evs: accumulated_evs[sk] += v
                            last_outcome = "defeated"
                        else:
                            total_xp += exp
                            defeated_pokemon.append(pkmn_info)

                    if commit:
                        # Insert history for caught or defeated
                        try:
                            from ..pyobj.trainer_card import POKEMON_TIERS
                            txp = POKEMON_TIERS.get(current_enemy_pokemon.tier.lower(), 10)
                            allow_to_choose_move = settings_obj.get("controls.allow_to_choose_moves") if settings_obj else False
                            if allow_to_choose_move: txp *= 0.5
                            txp = int(txp) if last_outcome == "defeated" else 0
                            history_entries_to_add.append({
                                "timestamp": int(time.time() * 1000),
                                "enemy_id": current_enemy_pokemon.id,
                                "enemy_name": current_enemy_pokemon.display_name,
                                "enemy_level": current_enemy_pokemon.level,
                                "enemy_shiny": current_enemy_pokemon.shiny,
                                "companion_name": main_pokemon_clone.display_name if main_pokemon_clone else None,
                                "companion_level": main_pokemon_clone.level if main_pokemon_clone else None,
                                "companion_id": main_pokemon_clone.individual_id if main_pokemon_clone else None,
                                "ev_yield": current_enemy_pokemon.ev_yield.copy() if (last_outcome == "defeated" and current_enemy_pokemon and getattr(current_enemy_pokemon, "ev_yield", None)) else {},
                                "outcome": last_outcome,
                                "xp_gained": battle_xp if last_outcome == "defeated" else 0,
                                "trainer_xp_gained": txp,
                                "cash_gained": current_battle_cash,
                            })
                        except Exception as ex:
                            if logger:
                                logger.log("error", f"Failed to record auto-resolve history: {ex}")
                        current_battle_cash = 0

                    reviews_spent_for_resolved += current_encounter_reviews
                    resolved_encounters += 1
                    current_enemy_pokemon = None
                    for c in team_clones:
                        _heal_to_full(c)

                elif isinstance(companion_hp, (int, float)) and companion_hp <= 0:
                    if commit:
                        # Insert history for loss
                        try:
                            history_entries_to_add.append({
                                "timestamp": int(time.time() * 1000),
                                "enemy_id": current_enemy_pokemon.id,
                                "enemy_name": current_enemy_pokemon.display_name,
                                "enemy_level": current_enemy_pokemon.level,
                                "enemy_shiny": current_enemy_pokemon.shiny,
                                "companion_name": main_pokemon_clone.display_name if main_pokemon_clone else None,
                                "companion_level": main_pokemon_clone.level if main_pokemon_clone else None,
                                "companion_id": main_pokemon_clone.individual_id if main_pokemon_clone else None,
                                "outcome": "lost",
                                "xp_gained": 0,
                                "trainer_xp_gained": 0,
                                "cash_gained": current_battle_cash,
                            })
                        except Exception as ex:
                            if logger:
                                logger.log("error", f"Failed to record auto-resolve loss history: {ex}")
                        current_battle_cash = 0

                    reviews_spent_for_resolved += current_encounter_reviews
                    resolved_encounters += 1
                    current_enemy_pokemon = None
                    for c in team_clones:
                        _heal_to_full(c)
        
        if commit and current_enemy_pokemon is not None:
            # Insert history for escaped / unfinished battle
            try:
                history_entries_to_add.append({
                    "timestamp": int(time.time() * 1000),
                    "enemy_id": current_enemy_pokemon.id,
                    "enemy_name": current_enemy_pokemon.display_name,
                    "enemy_level": current_enemy_pokemon.level,
                    "enemy_shiny": current_enemy_pokemon.shiny,
                    "companion_name": main_pokemon_clone.display_name if main_pokemon_clone else None,
                    "companion_level": main_pokemon_clone.level if main_pokemon_clone else None,
                    "companion_id": main_pokemon_clone.individual_id if main_pokemon_clone else None,
                    "outcome": "escaped",
                    "xp_gained": 0,
                    "trainer_xp_gained": 0,
                    "cash_gained": current_battle_cash,
                })
            except Exception as ex:
                if logger:
                    logger.log("error", f"Failed to record auto-resolve escape history: {ex}")
    finally:
        utils.load_collected_pokemon_ids = orig_load_ids

    if commit:
        if history_entries_to_add:
            try:
                if hasattr(db, "add_mobile_history_entries_batch"):
                    db.add_mobile_history_entries_batch(history_entries_to_add)
                else:
                    for entry in history_entries_to_add:
                        db.add_mobile_history_entry(entry)
            except Exception as ex:
                if logger:
                    logger.log("error", f"Failed to record batch auto-resolve history: {ex}")
        random.setstate(state)

        companion_xp = {}
        companion_evs = {}
        companion_battle_count = {}
        for entry in history_entries_to_add:
            cid = entry.get("companion_id")
            if not cid:
                continue
            xp_g = entry.get("xp_gained", 0)
            companion_xp[cid] = companion_xp.get(cid, 0) + xp_g
            
            if entry.get("outcome") in ("defeated", "caught"):
                companion_battle_count[cid] = companion_battle_count.get(cid, 0) + 1
            
            if cid not in companion_evs:
                companion_evs[cid] = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
            ev_yield = entry.get("ev_yield", {})
            for sk, v in _normalize_ev_yield(ev_yield).items():
                if sk in companion_evs[cid]:
                    companion_evs[cid][sk] += v

        # XP-Share (desktop parity): mirror encounter_functions.kill_pokemon —
        # the battling companion keeps half of its battle XP and the configured
        # trainer.xp_share target receives the other half. Without this the
        # winning companion took 100% and the share target got nothing on every
        # mobile-resolved battle. The share is applied through the same mobile
        # attribution path used for teammates (below), so bulk-resolve UI
        # suppression / thread serialisation still hold — the mobile path does
        # not open the evolution window for companions, so we intentionally do
        # not route through the desktop evo-triggering xp_share_gain_exp here.
        xp_share_pending = {}  # target_id -> accumulated xp across all companions
        for cid, earned_xp in companion_xp.items():
            evs_gained = companion_evs.get(cid, {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})
            battles_fought = companion_battle_count.get(cid, 0)
            grant_xp, share_targets = _xp_share_split(earned_xp, cid, settings_obj, db=db)
            for target_id, amount in share_targets.items():
                xp_share_pending[target_id] = xp_share_pending.get(target_id, 0) + amount
            if grant_xp > 0 or any(evs_gained.values()) or battles_fought > 0:
                if main_pokemon and cid == main_pokemon.individual_id:
                    class DummyEnemy:
                        def __init__(self, ev_yield): self.ev_yield = ev_yield
                    from ..services import services
                    save_main_pokemon_progress(
                        main_pokemon, DummyEnemy(evs_gained), grant_xp,
                        services.achievements, logger, get_evo_window()
                    )
                    # Apply additional battles fought to main_pokemon.pokemon_defeated
                    if battles_fought > 1:
                        extra = battles_fought - 1
                        main_pokemon.pokemon_defeated += extra
                        try:
                            mp_data = db.get_main_pokemon()
                            if mp_data:
                                mp_data["pokemon_defeated"] = main_pokemon.pokemon_defeated
                                db.save_main_pokemon(mp_data)
                        except Exception:
                            pass
                else:
                    _attribute_xp_and_evs_to_companion(cid, grant_xp, evs_gained, settings_obj, battles_fought=battles_fought, db=db, logger=logger)

        # Grant the accumulated XP-Share amount to each target Pokémon.
        for target_id, pending_amount in xp_share_pending.items():
            if pending_amount > 0:
                _attribute_xp_and_evs_to_companion(
                    str(target_id), pending_amount,
                    {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
                    settings_obj, battles_fought=0, db=db, logger=logger
                )

        total_trainer_xp = 0
        from ..pyobj.trainer_card import POKEMON_TIERS
        allow_to_choose_move = settings_obj.get("controls.allow_to_choose_moves") if settings_obj else False
        for enc in defeated_encounters:
            txp = POKEMON_TIERS.get(enc.get("tier", "normal").lower(), 10)
            if allow_to_choose_move: txp *= 0.5
            total_trainer_xp += txp

        if total_trainer_xp > 0 and trainer_card:
            new_txp = int(settings_obj.get("trainer.xp", 0) + total_trainer_xp)
            settings_obj.set("trainer.xp", new_txp)
            settings_obj.set("trainer.total_xp", int(settings_obj.get("trainer.total_xp", 0) + total_trainer_xp))
            trainer_card.xp = new_txp
            trainer_card.total_xp = settings_obj.get("trainer.total_xp")
            trainer_card.check_level_up()

        # Cash Reward — credited off the same carryover the per-encounter
        # history counter (payout_start) was seeded from, so the wallet credit
        # and the sum of history cash_gained agree.
        total_reviews_resolved = total_reviews_processed
        current_counter = payout_start
        new_counter = current_counter + total_reviews_resolved
        
        # Clamp to >=1: a raw config can hold 0 (the get() default only applies
        # when the key is absent, not when it is 0), which would crash the modulo
        # below with ZeroDivisionError. Matches commit_replay_outcome's clamp.
        ci = max(1, int(settings_obj.get("trainer.cash_reward_interval", 5))) if settings_obj else 5
        ca = int(settings_obj.get("trainer.cash_reward_amount", 10)) if settings_obj else 10

        gained_cash = accumulated_cash_earned_this_batch
        remaining_counter = new_counter % ci
        if settings_obj:
            settings_obj.set("trainer.mobile_reviews_resolved_since_payout", remaining_counter)
            if gained_cash > 0:
                settings_obj.set("trainer.mobile_cash_earned_today", mobile_cash_earned_today + gained_cash)
                settings_obj.set("trainer.cash", int(settings_obj.get("trainer.cash", 0) + gained_cash))
        if trainer_card and settings_obj:
            trainer_card.cash = settings_obj.get("trainer.cash")

        # Mark resolved
        res_ids = [r["id"] for r in reviews_list[:total_reviews_processed]]
        for rid in res_ids:
            db.mark_mobile_battle_resolved(rid)

        if encounters_fought > 0:
            try:
                cursor = db.execute("SELECT value FROM metadata WHERE key = 'mobile_resolved_encounters_count'")
                row = cursor.fetchone()
                if row is not None:
                    new_count = int(row[0]) + encounters_fought
                else:
                    cursor = db.execute("SELECT COUNT(*) FROM pending_mobile_battles WHERE resolved = 1")
                    resolved_reviews = cursor.fetchone()[0]
                    cards_per_round, _ = _parse_cards_per_round(settings_obj)
                    new_count = resolved_reviews // cards_per_round
                
                with db._get_connection():
                    db._get_connection().execute(
                        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('mobile_resolved_encounters_count', ?)",
                        (str(new_count),)
                    )
            except Exception:
                pass

        remaining = db.get_pending_mobile_count()
        from ..menu_buttons import update_mobile_badge
        update_mobile_badge(remaining)

        try:
            from ..events import events
            events.emit("stats_changed")
            from ..singletons import notify_stats_changed
            notify_stats_changed()
        except Exception: pass
        return {
            "success": True, "resolved": encounters_fought, "xp_gained": total_xp,
            "catches": caught_count, "cash_gained": gained_cash,
            "trainer_xp_gained": total_trainer_xp, "caught_list": caught_pokemon_list,
            "reviews_processed": total_reviews_processed,
        }
    else:
        # Estimate extrapolation
        avg_reviews_per_encounter = cards_per_round
        if resolved_encounters > 0:
            avg_reviews_per_encounter = reviews_spent_for_resolved / resolved_encounters

        extra_caught_count = 0
        if extra_reviews:
            extra_reviews_count = len(extra_reviews)
            extra_encounters = int(extra_reviews_count / avg_reviews_per_encounter)

            if extra_encounters > 0:
                encounters_count = resolved_encounters + extra_encounters
                
                # Estimate defeated and caught ratio from simulated pool
                defeated_ratio = 0.8
                caught_ratio = 0.2
                total_encs = len(caught_pokemon) + len(defeated_pokemon)
                if total_encs > 0:
                    defeated_ratio = len(defeated_pokemon) / total_encs
                    caught_ratio = len(caught_pokemon) / total_encs

                extra_defeated = extra_encounters * defeated_ratio
                extra_caught_count = int(extra_encounters * caught_ratio)

                est_exp = calc_experience(130, active_max_level)
                try:
                    est_exp = max(1, math.ceil(est_exp * choose_moves_penalty * lucky_egg_boost * xp_multiplier))
                except TypeError:
                    est_exp = 100
                total_xp += int(extra_defeated * est_exp)
            else:
                encounters_count = resolved_encounters
        else:
            encounters_count = resolved_encounters

        # Estimate trainer cash reward based on settings. Clamp to >=1: a raw
        # config value of 0 would crash the floor-division below (the get()
        # default only applies to an absent key, not a stored 0).
        cash_interval = max(1, int(settings_obj.get("trainer.cash_reward_interval", 5))) if settings_obj else 5
        cash_amount = int(settings_obj.get("trainer.cash_reward_amount", 10)) if settings_obj else 10
        total_reviews_count = len(reviews_list)
        cash_gained = (total_reviews_count // cash_interval) * cash_amount

        # Restore random state
        random.setstate(state)

        return {
            "xp": total_xp,
            "encounters": encounters_count,
            "caught": caught_pokemon,
            "defeated": defeated_pokemon,
            "catches_count": len(caught_pokemon) + extra_caught_count,
            "is_truncated": len(extra_reviews) > 0,  # True if >100 reviews, extrapolated
            "simulated_reviews": len(reviews_to_process),
            "total_reviews": len(reviews_list),
            "cash": cash_gained
        }


def estimate_pending_battles(pending_reviews: list[dict], main_pokemon, settings_obj, trainer_card, ankimon_tracker_obj, ankimon_db=None) -> dict:
    return run_mobile_battles(
        reviews=pending_reviews,
        commit=False,
        db=ankimon_db,
        settings_obj=settings_obj,
        tracker=ankimon_tracker_obj,
        trainer_card=trainer_card,
        main_pokemon=main_pokemon
    )


# Alias for backwards compatibility
simulate_pending_mobile_battles = estimate_pending_battles


def resolve_all(db, settings_obj, tracker, trainer_card, main_pokemon, logger=None, day_cutoff=0, limit=None, progress_callback=None) -> dict:
    return _resolve_internal(
        mode="all",
        companion_id="",
        limit=limit,
        db=db,
        settings_obj=settings_obj,
        tracker=tracker,
        trainer_card=trainer_card,
        main_pokemon=main_pokemon,
        logger=logger,
        day_cutoff=day_cutoff,
        progress_callback=progress_callback
    )


def resolve_next(companion_id: str, db, settings_obj, tracker, trainer_card, main_pokemon, logger=None, day_cutoff=0) -> dict:
    return _resolve_internal(
        mode="next",
        companion_id=companion_id,
        limit=None,
        db=db,
        settings_obj=settings_obj,
        tracker=tracker,
        trainer_card=trainer_card,
        main_pokemon=main_pokemon,
        logger=logger,
        day_cutoff=day_cutoff
    )


def commit_replay_outcome(choice: str, outcome_data: dict, db, settings_obj, trainer_card, main_pokemon, achievements_dict=None, logger=None) -> dict:
    try:
        if not outcome_data:
            return {"success": False, "error": "No pending battle to resolve."}

        enemy_pokemon = outcome_data["enemy_pokemon"]
        battle_xp = outcome_data["battle_xp"]
        total_xp = outcome_data["total_xp"]
        accumulated_evs = outcome_data["accumulated_evs"]
        total_trainer_xp = outcome_data["total_trainer_xp"]

        now_ms = int(time.time() * 1000)
        review_ids = outcome_data.get("review_ids", [])

        # 1. Pre-calculate values for immediate return
        gained_cash = 0
        remaining_counter = 0
        if review_ids and settings_obj:
            total_reviews_resolved = len(review_ids)
            current_counter = int(settings_obj.get("trainer.mobile_reviews_resolved_since_payout", 0))
            new_counter = current_counter + total_reviews_resolved
            
            # Clamp to >=1: an explicitly-stored 0 survives the default and would
            # crash `new_counter // ci` / `% ci` below (the UI clamps to >=5, but
            # a hand-edited config can reach 0). Mirrors the ci > 0 guard in the
            # auto-resolve path.
            ci = max(1, int(settings_obj.get("trainer.cash_reward_interval", 5)))
            ca = int(settings_obj.get("trainer.cash_reward_amount", 10))
            if getattr(main_pokemon, "held_item", None) in ("amulet-coin", "luck-incense"):
                ca *= 2
            
            raw_gained_cash = (new_counter // ci) * ca
            remaining_counter = new_counter % ci

            # Enforce daily mobile cash cap
            from datetime import date
            today_str = str(date.today())
            last_reward_date = settings_obj.get("trainer.last_mobile_cash_reward_date", "")
            mobile_cash_earned_today = settings_obj.get("trainer.mobile_cash_earned_today", 0)

            if last_reward_date != today_str:
                mobile_cash_earned_today = 0
                settings_obj.set("trainer.last_mobile_cash_reward_date", today_str)
                settings_obj.set("trainer.mobile_cash_earned_today", 0)

            if mobile_cash_earned_today < 400:
                gained_cash = min(raw_gained_cash, 400 - mobile_cash_earned_today)
            else:
                gained_cash = 0

        # Calculate CP for the return value
        from ..business import calculate_cp_from_dict
        enemy_dict = enemy_pokemon.to_dict()
        enemy_dict.update({
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        })
        cp_val = calculate_cp_from_dict(enemy_dict)

        # Pre-calculate remaining count
        current_pending = db.get_pending_mobile_count()
        remaining = max(0, current_pending - len(review_ids))

        # We will split the DB operations (heavy) and the GUI updates.
        # Run DB operations in the background thread:
        def do_db_work(col):
            nonlocal battle_xp
            # Set in_bulk_resolve to avoid tooltips/dialogs in background thread
            from .. import utils
            orig_in_bulk = getattr(utils, "in_bulk_resolve", False)
            utils.in_bulk_resolve = True

            # Serialize against run_mobile_battles (auto-resolve): both mutate the
            # pending_mobile_battles queue and companion rows, and this body runs
            # in a QueryOp background thread. Sharing _mobile_sync_lock guarantees
            # a concurrent auto-resolve can't double-resolve reviews or race writes.
            _mobile_sync_lock.acquire()
            try:
                # 1. Catch logic
                if choice == "catch":
                    from .encounter_functions import save_caught_pokemon
                    capture_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    enemy_pokemon.captured_date = capture_time
                    save_caught_pokemon(enemy_pokemon, nickname=None, achievements=achievements_dict)
                    battle_xp = 0
                
                # 2. Defeat logic
                elif choice == "defeat":
                    companion_id = outcome_data.get("companion_id", "")
                    if not companion_id and main_pokemon:
                        companion_id = getattr(main_pokemon, "individual_id", "")

                    # XP-Share (desktop parity): the manual replay-resolve path
                    # must split the battling companion's XP 50/50 with the
                    # configured trainer.xp_share target too, exactly like the
                    # bulk-resolve commit block above and desktop
                    # encounter_functions.kill_pokemon. Without this, XP-Share is
                    # still 100% bypassed for every single-battle replay defeat
                    # (total_xp here is the full, un-split battle XP built in the
                    # replay prep). Runs on this QueryOp background thread under
                    # _mobile_sync_lock via the mobile attribution path, so no
                    # evo-window / tooltip Qt work happens here.
                    grant_xp, share_targets = (
                        _xp_share_split(total_xp, companion_id, settings_obj, db=db)
                        if companion_id else (total_xp, {})
                    )

                    if companion_id and (grant_xp > 0 or any(accumulated_evs.values())):
                        _attribute_xp_and_evs_to_companion(companion_id, grant_xp, accumulated_evs, settings_obj, db=db, logger=logger)
                    if companion_id:
                        for target_id, amount in share_targets.items():
                            if amount > 0:
                                _attribute_xp_and_evs_to_companion(
                                    str(target_id), amount,
                                    {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
                                    settings_obj, battles_fought=0, db=db, logger=logger
                                )

                # 3. Mark resolved in DB
                if review_ids:
                    placeholders = ",".join("?" for _ in review_ids)
                    try:
                        cursor = db.execute(
                            f"SELECT revlog_id FROM pending_mobile_battles WHERE id IN ({placeholders})",
                            list(review_ids)
                        )
                        revlog_ids = [r[0] for r in cursor.fetchall() if r[0]]
                    except Exception:
                        revlog_ids = []

                    with db._get_connection():
                        db._get_connection().execute(
                            f"UPDATE pending_mobile_battles SET resolved=1, resolved_at=? WHERE id IN ({placeholders})",
                            [now_ms] + list(review_ids)
                        )
                    
                    if revlog_ids:
                        db.sync_resolutions_to_other_db(revlog_ids, now_ms)

                    try:
                        cursor = db.execute("SELECT value FROM metadata WHERE key = 'mobile_resolved_encounters_count'")
                        row = cursor.fetchone()
                        if row is not None:
                            new_count_meta = int(row[0]) + 1
                        else:
                            cursor = db.execute("SELECT COUNT(*) FROM pending_mobile_battles WHERE resolved = 1")
                            resolved_reviews = cursor.fetchone()[0]
                            cards_per_round, _ = _parse_cards_per_round(settings_obj)
                            new_count_meta = resolved_reviews // cards_per_round
                        
                        with db._get_connection():
                            db._get_connection().execute(
                                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('mobile_resolved_encounters_count', ?)",
                                (str(new_count_meta),)
                            )
                    except Exception:
                        pass

                # 4. Save to mobile history
                try:
                    comp_name = outcome_data.get("companion_name")
                    comp_level = outcome_data.get("companion_level")
                    if not comp_name:
                        comp_name = "Companion"
                        comp_level = 5
                        active_comp = None
                        if choice == "defeat" and main_pokemon:
                            active_comp = main_pokemon
                        
                        if active_comp:
                            comp_name = getattr(active_comp, "display_name", "Companion")
                            comp_level = getattr(active_comp, "level", 5)

                    outcome_val = "caught" if choice == "catch" else "defeated"
                    if outcome_data.get("companion_fainted", False):
                        outcome_val = "lost"

                    db.add_mobile_history_entry({
                        "timestamp": now_ms,
                        "enemy_id": enemy_pokemon.id,
                        "enemy_name": enemy_pokemon.display_name,
                        "enemy_level": enemy_pokemon.level,
                        "enemy_shiny": enemy_pokemon.shiny,
                        "companion_name": comp_name,
                        "companion_level": comp_level,
                        "outcome": outcome_val,
                        "xp_gained": battle_xp if outcome_val == "defeated" else 0,
                        "trainer_xp_gained": total_trainer_xp if outcome_val == "defeated" else 0,
                        "cash_gained": gained_cash,
                    })
                except Exception as ex:
                    if logger:
                        logger.log("error", f"Failed to record manual mobile battle history: {ex}")

            finally:
                utils.in_bulk_resolve = orig_in_bulk
                _mobile_sync_lock.release()

        def on_db_work_done(dummy_res):
            try:
                # Update mobile reviews payout counter settings
                if review_ids and settings_obj:
                    settings_obj.set("trainer.mobile_reviews_resolved_since_payout", remaining_counter)
                    if gained_cash > 0:
                        settings_obj.set("trainer.mobile_cash_earned_today", settings_obj.get("trainer.mobile_cash_earned_today", 0) + gained_cash)
                        settings_obj.set("trainer.cash", int(settings_obj.get("trainer.cash", 0) + gained_cash))
                        if trainer_card:
                            trainer_card.cash = settings_obj.get("trainer.cash")

                if choice == "catch":
                    try:
                        from ..reviewer_ui import _collected_pokemon_ids
                        if isinstance(_collected_pokemon_ids, set):
                            _collected_pokemon_ids.add(enemy_pokemon.id)
                    except Exception: pass
                elif choice == "defeat":
                    if total_trainer_xp > 0 and trainer_card:
                        new_txp = int(settings_obj.get("trainer.xp", 0) + total_trainer_xp)
                        settings_obj.set("trainer.xp", new_txp)
                        settings_obj.set("trainer.total_xp", int(settings_obj.get("trainer.total_xp", 0) + total_trainer_xp))
                        trainer_card.xp = new_txp
                        trainer_card.total_xp = settings_obj.get("trainer.total_xp")
                        trainer_card.check_level_up()

                # Update mobile badge
                remaining_real = db.get_pending_mobile_count()
                try:
                    from ..menu_buttons import update_mobile_badge
                    update_mobile_badge(remaining_real)
                except Exception: pass

                # Trigger sync notification to refresh UI
                try:
                    from ..events import events
                    events.emit("stats_changed")
                    from ..singletons import notify_stats_changed
                    notify_stats_changed()
                except Exception: pass
            except Exception as e:
                if logger:
                    logger.log("error", f"on_db_work_done in commit_replay_outcome failed: {e}")

        # 3. Schedule or execute work.
        # Test seam: under the suite aqt.operations is stubbed with a no-op
        # MagicMock, so QueryOp would never run do_db_work. Execute synchronously
        # there; production dispatches it off the main thread via QueryOp.
        import os
        if "PYTEST_CURRENT_TEST" in os.environ:
            do_db_work(None)
            on_db_work_done(None)
        else:
            from aqt.operations import QueryOp
            from aqt import mw
            QueryOp(
                parent=mw,
                op=do_db_work,
                success=on_db_work_done
            ).without_collection().run_in_background()

        return {"success": True, "outcome": "caught" if choice == "catch" else "defeated", "xp_gained": battle_xp, "cp": cp_val, "remaining": remaining, "cash_gained": gained_cash}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _resolve_internal(mode="all", companion_id="", limit=None, db=None, settings_obj=None, tracker=None, trainer_card=None, main_pokemon=None, logger=None, day_cutoff=0, progress_callback=None) -> dict:
    conn = db._get_connection()
    
    use_transaction = (mode == "all")
    if use_transaction:
        conn._disable_commit = True
        # Defer per-battle mirror-DB syncs: sync_resolutions_to_other_db commits on
        # a separate connection, so firing it inside the outer transaction would
        # leave the mirror DB resolved=1 even if this transaction later rolls back.
        db.begin_deferred_mirror_sync()
        from .. import utils
        utils.in_bulk_resolve = True

    try:
        if use_transaction:
            with conn:
                result = run_mobile_battles(
                    reviews=None,
                    commit=True,
                    db=db,
                    settings_obj=settings_obj,
                    tracker=tracker,
                    trainer_card=trainer_card,
                    main_pokemon=main_pokemon,
                    companion_override_id=companion_id,
                    logger=logger,
                    day_cutoff=day_cutoff,
                    limit=limit,
                    mode=mode,
                    progress_callback=progress_callback
                )
            # Outer transaction committed successfully — now safe to propagate the
            # resolutions to the mirror DB.
            db.flush_deferred_mirror_sync()
        else:
            result = run_mobile_battles(
                reviews=None,
                commit=True,
                db=db,
                settings_obj=settings_obj,
                tracker=tracker,
                trainer_card=trainer_card,
                main_pokemon=main_pokemon,
                companion_override_id=companion_id,
                logger=logger,
                day_cutoff=day_cutoff,
                limit=limit,
                mode=mode,
                progress_callback=progress_callback
            )
        return result
    finally:
        if use_transaction:
            conn._disable_commit = False
            # Drop any mirror resolutions that were not flushed (transaction rolled
            # back / raised). No-op after a successful flush above.
            db.discard_deferred_mirror_sync()
            # Reset the bulk-resolve flag. If this is dropped, in_bulk_resolve
            # stays True for the rest of the session and silently suppresses
            # level-up tooltips, evolution prompts and learn-move dialogs in
            # normal desktop play (encounter_functions / pokedex_functions gate
            # those on `not in_bulk_resolve`).
            from .. import utils
            utils.in_bulk_resolve = False


def _attribute_xp_and_evs_to_companion(companion_id: str, xp_gained: int, ev_yield_gained: dict, settings_obj, battles_fought=1, db=None, logger=None) -> None:
    if xp_gained <= 0 and not any(ev_yield_gained.values()) and battles_fought <= 0:
        return

    if db is None:
        from ..services import services
        db = services.db
    pkmndata = None
    if companion_id:
        try:
            pkmndata = db.get_pokemon(companion_id)
        except Exception:
            pass

    if not pkmndata:
        return

    from .pokemon_functions import find_experience_for_level, get_levelup_move_for_pokemon
    from .drawing_utils import tooltipWithColour
    from ..pyobj.pokemon_obj import PokemonObject
    from .. import utils

    growth_rate = pkmndata.get("growth_rate", "medium-fast")
    level = int(pkmndata.get("level", 1))
    xp = int(pkmndata.get("xp", 0))
    remove_cap = settings_obj.get("misc.remove_level_cap") if settings_obj else False

    experience_req = int(find_experience_for_level(growth_rate, level, remove_cap))
    if remove_cap:
        xp += xp_gained
        level_cap = None
    elif level != 100:
        xp += xp_gained
        level_cap = 100
    else:
        level_cap = 100

    from ..services import services
    main_pokemon_singleton = services.main_pokemon
    is_active = (main_pokemon_singleton is not None and getattr(main_pokemon_singleton, "individual_id", None) == companion_id)
    in_bulk = getattr(utils, "in_bulk_resolve", False)
    
    color = "#6A4DAC"

    levels_gained = 0
    # level-ups
    while int(find_experience_for_level(growth_rate, level, remove_cap)) < xp and (level_cap is None or level < level_cap):
        if levels_gained >= 10:
            if is_active and not in_bulk:
                try:
                    active_logger = logger or (services.logger if (services and getattr(services, "logger", None)) else None)
                    if active_logger:
                        active_logger.log("error", f"Mobile sync level-up loop exceeded safety cap of 10 for {pkmndata.get('name')}")
                except Exception:
                    pass
            next_level_cost = int(find_experience_for_level(growth_rate, level, remove_cap))
            xp = max(0, next_level_cost - 1)
            break
        levels_gained += 1
        level += 1
        msg = f"Your {pkmndata.get('name', 'Pokemon')} is now level {level} !"
        
        if is_active and not in_bulk:
            try:
                if services.logger:
                    services.logger.game_log(f"Level Up: {msg}")
                tooltipWithColour(msg, color)
                if settings_obj and settings_obj.get("gui.pop_up_dialog_message_on_defeat") is True:
                    if services.logger:
                        services.logger.log_and_showinfo("info", f"{msg}")
            except Exception:
                pass
                
        xp = int(max(0, xp - int(experience_req)))
        experience_req = int(find_experience_for_level(growth_rate, level, remove_cap))
        
        # level-up moves
        name_lower = pkmndata.get("name", "").lower()
        new_attacks = get_levelup_move_for_pokemon(name_lower, level)
        if new_attacks:
            attacks = pkmndata.get("attacks", [])
            if isinstance(attacks, str):
                try:
                    attacks = json.loads(attacks)
                except Exception:
                    attacks = []
            
            for new_attack in new_attacks:
                if len(attacks) < 4 and new_attack not in attacks:
                    attacks.append(new_attack)
                    if is_active and not in_bulk:
                        msg_learn = f"{pkmndata.get('name', '').capitalize()} learned {new_attack}!"
                        tooltipWithColour(msg_learn, color)
                elif new_attack not in attacks:
                    if is_active and not in_bulk:
                        from ..pyobj.attack_dialog import AttackDialog
                        from PyQt6.QtWidgets import QDialog
                        dialog = AttackDialog(attacks, new_attack)
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_attack = dialog.selected_attack
                            if selected_attack in attacks:
                                idx = attacks.index(selected_attack)
                                attacks[idx] = new_attack
            pkmndata["attacks"] = attacks

    pkmndata["level"] = level
    pkmndata["xp"] = xp
    
    # EV Updates
    if "ev" not in pkmndata or not isinstance(pkmndata["ev"], dict):
        pkmndata["ev"] = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
    else:
        pkmndata["ev"] = _normalize_ev_yield(pkmndata["ev"])

    # IV Updates/Defaults
    def normalize_iv(value):
        try:
            return max(0, min(31, int(value)))
        except (TypeError, ValueError):
            return 15

    if "iv" not in pkmndata or not isinstance(pkmndata["iv"], dict):
        pkmndata["iv"] = {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15}
    else:
        # Ensure all keys exist and are valid integers between 0 and 31
        pkmndata["iv"] = {k: normalize_iv(pkmndata["iv"].get(k, 15)) for k in ("hp", "atk", "def", "spa", "spd", "spe")}
        
    normalized_yield = {
        "hp": ev_yield_gained.get("hp", 0),
        "attack": ev_yield_gained.get("attack", 0) + ev_yield_gained.get("atk", 0),
        "defense": ev_yield_gained.get("defense", 0) + ev_yield_gained.get("def", 0),
        "special-attack": ev_yield_gained.get("special-attack", 0) + ev_yield_gained.get("spa", 0),
        "special-defense": ev_yield_gained.get("special-defense", 0) + ev_yield_gained.get("spd", 0),
        "speed": ev_yield_gained.get("speed", 0) + ev_yield_gained.get("spe", 0),
    }
    
    held_item = pkmndata.get("held_item", None)
    if held_item == "macho-brace":
        for stat in normalized_yield:
            normalized_yield[stat] *= 2
    else:
        power_item_mapping = {
            "power-weight": "hp",
            "power-bracer": "attack",
            "power-belt": "defense",
            "power-lens": "special-attack",
            "power-band": "special-defense",
            "power-anklet": "speed",
        }
        if held_item in power_item_mapping:
            stat_to_boost = power_item_mapping[held_item]
            normalized_yield[stat_to_boost] += 8

    ev_yield = utils.limit_ev_yield(pkmndata["ev"], normalized_yield)
    pkmndata["ev"]["hp"] += ev_yield["hp"]
    pkmndata["ev"]["atk"] += ev_yield["attack"]
    pkmndata["ev"]["def"] += ev_yield["defense"]
    pkmndata["ev"]["spa"] += ev_yield["special-attack"]
    pkmndata["ev"]["spd"] += ev_yield["special-defense"]
    pkmndata["ev"]["spe"] += ev_yield["speed"]

    # Recompute stats
    base_stats = pkmndata.get("base_stats")
    from .pokedex_functions import is_valid_base_stats

    if not is_valid_base_stats(base_stats):
        # Fall back to stats key if it contains original stats (before scaling/growth)
        base_stats = base_stats or pkmndata.get("stats")
        
        # Fall back to pokedex search
        if not is_valid_base_stats(base_stats):
            from .pokedex_functions import search_pokedex
            base_stats = search_pokedex(pkmndata.get("name", ""), "baseStats") or {}
            
        if is_valid_base_stats(base_stats):
            pkmndata["base_stats"] = base_stats
        else:
            from ..services import services
            services.logger.log(
                "warning",
                f"Could not resolve base_stats for {pkmndata.get('name')!r} "
                f"({pkmndata.get('individual_id')}); stats left unscaled."
            )

    if is_valid_base_stats(base_stats):
        pkmndata["stats"] = {
            k: PokemonObject.calc_stat(k, int(val), level, pkmndata["iv"][k], pkmndata["ev"][k], pkmndata.get("nature", "serious"))
            for k, val in base_stats.items()
            if k in ("hp", "atk", "def", "spa", "spd", "spe")
        }
        pkmndata["current_hp"] = pkmndata["stats"].get("hp", 15)

    
    friendship = int(pkmndata.get("friendship", 0))
    friendship_gain = random.randint(5, 9)
    if pkmndata.get("held_item") == "soothe-bell":
        friendship_gain = int(friendship_gain * 1.5)
    friendship += friendship_gain
    pkmndata["friendship"] = min(255, friendship)
    
    pkmndata["pokemon_defeated"] = int(pkmndata.get("pokemon_defeated", 0)) + battles_fought

    # Call db.save_pokemon(updated_entry)
    db.save_pokemon(pkmndata)

    # 4. If active, also update the in-memory singleton
    if is_active:
        mp = main_pokemon_singleton
        mp.xp = pkmndata["xp"]
        mp.level = pkmndata["level"]
        mp.ev = pkmndata["ev"].copy()
        mp.friendship = pkmndata["friendship"]
        mp.pokemon_defeated = pkmndata["pokemon_defeated"]
        if "attacks" in pkmndata:
            mp.attacks = list(pkmndata["attacks"])
        mp.invalidate_cp_cache()

