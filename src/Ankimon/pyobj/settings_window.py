250
rounded_pixmap = create_rounded_pixmap(scaled_pixmap, 15)
251
image_label.setPixmap(rounded_pixmap)
252
else:
253
image_label.setText("Ankimon Logo Not Found")
254
image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
255
layout.addWidget(image_label)
256
self.search_bar = QLineEdit()
257
self.search_bar.setPlaceholderText("Search settings...")
258
self.search_bar.textChanged.connect(self._on_search_changed)
259
layout.addWidget(self.search_bar)
260
scroll_area = QScrollArea()
261
scroll_area.setWidgetResizable(True)
262
scroll_area_content = QWidget()
263
scroll_area_layout = QVBoxLayout(scroll_area_content)
264
scroll_area.setWidget(scroll_area_content)
265

266
        # First handle hierarchical groups from peace/settings-enhanced
267
        hierarchical_groups = {
268
            "General": {"settings": ["Trainer Name", "Language", "Show tip of the day on startup"], "subgroups": {"Technical Settings": {"settings": ["SSH Access", "Receive Ankimon News", "AnkiWeb Sync"]}, "Discord Integration": {"settings": ["Discord Rich Presence - Ankimon", "Discord Rich Presence - Quote Type"]} } },
269
            "Battle": {"settings": ["Damage in reviewer", "Automatic Battle", "Cards per round", "Show Main Pokémon in Reviewer", "Show Pokémon buttons", "Pop-Up on Defeat", "Show Text Message Box in Reviewer", "Message Box Display Time"], "subgroups": {"Fight Hotkeys": {"settings": ["Key for Defeat", "Key for Catching", "Key for Opening/Closing Ankimon", "Allow Choosing Moves"]}, "HP, XP and Level Settings": {"settings": ["HP Bar Configuration", "XP Bar Configruation", "XP Bar Location", "Remove Level Cap"]} } },
270
            "Styling": {"settings": ["Styling in Reviewer", "Animate Time", "HP Bar Thickness", "Reviewer Image as GIF", "View Main Pokémon Front"]},
271
            "Sound": {"settings": ["Enable Sound Effects", "Enable Sounds", "Enable Battle Sounds"]},
272
            "Study": {"settings": ["Goal of Daily Average", "Card Max Time"]},
273
            "Generations": {"settings": ["Generation 1", "Generation 2", "Generation 3", "Generation 4", "Generation 5", "Generation 6", "Generation 7", "Generation 8", "Generation 9"]}
274
        }
275

276
        for l1_title, l1_data in hierarchical_groups.items():
277
            self.group_states[l1_title] = True
278
            l1_widgets = []
279
            l1_button = self._create_title(l1_title, level=1)
280
            scroll_area_layout.addWidget(l1_button)
281
            self.title_buttons[l1_title] = l1_button
282
            for friendly_name in l1_data.get("settings", []):
283
                key = self.key_map.get(friendly_name)
284
                widgets, name, desc = self._create_setting(key, scroll_area_layout)
285
                if widgets:
286
                    l1_widgets.extend(widgets)
287
                    self.searchable_settings.append({"widgets": widgets, "friendly_name": name, "description": desc, "l1_title": l1_title, "l2_title": None})
288
            if "subgroups" in l1_data:
289
                for l2_title, l2_data in l1_data["subgroups"].items():
290
                    self.group_states[l2_title] = True
291
                    l2_widgets = []
292
                    l2_button = self._create_title(l2_title, level=2)
293
                    scroll_area_layout.addWidget(l2_button)
294
                    self.title_buttons[l2_title] = l2_button
295
                    l1_widgets.append(l2_button)
296
                    for friendly_name in l2_data.get("settings", []):
297
                        key = self.key_map.get(friendly_name)
298
                        widgets, name, desc = self._create_setting(key, scroll_area_layout)
299
                        if widgets:
300
                            l1_widgets.extend(widgets)
301
                            l2_widgets.extend(widgets)
302
                            self.searchable_settings.append({"widgets": widgets, "friendly_name": name, "description": desc, "l1_title": l1_title, "l2_title": l2_title})
303
                    self.group_widgets[l2_title] = l2_widgets
304
                    l2_button.clicked.connect(lambda _, t=l2_title, b=l2_button: self._toggle_group_visibility(t, b))
305
            self.group_widgets[l1_title] = l1_widgets
306
            l1_button.clicked.connect(lambda _, t=l1_title, b=l1_button: self._toggle_group_visibility(t, b))
307

308
        # Now add label-based settings handling from main branch
309
        # Track label-based settings
310
        self.label_settings = {}
311

312
        keys_to_skip = {"debug_mode", "deprecated_setting", "trainer.cash", "trainer.xp", "trainer.id", "trainer.sprite", "misc.last_tip_index"}
313

314
        # Handle different setting types
315
        for key, value in self.config.items():
316
            if key in keys_to_skip:
317
                continue
318

319
            friendly_name = self.friendly_names.get(key, key)  # Friendly name if available
320

321
            if isinstance(value, bool):
322
                label = QLabel(friendly_name)
323
                description_label = QLabel(self.descriptions.get(key, "No description available."))
324

325
                # Enable word wrap and set maximum width for the description label