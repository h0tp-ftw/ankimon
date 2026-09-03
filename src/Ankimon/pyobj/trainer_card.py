from ..resources import trainer_sprites_path, mypokemon_path, team_pokemon_path
from ..functions.trainer_functions import find_trainer_rank
from ..functions.badges_functions import get_achieved_badges
from ..services import services
from ..events import events
import math
import json
import time


# Constants for leveling
BASE_XP = 50  # Base XP required for level 1
EXPONENTIAL_FACTOR = 1.5  # Scaling factor for exponential XP curve

# Tier-based XP rewards (can be extended)
POKEMON_TIERS = {
    "normal": 10,
    "baby": 16,
    "ultra": 30,
    "legendary": 120,
    "mythical": 160,
}

# Leaderboard pushes are rate limited. TrainerCard.sync_leaderboard() is wired to
# singletons.notify_stats_changed(), which fires on every XP gain, catch and cash
# reward, so an unthrottled hook would build a payload and open a socket per
# review. The leaderboard only ever needs the newest snapshot, so dropping the
# in-between pushes loses nothing: the next gameplay event publishes the newer
# numbers.
LEADERBOARD_SYNC_MIN_INTERVAL = 60.0  # seconds

# Monotonic timestamp of the last push handed to the leaderboard module. Module
# level rather than per-instance, so that rebuilding the TrainerCard (profile
# load, hot reload) cannot silently reset the rate limit; the call sites that
# legitimately need to bypass it pass force=True.
_last_leaderboard_sync = 0.0


