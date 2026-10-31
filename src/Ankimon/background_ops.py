"""Collection-free work and automatic UI scheduling across supported Anki versions."""

import inspect
import time


def run_in_background(task, on_done=None, *, taskman=None):
    """Submit once without occupying Anki's serialized collection executor.

    Anki 2.1.66 has a shared pool and no ``uses_collection`` argument. Detect
    that API before submitting: retrying a TypeError could run a task twice.
    Completion callbacks are delivered on the GUI thread by Anki's taskman.
    """
    if taskman is None:
        from aqt import mw

        taskman = mw.taskman
    submit = taskman.run_in_background
    kwargs = {}
    if "uses_collection" in inspect.signature(submit).parameters:
        kwargs["uses_collection"] = False
    return submit(task, on_done, **kwargs)


def run_query(task, success, failure):
    """Run collection-independent work and deliver its result on the GUI thread."""
    from aqt import mw
    from aqt.operations import QueryOp

    if callable(getattr(QueryOp, "without_collection", None)):
        return (
            QueryOp(parent=mw, op=lambda _col: task(), success=success)
            .failure(failure)
            .without_collection()
            .run_in_background()
        )

    def done(future):
        try:
            result = future.result()
        except Exception as exc:
            failure(exc)
        else:
            # Match QueryOp: a success callback exception is not an op failure.
            success(result)

    return run_in_background(task, done)


def call_when_anki_idle(
    callback, reason, *, on_cancel=None, delay_ms=0, profile_bound=True
):
    """Defer automatic UI until a profile is open and Anki is idle.

    Always leave the current callback stack first. In particular,
    profile_did_open fires *before* Anki starts auto-sync. Never bypass a busy
    progress dialog after a timeout: its modality is the reason for waiting.
    Work scheduled for a closed profile or torn-down add-on is discarded.
    Returns a cancellation function; all functions here run on the GUI thread.
    """
    from aqt import mw
    from aqt.qt import QApplication, QTimer

    from .services import services

    profile_col = getattr(mw, "col", None) if profile_bound else None
    generation = getattr(services, "_background_generation", 0)
    pending = getattr(services, "_idle_callbacks", None)
    if pending is None:
        pending = services._idle_callbacks = set()
    started = time.monotonic()
    finished = False
    warned = False

    def finish():
        nonlocal finished
        finished = True
        pending.discard(cancel)

    def cancel():
        if finished:
            return
        finish()
        if on_cancel is not None:
            on_cancel()

    def check():
        nonlocal warned
        if finished:
            return
        if generation != getattr(services, "_background_generation", 0):
            cancel()
            return
        col = getattr(mw, "col", None)
        if profile_col is not None and col is not profile_col:
            cancel()
            return
        busy = getattr(getattr(mw, "progress", None), "busy", None)
        modal = getattr(QApplication, "activeModalWidget", None)
        if (
            col is None
            or (callable(busy) and busy())
            or (callable(modal) and modal() is not None)
        ):
            if not warned and time.monotonic() - started >= 30:
                warned = True
                logger = getattr(services, "logger", None)
                if logger is not None:
                    logger.log("info", f"Ankimon waiting for Anki to be idle: {reason}")
            QTimer.singleShot(100, check)
            return
        finish()
        callback()

    pending.add(cancel)
    try:
        QTimer.singleShot(delay_ms, check)
    except Exception:
        cancel()
        raise
    return cancel


def cancel_pending_callbacks():
    """Retire automatic callbacks before hot-reload pumps Qt's event queue."""
    from .services import services

    services._background_generation = getattr(services, "_background_generation", 0) + 1
    for cancel in tuple(getattr(services, "_idle_callbacks", ())):
        cancel()
