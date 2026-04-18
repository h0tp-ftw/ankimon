try:
    from . import _main
except Exception as e:
    import traceback
    error_msg = f"{str(e)}\n\n{traceback.format_exc()}"
    try:
        from .pyobj.recovery import show_recovery_dialog
        show_recovery_dialog(error_msg)
    except Exception as recovery_exc:
        raise Exception(f"Ankimon Update Recovery failed to load. Original error:\n{error_msg}")
