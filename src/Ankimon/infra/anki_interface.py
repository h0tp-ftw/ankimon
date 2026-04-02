import os

# We import mw here locally to centralize all mw calls,
# rather than having them sprinkled across the codebase.
# This makes unit testing easier as we only have to mock this file.
try:
    from aqt import mw
    from aqt.utils import showWarning, tooltip, showInfo
except ImportError:
    # Handle the case where aqt is not available (e.g., during tests)
    mw = None
    def showWarning(msg, *args, **kwargs): pass
    def tooltip(msg, *args, **kwargs): pass
    def showInfo(msg, *args, **kwargs): pass

class AnkiInterface:
    @staticmethod
    def get_mw():
        """Returns the main Anki window object."""
        return mw

    @staticmethod
    def show_warning(msg, *args, **kwargs):
        """Displays a warning dialog."""
        if mw:
            showWarning(msg, *args, **kwargs)
        else:
            print(f"WARNING: {msg}")

    @staticmethod
    def show_tooltip(msg, *args, **kwargs):
        """Displays a tooltip."""
        if mw:
            tooltip(msg, *args, **kwargs)
        else:
            print(f"TOOLTIP: {msg}")

    @staticmethod
    def show_info(msg, *args, **kwargs):
        """Displays an info dialog."""
        if mw:
            showInfo(msg, *args, **kwargs)
        else:
            print(f"INFO: {msg}")


    @staticmethod
    def get_profile_folder():
        """Gets the Anki profile folder path."""
        if mw and mw.pm:
            return mw.pm.profileFolder()
        return os.getcwd() # Fallback for tests

anki_interface = AnkiInterface()
