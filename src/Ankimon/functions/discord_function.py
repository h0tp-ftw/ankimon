from __future__ import annotations

import threading
import random
import time
from typing import TYPE_CHECKING

from ..addon_files.lib.pypresence import (
    DiscordError,
    Presence,
    ResponseTimeout,
    ServerError,
)
from ..events import events

if TYPE_CHECKING:
    from ..pyobj.ankimon_tracker import AnkimonTracker


def _show_discord_error(message: str) -> None:
    events.emit("tooltip", message=message)
    try:
        from aqt import mw
        from aqt.utils import tooltip

        mw.taskman.run_on_main(lambda: tooltip(message))
    except Exception:
        # The notification is best-effort during headless runs and shutdown.
        return


def _run_on_main(callback) -> None:
    """Run ``callback`` on Anki's GUI thread.

    Qt widgets may only be created on the main thread, and anything routed
    through ``logger_obj.log_and_showinfo`` ends in a ``QMessageBox``. The
    presence worker is a plain daemon thread, so whatever it wants to show has
    to hop over here first. Only a missing ``aqt`` falls back to running inline:
    that is the headless case, where there is no GUI thread to violate. Anything
    else — a half-built ``mw`` during a hot reload, say — is left to raise rather
    than quietly doing the unsafe thing on the worker.
    """
    try:
        from aqt import mw
    except ImportError:
        callback()
        return
    mw.taskman.run_on_main(callback)


