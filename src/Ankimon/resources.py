from pathlib import Path
import os
import json

addon_dir = Path(__file__).parents[0]

#safe route for updates
user_path = addon_dir / "user_files"
user_path_data = addon_dir / "user_files" / "data_files"
user_path_sprites = addon_dir / "user_files" / "sprites"
user_path_credentials = addon_dir / "user_files" / "data.json"
manifest_path = addon_dir / "manifest.json"

font_path = addon_dir / "addon_files"

# Assign Pokemon Image folder directory name
pkmnimgfolder = addon_dir / "user_files" / "sprites"
backdefault = addon_dir / "user_files" / "sprites" / "back_default"
frontdefault = addon_dir / "user_files" / "sprites" / "front_default"
#Assign saved Pokemon Directory
mypokemon_path = addon_dir / "user_files" / "mypokemon.json"
mainpokemon_path = addon_dir / "user_files" / "mainpokemon.json"
pokemon_history_path = addon_dir / "user_files" / "pokemon_history.json"
battlescene_path = addon_dir / "addon_sprites" / "battle_scenes"
trainer_sprites_path = addon_dir / "addon_sprites" / "trainers"
battlescene_path_without_dialog = addon_dir / "addon_sprites" / "battle_scenes_without_dialog"
battle_ui_path = addon_dir / "pkmnbattlescene - UI_transp"
type_style_file = addon_dir / "addon_files" / "types.json"
next_lvl_file_path = addon_dir / "addon_files" / "ExpPokemonAddon.csv"
berries_path = addon_dir / "user_files" / "sprites" / "berries"
background_dialog_image_path  = addon_dir / "background_dialog_image.png"
pokeball_path = addon_dir / "addon_files" / "pokeball.png"
pokedex_image_path = addon_dir / "addon_sprites" / "pokedex_template.jpg"
evolve_image_path = addon_dir / "addon_sprites" / "evo_temp.jpg"
learnset_path = addon_dir / "user_files" / "data_files" / "learnsets.json"
pokedex_path = addon_dir / "user_files" / "data_files" / "pokedex.json"
stats_csv = addon_dir / "user_files" / "data_files" / "pokemon_stats.csv"
moves_file_path = addon_dir / "user_files" / "data_files" / "moves.json"
move_names_file_path = addon_dir / "user_files" / "data_files" / "move_names.json"
items_path = addon_dir / "user_files" / "sprites" / "items"
badges_path = addon_dir / "user_files" / "sprites" / "badges"
itembag_path = addon_dir / "user_files" / "items.json"
badgebag_path = addon_dir / "user_files" / "badges.json"
pokenames_lang_path = addon_dir / "user_files" / "data_files" / "pokemon_species_names.csv"
pokedesc_lang_path = addon_dir / "user_files" / "data_files" / "pokemon_species_flavor_text.csv"
poke_evo_path = addon_dir / "user_files" / "data_files" / "pokemon_evolution.csv"
poke_species_path = addon_dir / "user_files" / "data_files" / "pokemon_species.csv"
eff_chart_html_path = addon_dir / "addon_files" / "eff_chart_html.html"
effectiveness_chart_file_path = addon_dir / "addon_files" / "eff_chart.json"
table_gen_id_html_path = addon_dir / "addon_files" / "table_gen_id.html"
icon_path = addon_dir / "addon_files" / "pokeball.png"
sound_list_path = addon_dir / "addon_files" / "sound_list.json"
badges_list_path = addon_dir / "addon_files" / "badges.json"
items_list_path = addon_dir / "addon_files" / "items.json"
rate_path = addon_dir / "user_files" / "rate_this.json"
csv_file_items = addon_dir / "user_files" / "data_files" / "item_names.csv"
csv_file_descriptions = addon_dir / "user_files" / "data_files" / "item_flavor_text.csv"
csv_file_items_cost = addon_dir / "user_files" / "data_files" / "items.csv"
pokemon_csv = addon_dir / "user_files" / "data_files" / "pokemon.csv"
pokemon_tm_learnset_path = addon_dir / "user_files" / "data_files" / "pokemon_tm_learnset.json"

