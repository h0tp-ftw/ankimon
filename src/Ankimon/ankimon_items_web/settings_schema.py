"""Settings schema + validation for the web settings screen.

Mirrors the hierarchical structure of the legacy QMainWindow settings_window.py
so users see the same groups in the same order, but exposed as data so the
web shell can render it.
"""

# Two-level group structure. Each group has a friendly-name list of settings
# (matched against lang/setting_name.json) and optional subgroups.
GROUPS = [
    {
        "label": "General",
        "settings": [
            "Trainer Name",
            "Language",
            "Show Tip of the Day On Startup",
        ],
        "subgroups": [
            {
                "label": "Technical Settings",
                "settings": [
                    "SSH Access",
                    "Prevent Ankimon News on Startup",
                    "AnkiWeb Sync",
                    "Developer Mode",
                ],
            },
            {
                "label": "Discord Integration",
                "settings": [
                    "Discord Rich Presence - Ankimon",
                    "Discord Rich Presence - Quote Type",
                ],
            },
        ],
    },
    {
        "label": "Battle",
        "settings": [],
        "subgroups": [
            {
                "label": "Auto-Battle Rules",
                "settings": [
                    "Automatic Battle",
                    "Always Catch Wishlist",
                ],
                "chip_group": {
                    "label": "Always Catch Tiers",
                    "description": (
                        "In automatic battle mode, Pokémon of these tiers are always caught "
                        "regardless of your collection status or mode setting. "
                        "Shiny Pokémon are always caught automatically."
                    ),
                    "keys": [
                        ("battle.auto_catch_legendary", "Legendary"),
                        ("battle.auto_catch_mythical",  "Mythical"),
                        ("battle.auto_catch_ultra",     "Ultra Beast"),
                        ("battle.auto_catch_starter",   "Starter"),
                        ("battle.auto_catch_mega",      "Mega"),
                        ("battle.auto_catch_gmax",      "Gigantamax"),
                        ("battle.auto_catch_regional",  "Regional Form"),
                    ],
                },
            },
            {
                "label": "Gameplay & Mechanics",
                "settings": [
                    "Cards per Round",
                    "Review Based Damage",
                    "Friendship & Time Evolution",
                    "Auto-detect Time Zone",
                    "Time Zone UTC Offset",
                ],
            },
            {
                "label": "Fight Hotkeys",
                "settings": [
                    "Key for Defeat",
                    "Key for Catching",
                    "Key for Team Cycling",
                    "Key for Opening/Closing Ankimon",
                    "Allow Choosing Moves",
                ],
            },
            {
                "label": "Level Settings",
                "settings": [
                    "Remove Level Cap",
                ],
            },
        ],
    },
    {
        "label": "Styling",
        "settings": [
            "Team Overview in Deck Overview",
            "Animate Time",
            "Show GIFs in Collection",
            "Show Sprites Across Ankimon",
        ],
    },
    {
        "label": "HUD and Reviewer",
        "settings": [
            "Show Main Pokémon in Reviewer",
            "Hide HUD on Reviewer Startup",
            "Show Pokémon Buttons",
            "Message Box Display Time",
            "HP Bar Thickness",
            "Reviewer Image as GIF",
            "View Main Pokémon Front",
            "XP Bar Location",
            "Pop-Up on Defeat",
            "Pop-Up on Item Receive",
        ],
        "chip_group": {
            "label": "HUD Element Toggles",
            "description": "Selectively hide or show individual elements of the Ankimon HUD in the reviewer.",
            "keys": [
                ("gui.hud_player_sprite", "Player Sprite"),
                ("gui.hud_enemy_sprite", "Enemy Sprite"),
                ("gui.hud_xp_bar", "XP Progress Bar"),
                ("gui.hud_hp_bars", "HP Bars"),
                ("gui.hud_hp_text", "HP Values"),
                ("gui.hud_pokemon_id", "Pokémon ID"),
                ("gui.hud_pokemon_gen", "Pokémon Gen"),
                ("gui.hud_pokemon_lvl", "Pokémon Lvl"),
                ("gui.hud_pokemon_name", "Pokémon Name"),
                ("gui.hud_status_badge", "Status Badge"),
                ("gui.hud_owned_indicator", "Pokeball Icon"),
                ("gui.hud_enemy_shiny_indicator", "Enemy Shiny Star"),
                ("gui.hud_player_shiny_indicator", "Player Shiny Star"),
                ("gui.reviewer_text_message_box", "Battle Log"),
                ("gui.hud_styling", "Styling"),
            ],
        },
    },
    {
        "label": "Sound",
        "settings": [
            "Enable Sound Effects",
            "Enable Sounds",
            "Enable Battle Sounds",
            "Volume",
        ],
    },
    {
        "label": "Study",
        "settings": [
            "Goal of Daily Average Cards",
            "Card Max Time",
            "Cash Reward Per Interval",
            "Cards Per Cash Reward",
        ],
    },
    {
        "label": "Generations",
        "settings": [
            "Active Region",
        ],
        # The 9 per-generation booleans render as a single chip row instead of
        # 9 separate Enabled/Disabled rows — much faster to scan and toggle.
        "chip_group": {
            "label": "Enabled Generations",
            "description": (
                "Toggle which generations can spawn. Disabled gens are "
                "excluded from the encounter pool entirely; the Active Region "
                "only biases spawns within the enabled set."
            ),
            "keys": [
                ("misc.gen1", "Gen 1"),
                ("misc.gen2", "Gen 2"),
                ("misc.gen3", "Gen 3"),
                ("misc.gen4", "Gen 4"),
                ("misc.gen5", "Gen 5"),
                ("misc.gen6", "Gen 6"),
                ("misc.gen7", "Gen 7"),
                ("misc.gen8", "Gen 8"),
                ("misc.gen9", "Gen 9"),
            ],
        },
    },
    {
        "label": "Mobile Reviews",
        "settings": [
            {
                "key": "mobile.enabled",
                "label": "Mobile Reviews Integration",
                "description": "Expose reviews completed on AnkiMobile during syncing.",
                "type": "boolean",
            },
            {
                "key": "mobile.resolution_mode",
                "label": "Mobile Resolution Mode",
                "description": (
                    "Choose how your synced mobile reviews are processed:<br>"
                    "• <strong>Manual</strong>: Play through battles one-by-one with full combat animations under the Mobile Reviews tab, with the choice to manually override your active companion before starting each battle.<br>"
                    "• <strong>Auto-Resolve</strong>: Automatically and silently resolve all pending battles in the background immediately after syncing, using optimal team matchups and payouts."
                ),
                "type": "select",
                "options": [
                    {"value": "manual", "label": "Manual"},
                    {"value": "auto", "label": "Auto-Resolve"}
                ]
            }
        ]
    },
    {
        "label": "Leaderboard",
        "settings": [
            "Enable Leaderboard Sync",
            "Username",
            "API Key"
        ]
    }
]