class DiscordPresence:
    #: Seconds between connection attempts once one has failed.
    RECONNECT_INTERVAL = 60
    #: How long a UI-thread caller waits for the worker to finish an RPC
    #: round-trip before skipping its own call. Deliberately short: these run on
    #: Anki's main thread from the sync and reviewer hooks.
    RPC_LOCK_TIMEOUT = 0.5
    #: Errors that mean "this request failed", not "the pipe is dead".
    #: ``read_output()`` raises ``ResponseTimeout`` when Discord is slow to reply
    #: and ``ServerError`` / ``DiscordError`` on an error payload, all with the
    #: socket still open; only ``BrokenPipeError`` / ``struct.error`` become
    #: ``PipeClosed``. Deliberately no ``RuntimeError``: ``_rpc_lock`` makes the
    #: "this event loop is already running" collision impossible, so the only
    #: RuntimeError left here is "Event loop is closed", which is fatal.
    TRANSIENT_RPC_ERRORS = (ResponseTimeout, ServerError, DiscordError)

    def __init__(
        self,
        client_id,
        large_image_url,
        ankimon_tracker,
        logger,
        settings_obj,
        parent=None,
    ):
        self.loop = False
        self.logger_obj = logger
        self.connected = False
        # Every plain attribute is assigned here, before anything can fail, so a
        # failed connect can never leave a half-built object whose start() /
        # stop() / update_presence() then blow up with "no attribute 'settings'".
        self.RPC = None
        self.large_image_url = large_image_url
        self.ankimon_tracker: AnkimonTracker = ankimon_tracker
        self.settings = settings_obj
        # Discord renders this as the "elapsed" timer, so it marks the study
        # session and is stamped once. A reconnect must not reset it, or someone
        # who has been reviewing for hours watches it snap back to 0:00.
        self.start_time = time.time()
        self.thread = None
        self.quotes = [
            "Study hard, your Ankimon is watching!",
            "Ankimon, I choose you—let’s master this deck!",
            "Your knowledge is super effective!",
            "Critical hit! You mastered that concept.",
            "Never give up! Every review gets you closer to evolution.",
            "Your brain gained 50 XP! Keep going!",
            "It’s dangerous to go alone—take your Ankimon deck!",
            "A wild Flashcard appeared! What will you do?",
            "Evolve your knowledge—level up with every session!",
            "Gotta review ‘em all, Ankimon style!"
        ]
        self.state = random.choice(self.quotes)
        self._client_id = client_id
        self._checked_conflicts = False
        self._first_attempt = True
        # Monotonic, not wall clock: resuming from sleep or an NTP correction
        # must not silently extend (or erase) the retry interval.
        self._last_connect_attempt = float("-inf")
        # pypresence drives one asyncio loop per client, and update() / clear()
        # both call run_until_complete() on it. Two threads inside that loop
        # raise "This event loop is already running" and leave the
        # request/response stream off by one, so every RPC call takes this lock.
        # Re-entrant because the failure paths drop the connection while holding
        # it.
        self._rpc_lock = threading.RLock()
        # No connect here. setup_discord_hooks() builds this object at add-on
        # import time on Anki's main thread, and pypresence's handshake reads
        # (baseclient.handshake) have no timeout at all, so a Discord that
        # accepts the socket and never replies would hang Anki's boot. The first
        # start() worker does the initial connect instead.

    def _connect_due(self) -> bool:
        """Is another connection attempt allowed yet?

        ``start()`` checks this too, not just the worker, so a session spent
        with Discord closed doesn't spawn a thread per answered card only for it
        to fail this same test and exit.
        """
        if self.connected or self._first_attempt:
            return True
        return time.monotonic() - self._last_connect_attempt >= self.RECONNECT_INTERVAL

    def _connect(self) -> bool:
        """Open the RPC connection. A no-op when already connected, rate-limited
        when not, and safe to call on every review — this is what makes Discord
        opening *after* Anki started still get picked up.

        Runs on the worker thread only.
        """
        if self.connected:
            return True
        if not self._connect_due():
            return False
        initial = self._first_attempt
        self._first_attempt = False
        try:
            with self._rpc_lock:
                rpc = Presence(self._client_id)
                rpc.connect()
                self.RPC = rpc
            self.connected = True
        except Exception as e:
            # Only a *failed* attempt arms the throttle. Stamping successful ones
            # too would make a drop that happens seconds later sit out the whole
            # interval before the first retry.
            self._last_connect_attempt = time.monotonic()
            self.RPC = None
            self.connected = False
            # "info" rather than "debug" for the quiet retries: InfoLogger has no
            # debug branch, so a debug line is written nowhere and someone
            # reporting "presence never came back" would have no record of them.
            self.logger_obj.log(
                "error" if initial else "info", f"Error with Discord setup: {e}"
            )
            if initial:
                _show_discord_error("Error with Discord setup. Is Discord running?")
            return False

        # First successful connection only, and on the GUI thread: this method
        # runs on the worker and the warning ends in a QMessageBox.
        if not self._checked_conflicts:
            self._checked_conflicts = True
            _run_on_main(self._warn_conflicting_addons)
        return True

    def _warn_conflicting_addons(self):
        """Warn about other add-ons driving Discord Rich Presence. Main thread
        only — see ``_run_on_main``."""
        try:
            conflicting_addons = check_conflicting_discord_addons()
            if conflicting_addons:
                conflict_list = ', '.join(conflicting_addons)
                self.logger_obj.log_and_showinfo("warning", f"⚠️ Conflicting Discord Rich Presence addons detected: \n{conflict_list}\n\nPlease remove them to avoid issues with Ankimon's Discord status, or turn off Discord Rich Presence in Ankimon settings :) ")
        except Exception as e:
            self.logger_obj.log(
                "error", f"Error warning about conflicting Discord addons: {e}"
            )

    def _drop_connection(self, close: bool = False):
        """Release the RPC and mark the presence disconnected so the next
        ``start()`` reconnects.

        ``close=True`` also shuts the client down, which nothing in pypresence
        does for us — dropping the reference alone leaves the socket and its two
        event loops for the garbage collector. Only the worker asks for that:
        ``Presence.close()`` writes to the pipe and closes the event loop, and on
        Windows a pipe transport fails writes asynchronously, so it is not
        something to run on the UI thread. Off the worker we settle for the GC.
        """
        with self._rpc_lock:
            rpc, self.RPC = self.RPC, None
            self.connected = False
        if close and rpc is not None:
            try:
                rpc.close()
            except Exception:
                pass

    def _main_thread_rpc(self, action, log_prefix, tooltip_message):
        """Run one RPC call from a UI-thread hook.

        Takes ``_rpc_lock`` so it cannot enter pypresence's asyncio loop while
        the worker is already inside it — but only briefly. These run on Anki's
        main thread during a sync or when the reviewer closes, so a busy worker
        means we skip the call rather than stall the UI behind a round-trip.
        """
        if not self._rpc_lock.acquire(timeout=self.RPC_LOCK_TIMEOUT):
            self.logger_obj.log(
                "info", f"{log_prefix}: presence worker busy, skipped"
            )
            return
        try:
            if self.RPC is None:
                return
            action(self.RPC)
        except Exception as e:
            self.logger_obj.log("error", f"{log_prefix}: {e}")
            if isinstance(e, self.TRANSIENT_RPC_ERRORS):
                # The socket is still good; don't discard a working connection
                # or alarm the user over one late reply.
                return
            self._drop_connection()
            _show_discord_error(tooltip_message)
        finally:
            self._rpc_lock.release()

    def _get_special_quotes(self):
        return [
            f"In battle with {self.ankimon_tracker.main_pokemon.name.capitalize()} Lvl {self.ankimon_tracker.main_pokemon.level}",
            f"{self.ankimon_tracker.main_pokemon.name.capitalize()} is fired up and ready to fight!",
            f"{self.ankimon_tracker.main_pokemon.name.capitalize()} is waiting for your next move!",
            f"The battle is intense, but {self.ankimon_tracker.main_pokemon.name.capitalize()} won't back down!",
            f"The stakes are high! {self.ankimon_tracker.main_pokemon.name.capitalize()} needs your help to win this fight!",
            f"Victory is within reach for {self.ankimon_tracker.main_pokemon.nickname or self.ankimon_tracker.main_pokemon.name.capitalize()}!",
            f"{self.ankimon_tracker.main_pokemon.name.capitalize()} is determined to show its strength!",

            f"Keep your guard up! {self.ankimon_tracker.enemy_pokemon.name.capitalize()} is no pushover.",
            f"Strategy is key! Plan your moves wisely against {self.ankimon_tracker.enemy_pokemon.name.capitalize()}!",
            f"Currently battling {self.ankimon_tracker.enemy_pokemon.name.capitalize()} Lvl {self.ankimon_tracker.enemy_pokemon.level}",
            f"The opponent {self.ankimon_tracker.enemy_pokemon.name.capitalize()} seems tough—stay sharp!",
            f"Level up and take down {self.ankimon_tracker.enemy_pokemon.name.capitalize()}!",

            f"Total reviews completed: {self.ankimon_tracker.get_total_reviews()}",
            f"{self.ankimon_tracker.card_ratings_count['good']} good reviews so far—keep it up!",
            f"You've marked {self.ankimon_tracker.card_ratings_count['again']} cards as Again—let's focus and improve!",
            f"Great job! {self.ankimon_tracker.card_ratings_count['easy']} cards rated Easy!",
            f"{self.ankimon_tracker.card_ratings_count['hard']} cards rated Hard—you're tackling the tough ones!",
        ]

    def update_presence(self):
        """
        Update the Discord Rich Presence with a new state message.
        """
        if not self.connected:
            return
        while self.loop:
            try:
                with self._rpc_lock:
                    if self.RPC is None:
                        break
                    self.RPC.update(
                        state = random.choice(self.quotes) if int(self.settings.get("misc.discord_rich_presence_text")) == 1 else random.choice(self._get_special_quotes()),
                        large_image=self.large_image_url,
                        start=self.start_time
                    )
            except Exception as e:
                self.logger_obj.log("error", f"Error with Discord Rich Presence: {e}")
                if isinstance(e, self.TRANSIENT_RPC_ERRORS):
                    # A slow reply or a server-side error: the pipe is still
                    # open, so keep the worker and try again on the next tick.
                    pass
                else:
                    # Connection dropped (Discord was closed mid-session). Clear
                    # self.loop too: this worker is about to exit, and start()
                    # decides what to do next.
                    self.loop = False
                    self._drop_connection(close=True)
                    _show_discord_error(
                        "Error with Discord Rich Presence. Is Discord running?"
                    )
                    return
            # Outside the lock: holding it across the sleep would park every
            # main-thread caller for up to 30 seconds.
            time.sleep(30)  # Sleep for 30 seconds before updating again

    def start(self):
        """
        Ensure the presence worker is running (idempotent).

        The worker owns the connection: ``_connect()`` runs inside it, never
        here, so answering a card is never blocked behind pypresence's untimed
        handshake reads. Main-thread hooks still reach the RPC through
        ``stop()`` / ``stop_presence()``, so every RPC access takes
        ``_rpc_lock``.
        """
        try:
            if self.thread is not None and self.thread.is_alive():
                # A worker is still parked in its sleep. Re-arm the loop so it
                # resumes on the next wake instead of exiting — this is what
                # brings the presence back when someone leaves the reviewer and
                # returns inside the same 30s window.
                self.loop = True
                return
            if not self._connect_due():
                return
            self.loop = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        except Exception as e:
            self.loop = False
            self.logger_obj.log("error",f"Error starting Discord Rich Presence: {e}")
            _show_discord_error(
                "Error starting Discord Rich Presence. Is Discord running?"
            )

    def _run(self):
        """Worker body: connect, then loop updating presence. Off the UI thread
        so a slow or blocking RPC handshake can't stall a card answer."""
        try:
            if self._connect():
                self.update_presence()
        except Exception as e:
            self.logger_obj.log(
                "error", f"Error in Discord Rich Presence worker: {e}"
            )
        finally:
            # Never leave a dead worker looking like a running one.
            self.loop = False

    def stop(self):
        """
        Stop updating the Discord Rich Presence.
        """
        self.loop = False
        if not self.connected:
            return
        # self.thread is deliberately NOT reset here. The worker may still be
        # parked in its sleep, and nulling a live handle lets the next start()
        # spawn a second worker beside it — two threads then share one asyncio
        # loop and collide.
        self._main_thread_rpc(
            lambda rpc: rpc.clear(),
            "Error clearing Discord Rich Presence",
            "Error clearing Discord Rich Presence. Please check Logger for info.",
        )

    def stop_presence(self):
        """
        Update the Discord Rich Presence to indicate a break when stopping.
        """
        self.loop = False
        if not self.connected:
            return
        self._main_thread_rpc(
            lambda rpc: rpc.update(
                state="Break time! You’ve earned it.",
                large_image=self.large_image_url,
            ),
            "Error stopping Discord Rich Presence",
            "Error stopping Discord Rich Presence. Please check Logger for info.",
        )

