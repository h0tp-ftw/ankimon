import re

with open("src/Ankimon/gui_classes/pokemon_details.py", "r") as f:
    content = f.read()

sig_str = """    gif_in_collection,
    remove_levelcap: bool,
    logger: ShowInfoLogger,
    refresh_callback,"""
new_sig_str = """    gif_in_collection,
    remove_levelcap: bool,
    logger: ShowInfoLogger,
    refresh_callback,
    monthly_month: str = None,"""
content = content.replace(sig_str, new_sig_str)

label_str = """        name_label = QLabel(display_full_name)
        name_label.setFont(namefont)
        description_label = QLabel(description_txt)"""
new_label_str = """        name_label = QLabel(display_full_name)
        name_label.setFont(namefont)

        monthly_label = None
        if monthly_month:
            monthly_label = QLabel(f"Monthly Challenge: {monthly_month}")
            monthly_font = load_custom_font(12, language)
            monthly_label.setFont(monthly_font)
            monthly_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            monthly_label.setStyleSheet("color: #fbbf24; font-weight: bold; letter-spacing: 0.5px;")

        description_label = QLabel(description_txt)"""
content = content.replace(label_str, new_label_str)

layout_str = """        header_layout = QVBoxLayout(header_widget)
        header_layout.addWidget(name_label)
        header_layout.addLayout(first_layout)"""
new_layout_str = """        header_layout = QVBoxLayout(header_widget)
        header_layout.addWidget(name_label)
        if monthly_label:
            header_layout.addWidget(monthly_label)
        header_layout.addLayout(first_layout)"""
content = content.replace(layout_str, new_layout_str)

wrapper_call = """    header_widget, stats_tabs, footer_widget, _stats_dict = (
        PokemonCollectionDetailsSplit(
            name=name,
            level=level,
            id=id,
            shiny=shiny,
            ability=ability,
            type=type,
            detail_stats=detail_stats,
            attacks=attacks,
            base_experience=base_experience,
            growth_rate=growth_rate,
            ev=ev,
            iv=iv,
            gender=gender,
            nickname=nickname,
            individual_id=individual_id,
            pokemon_defeated=pokemon_defeated,
            everstone=everstone,
            captured_date=captured_date,
            language=language,
            gif_in_collection=gif_in_collection,
            remove_levelcap=remove_levelcap,
            logger=logger,
            refresh_callback=refresh_callback,
            initial_tab_index=initial_tab_index,
            tab_changed_callback=tab_changed_callback,
            nature=nature,
            base_stats=base_stats,
            old_stats=old_stats,
            friendship=friendship,
            evolution_rejected=evolution_rejected,
            friendship_time_enabled=friendship_time_enabled,
            trigger_evo_callback=trigger_evo_callback,
            show_sprites=show_sprites,
        )
    )"""

new_wrapper_call = """    header_widget, stats_tabs, footer_widget, _stats_dict = (
        PokemonCollectionDetailsSplit(
            name=name,
            level=level,
            id=id,
            shiny=shiny,
            ability=ability,
            type=type,
            detail_stats=detail_stats,
            attacks=attacks,
            base_experience=base_experience,
            growth_rate=growth_rate,
            ev=ev,
            iv=iv,
            gender=gender,
            nickname=nickname,
            individual_id=individual_id,
            pokemon_defeated=pokemon_defeated,
            everstone=everstone,
            captured_date=captured_date,
            language=language,
            gif_in_collection=gif_in_collection,
            remove_levelcap=remove_levelcap,
            logger=logger,
            refresh_callback=refresh_callback,
            monthly_month=monthly_month,
            initial_tab_index=initial_tab_index,
            tab_changed_callback=tab_changed_callback,
            nature=nature,
            base_stats=base_stats,
            old_stats=old_stats,
            friendship=friendship,
            evolution_rejected=evolution_rejected,
            friendship_time_enabled=friendship_time_enabled,
            trigger_evo_callback=trigger_evo_callback,
            show_sprites=show_sprites,
        )
    )"""

content = content.replace(wrapper_call, new_wrapper_call)

with open("src/Ankimon/gui_classes/pokemon_details.py", "w") as f:
    f.write(content)