SECRET_SETTING_PLACEHOLDER = "********"


def serialize_secret_setting(value):
    """Return a browser-safe representation of a stored secret setting."""
    configured = bool(value)
    return {
        "type": "password",
        "value": SECRET_SETTING_PLACEHOLDER if configured else "",
        "secret_configured": configured,
        "secret_placeholder": SECRET_SETTING_PLACEHOLDER,
    }


def is_unchanged_secret_placeholder(value) -> bool:
    """Whether a web-settings value means "leave the stored secret unchanged"."""
    return value == SECRET_SETTING_PLACEHOLDER


def display_setting_value(key, value):
    """Redact secrets before rendering a settings-change summary."""
    if key == "leaderboard.api_key" and value:
        return SECRET_SETTING_PLACEHOLDER
    return value


# Active region dropdown options — preserved verbatim from the legacy window.
ACTIVE_REGION_OPTIONS = [
    {"value": None, "label": "No Region"},
    {"value": "kanto", "label": "Kanto (Gen 1)"},
    {"value": "johto", "label": "Johto (Gen 2)"},
    {"value": "hoenn", "label": "Hoenn (Gen 3)"},
    {"value": "sinnoh", "label": "Sinnoh (Gen 4)"},
    {"value": "unova", "label": "Unova (Gen 5)"},
    {"value": "kalos", "label": "Kalos (Gen 6)"},
    {"value": "alola", "label": "Alola (Gen 7)"},
    {"value": "galar", "label": "Galar (Gen 8)"},
    {"value": "hisui", "label": "Hisui (Gen 8)"},
    {"value": "paldea", "label": "Paldea (Gen 9)"},
]


def validate_and_clamp(config, original=None):
    """Apply the legacy window's save-time bounds. Returns (config, adjustments)
    where adjustments is a list of human-readable strings describing what
    was changed (empty if nothing was clamped).

    ``original`` is the pre-edit config snapshot; it lets cards_per_round fall
    back to the prior stored value on non-numeric input, matching the legacy
    window (pyobj/settings_window.py::on_save) instead of silently resetting."""
    adjustments = []
    original = original or {}

    if "battle.cards_per_round" in config:
        config["battle.cards_per_round"] = _coerce_cards_per_round(
            config["battle.cards_per_round"],
            original.get("battle.cards_per_round", 2),
        )

    if "trainer.cash_reward_interval" in config:
        v = config["trainer.cash_reward_interval"]
        if isinstance(v, int):
            new_v = max(5, min(250, v))
            if new_v != v:
                config["trainer.cash_reward_interval"] = new_v
                adjustments.append(
                    f"Reward Interval adjusted to {new_v} (range 5–250)."
                )

    if "trainer.cash_reward_amount" in config:
        amt = config["trainer.cash_reward_amount"]
        if isinstance(amt, int):
            new_amt = max(10, min(2000, amt))
            interval = config.get("trainer.cash_reward_interval", 10)
            max_allowed = interval * 100
            if new_amt > max_allowed:
                new_amt = max_allowed
                adjustments.append(
                    f"Reward Amount capped at {new_amt}¥ (100:1 ratio limit)."
                )
            elif new_amt != amt:
                adjustments.append(
                    f"Reward Amount adjusted to {new_amt}¥ (range 10–2000)."
                )
            config["trainer.cash_reward_amount"] = new_amt

    return config, adjustments


def _coerce_cards_per_round(value, original=2):
    """Accept an int or the string "a-b" range. On unparseable input, match the
    legacy window (settings_window.py::on_save): a malformed dashed range falls
    back to 2, but non-dashed garbage preserves the prior stored value
    (``original``) rather than silently resetting to 2."""
    if isinstance(value, int):
        return 1 if value <= 0 else value
    text = str(value).strip()
    try:
        n = int(text)
        return 1 if n <= 0 else n
    except ValueError:
        pass
    if "-" in text:
        try:
            a, b = (int(x) for x in text.split("-", 1))
            low, high = min(a, b), max(a, b)
            return f"{low}-{high}"
        except ValueError:
            return 2
    return original
