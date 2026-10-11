
## Team screen ↔ Pokémon PC (party edits)

`team_functions._write_team` (every PC party edit) calls
`services.items_web.notify_team_changed()`. The shell pushes the fresh
`get_team_data()` payload into the Team page as `window.teamChangedElsewhere(data)`
only when the Team screen is visible, current and loaded. The page reloads a clean
view, keeps an unsaved draft (and says so in the status line), and sends the raw
`team_ids` it loaded back with Save; `ProfileData.handle_save_team` refuses the save
as `stale` when the stored ids no longer match. `harness/checks/probe_real_team_sync.py`
drives both windows together in the real browser.
