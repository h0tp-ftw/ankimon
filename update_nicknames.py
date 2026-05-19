import json

with open(".github/contributor-nicknames.json", "r") as f:
    nicknames = json.load(f)

# The missing contributors from .all-contributorsrc compared to .github/contributor-nicknames.json
missing_contributors = [
    "abr-projects", "Zuukiny", "marbaret", "gjgress", "richy431", "MrElektronz", "Koconnor03", "MORGANlTE", "OuraN2O"
]

for contributor in missing_contributors:
    if contributor not in nicknames:
        nicknames[contributor] = { "nickname": "", "discord_id": "" }

with open(".github/contributor-nicknames.json", "w") as f:
    json.dump(nicknames, f, indent=2)
