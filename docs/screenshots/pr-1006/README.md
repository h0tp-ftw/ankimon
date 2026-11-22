# Compact evolution requirements

These screenshots come from the real Qt Pokémon PC, booted through the Tier 2
harness with a disposable SQLite save and real Pokémon sprites. The Anki host is
faked; the widgets, layout, evolution logic and database are the add-on's own.
No user save is opened. Sprite files are copied into the disposable profile.

The light screenshots use a 1280 × 720 window. The dark screenshot uses
1100 × 600 and scrolls to the expanded requirements. The action footer remains
visible. The two detail images are direct captures of the requirements widget.

| Scenario | Screenshot |
| --- | --- |
| Eevee: day/night and unmet Fairy-move gate | [Full window](eevee-routes.png) · [Detail](eevee-routes-detail.png) |
| Eevee: expanded item alternatives | [Full window](eevee-items-expanded.png) · [Detail](eevee-items-detail.png) |
| Eevee: Fairy move selects Sylveon | [Screenshot](eevee-fairy-move.png) |
| Eevee: ready evolution button | [Screenshot](eevee-ready.png) |
| Golbat: one friendship target | [Screenshot](golbat-routes.png) |
| Riolu: daytime condition | [Screenshot](riolu-routes.png) |
| Kirlia: level and item alternatives | [Screenshot](kirlia-routes.png) |
| Pikachu: item requirement immediately visible | [Screenshot](pikachu-items.png) |
| Dark theme, expanded routes, smaller window | [Screenshot](eevee-dark-small.png) |

Regenerate from the repository root:

```sh
QT_QPA_PLATFORM=offscreen python3 -m harness.checks.probe_real_evolution_requirements \
  --screenshots docs/screenshots/pr-1006 \
  --sprites /path/to/sprites
```

The sprite directory should contain `front_default/133.png`, `42.png`, `447.png`,
`25.png` and `281.png`. Omitting `--sprites` uses the harness placeholder artwork;
the layout assertions still run. Omitting `--screenshots` runs validation without
writing images, as the Tier 2 CI job does.

The probe verifies route selection, item expansion without window growth, label
height, horizontal bounds, footer visibility, ready buttons, and both window sizes.
