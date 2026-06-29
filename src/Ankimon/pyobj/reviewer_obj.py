from aqt import gui_hooks, mw, utils
from aqt.utils import showInfo
from ..functions.pokemon_functions import find_experience_for_level
from ..business import get_image_as_base64
from ..functions.create_css_for_reviewer import create_css_for_reviewer
import json
import os
from ..functions.create_gui_functions import create_status_html
from ..functions.pokedex_functions import get_pokemon_diff_lang_name

from .pokemon_obj import PokemonObject

class Reviewer_Manager:
    def __init__(self, settings_obj, main_pokemon, enemy_pokemon, ankimon_tracker):
        self.settings = settings_obj
        self.main_pokemon = main_pokemon
        self.enemy_pokemon = enemy_pokemon
        self.ankimon_tracker = ankimon_tracker
        self.life_bar_injected = False
        self.seconds = 0
        self.myseconds = 0
        # === PERFORMANCE FIXES ===
        self._ownership_cache = {} # {pokemon_id: bool}
        self._last_state = None # To avoid redundant HUD updates
        self._listener_registered = False # To register keylistener only once

        # Register the functions for the hooks
        gui_hooks.reviewer_will_end.append(self.reviewer_reset_life_bar_inject)
        # register hook: remove if exists to avoid duplicates, then append
        try:
            gui_hooks.reviewer_did_answer_card.remove(self.update_life_bar)
        except (ValueError, AttributeError):
            pass
        gui_hooks.reviewer_did_answer_card.append(self.update_life_bar)

    def reviewer_reset_life_bar_inject(self):
        """Clear state when the battle/review session ends"""
        self.life_bar_injected = False
        self._ownership_cache.clear()
        self._last_state = None

    def get_boost_values_string(self, pokemon: PokemonObject, display_neutral_boost: bool=False) -> str:
        """Generates a formatted string representing the stat boost multipliers of a Pokémon."""
        pokemon_dict = pokemon.to_engine_format()
        boosts = {
            "atk": pokemon_dict.get('attack_boost', 0),
            "def": pokemon_dict.get('defense_boost', 0),
            "SpA": pokemon_dict.get('special_attack_boost', 0),
            "SpD": pokemon_dict.get('special_defense_boost', 0),
            "SPE": pokemon_dict.get('speed_boost', 0),
            "ACC": pokemon_dict.get('accuracy_boost', 0),
            "EVD": pokemon_dict.get('evasion_boost', 0),
        }

        boost_to_mult = {
            0: "x1", 1: "x1.5", 2: "x2", 3: "x2.5", 4: "x3", 5: "x3.5", 6: "x4",
            -1: "x0.67", -2: "x0.5", -3: "x0.4", -4: "x0.33", -5: "x0.29", -6: "x0.25",
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

    def update_life_bar(self, reviewer, card, ease):
        # GUARD: Block hook calls (ease != 0 or card != 0)
        # Only allow direct calls from battle_loop (ease=0, card=0)
        if isinstance(ease, int) and ease != 0:
            return  # Hook from reviewer_did_answer_card
        if card is not None and not isinstance(card, int):
            return  # Hook received a Card object
 
        if int(self.settings.get("gui.show_mainpkmn_in_reviewer")) == 3:
            reviewer.web.eval("if(window.__ankimonHud) window.__ankimonHud.clear();")
            return

        # 1. Ownership cache (avoid repeated DB queries)
        is_pokemon_owned = self._ownership_cache.get(self.enemy_pokemon.id)
        if is_pokemon_owned is None:
            is_pokemon_owned = False
            try:
                db = mw.ankimon_db
                cursor = db.execute(
                    "SELECT 1 FROM captured_pokemon WHERE pokedex_id = ? LIMIT 1",
                    (self.enemy_pokemon.id,),
                )
                is_pokemon_owned = cursor.fetchone() is not None
                self._ownership_cache[self.enemy_pokemon.id] = is_pokemon_owned
            except Exception:
                pass

        # Register keydown listener (8) to toggle HUD visibility
        # We call this every time because Anki reloads the webview on card switches, 
        # but the JS itself has a guard (window.ankimonKeyListener) to avoid duplicates.
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

        # 2. State-based update check (avoid redundant eval() calls for HTML/CSS)
        current_state = (
            self.enemy_pokemon.id,
            self.enemy_pokemon.hp,
            self.enemy_pokemon.max_hp,
            self.enemy_pokemon.battle_status,
            self.main_pokemon.id,
            self.main_pokemon.hp,
            self.main_pokemon.max_hp,
            self.main_pokemon.xp,
            self.settings.get("gui.show_mainpkmn_in_reviewer"),
            self.settings.get("gui.reviewer_image_gif"),
            self.settings.get("misc.language"),
        )
        if self._last_state == current_state and card is not None:
            return # No changes, skip update
        self._last_state = current_state

        image_format = "gif" if self.settings.get('gui.reviewer_image_gif') else "png"
        
        # Get addon package for URLs
        try:
            addon_package = mw.addonManager.addonFromModule(__name__)
        except Exception:
            addon_package = "1908235722"

        # 3. Use URLs instead of Base64 for images (Huge performance gain)
        def get_sprite_url(pkmn, side):
            abs_path = pkmn.get_sprite_path(side, image_format)
            from ..resources import addon_dir
            # Convert absolute path to /_addons/ relative URL
            rel_path = os.path.relpath(abs_path, addon_dir).replace("\\", "/")
            return f"/_addons/{addon_package}/{rel_path}"

        enemy_sprite_url = get_sprite_url(self.enemy_pokemon, "front")

        main_pkmn_sprite_url = None
        side = "back" # Default side
        if int(self.settings.get('gui.show_mainpkmn_in_reviewer')) > 0:
            if image_format == "gif":
                if self.settings.compute_special_variable('view_main_front') == -1:
                    side = "front"
                else:
                    side = "back"
            else: # png
                side = "back"
            main_pkmn_sprite_url = get_sprite_url(self.main_pokemon, side)

        if int(self.settings.get('gui.show_mainpkmn_in_reviewer')) > 0:
            pokemon_hp_percent = int((self.enemy_pokemon.hp / self.enemy_pokemon.max_hp) * 50) if self.enemy_pokemon.max_hp > 0 else 0
            mainpkmn_hp_percent = int((self.main_pokemon.hp / self.main_pokemon.max_hp) * 50) if self.main_pokemon.max_hp > 0 else 0
        else:
            pokemon_hp_percent = int((self.enemy_pokemon.hp / self.enemy_pokemon.max_hp) * 100) if self.enemy_pokemon.max_hp > 0 else 0
            mainpkmn_hp_percent = 0 # Not used in this mode

        enemy_hp_true_percent = (self.enemy_pokemon.hp / self.enemy_pokemon.max_hp) * 100 if self.enemy_pokemon.max_hp > 0 else 0
        main_hp_true_percent = (self.main_pokemon.hp / self.main_pokemon.max_hp) * 100 if self.main_pokemon.max_hp > 0 else 0

        # Build hud_html
        hud_html = '<div id="ankimon-hud">'
        if self.settings.get("gui.hp_bar_config") is True:
            hud_html += '<div id="life-bar" class="Ankimon"></div>'
        if self.settings.get("gui.xp_bar_config") is True:
            hud_html += '<div id="xp-bar" class="Ankimon"></div>'
            hud_html += '<div id="xp_text" class="Ankimon">XP</div>'

        # Use the smart display_name property which handles translations, regional forms, megas, and gmax
        enemy_lang_name = self.enemy_pokemon.display_name
        if self.enemy_pokemon.shiny is True:
            enemy_lang_name += " ⭐ "
        # Format: [#ID] Name (Gen X) LvL: Y
        pokedex_id = getattr(self.enemy_pokemon, 'pokedex_id', self.enemy_pokemon.id)
        generation = getattr(self.enemy_pokemon, 'generation', 1)
        name_display_text = f"[#{pokedex_id}] {enemy_lang_name} (Gen {generation}) LvL: {self.enemy_pokemon.level}"
        name_display_text += self.get_boost_values_string(self.enemy_pokemon, display_neutral_boost=False)
        hud_html += f'<div id="name-display" class="Ankimon">{name_display_text}</div>'

        if self.enemy_pokemon.hp > 0:
            hud_html += create_status_html(f"{self.enemy_pokemon.battle_status}", self.settings, is_pokemon_owned, addon_package)
        else:
            hud_html += create_status_html("fainted", self.settings, is_pokemon_owned, addon_package)

        hud_html += f'<div id="hp-display" class="Ankimon">HP: {int(self.enemy_pokemon.hp)}/{int(self.enemy_pokemon.max_hp)}</div>'

        enemy_poke_animation_style = f"animation: ankimon-shake-normal {self.seconds}s ease;"
        hud_html += f'<div id="PokeImage" class="Ankimon"><img src="{enemy_sprite_url}" alt="PokeImage" style="{enemy_poke_animation_style}"></div>'

        if int(self.settings.get('gui.show_mainpkmn_in_reviewer')) > 0:

            my_poke_html_attributes = ""
            # SPECIAL CASE: For front-facing GIFs, the animation conflicts with the transform.
            # We will sacrifice the animation in this case to force the flip using a static class.
            if image_format == "gif" and side == "front":
                my_poke_html_attributes = 'class="force-flip"'
            else:
                # For all other cases, the flipped animation works correctly.
                animation_style = f"animation: ankimon-shake-flipped {self.myseconds}s ease;"
                my_poke_html_attributes = f'style="{animation_style}"'

            hud_html += (f'<div id="MyPokeImage" class="Ankimon">'
                         f'<img src="{main_pkmn_sprite_url}" alt="MyPokeImage" {my_poke_html_attributes}>'
                         f'</div>')

            main_lang_name = self.main_pokemon.display_name
            if self.main_pokemon.shiny:
                main_lang_name += " ⭐ "
            # Format: [#ID] Name (Gen X) LvL: Y
            main_pokedex_id = getattr(self.main_pokemon, 'pokedex_id', self.main_pokemon.id)
            main_generation = getattr(self.main_pokemon, 'generation', 1)
            main_name_display_text = f"[#{main_pokedex_id}] {main_lang_name} (Gen {main_generation}) LvL: {self.main_pokemon.level}"
            main_name_display_text += self.get_boost_values_string(self.main_pokemon, display_neutral_boost=False)
            hud_html += f'<div id="myname-display" class="Ankimon">{main_name_display_text}</div>'
            hud_html += f'<div id="myhp-display" class="Ankimon">HP: {int(self.main_pokemon.hp)}/{int(self.main_pokemon.max_hp)}</div>'
            if self.settings.get("gui.hp_bar_config") is True:
                hud_html += '<div id="mylife-bar" class="Ankimon"></div>'

        hud_html += '</div>'

        # Build hud_css
        hud_css = create_css_for_reviewer(
            int(self.settings.get('gui.show_mainpkmn_in_reviewer')),
            pokemon_hp_percent,
            self.settings.get("gui.review_hp_bar_thickness") * 4,
            int(self.settings.compute_special_variable('xp_bar_spacer')),
            -1 if int(self.settings.get('gui.show_mainpkmn_in_reviewer')) == 1 else self.settings.compute_special_variable('view_main_front'),
            mainpkmn_hp_percent,
            int(self.settings.compute_special_variable('hp_only_spacer')),
            int(self.settings.compute_special_variable('wild_hp_spacer')),
            self.settings.get("gui.xp_bar_config"),
            self.main_pokemon,
            int(find_experience_for_level(self.main_pokemon.growth_rate, self.main_pokemon.level, self.settings.get("misc.remove_level_cap"))),
            self.settings.compute_special_variable('xp_bar_location'),
            enemy_hp_true_percent,
            main_hp_true_percent
        )
        hud_css += """
        #ankimon-hud #name-display, #ankimon-hud #myname-display, #ankimon-hud #hp-display, #ankimon-hud #myhp-display, #ankimon-hud #xp_text {
            font-family: Arial, sans-serif;
            background: white !important;
            color: var(--text-fg, #6D6D6E);
            border-radius: 5px !important;
            padding: 4px 8px !important;
        }

        @media (prefers-color-scheme: dark) {
            #ankimon-hud #name-display, #ankimon-hud #myname-display, #ankimon-hud #hp-display, #ankimon-hud #myhp-display, #ankimon-hud #xp_text {
                font-family: Arial, sans-serif;
                background: #1f1f1f !important;
                color: white !important;
                border-radius: 5px !important;
                padding: 4px 8px !important;
            }
        }

        .night_mode #ankimon-hud #name-display, .night_mode #ankimon-hud #myname-display, .night_mode #ankimon-hud #hp-display,
        .night_mode #ankimon-hud #myhp-display, .night_mode #ankimon-hud{
            font-family: Arial, sans-serif;
            background: #1f1f1f !important;
            color: white !important;
            border-radius: 5px !important;
            padding: 4px 8px !important;
        }

        .night_mode #xp_text {
            font-color: rgba(0, 191, 255, 0.85)
            font-family: Arial, sans-serif;
            background: #1f1f1f !important;
            border-radius: 5px !important;
            padding: 4px 8px !important;
        }
        """

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