def check_conflicting_discord_addons():
    """
    Check for other Anki addons that may be showing Discord status.
    Returns a list of conflicting addon names if found.
    """
    try:
        from aqt import mw

        # Get list of all installed addons
        addon_manager = mw.addonManager
        installed_addons = addon_manager.allAddons()

        # Known conflicting addon identifiers and names
        conflicting_addons = {
            # Known AnkiCord and Discord addon IDs
            '933207442': 'AnkiCord - Discord Rich Presence (Customized by Shigeඞ)',
            '1133851639': 'AnkiDiscord - Discord integration for Anki',
            '1828536813': 'Ankicord - Discord Rich Presence',
            # Add more known conflicting addon IDs as discovered
        }

        found_conflicts = []

        for addon_id in installed_addons:
            try:
                # Skip if addon is not enabled
                if not addon_manager.isEnabled(addon_id):
                    continue

                # Check against known conflicting addon IDs
                if addon_id in conflicting_addons:
                    addon_name = conflicting_addons[addon_id]
                    found_conflicts.append(addon_name)
                    continue

                # Check addon metadata for potential Discord-related conflicts
                addon_meta = addon_manager.addonMeta(addon_id)
                if addon_meta:
                    addon_name = addon_meta.get('name', '').lower()
                    addon_description = addon_meta.get('description', '').lower()

                    # Keywords that indicate Discord Rich Presence functionality
                    discord_keywords = [
                        'discord', 'ankicord', 'rich presence', 'discord rpc',
                        'discord status'
                    ]

                    # Check if addon name or description contains Discord-related keywords
                    if any(keyword in addon_name for keyword in discord_keywords) or \
                    any(keyword in addon_description for keyword in discord_keywords):
                        display_name = addon_meta.get('name', f'Unknown addon ({addon_id})')
                        found_conflicts.append(display_name)

            except Exception as e:
                # Log but don't fail on individual addon checks
                if hasattr(mw, 'logger') and mw.logger:
                    mw.logger.log("debug", f"Error checking addon {addon_id}: {e}")
                continue

        return found_conflicts

    except Exception as e:
        # Return empty list if checking fails entirely
        try:
            from ..services import services

            if services.logger is not None:
                services.logger.log(
                    "error", f"Error checking for conflicting Discord addons: {e}"
                )
        except Exception:
            pass
        return []