#effect sounds paths
hurt_normal_sound_path = addon_dir / "addon_sprites" / "sounds" / "HurtNormal.mp3"
hurt_noteff_sound_path = addon_dir / "addon_sprites" / "sounds" / "HurtNotEffective.mp3"
hurt_supereff_sound_path = addon_dir / "addon_sprites" / "sounds" / "HurtSuper.mp3"
ownhplow_sound_path = addon_dir / "addon_sprites" / "sounds" / "OwnHpLow.mp3"
hpheal_sound_path = addon_dir / "addon_sprites" / "sounds" / "HpHeal.mp3"
fainted_sound_path = addon_dir / "addon_sprites" / "sounds" / "Fainted.mp3"

#utils
json_file_structure = addon_dir / "addon_files" / "folder_structure.json"

#move ui paths
type_icon_path_resources = addon_dir / "addon_sprites" / "Types"

team_pokemon_path = addon_dir / "user_files" / "team.json"

#lang routes
lang_path = addon_dir / "lang"
lang_path_de = addon_dir / "lang" / "de_text.json"
lang_path_ch = addon_dir / "lang" / "ch_text.json"
lang_path_en = addon_dir / "lang" / "en_text.json"
lang_path_fr = addon_dir / "lang" / "fr_text.json"
lang_path_jp = addon_dir / "lang" / "jp_text.json"
lang_path_sp = addon_dir / "lang" / "sp_text.json"
lang_path_it = addon_dir / "lang" / "it_text.json"
lang_path_cz = addon_dir / "lang" / "cz_text.json"
lang_path_po = addon_dir / "lang" / "po_text.json"
lang_path_kr = addon_dir / "lang" / "kr_text.json"
lang_path_es_latam = addon_dir / "lang" / "es_latam_text.json"

#backup_routes
backup_root = addon_dir / "user_files" / "backups"
backup_folder_1 = backup_root / "backup_1"
backup_folder_2 = backup_root / "backup_2"
backup_folders = [os.path.join(backup_root, f"backup_{i}") for i in range(1, 4)]

#detect add-on version
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    addon_ver = manifest.get("version", "unknown")
except Exception:
    addon_ver = "unknown"

#note if it is an experimental build
IS_EXPERIMENTAL_BUILD = addon_ver.endswith("-E")