class TrainerCard:
    def __init__(
        self,
        logger,
        main_pokemon,
        settings_obj,
        trainer_name,
        trainer_id,
        level=1,
        achievements=None,
        team=None,
        image_path=trainer_sprites_path,
        league="unranked",
    ):
        self.logger = logger
        self.main_pokemon = main_pokemon
        self.settings_obj = settings_obj
        self.trainer_name = trainer_name  # Name of the trainer
        self.favorite_pokemon = main_pokemon.name  # Trainer's favorite Pokémon
        self.trainer_id = trainer_id  # Unique ID for the trainer
        self.level = int(settings_obj.get("trainer.level"))  # Trainer's level
        self.xp = int(settings_obj.get("trainer.xp"))  # Experience points
        self.total_xp = int(settings_obj.get("trainer.total_xp", 0)) # Total Experience points
        self.achievements = (
            achievements if achievements else []
        )  # List of achievements (if any)
        self.team = team if team is not None else self.get_team()  # Team as a simple string
        highest_level = self.get_highest_level_pokemon()
        self.highest_level = highest_level  # Highest level Pokémon
        highest_pokemon_level = int(self.highest_pokemon_level())
        self.image_path = (
            f"{trainer_sprites_path}"
            + "/"
            + settings_obj.get("trainer.sprite")
            + ".png"
        )
        league = find_trainer_rank(
            highest_pokemon_level, int(self.level)
        )  # Trainer's rank in the Pokémon world
        self.league = league
        cash = int(settings_obj.get("trainer.cash"))
        self.cash = cash

        # Startup sync. force=True because a freshly built card means a fresh
        # profile, whose true state must go up even if some previous card in
        # this process pushed moments ago. (A same-process account switch goes
        # through refresh(), not through here.)
        self.sync_leaderboard(force=True)

    def refresh(self):
        """Reload trainer data from current settings + database (in place).

        Used after a database switch (swap_ankimon_account) so the cached
        level/xp/cash/sprite/league/team fields reflect the now-active account.
        """
        # settings_obj can be uninitialised during a partial populate / reset;
        # fall back to safe defaults (and default each key) rather than raising
        # an AttributeError / int(None) TypeError.
        settings = self.settings_obj
        if settings is None:
            self.trainer_name = "Trainer"
            self.level = 1
            self.xp = 0
            self.total_xp = 0
            self.cash = 0
            sprite = "default"
        else:
            self.trainer_name = settings.get("trainer.name", "Trainer")
            self.level = int(settings.get("trainer.level", 1))
            self.xp = int(settings.get("trainer.xp", 0))
            self.total_xp = int(settings.get("trainer.total_xp", 0))
            self.cash = int(settings.get("trainer.cash", 0))
            sprite = settings.get("trainer.sprite", "default")
        self.image_path = f"{trainer_sprites_path}/{sprite}.png"
        self.league = find_trainer_rank(
            int(self.highest_pokemon_level()), int(self.level)
        )
        self.reload_team()
        if getattr(self, "main_pokemon", None):
            self.favorite_pokemon = self.main_pokemon.name
        else:
            self.favorite_pokemon = "None"

        # Every caller of refresh() has just changed something the leaderboard
        # publishes — the active account (swap_ankimon_account), the trainer's
        # name or sprite (the profile screen), or the settings behind them — so
        # republish. Deliberately not forced: these are user actions that can be
        # repeated quickly, and the rate limit already guarantees the change
        # goes up within one interval.
        self.sync_leaderboard()

    # Number of badges the trainer has earned
    def badge_count(self):
        return len(self.badges)

    @property
    def badges(self):
        return get_achieved_badges()

    def get_highest_level_pokemon(self):
        """Method to find the name of the highest-level Pokémon from the database."""
        try:
            db = services.db
            cursor = db.execute("SELECT name, level FROM captured_pokemon WHERE level IS NOT NULL ORDER BY level DESC LIMIT 1")
            row = cursor.fetchone()

            if not row:
                return None  # Return None if the data is empty

            return f"{row['name']} (Level {row['level']})"
        except Exception as e:
            services.ui.notify("info", f"Error getting highest level pokemon: {e}")
            return "None"

    def highest_pokemon_level(self):
        """Method to find the highest level from all Pokémon in the database."""
        try:
            db = services.db
            cursor = db.execute("SELECT level FROM captured_pokemon WHERE level IS NOT NULL ORDER BY level DESC LIMIT 1")
            row = cursor.fetchone()

            if not row:
                return 0  # Return 0 if the data is empty

            return int(row["level"])
        except Exception as e:
            services.ui.notify("info", f"Error getting highest level: {e}")
            return 0

    def add_achievement(self, achievement):
        """Method to add a new achievement"""
        self.achievements.append(achievement)

    def get_team(self):
        """Method to get the trainer's active team (team as a string)"""
        try:
            team_data = services.db.get_team()
            
            if not team_data:
                return "No Team Set"

            # Use new DB method for targeted fetch
            ids_to_fetch = [str(t.get("individual_id")) for t in team_data if t.get("individual_id")]
            my_pokemon_data = services.db.get_pokemons_by_individual_ids(ids_to_fetch)

            # Create lookup dict
            pokemon_map = {str(p.get("individual_id")): p for p in my_pokemon_data}

            pokemon_strings = []
            for pokemon in team_data:
                ind_id = str(pokemon.get("individual_id"))
                if ind_id in pokemon_map:
                    p = pokemon_map[ind_id]
                    pokemon_strings.append(f"{p.get('name')} (Level {p.get('level')})")
                else:
                    pokemon_strings.append("Unknown Pokemon")

            return ", ".join(pokemon_strings)

        except FileNotFoundError:
            return "No Team Set"
        except Exception as e:
            self.logger.log_and_showinfo("error", f"Error ; team.json: {e}")
            return "Error Loading Team"

    def set_team(self, team_pokemons):
        """Method to set the trainer's active team (team as a string)"""
        self.team = ", ".join(team_pokemons)

    def reload_team(self):
        """Reload the team data from the file"""
        self.team = self.get_team()

    def sync_leaderboard(self, force=False):
        """Publish the trainer's current stats to the Ankimon leaderboard.

        Called at construction, from :meth:`refresh` (account switch, rename,
        sprite change), and from ``singletons.notify_stats_changed()`` on every
        gameplay stat change — so it is built to be cheap and safe to call very
        often:

        * the ``misc.leaderboard`` opt-in is read first, before any database
          work, so the users who leave the leaderboard off (the default) pay
          nothing for the hook;
        * pushes are rate limited to one per ``LEADERBOARD_SYNC_MIN_INTERVAL``
          seconds unless ``force`` is set. The leaderboard can therefore lag the
          live save by up to one interval, and by the tail of a session that
          ended inside it — the forced startup push republishes the true state
          on the next launch, so it converges without a shutdown hook;
        * every value is read fresh instead of from the cached ``self.*``
          attributes. Those go stale mid-session — ``self.league`` is only
          recomputed by :meth:`refresh`, and ``self.cash`` is not updated by
          shop purchases — so uploading them would re-create exactly the
          staleness this hook exists to remove.

        Never raises and never opens a dialog: it runs on gameplay write paths
        where a leaderboard hiccup must not interrupt a review, so it avoids
        the helpers that report through ``services.ui`` and prints instead,
        matching ``ankimon_leaderboard``'s own reporting.

        Returns True when a payload was handed to the leaderboard module, and
        False when the sync was opted out of, rate limited, unavailable, or
        failed.
        """
        global _last_leaderboard_sync

        settings_obj = self.settings_obj
        if settings_obj is None or not settings_obj.get("misc.leaderboard"):
            return False
        if services.db is None:
            return False

        now = time.monotonic()
        if not force and (now - _last_leaderboard_sync) < LEADERBOARD_SYNC_MIN_INTERVAL:
            return False

        try:
            # Lazy import: ankimon_leaderboard pulls in Qt/Anki, so importing it
            # at module top would break the headless core. Imported here instead,
            # and an ImportError simply means "no leaderboard available" (harness).
            from .ankimon_leaderboard import sync_data_to_leaderboard
        except ImportError:
            return False

        # Consume the rate-limit window up front, so a payload that keeps
        # failing (missing table, server down) backs off for a full interval
        # instead of re-running these queries on every single stat change.
        _last_leaderboard_sync = now

        try:
            level = int(settings_obj.get("trainer.level", 1))
            # Query the highest level here rather than through
            # highest_pokemon_level(): that helper reports a database error via
            # services.ui.notify(), which in production is a modal showInfo()
            # — precisely the popup this method must never open on a review
            # path — and then returns a sentinel 0 that would quietly publish a
            # "Novice Trainer" rank over the player's real one. Failing the
            # push outright is the better answer; the next one retries.
            row = services.db.execute(
                "SELECT level FROM captured_pokemon WHERE level IS NOT NULL ORDER BY level DESC LIMIT 1"
            ).fetchone()
            highest_level = int(row["level"]) if row else 0
            data = {
                "trainerRank": f"{find_trainer_rank(highest_level, level)}",
                "trainerName": settings_obj.get("trainer.name", self.trainer_name),
                "level": max(1, level),
                "pokedex": services.db.execute("SELECT COUNT(DISTINCT pokedex_id) FROM captured_pokemon WHERE pokedex_id IS NOT NULL").fetchone()[0],
                "caughtPokemon": services.db.get_pokemon_count(),
                "trainerLevel": level,
                "highestLevel": highest_level,
                "shinies": f"{services.db.get_shiny_count()}",
                "cash": int(settings_obj.get("trainer.cash", 0)),
                "trainerSprite": f"{settings_obj.get('trainer.sprite', 'default')}.png",
            }
            sync_data_to_leaderboard(data)
        except Exception as e:
            print(f"Ankimon: Error in syncing data to leaderboard: {e}")
            return False
        return True

    def display_card_data(self):
        """Method to return trainer card data as a dictionary"""
        return {
            "trainer_name": self.trainer_name,
            "trainer_id": self.trainer_id,
            "level": self.level,
            "xp": self.xp,
            "total_xp": self.total_xp,
            "badges": self.badge_count(),
            "favorite_pokemon": self.main_pokemon.name,
            "highest_level_pokemon": self.get_highest_level_pokemon(),
            "team": self.team,
            "achievements": self.achievements,
            "xp_for_next_level": self.xp_for_next_level,
            "league": self.league,
        }

    def xp_for_next_level(self):
        """Calculate XP required for the next level."""
        return int(BASE_XP * math.pow(self.level, EXPONENTIAL_FACTOR))

    def on_level_up(self):
        """Triggered when leveling up."""
        self.logger.log_and_showinfo(
            "game", f"Congratulations! You reached Level {self.level}!"
        )

    def gain_xp(self, tier, allow_to_choose_move=False):
        """Add XP based on defeated Pokémon's tier."""
        xp_gained = POKEMON_TIERS.get(tier.lower(), 0)
        if allow_to_choose_move is True:
            xp_gained = xp_gained * 0.5
        self.settings_obj.set(
            "trainer.xp", int(self.settings_obj.get("trainer.xp") + xp_gained)
        )
        self.settings_obj.set(
            "trainer.total_xp", int(self.settings_obj.get("trainer.total_xp", 0) + xp_gained)
        )
        self.xp = self.settings_obj.get("trainer.xp")
        self.total_xp = self.settings_obj.get("trainer.total_xp")
        self.check_level_up()

        # Announce the XP / level / Total-XP change on the shared "stats_changed"
        # seam — the same signal ankimon_sync / mobile_sync / shop_obj fire as the
        # replacement for exp's singletons.notify_stats_changed(). This is aqt-free
        # core logic with no handle to the GUI shell, so it only emits the seam
        # signal; the call sites that DO hold a shell handle (shop_obj) drive the
        # open screen's actual re-render via refresh_live_screen(). events.emit is
        # free/no-op unless the agent harness (or an opt-in dev console) has
        # enabled capture, so this adds zero production overhead.
        events.emit("stats_changed")
        try:
            from ..singletons import notify_stats_changed
            notify_stats_changed()
        except Exception:
            pass

    def check_level_up(self):
        """Update level based on XP."""
        xp_needed = self.xp_for_next_level()
        while self.xp >= xp_needed:
            self.xp -= xp_needed
            self.level += 1
            self.settings_obj.set("trainer.level", self.level)
            self.settings_obj.set("trainer.xp", self.xp)
            self.on_level_up()
            # Recalculate for next iteration (in case multiple levels gained)
            xp_needed = self.xp_for_next_level()
