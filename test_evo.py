import json

data = {
    "individual_id": "123",
    "id": 273,
    "name": "Seedot",
    "shiny": True,
    "level": 14,
    "attacks": ["Tackle"],
    "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
    "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
}
pokemon = data
prevo_id = 273
evo_id = 274
evo_name = "Nuzleaf"

pokemon["name"] = evo_name.capitalize()
pokemon["id"] = evo_id
print(pokemon)