POKEMON_TIERS = {
  "Normal": [
    # Generation 1
    10, 11, 12,	# caterpie, metapod, butterfree
    13, 14, 15,	# weedle, kakuna, beedrill
    16, 17, 18,	# pidgey, pidgeotto, pidgeot
    19, 20, 21,	# rattata, raticate, spearow
    22, 23, 24,	# fearow, ekans, arbok
    25, 26, 27,	# pikachu, raichu, sandshrew
    28, 29, 30,	# sandslash, nidoran-f, nidorina
    31, 32, 33,	# nidoqueen, nidoran-m, nidorino
    34, 35, 36,	# nidoking, clefairy, clefable
    37, 38, 39,	# vulpix, ninetales, jigglypuff
    40, 41, 42,	# wigglytuff, zubat, golbat
    43, 44, 45,	# oddish, gloom, vileplume
    46, 47, 48,	# paras, parasect, venonat
    49, 50, 51,	# venomoth, diglett, dugtrio
    52, 53, 54,	# meowth, persian, psyduck
    55, 56, 57,	# golduck, mankey, primeape
    58, 59, 60,	# growlithe, arcanine, poliwag
    61, 62, 63,	# poliwhirl, poliwrath, abra
    64, 65, 66,	# kadabra, alakazam, machop
    67, 68, 69,	# machoke, machamp, bellsprout
    70, 71, 72,	# weepinbell, victreebel, tentacool
    73, 74, 75,	# tentacruel, geodude, graveler
    76, 77, 78,	# golem, ponyta, rapidash
    79, 80, 81,	# slowpoke, slowbro, magnemite
    82, 83, 84,	# magneton, farfetchd, doduo
    85, 86, 87,	# dodrio, seel, dewgong
    88, 89, 90,	# grimer, muk, shellder
    91, 92, 93,	# cloyster, gastly, haunter
    94, 95, 96,	# gengar, onix, drowzee
    97, 98, 99,	# hypno, krabby, kingler
    100, 101, 102,	# voltorb, electrode, exeggcute
    103, 104, 105,	# exeggutor, cubone, marowak
    106, 107, 108,	# hitmonlee, hitmonchan, lickitung
    109, 110, 111,	# koffing, weezing, rhyhorn
    112, 113, 114,	# rhydon, chansey, tangela
    115, 116, 117,	# kangaskhan, horsea, seadra
    118, 119, 120,	# goldeen, seaking, staryu
    121, 122, 123,	# starmie, mr-mime, scyther
    124, 125, 126,	# jynx, electabuzz, magmar
    127, 128, 129,	# pinsir, tauros, magikarp
    130, 131, 132,	# gyarados, lapras, ditto
    133, 134, 135,	# eevee, vaporeon, jolteon
    136, 137, 143,	# flareon, porygon, snorlax
    147, 148, 149,	# dratini, dragonair, dragonite
    # Generation 2
    161, 162, 163,	# sentret, furret, hoothoot
    164, 165, 166,	# noctowl, ledyba, ledian
    167, 168, 169,	# spinarak, ariados, crobat
    170, 171, 176,	# chinchou, lanturn, togetic
    177, 178, 179,	# natu, xatu, mareep
    180, 181, 182,	# flaaffy, ampharos, bellossom
    183, 184, 185,	# marill, azumarill, sudowoodo
    186, 187, 188,	# politoed, hoppip, skiploom
    189, 190, 191,	# jumpluff, aipom, sunkern
    192, 193, 194,	# sunflora, yanma, wooper
    195, 196, 197,	# quagsire, espeon, umbreon
    198, 199, 200,	# murkrow, slowking, misdreavus
    201, 202, 203,	# unown, wobbuffet, girafarig
    204, 205, 206,	# pineco, forretress, dunsparce
    207, 208, 209,	# gligar, steelix, snubbull
    210, 211, 212,	# granbull, qwilfish, scizor
    213, 214, 215,	# shuckle, heracross, sneasel
    216, 217, 218,	# teddiursa, ursaring, slugma
    219, 220, 221,	# magcargo, swinub, piloswine
    222, 223, 224,	# corsola, remoraid, octillery
    225, 226, 227,	# delibird, mantine, skarmory
    228, 229, 230,	# houndour, houndoom, kingdra
    231, 232, 233,	# phanpy, donphan, porygon2
    234, 235, 237,	# stantler, smeargle, hitmontop
    241, 242, 246,	# miltank, blissey, larvitar
    247, 248,	# pupitar, tyranitar
    # Generation 3
    261, 262, 263,	# poochyena, mightyena, zigzagoon
    264, 265, 266,	# linoone, wurmple, silcoon
    267, 268, 269,	# beautifly, cascoon, dustox
    270, 271, 272,	# lotad, lombre, ludicolo
    273, 274, 275,	# seedot, nuzleaf, shiftry
    276, 277, 278,	# taillow, swellow, wingull
    279, 280, 281,	# pelipper, ralts, kirlia
    282, 283, 284,	# gardevoir, surskit, masquerain
    285, 286, 287,	# shroomish, breloom, slakoth
    288, 289, 290,	# vigoroth, slaking, nincada
    291, 292, 293,	# ninjask, shedinja, whismur
    294, 295, 296,	# loudred, exploud, makuhita
    297, 299, 300,	# hariyama, nosepass, skitty
    301, 302, 303,	# delcatty, sableye, mawile
    304, 305, 306,	# aron, lairon, aggron
    307, 308, 309,	# meditite, medicham, electrike
    310, 311, 312,	# manectric, plusle, minun
    313, 314, 315,	# volbeat, illumise, roselia
    316, 317, 318,	# gulpin, swalot, carvanha
    319, 320, 321,	# sharpedo, wailmer, wailord
    322, 323, 324,	# numel, camerupt, torkoal
    325, 326, 327,	# spoink, grumpig, spinda
    328, 329, 330,	# trapinch, vibrava, flygon
    331, 332, 333,	# cacnea, cacturne, swablu
    334, 335, 336,	# altaria, zangoose, seviper
    337, 338, 339,	# lunatone, solrock, barboach
    340, 341, 342,	# whiscash, corphish, crawdaunt
    343, 344, 349,	# baltoy, claydol, feebas
    350, 351, 352,	# milotic, castform, kecleon
    353, 354, 355,	# shuppet, banette, duskull
    356, 357, 358,	# dusclops, tropius, chimecho
    359, 361, 362,	# absol, snorunt, glalie
    363, 364, 365,	# spheal, sealeo, walrein
    366, 367, 368,	# clamperl, huntail, gorebyss
    369, 370, 371,	# relicanth, luvdisc, bagon
    372, 373, 374,	# shelgon, salamence, beldum
    375, 376,	# metang, metagross
    # Generation 4
    396, 397, 398,	# starly, staravia, staraptor
    399, 400, 401,	# bidoof, bibarel, kricketot
    402, 403, 404,	# kricketune, shinx, luxio
    405, 407, 412,	# luxray, roserade, burmy
    413, 414, 415,	# wormadam-plant, mothim, combee
    416, 417, 418,	# vespiquen, pachirisu, buizel
    419, 420, 421,	# floatzel, cherubi, cherrim
    422, 423, 424,	# shellos, gastrodon, ambipom
    425, 426, 427,	# drifloon, drifblim, buneary
    428, 429, 430,	# lopunny, mismagius, honchkrow
    431, 432, 434,	# glameow, purugly, stunky
    435, 436, 437,	# skuntank, bronzor, bronzong
    441, 442, 443,	# chatot, spiritomb, gible
    444, 445, 448,	# gabite, garchomp, lucario
    449, 450, 451,	# hippopotas, hippowdon, skorupi
    452, 453, 454,	# drapion, croagunk, toxicroak
    455, 456, 457,	# carnivine, finneon, lumineon
    459, 460, 461,	# snover, abomasnow, weavile
    462, 463, 464,	# magnezone, lickilicky, rhyperior
    465, 466, 467,	# tangrowth, electivire, magmortar
    468, 469, 470,	# togekiss, yanmega, leafeon
    471, 472, 473,	# glaceon, gliscor, mamoswine
    474, 475, 476,	# porygon-z, gallade, probopass
    477, 478, 479,	# dusknoir, froslass, rotom
    # Generation 5
    504, 505, 506,	# patrat, watchog, lillipup
    507, 508, 509,	# herdier, stoutland, purrloin
    510, 511, 512,	# liepard, pansage, simisage
    513, 514, 515,	# pansear, simisear, panpour
    516, 517, 518,	# simipour, munna, musharna
    519, 520, 521,	# pidove, tranquill, unfezant
    522, 523, 524,	# blitzle, zebstrika, roggenrola
    525, 526, 527,	# boldore, gigalith, woobat
    528, 529, 530,	# swoobat, drilbur, excadrill
    531, 532, 533,	# audino, timburr, gurdurr
    534, 535, 536,	# conkeldurr, tympole, palpitoad
    537, 538, 539,	# seismitoad, throh, sawk
    540, 541, 542,	# sewaddle, swadloon, leavanny
    543, 544, 545,	# venipede, whirlipede, scolipede
    546, 547, 548,	# cottonee, whimsicott, petilil
    549, 550, 551,	# lilligant, basculin-red-striped, sandile
    552, 553, 554,	# krokorok, krookodile, darumaka
    555, 556, 557,	# darmanitan-standard, maractus, dwebble
    558, 559, 560,	# crustle, scraggy, scrafty
    561, 562, 563,	# sigilyph, yamask, cofagrigus
    568, 569, 570,	# trubbish, garbodor, zorua
    571, 572, 573,	# zoroark, minccino, cinccino
    574, 575, 576,	# gothita, gothorita, gothitelle
    577, 578, 579,	# solosis, duosion, reuniclus
    580, 581, 582,	# ducklett, swanna, vanillite
    583, 584, 585,	# vanillish, vanilluxe, deerling
    586, 587, 588,	# sawsbuck, emolga, karrablast
    589, 590, 591,	# escavalier, foongus, amoonguss
    592, 593, 594,	# frillish, jellicent, alomomola
    595, 596, 597,	# joltik, galvantula, ferroseed
    598, 599, 600,	# ferrothorn, klink, klang
    601, 602, 603,	# klinklang, tynamo, eelektrik
    604, 605, 606,	# eelektross, elgyem, beheeyem
    607, 608, 609,	# litwick, lampent, chandelure
    610, 611, 612,	# axew, fraxure, haxorus
    613, 614, 615,	# cubchoo, beartic, cryogonal
    616, 617, 618,	# shelmet, accelgor, stunfisk
    619, 620, 621,	# mienfoo, mienshao, druddigon
    622, 623, 624,	# golett, golurk, pawniard
    625, 626, 627,	# bisharp, bouffalant, rufflet
    628, 629, 630,	# braviary, vullaby, mandibuzz
    631, 632, 633,	# heatmor, durant, deino
    634, 635, 636,	# zweilous, hydreigon, larvesta
    637,	# volcarona
    # Generation 6
    659, 660, 661,	# bunnelby, diggersby, fletchling
    662, 663, 664,	# fletchinder, talonflame, scatterbug
    665, 666, 667,	# spewpa, vivillon, litleo
    668, 669, 670,	# pyroar, flabebe, floette
    671, 672, 673,	# florges, skiddo, gogoat
    674, 675, 676,	# pancham, pangoro, furfrou
    677, 678, 679,	# espurr, meowstic-male, honedge
    680, 681, 682,	# doublade, aegislash-shield, spritzee
    683, 684, 685,	# aromatisse, swirlix, slurpuff
    686, 687, 688,	# inkay, malamar, binacle
    689, 690, 691,	# barbaracle, skrelp, dragalge
    692, 693, 694,	# clauncher, clawitzer, helioptile
    695, 700, 701,	# heliolisk, sylveon, hawlucha
    702, 703, 704,	# dedenne, carbink, goomy
    705, 706, 707,	# sliggoo, goodra, klefki
    708, 709, 710,	# phantump, trevenant, pumpkaboo-average
    711, 712, 713,	# gourgeist-average, bergmite, avalugg
    714, 715,	# noibat, noivern
    # Generation 7
    731, 732, 733,	# pikipek, trumbeak, toucannon
    734, 735, 736,	# yungoos, gumshoos, grubbin
    737, 738, 739,	# charjabug, vikavolt, crabrawler
    740, 741, 742,	# crabominable, oricorio-baile, cutiefly
    743, 744, 745,	# ribombee, rockruff, lycanroc-midday
    746, 747, 748,	# wishiwashi-solo, mareanie, toxapex
    749, 750, 751,	# mudbray, mudsdale, dewpider
    752, 753, 754,	# araquanid, fomantis, lurantis
    755, 756, 757,	# morelull, shiinotic, salandit
    758, 759, 760,	# salazzle, stufful, bewear
    761, 762, 763,	# bounsweet, steenee, tsareena
    764, 765, 766,	# comfey, oranguru, passimian
    767, 768, 769,	# wimpod, golisopod, sandygast
    770, 771, 774,	# palossand, pyukumuku, minior-red-meteor
    775, 776, 777,	# komala, turtonator, togedemaru
    778, 779, 780,	# mimikyu-disguised, bruxish, drampa
    781, 782, 783,	# dhelmise, jangmo-o, hakamo-o
    784,	# kommo-o
    # Generation 8
    819, 820, 821,	# skwovet, greedent, rookidee
    822, 823, 824,	# corvisquire, corviknight, blipbug
    825, 826, 827,	# dottler, orbeetle, nickit
    828, 829, 830,	# thievul, gossifleur, eldegoss
    831, 832, 833,	# wooloo, dubwool, chewtle
    834, 835, 836,	# drednaw, yamper, boltund
    837, 838, 839,	# rolycoly, carkol, coalossal
    840, 841, 842,	# applin, flapple, appletun
    843, 844, 845,	# silicobra, sandaconda, cramorant
    846, 847, 849,	# arrokuda, barraskewda, toxtricity-amped
    850, 851, 852,	# sizzlipede, centiskorch, clobbopus
    853, 854, 855,	# grapploct, sinistea, polteageist
    856, 857, 858,	# hatenna, hattrem, hatterene
    859, 860, 861,	# impidimp, morgrem, grimmsnarl
    862, 863, 864,	# obstagoon, perrserker, cursola
    865, 866, 867,	# sirfetchd, mr-rime, runerigus
    868, 869, 870,	# milcery, alcremie, falinks
    871, 872, 873,	# pincurchin, snom, frosmoth
    874, 875, 876,	# stonjourner, eiscue-ice, indeedee-male
    877, 878, 879,	# morpeko-full-belly, cufant, copperajah
    884, 885, 886,	# duraludon, dreepy, drakloak
    887, 899, 900,	# dragapult, wyrdeer, kleavor
    901, 902, 903,	# ursaluna, basculegion-male, sneasler
    904, 905,	# overqwil, enamorus-incarnate
    # Generation 9
    915, 916, 917,	# lechonk, oinkologne, tarountula
    918, 919, 920,	# spidops, nymble, lokix
    921, 922, 923,	# pawmi, pawmo, pawmot
    924, 925, 926,	# tandemaus, maushold, fidough
    927, 928, 929,	# dachsbun, smoliv, dolliv
    930, 931, 932,	# arboliva, squawkabilly, nacli
    933, 934, 935,	# naclstack, garganacl, charcadet
    936, 937, 938,	# armarouge, ceruledge, tadbulb
    939, 940, 941,	# bellibolt, wattrel, kilowattrel
    942, 943, 944,	# maschiff, mabosstiff, shroodle
    945, 946, 947,	# grafaiai, bramblin, brambleghast
    948, 949, 950,	# toedscool, toedscruel, klawf
    951, 952, 953,	# capsakid, scovillain, rellor
    954, 955, 956,	# rabsca, flittle, espathra
    957, 958, 959,	# tinkatink, tinkatuff, tinkaton
    960, 961, 962,	# wiglett, wugtrio, bombirdier
    963, 964, 965,	# finizen, palafin, varoom
    966, 967, 968,	# revavroom, cyclizar, orthworm
    969, 970, 971,	# glimmet, glimmora, greavard
    972, 973, 974,	# houndstone, flamigo, cetoddle
    975, 976, 977,	# cetitan, veluza, dondozo
    978, 979, 980,	# tatsugiri, annihilape, clodsire
    981, 982, 983,	# farigiraf, dudunsparce, kingambit
    984, 985, 986,	# great-tusk, scream-tail, brute-bonnet
    987, 988, 989,	# flutter-mane, slither-wing, sandy-shocks
    990, 991, 992,	# iron-treads, iron-bundle, iron-hands
    993, 994, 995,	# iron-jugulis, iron-moth, iron-thorns
    996, 997, 998,	# frigibax, arctibax, baxcalibur
    999, 1000, 1005,	# gimmighoul, gholdengo, roaring-moon
    1006, 1011, 1012,	# iron-valiant, dipplin, poltchageist
    1013, 1018, 1019,	# sinistcha, archaludon, hydrapple
],
  "Legendary": [
  # Gen 1
  144, 145, 146, 150,
  # Gen 2
  243, 244, 245, 249, 250,
  # Gen 3
  377, 378, 379, 380, 381, 382, 383, 384,
  # Gen 4
  480, 481, 482, 483, 484, 485, 486, 487, 488,
  # Gen 5
  638, 639, 640, 641, 642, 643, 644, 645, 646,
  # Gen 6
  716, 717, 718,
  # Gen 7
  772, 773, 785, 786, 787, 788, 789, 790, 791, 792, 800,
  # Gen 8
  888, 889, 890, 891, 892, 894, 895, 896, 897, 898,
  # Gen 9
  1001,  # wo-chien
  1002,  # chien-pao
  1003,  # ting-lu
  1004,  # chi-yu
  1007,  # koraidon
  1008,  # miraidon
  1009,  # walking-wake
  1010,  # iron-leaves
  1014,  # okidogi
  1015,  # munkidori
  1016,  # fezandipiti
  1017,  # ogerpon
  1020,  # gouging-fire
  1021,  # raging-bolt
  1022,  # iron-boulder
  1023,  # iron-crown
  1024,  # terapagos
  1025,  # pecharunt
]
,
  "Mythical": [
  # Gen 1
  151,        # Mew
  # Gen 2
  251,        # Celebi
  # Gen 3
  385, 386,   # Jirachi, Deoxys
  # Gen 4
  489, 490, 491, 492, 493,   # Phione, Manaphy, Darkrai, Shaymin, Arceus
  # Gen 5
  494, 647, 648, 649,        # Victini, Keldeo, Meloetta, Genesect
  # Gen 6
  719, 720, 721,             # Diancie, Hoopa, Volcanion
  # Gen 7
  801, 802, 807, 808, 809,   # Magearna, Marshadow, Zeraora, Meltan, Melmetal
  # Gen 8
  893                        # Zarude
]
,
  "Ultra": [
  793,  # Nihilego
  794,  # Buzzwole
  795,  # Pheromosa
  796,  # Xurkitree
  797,  # Celesteela
  798,  # Kartana
  799,  # Guzzlord
  803,  # Poipole
  804,  # Naganadel
  805,  # Stakataka
  806   # Blacephalon
]
,
  "Fossil": [
  # Gen 1
  138, 139, 140, 141, 142,        # Omanyte, Omastar, Kabuto, Kabutops, Aerodactyl
  # Gen 3
  345, 346, 347, 348,             # Lileep, Cradily, Anorith, Armaldo
  # Gen 4
  408, 409, 410, 411,             # Cranidos, Rampardos, Shieldon, Bastiodon
  # Gen 5
  564, 565, 566, 567,             # Tirtouga, Carracosta, Archen, Archeops
  # Gen 6
  696, 697, 698, 699,             # Tyrunt, Tyrantrum, Amaura, Aurorus
  # Gen 8
  880, 881, 882, 883              # Dracozolt, Arctozolt, Dracovish, Arctovish
]
,
  "Starter": [
  # Gen 1 (Kanto)
  1, 2, 3,      # Bulbasaur, Ivysaur, Venusaur
  4, 5, 6,      # Charmander, Charmeleon, Charizard
  7, 8, 9,      # Squirtle, Wartortle, Blastoise

  # Gen 2 (Johto)
  152, 153, 154,  # Chikorita, Bayleef, Meganium
  155, 156, 157,  # Cyndaquil, Quilava, Typhlosion
  158, 159, 160,  # Totodile, Croconaw, Feraligatr

  # Gen 3 (Hoenn)
  252, 253, 254,  # Treecko, Grovyle, Sceptile
  255, 256, 257,  # Torchic, Combusken, Blaziken
  258, 259, 260,  # Mudkip, Marshtomp, Swampert

  # Gen 4 (Sinnoh)
  387, 388, 389,  # Turtwig, Grotle, Torterra
  390, 391, 392,  # Chimchar, Monferno, Infernape
  393, 394, 395,  # Piplup, Prinplup, Empoleon

  # Gen 5 (Unova)
  495, 496, 497,  # Snivy, Servine, Serperior
  498, 499, 500,  # Tepig, Pignite, Emboar
  501, 502, 503,  # Oshawott, Dewott, Samurott

  # Gen 6 (Kalos)
  650, 651, 652,  # Chespin, Quilladin, Chesnaught
  653, 654, 655,  # Fennekin, Braixen, Delphox
  656, 657, 658,  # Froakie, Frogadier, Greninja

  # Gen 7 (Alola)
  722, 723, 724,  # Rowlet, Dartrix, Decidueye
  725, 726, 727,  # Litten, Torracat, Incineroar
  728, 729, 730,  # Popplio, Brionne, Primarina

  # Gen 8 (Galar)
  810, 811, 812,  # Grookey, Thwackey, Rillaboom
  813, 814, 815,  # Scorbunny, Raboot, Cinderace
  816, 817, 818,   # Sobble, Drizzile, Inteleon
  
  # Gen 9
  906, 907, 908, # Sprigatito, Floragato, Meowscarada
  909, 910, 911, # Fuecoco, Crocalor, Skeledirge
  912, 913, 914 # Quaxly, Quaxwell, Quaquaval
]
,
  "Baby": [
    # Gen 2 (Johto)
    172,  # Pichu
    173,  # Cleffa
    174,  # Igglybuff
    175,  # Togepi
    236,  # Tyrogue
    238,  # Smoochum
    239,  # Elekid
    240,  # Magby

    # Gen 3 (Hoenn)
    298,  # Azurill
    360,  # Wynaut

    # Gen 4 (Sinnoh)
    406,  # Budew
    433,  # Chingling
    438,  # Bonsly
    439,  # Mime Jr.
    440,  # Happiny
    446,  # Munchlax
    447,  # Riolu
    458,  # Mantyke

    # Gen 8 (Galar)
    848,  # Toxel
]
,
  "Hisuian": [
    # Gen 8 (Legends: Arceus - Hisui region)
    899,  # Wyrdeer
    900,  # Kleavor
    901,  # Ursaluna
    902,  # Basculegion
    903,  # Sneasler
    904,  # Overqwil
    905,  # Enamorus
]

}

