from aqt import gui_hooks, mw, utils
from aqt.utils import showInfo
from ..functions.pokemon_functions import find_experience_for_level
from ..functions.create_css_for_reviewer import create_css_for_reviewer
import json
import os
from ..functions.create_gui_functions import create_status_html
from ..services import services

from .pokemon_obj import PokemonObject


def _anki_night_mode():
    """Anki's resolved night-mode flag, or False if the theme layer is
    unavailable (older Anki, or a headless context without a theme manager).
    The HUD only uses it to pick a class, so degrading to light is harmless."""
    try:
        from aqt.theme import theme_manager

        return bool(theme_manager.night_mode)
    except Exception:
        return False


class Reviewer_Manager:
    def __init__(self, settings_obj, main_pokemon, enemy_pokemon, ankimon_tracker):
        self.settings = settings_obj
        self.main_pokemon = main_pokemon
        self.enemy_pokemon = enemy_pokemon
        self.ankimon_tracker = ankimon_tracker
        self.life_bar_injected = False
        self.seconds = 0
        self.myseconds = 0
        # === PERFORMANCE STATE ===
        self._ownership_cache = {}  # {pokemon_id: bool} — cache HUD ownership DB reads
        self._last_state = None  # skip redundant HUD repaints when nothing changed
        self._listener_registered = (
            False  # JS keydown listener registered once per view
        )

        # Register the functions for the hooks. Reload safety (F31 registry-anchored
        # pattern, matching __init__.py's _review_card_handlers): the (hook, handler)
        # records live on the services registry, which survives a re-construction of
        # this manager. A rebuilt instance therefore removes the PREVIOUS instance's
        # handlers before appending its own — a self-only remove cannot, since a
        # bound method of a new instance never compares equal to the old one.
        _RECORD_ATTR = "_reviewer_hook_handlers"
        for _hook, _handler in getattr(services, _RECORD_ATTR, ()):
            try:
                _hook.remove(_handler)
            except (ValueError, AttributeError):
                pass
        _handlers = (
            (gui_hooks.reviewer_will_end, self.reviewer_reset_life_bar_inject),
            (gui_hooks.reviewer_did_answer_card, self.update_life_bar),
            # Anki announces a theme flip by toggling classes on <html>/<body>
            # (aqt.webview.on_theme_did_change); it never reloads the page.
            # That signal cannot cross the HUD's closed shadow boundary, so the
            # HUD has to repaint itself or it keeps the old palette until the
            # next answered card. The same hook covers an OS flip while Anki is
            # set to "Automatic" -- theme_manager fires it either way -- which
            # is what @media (prefers-color-scheme: dark) used to handle live.
            (gui_hooks.theme_did_change, self.refresh_hud),
        )
        for _hook, _handler in _handlers:
            _hook.append(_handler)
        setattr(services, _RECORD_ATTR, _handlers)

    def reviewer_reset_life_bar_inject(self):
        """Clear per-session HUD state when the battle/review session ends."""
        self.life_bar_injected = False
        self._ownership_cache.clear()
        self._last_state = None

    def invalidate_hud_cache(self):
        """Clear the per-encounter HUD perf caches so the next repaint rebuilds.

        Public entry point for external callers (e.g. a new wild encounter) that
        need the HUD to forget the previous Pokémon's ownership/state without
        reaching into these private attributes directly.
        """
        self._last_state = None
        if isinstance(self._ownership_cache, dict):
            self._ownership_cache.clear()

    def get_boost_values_string(
        self, pokemon: PokemonObject, display_neutral_boost: bool = False
    ) -> str:
        """Generates a formatted string representing the stat boost multipliers of a Pokémon."""
        pokemon_dict = pokemon.to_engine_format()
        boosts = {
            "atk": pokemon_dict.get("attack_boost", 0),
            "def": pokemon_dict.get("defense_boost", 0),
            "SpA": pokemon_dict.get("special_attack_boost", 0),
            "SpD": pokemon_dict.get("special_defense_boost", 0),
            "SPE": pokemon_dict.get("speed_boost", 0),
            "ACC": pokemon_dict.get("accuracy_boost", 0),
            "EVD": pokemon_dict.get("evasion_boost", 0),
        }

        boost_to_mult = {
            0: "x1",
            1: "x1.5",
            2: "x2",
            3: "x2.5",
            4: "x3",
            5: "x3.5",
            6: "x4",
            -1: "x0.67",
            -2: "x0.5",
            -3: "x0.4",
            -4: "x0.33",
            -5: "x0.29",
            -6: "x0.25",
        }

        boost_display = " "
        for key, boost_val in boosts.items():
            if display_neutral_boost is False and boost_val == 0:
                continue
            mult_str = boost_to_mult[boost_val]
            boost_display += f" | {key} {mult_str} | "

        return boost_display

    def inject_life_bar(self, web_content, context):
        """This function is now a no-op. The HUD is injected via the Shadow DOM portal."""
        return web_content

    def refresh_hud(self):
        """Re-render the HUD against the live reviewer webview.

        Builds the tiny reviewer shim ``update_life_bar`` expects (an object with
        a ``.web``) from ``mw.reviewer`` and calls it. Guarded so a missing
        reviewer/webview — or no Anki at all (the agent harness uses a recording
        fake instead of this class) — is a silent no-op, not a crash. This is the
        single entry point battle_loop / encounter_functions use to repaint.
        """
        try:
            from aqt import mw

            class _ReviewerShim:
                pass

            shim = _ReviewerShim()
            shim.web = mw.reviewer.web
            self.update_life_bar(shim, 0, 0)
        except Exception:
            pass

    def _resolve_addon_package(self):
        """Return the add-on package name for building /_addons/ media URLs."""
        try:
            addon_package = mw.addonManager.addonFromModule(__name__)
        except Exception:
            addon_package = None
        if not addon_package:
            # Fallback add-on folder names (AnkiWeb id / source checkout).
            try:
                folder = mw.addonManager.addonsFolder()
                for name in ["1908235722", "Ankimon"]:
                    if os.path.exists(os.path.join(folder, name)):
                        addon_package = name
                        break
            except Exception:
                pass
        return addon_package or "1908235722"

    @staticmethod
    def _safe_hp_pair(hp, max_hp):
        """Return numeric HP values that are safe for HUD rendering."""
        try:
            safe_hp = int(hp) if hp is not None else 0
        except (TypeError, ValueError, OverflowError):
            safe_hp = 0

        try:
            safe_max_hp = int(max_hp) if max_hp is not None else 1
        except (TypeError, ValueError, OverflowError):
            safe_max_hp = 1

        safe_max_hp = max(1, safe_max_hp)
        safe_hp = min(max(0, safe_hp), safe_max_hp)
        return safe_hp, safe_max_hp

    def update_life_bar(self, reviewer, card, ease):
        # GUARD: only repaint on direct calls (refresh_hud / battle_loop pass
        # card=0, ease=0). The reviewer_did_answer_card hook fires with a real
        # Card object and a non-zero ease; those are handled by battle_loop's
        # refresh_hud() call, so short-circuit here to avoid a redundant repaint.
        if isinstance(ease, int) and ease != 0:
            return  # Hook from reviewer_did_answer_card
        if card is not None and not isinstance(card, int):
            return  # Hook received a Card object

        if self.enemy_pokemon is None or self.main_pokemon is None:
            self._last_state = None
            return

        if int(self.settings.get("gui.show_mainpkmn_in_reviewer")) == 3:
            reviewer.web.eval("if(window.__ankimonHud) window.__ankimonHud.clear();")
            self._last_state = None
            return

        enemy_hp, enemy_max_hp = self._safe_hp_pair(
            self.enemy_pokemon.hp, self.enemy_pokemon.max_hp
        )
        main_hp, main_max_hp = self._safe_hp_pair(
            self.main_pokemon.hp, self.main_pokemon.max_hp
        )

        # 1. Ownership cache (avoid a DB query on every repaint of the same enemy).
        is_pokemon_owned = self._ownership_cache.get(self.enemy_pokemon.id)
        if is_pokemon_owned is None:
            is_pokemon_owned = False
            try:
                db = services.db
                cursor = db.execute(
                    "SELECT 1 FROM captured_pokemon WHERE pokedex_id = ? LIMIT 1",
                    (self.enemy_pokemon.id,),
                )
                is_pokemon_owned = cursor.fetchone() is not None
                self._ownership_cache[self.enemy_pokemon.id] = is_pokemon_owned
            except Exception:
                pass

        # Register keydown listener (8) to toggle HUD visibility. Called every
        # time because Anki reloads the webview on card switches; the JS itself
        # guards against duplicate listeners (window.ankimonKeyListener).
        reviewer.web.eval("""
            (function() {
                if (window.ankimonKeyListener) return;
                window.ankimonKeyListener = true;
                let originalParent = null;
                let hudHost = null;

                document.addEventListener('keydown', function(event) {
                    if (event.key === '8') {
                        if (!hudHost) {
                            hudHost = document.getElementById('ankimon-hud-host');
                            if (hudHost) {
                                originalParent = hudHost.parentNode;
                            } else {
                                console.error('Ankimon: ankimon-hud-host not found.');
                                return;
                            }
                        }

                        // First time: render if not yet rendered
                        if (!window.__ankimonHudRendered && window.__ankimonHudData && window.__ankimonHud) {
                            window.__ankimonHud.update(window.__ankimonHudData.html, window.__ankimonHudData.css);
                            window.__ankimonHudRendered = true;
                            originalParent = hudHost.parentNode;
                            // Toggle visibility on first render
                            window.__ankimonHudHidden = !window.__ankimonHudHidden;
                            // If should be hidden, remove it from DOM
                            if (window.__ankimonHudHidden && originalParent) {
                                originalParent.removeChild(hudHost);
                            }
                        } else if (window.__ankimonHudRendered) {
                            // Already rendered, toggle visibility
                            window.__ankimonHudHidden = !window.__ankimonHudHidden;

                            // If showing (unhiding), update with latest data
                            if (!window.__ankimonHudHidden && window.__ankimonHudData && window.__ankimonHud) {
                                window.__ankimonHud.update(window.__ankimonHudData.html, window.__ankimonHudData.css);
                            }

                            // Toggle DOM visibility
                            if (hudHost.parentNode) {
                                hudHost.parentNode.removeChild(hudHost);
                            } else if (originalParent) {
                                originalParent.appendChild(hudHost);
                            }
                        }
                    }
                });
            })();
        """)

        # 2. State-based update check (avoid redundant eval() calls for HTML/CSS).
        def _boost_snapshot(pkmn):
            # Hashable snapshot of the stat boosts rendered into the HUD name
            # display; stat-only moves (Growl/Swords Dance) change these without
            # touching hp/status/xp, so they must be part of the repaint key.
            stages = getattr(pkmn, "stat_stages", None)
            if isinstance(stages, dict):
                return tuple(sorted(stages.items()))
            return None

        current_state = (
            self.enemy_pokemon.id,
            enemy_hp,
            enemy_max_hp,
            self.enemy_pokemon.battle_status,
            self.main_pokemon.id,
            main_hp,
            main_max_hp,
            self.main_pokemon.xp,
            self.settings.get("gui.show_mainpkmn_in_reviewer"),
            self.settings.get("gui.reviewer_image_gif"),
            self.settings.get("misc.language"),
            _boost_snapshot(self.enemy_pokemon),
            _boost_snapshot(self.main_pokemon),
            self.settings.get("gui.hud_styling", True),
            self.settings.get("gui.hud_player_sprite"),
            self.settings.get("gui.hud_enemy_sprite"),
            self.settings.get("gui.hud_xp_bar"),
            self.settings.get("gui.hud_hp_bars"),
            self.settings.get("gui.hud_hp_text"),
            self.settings.get("gui.hud_pokemon_id"),
            self.settings.get("gui.hud_pokemon_gen"),
            self.settings.get("gui.hud_pokemon_lvl"),
            self.settings.get("gui.hud_pokemon_name"),
            self.settings.get("gui.hud_status_badge"),
            self.settings.get("gui.hud_owned_indicator"),
            self.settings.get("gui.hud_enemy_shiny_indicator"),
            self.settings.get("gui.hud_player_shiny_indicator"),
            self.settings.get("gui.reviewer_text_message_box"),
            # Anki's theme drives a class on the HUD, so a theme flip has to
            # invalidate the repaint cache or the HUD keeps the old palette
            # until some other piece of state happens to change.
            _anki_night_mode(),
        )
        if self._last_state == current_state and card is not None:
            return  # No changes, skip update
        self._last_state = current_state

        image_format = "gif" if self.settings.get("gui.reviewer_image_gif") else "png"

        addon_package = self._resolve_addon_package()

        # 3. Serve sprites via the Anki media server (/_addons/...) instead of
        # inlining base64 — a large repaint-cost saving on every HUD refresh.
        def get_sprite_url(pkmn, side):
            try:
                abs_path = pkmn.get_sprite_path(side, image_format)
                norm = str(abs_path).replace("\\", "/")
                marker = "user_files/sprites/"
                idx = norm.find(marker)
                if idx != -1:
                    rel_path = norm[idx:]
                    return f"/_addons/{addon_package}/{rel_path}"
            except Exception:
                pass
            return ""

        enemy_sprite_url = get_sprite_url(self.enemy_pokemon, "front")

        main_pkmn_sprite_url = None
        side = "back"  # Default side
        if int(self.settings.get("gui.show_mainpkmn_in_reviewer")) > 0:
            if image_format == "gif":
                if self.settings.compute_special_variable("view_main_front") == -1:
                    side = "front"
                else:
                    side = "back"
            else:  # png
                side = "back"
            main_pkmn_sprite_url = get_sprite_url(self.main_pokemon, side)

        pokemon_hp_percent = int((enemy_hp / enemy_max_hp) * 50)
        if int(self.settings.get("gui.show_mainpkmn_in_reviewer")) > 0:
            mainpkmn_hp_percent = int((main_hp / main_max_hp) * 50)
        else:
            mainpkmn_hp_percent = 0  # Not used in this mode

        enemy_hp_true_percent = (enemy_hp / enemy_max_hp) * 100
        main_hp_true_percent = (main_hp / main_max_hp) * 100

        # Build hud_html. The HUD carries Anki's theme as its own class: its
        # stylesheet lives in a closed shadow root, so it cannot see the
        # night-mode class Anki sets on <body>. theme_manager.night_mode is the
        # resolved value, so "Automatic" follows the OS correctly.
        hud_html = (
            '<div id="ankimon-hud" class="night_mode">'
            if _anki_night_mode()
            else '<div id="ankimon-hud">'
        )
        if self.settings.get("gui.hud_hp_bars"):
            hud_html += '<div id="life-bar" class="Ankimon"></div>'
        if self.settings.get("gui.hud_xp_bar"):
            hud_html += '<div id="xp-bar" class="Ankimon"></div>'
            hud_html += '<div id="xp_text" class="Ankimon">XP</div>'

        # display_name handles translations, regional forms, megas and gmax.
        enemy_lang_name = self.enemy_pokemon.display_name
        pokedex_id = getattr(self.enemy_pokemon, "pokedex_id", self.enemy_pokemon.id)
        generation = getattr(self.enemy_pokemon, "generation", 1)

        enemy_parts = []
        if self.settings.get("gui.hud_pokemon_id"):
            enemy_parts.append(f"[#{pokedex_id}]")
        if self.settings.get("gui.hud_pokemon_name"):
            enemy_parts.append(enemy_lang_name)
        if self.enemy_pokemon.shiny and self.settings.get("gui.hud_enemy_shiny_indicator"):
            enemy_parts.append("⭐")
        if self.settings.get("gui.hud_pokemon_gen"):
            enemy_parts.append(f"(Gen {generation})")
        if self.settings.get("gui.hud_pokemon_lvl"):
            enemy_parts.append(f"LvL: {self.enemy_pokemon.level}")

        if enemy_parts:
            name_display_text = " ".join(enemy_parts)
            name_display_text += self.get_boost_values_string(
                self.enemy_pokemon, display_neutral_boost=False
            )
            hud_html += f'<div id="name-display" class="Ankimon">{name_display_text}</div>'

        if enemy_hp > 0:
            hud_html += create_status_html(
                f"{self.enemy_pokemon.battle_status}",
                self.settings,
                is_pokemon_owned,
                addon_package,
            )
        else:
            hud_html += create_status_html(
                "fainted", self.settings, is_pokemon_owned, addon_package
            )

        if self.settings.get("gui.hud_hp_text"):
            hud_html += f'<div id="hp-display" class="Ankimon">HP: {enemy_hp}/{enemy_max_hp}</div>'

        if self.settings.get("gui.hud_enemy_sprite"):
            enemy_poke_animation_style = (
                f"animation: ankimon-shake-normal {self.seconds}s ease;"
            )
            hud_html += f'<div id="PokeImage" class="Ankimon"><img src="{enemy_sprite_url}" alt="PokeImage" style="{enemy_poke_animation_style}"></div>'

        if int(self.settings.get("gui.show_mainpkmn_in_reviewer")) > 0:
            if self.settings.get("gui.hud_player_sprite"):
                my_poke_html_attributes = ""
                # SPECIAL CASE: For front-facing GIFs, the animation conflicts with the transform.
                # We will sacrifice the animation in this case to force the flip using a static class.
                if image_format == "gif" and side == "front":
                    my_poke_html_attributes = 'class="force-flip"'
                else:
                    # For all other cases, the flipped animation works correctly.
                    animation_style = (
                        f"animation: ankimon-shake-flipped {self.myseconds}s ease;"
                    )
                    my_poke_html_attributes = f'style="{animation_style}"'

                hud_html += (
                    f'<div id="MyPokeImage" class="Ankimon">'
                    f'<img src="{main_pkmn_sprite_url}" alt="MyPokeImage" {my_poke_html_attributes}>'
                    f"</div>"
                )

            main_lang_name = self.main_pokemon.display_name
            main_pokedex_id = getattr(
                self.main_pokemon, "pokedex_id", self.main_pokemon.id
            )
            main_generation = getattr(self.main_pokemon, "generation", 1)

            main_parts = []
            if self.settings.get("gui.hud_pokemon_id"):
                main_parts.append(f"[#{main_pokedex_id}]")
            if self.settings.get("gui.hud_pokemon_name"):
                main_parts.append(main_lang_name)
            if self.main_pokemon.shiny and self.settings.get("gui.hud_player_shiny_indicator"):
                main_parts.append("⭐")
            if self.settings.get("gui.hud_pokemon_gen"):
                main_parts.append(f"(Gen {main_generation})")
            if self.settings.get("gui.hud_pokemon_lvl"):
                main_parts.append(f"LvL: {self.main_pokemon.level}")

            if main_parts:
                main_name_display_text = " ".join(main_parts)
                main_name_display_text += self.get_boost_values_string(
                    self.main_pokemon, display_neutral_boost=False
                )
                hud_html += f'<div id="myname-display" class="Ankimon">{main_name_display_text}</div>'
            if self.settings.get("gui.hud_hp_text"):
                hud_html += f'<div id="myhp-display" class="Ankimon">HP: {main_hp}/{main_max_hp}</div>'
            if self.settings.get("gui.hud_hp_bars"):
                hud_html += '<div id="mylife-bar" class="Ankimon"></div>'

        hud_html += "</div>"

        # Build hud_css
        if self.settings.get("gui.hud_styling", True):
            hud_css = create_css_for_reviewer(
                int(self.settings.get("gui.show_mainpkmn_in_reviewer")),
                pokemon_hp_percent,
                self.settings.get("gui.review_hp_bar_thickness") * 4,
                int(self.settings.compute_special_variable("xp_bar_spacer")),
                -1
                if int(self.settings.get("gui.show_mainpkmn_in_reviewer")) == 1
                else self.settings.compute_special_variable("view_main_front"),
                mainpkmn_hp_percent,
                int(self.settings.compute_special_variable("hp_only_spacer")),
                int(self.settings.compute_special_variable("wild_hp_spacer")),
                self.settings.get("gui.hud_xp_bar"),
                self.main_pokemon,
                int(
                    find_experience_for_level(
                        self.main_pokemon.growth_rate,
                        self.main_pokemon.level,
                        self.settings.get("misc.remove_level_cap"),
                    )
                ),
                self.settings.compute_special_variable("xp_bar_location"),
                enemy_hp_true_percent,
                main_hp_true_percent,
            )
            # Theme selectors are anchored on #ankimon-hud itself (see the
            # ``night_mode`` class added in hud_html above), never on an
            # ancestor such as ``.night_mode``/``html.dark``. The HUD lives in
            # a closed shadow root whose host is appended to <html>, so a class
            # Anki sets on <body> is neither reachable across the shadow
            # boundary nor an ancestor of the host — those rules never matched.
            #
            # #xp_text is deliberately absent here: create_css_for_reviewer
            # gives it its own cyan-on-black pill matching the XP bar, and
            # listing it in these groups overrode that with a plain pill.
            hud_css += """
            #ankimon-hud #name-display, #ankimon-hud #myname-display, #ankimon-hud #hp-display, #ankimon-hud #myhp-display {
                font-family: Arial, sans-serif;
                background: white !important;
                color: var(--text-fg, #6D6D6E);
                border-radius: 5px !important;
                padding: 4px 8px !important;
            }

            /* Dark mode keys off Anki's own theme, not the OS. There is no
               @media (prefers-color-scheme: dark) rule here on purpose:
               theme_manager.night_mode already resolves Anki's "Automatic"
               setting against the OS, so a media query adds nothing when the
               two agree and actively contradicts Anki when they differ — a
               user on a dark OS who deliberately runs Anki in light mode got
               dark HUD pills. */
            #ankimon-hud.night_mode #name-display, #ankimon-hud.night_mode #myname-display,
            #ankimon-hud.night_mode #hp-display, #ankimon-hud.night_mode #myhp-display {
                font-family: Arial, sans-serif;
                background: #1f1f1f !important;
                color: white !important;
                border-radius: 5px !important;
                padding: 4px 8px !important;
            }
            """
        else:
            hud_css = ""

        # Use reviewer.web.eval to call the portal
        # Store HUD data and auto-render if not hidden on startup
        hud_hidden_on_startup = bool(self.settings.get("gui.hud_hidden_on_startup"))
        js_code = f"""
        (function(h,c,hiddenOnStartup){{
            if(window.__ankimonHud){{
                // Always update the stored data, regardless of visibility
                window.__ankimonHudData = {{html: h, css: c}};

                // Only initialize on first call - preserve user's toggle state on subsequent updates
                if(window.__ankimonHudHidden === undefined){{
                    window.__ankimonHudHidden = hiddenOnStartup;
                    window.__ankimonHudRendered = false;
                }}

                // If HUD should be visible on startup, render it now
                if(!hiddenOnStartup && !window.__ankimonHudRendered){{
                    window.__ankimonHud.update(h,c);
                    window.__ankimonHudRendered = true;
                }}
                // If HUD is already rendered and visible, update it with new data
                else if(window.__ankimonHudRendered && !window.__ankimonHudHidden){{
                    window.__ankimonHud.update(h,c);
                }}
                // If HUD is hidden, data is still stored in __ankimonHudData for when it's toggled on
            }}
        }})({json.dumps(hud_html)}, {json.dumps(hud_css)}, {str(hud_hidden_on_startup).lower()});
        """
        reviewer.web.eval(js_code)
