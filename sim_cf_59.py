# Wait! In Ankimon, auto-catch is usually handled by `battle_loop` (checking catch_rate).
# But if you open the item bag and use a Pokeball during an encounter, what does it do?
# In `Handle_Pokeball`:
# "Simulate catching the Pokémon based on the catch chance"
# Wait, it just says "successfully caught the Pokémon!" but DOES IT ACTUALLY CALL `catch_pokemon()`???
# Let's check `Handle_Pokeball` again. It literally says:
# ```
#                 self.logger.log_and_showinfo(
#                     "info", f"{item_name} successfully caught the Pokémon!"
#                 )
#                 self.delete_item(item_name)  # Delete the Poké Ball after use
# ```
# But wait, it NEVER CALLS `catch_pokemon` or saves the pokemon to the database?!
# Wait, really?
pass