def generate_startup_files(base_path, base_user_path):  # Add base_user_path parameter
    """
    Generates blank personal files at startup with the value [].
    Introduced as a workaround to gitignore personal files.
    """
    files = ['mypokemon.json', 'mainpokemon.json', 'items.json',
             'team.json', 'data.json', 'badges.json']

    for file in files:
        file_path = os.path.join(base_user_path, file)  # Use base_user_path parameter
        # Create parent directory if needed
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2)

    # Default data for the file
    default_rating_data = {"rate_this": False}
    rate_path = os.path.join(base_user_path, 'rate_this.json')

    # Create the file with default contents if it doesn't exist
    if not os.path.exists(rate_path):
        os.makedirs(os.path.dirname(rate_path), exist_ok=True)
        with open(rate_path, "w", encoding="utf-8") as f:
            json.dump(default_rating_data, f, indent=4)

    # Create blank HelpInfos.html and updateinfos.md at base_path if they don't exist
    helpinfos_path = os.path.join(base_path, 'HelpInfos.html')
    updateinfos_path = os.path.join(base_path, 'updateinfos.md')

    if not os.path.exists(helpinfos_path):
        os.makedirs(os.path.dirname(helpinfos_path), exist_ok=True)
        with open(helpinfos_path, 'w', encoding='utf-8') as f:
            f.write('')

    if not os.path.exists(updateinfos_path):
        os.makedirs(os.path.dirname(updateinfos_path), exist_ok=True)
        with open(updateinfos_path, 'w', encoding='utf-8') as f:
            f.write('')

    return True

