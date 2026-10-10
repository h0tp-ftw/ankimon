# List of pokemon that *can be encountered*
# This is not a list of *ALL* pokemon

LEGENDARY = [
    # Generation 1
    144,
    145,
    146,  # articuno, zapdos, moltres
    150,  # mewtwo
    # Generation 2
    243,
    244,
    245,  # raikou, entei, suicune
    249,
    250,  # lugia, ho-oh
    # Generation 3
    377,
    378,
    379,  # regirock, regice, registeel
    380,
    381,  # latias, latios
    382,
    383,
    384,  # kyogre, groudon, rayquaza
    # Generation 4
    480,
    481,
    482,  # uxie, mesprit, azelf
    483,
    484,  # dialga, palkia
    10245,
    10246,  # dialgaorigin, palkiaorigin
    485,
    486,
    487,  # heatran, regigigas, giratina
    10007,  # giratinaorigin
    488,  # cresselia
    # Generation 5
    638,
    639,
    640,  # cobalion, terrakion, virizion
    641,
    642,
    643,  # tornadus-incarnate, thundurus-incarnate, reshiram
    10019,
    10020,  # tornadustherian, thundurustherian
    644,
    645,
    646,  # zekrom, landorus-incarnate, kyurem
    10021,  # landorustherian
    # Generation 6
    716,
    717,
    10119,  # xerneas, yveltal, zygarde-50 (actual_id=10119)
    10181,  # zygarde10
    # Generation 7
    772,
    773,
    785,  # type-null, silvally, tapu-koko
    786,
    787,
    788,  # tapu-lele, tapu-bulu, tapu-fini
    789,
    790,
    791,  # cosmog, cosmoem, solgaleo
    792,
    800,  # lunala, necrozma
    # Generation 8
    888,
    889,
    890,  # zacian, zamazenta, eternatus
    891,
    892,
    894,  # kubfu, urshifu-single-strike, regieleki
    895,
    896,
    897,  # regidrago, glastrier, spectrier
    898,
    905,  # calyrex, enamorus-incarnate
    10249,  # enamorustherian
    # Generation 9
    1001,
    1002,
    1003,  # wo-chien, chien-pao, ting-lu
    1004,
    1007,
    1008,  # chi-yu, koraidon, miraidon
    1009,
    1010,
    1014,  # walking-wake, iron-leaves, okidogi  [Paradox Legendary]
    1015,
    1016,
    1017,  # munkidori, fezandipiti, ogerpon
    1020,
    1021,
    1022,  # gouging-fire, raging-bolt, iron-boulder  [Paradox Legendary]
    1023,
    1024,  # iron-crown, terapagos
]

MYTHICAL = [
    # Generation 1
    151,  # mew  [from encounter.txt]
    # Generation 2
    251,  # celebi  [from encounter.txt]
    # Generation 3
    385,  # jirachi
    386,  # deoxys  [from encounter.txt]
    10001,
    10002,
    10003,  # deoxysattack, deoxysdefense, deoxysspeed
    # Generation 4
    489,
    490,
    491,  # phione, manaphy, darkrai
    492,  # shaymin-land
    10006,  # shayminsky
    493,  # arceus  [from encounter.txt]
    # Generation 5
    494,
    647,
    648,  # victini, keldeo-ordinary, meloetta-aria
    10024,
    10018,  # keldeoresolute, meloettapirouette
    649,  # genesect  [from encounter.txt]
    # Generation 6
    719,
    720,
    721,  # diancie, hoopa, volcanion
    # Generation 7
    801,
    802,
    807,  # magearna, marshadow, zeraora
    808,
    809,  # meltan, melmetal
    # Generation 8
    893,  # zarude
    # Generation 9
    1025,  # pecharunt
]

BABY = [
    # Generation 2
    172,
    173,
    174,  # pichu, cleffa, igglybuff
    175,
    236,
    238,  # togepi, tyrogue, smoochum
    239,
    240,  # elekid, magby
    # Generation 3
    298,
    360,  # azurill, wynaut
    # Generation 4
    406,
    433,
    438,  # budew, chingling, bonsly
    439,
    440,
    446,  # mime-jr, happiny, munchlax
    447,
    458,  # riolu, mantyke
    # Generation 8
    848,  # toxel
]

ULTRA = [
    # Generation 7
    793,
    794,
    795,  # nihilego, buzzwole, pheromosa
    796,
    797,
    798,  # xurkitree, celesteela, kartana
    799,
    803,
    804,  # guzzlord, poipole, naganadel
    805,
    806,  # stakataka, blacephalon
]

NORMAL = [
    # Generation 1
    10,
    11,
    12,  # caterpie, metapod, butterfree
    13,
    14,
    15,  # weedle, kakuna, beedrill
    16,
    17,
    18,  # pidgey, pidgeotto, pidgeot
    19,
    20,
    21,  # rattata, raticate, spearow
    22,
    23,
    24,  # fearow, ekans, arbok
    25,
    26,
    27,  # pikachu, raichu, sandshrew
    28,
    29,
    30,  # sandslash, nidoran-f, nidorina
    31,
    32,
    33,  # nidoqueen, nidoran-m, nidorino
    34,
    35,
    36,  # nidoking, clefairy, clefable
    37,
    38,
    39,  # vulpix, ninetales, jigglypuff
    40,
    41,
    42,  # wigglytuff, zubat, golbat
    43,
    44,
    45,  # oddish, gloom, vileplume
    46,
    47,
    48,  # paras, parasect, venonat
    49,
    50,
    51,  # venomoth, diglett, dugtrio
    52,
    53,
    54,  # meowth, persian, psyduck
    55,
    56,
    57,  # golduck, mankey, primeape
    58,
    59,
    60,  # growlithe, arcanine, poliwag
    61,
    62,
    63,  # poliwhirl, poliwrath, abra
    64,
    65,
    66,  # kadabra, alakazam, machop
    67,
    68,
    69,  # machoke, machamp, bellsprout
    70,
    71,
    72,  # weepinbell, victreebel, tentacool
    73,
    74,
    75,  # tentacruel, geodude, graveler
    76,
    77,
    78,  # golem, ponyta, rapidash
    79,
    80,
    81,  # slowpoke, slowbro, magnemite
    82,
    83,
    84,  # magneton, farfetchd, doduo
    85,
    86,
    87,  # dodrio, seel, dewgong
    88,
    89,
    90,  # grimer, muk, shellder
    91,
    92,
    93,  # cloyster, gastly, haunter
    94,
    95,
    96,  # gengar, onix, drowzee
    97,
    98,
    99,  # hypno, krabby, kingler
    100,
    101,
    102,  # voltorb, electrode, exeggcute
    103,
    104,
    105,  # exeggutor, cubone, marowak
    106,
    107,
    108,  # hitmonlee, hitmonchan, lickitung
    109,
    110,
    111,  # koffing, weezing, rhyhorn
    112,
    113,
    114,  # rhydon, chansey, tangela
    115,
    116,
    117,  # kangaskhan, horsea, seadra
    118,
    119,
    120,  # goldeen, seaking, staryu
    121,
    122,
    123,  # starmie, mr-mime, scyther
    124,
    125,
    126,  # jynx, electabuzz, magmar
    127,
    128,
    129,  # pinsir, tauros, magikarp
    130,
    131,
    132,  # gyarados, lapras, ditto
    133,
    134,
    135,  # eevee, vaporeon, jolteon
    136,
    137,
    143,  # flareon, porygon, snorlax
    147,
    148,
    149,  # dratini, dragonair, dragonite
    # Generation 2
    161,
    162,
    163,  # sentret, furret, hoothoot
    164,
    165,
    166,  # noctowl, ledyba, ledian
    167,
    168,
    169,  # spinarak, ariados, crobat
    170,
    171,
    176,  # chinchou, lanturn, togetic
    177,
    178,
    179,  # natu, xatu, mareep
    180,
    181,
    182,  # flaaffy, ampharos, bellossom
    183,
    184,
    185,  # marill, azumarill, sudowoodo
    186,
    187,
    188,  # politoed, hoppip, skiploom
    189,
    190,
    191,  # jumpluff, aipom, sunkern
    192,
    193,
    194,  # sunflora, yanma, wooper
    195,
    196,
    197,  # quagsire, espeon, umbreon
    198,
    199,
    200,  # murkrow, slowking, misdreavus
    201,
    202,
    203,  # unown, wobbuffet, girafarig
    204,
    205,
    206,  # pineco, forretress, dunsparce
    207,
    208,
    209,  # gligar, steelix, snubbull
    210,
    211,
    212,  # granbull, qwilfish, scizor
    213,
    214,
    215,  # shuckle, heracross, sneasel
    216,
    217,
    218,  # teddiursa, ursaring, slugma
    219,
    220,
    221,  # magcargo, swinub, piloswine
    222,
    223,
    224,  # corsola, remoraid, octillery
    225,
    226,
    227,  # delibird, mantine, skarmory
    228,
    229,
    230,  # houndour, houndoom, kingdra
    231,
    232,
    233,  # phanpy, donphan, porygon2
    234,
    235,
    237,  # stantler, smeargle, hitmontop
    241,
    242,
    246,  # miltank, blissey, larvitar
    247,
    248,  # pupitar, tyranitar
    # Generation 3
    261,
    262,
    263,  # poochyena, mightyena, zigzagoon
    264,
    265,
    266,  # linoone, wurmple, silcoon
    267,
    268,
    269,  # beautifly, cascoon, dustox
    270,
    271,
    272,  # lotad, lombre, ludicolo
    273,
    274,
    275,  # seedot, nuzleaf, shiftry
    276,
    277,
    278,  # taillow, swellow, wingull
    279,
    280,
    281,  # pelipper, ralts, kirlia
    282,
    283,
    284,  # gardevoir, surskit, masquerain
    285,
    286,
    287,  # shroomish, breloom, slakoth
    288,
    289,
    290,  # vigoroth, slaking, nincada
    291,
    292,
    293,  # ninjask, shedinja, whismur
    294,
    295,
    296,  # loudred, exploud, makuhita
    297,
    299,
    300,  # hariyama, nosepass, skitty
    301,
    302,
    303,  # delcatty, sableye, mawile
    304,
    305,
    306,  # aron, lairon, aggron
    307,
    308,
    309,  # meditite, medicham, electrike
    310,
    311,
    312,  # manectric, plusle, minun
    313,
    314,
    315,  # volbeat, illumise, roselia
    316,
    317,
    318,  # gulpin, swalot, carvanha
    319,
    320,
    321,  # sharpedo, wailmer, wailord
    322,
    323,
    324,  # numel, camerupt, torkoal
    325,
    326,
    327,  # spoink, grumpig, spinda
    328,
    329,
    330,  # trapinch, vibrava, flygon
    331,
    332,
    333,  # cacnea, cacturne, swablu
    334,
    335,
    336,  # altaria, zangoose, seviper
    337,
    338,
    339,  # lunatone, solrock, barboach
    340,
    341,
    342,  # whiscash, corphish, crawdaunt
    343,
    344,
    349,  # baltoy, claydol, feebas
    350,
    351,
    352,  # milotic, castform, kecleon
    353,
    354,
    355,  # shuppet, banette, duskull
    356,
    357,
    358,  # dusclops, tropius, chimecho
    359,
    361,
    362,  # absol, snorunt, glalie
    363,
    364,
    365,  # spheal, sealeo, walrein
    366,
    367,
    368,  # clamperl, huntail, gorebyss
    369,
    370,
    371,  # relicanth, luvdisc, bagon
    372,
    373,
    374,  # shelgon, salamence, beldum
    375,
    376,  # metang, metagross
    # Generation 4
    396,
    397,
    398,  # starly, staravia, staraptor
    399,
    400,
    401,  # bidoof, bibarel, kricketot
    402,
    403,
    404,  # kricketune, shinx, luxio
    405,
    407,
    412,  # luxray, roserade, burmy
    413,
    414,
    415,  # wormadam-plant, mothim, combee
    416,
    417,
    418,  # vespiquen, pachirisu, buizel
    419,
    420,
    421,  # floatzel, cherubi, cherrim
    422,
    423,
    424,  # shellos, gastrodon, ambipom
    425,
    426,
    427,  # drifloon, drifblim, buneary
    428,
    429,
    430,  # lopunny, mismagius, honchkrow
    431,
    432,
    434,  # glameow, purugly, stunky
    435,
    436,
    437,  # skuntank, bronzor, bronzong
    441,
    442,
    443,  # chatot, spiritomb, gible
    444,
    445,
    448,  # gabite, garchomp, lucario
    449,
    450,
    451,  # hippopotas, hippowdon, skorupi
    452,
    453,
    454,  # drapion, croagunk, toxicroak
    455,
    456,
    457,  # carnivine, finneon, lumineon
    459,
    460,
    461,  # snover, abomasnow, weavile
    462,
    463,
    464,  # magnezone, lickilicky, rhyperior
    465,
    466,
    467,  # tangrowth, electivire, magmortar
    468,
    469,
    470,  # togekiss, yanmega, leafeon
    471,
    472,
    473,  # glaceon, gliscor, mamoswine
    474,
    475,
    476,  # porygon-z, gallade, probopass
    477,
    478,
    479,  # dusknoir, froslass, rotom
    10004,
    10005,  # wormadam forms
    10008,
    10009,
    10010,
    10011,
    10012,  # rotom forms
    # Generation 5
    504,
    505,
    506,  # patrat, watchog, lillipup
    507,
    508,
    509,  # herdier, stoutland, purrloin
    510,
    511,
    512,  # liepard, pansage, simisage
    513,
    514,
    515,  # pansear, simisear, panpour
    516,
    517,
    518,  # simipour, munna, musharna
    519,
    520,
    521,  # pidove, tranquill, unfezant
    522,
    523,
    524,  # blitzle, zebstrika, roggenrola
    525,
    526,
    527,  # boldore, gigalith, woobat
    528,
    529,
    530,  # swoobat, drilbur, excadrill
    531,
    532,
    533,  # audino, timburr, gurdurr
    534,
    535,
    536,  # conkeldurr, tympole, palpitoad
    537,
    538,
    539,  # seismitoad, throh, sawk
    540,
    541,
    542,  # sewaddle, swadloon, leavanny
    543,
    544,
    545,  # venipede, whirlipede, scolipede
    546,
    547,
    548,  # cottonee, whimsicott, petilil
    549,
    550,
    551,  # lilligant, basculin-red-striped, sandile
    552,
    553,
    554,  # krokorok, krookodile, darumaka
    555,
    556,
    557,  # darmanitan-standard, maractus, dwebble
    558,
    559,
    560,  # crustle, scraggy, scrafty
    561,
    562,
    563,  # sigilyph, yamask, cofagrigus
    568,
    569,
    570,  # trubbish, garbodor, zorua
    571,
    572,
    573,  # zoroark, minccino, cinccino
    574,
    575,
    576,  # gothita, gothorita, gothitelle
    577,
    578,
    579,  # solosis, duosion, reuniclus
    580,
    581,
    582,  # ducklett, swanna, vanillite
    583,
    584,
    585,  # vanillish, vanilluxe, deerling
    586,
    587,
    588,  # sawsbuck, emolga, karrablast
    589,
    590,
    591,  # escavalier, foongus, amoonguss
    592,
    593,
    594,  # frillish, jellicent, alomomola
    595,
    596,
    597,  # joltik, galvantula, ferroseed
    598,
    599,
    600,  # ferrothorn, klink, klang
    601,
    602,
    603,  # klinklang, tynamo, eelektrik
    604,
    605,
    606,  # eelektross, elgyem, beheeyem
    607,
    608,
    609,  # litwick, lampent, chandelure
    610,
    611,
    612,  # axew, fraxure, haxorus
    613,
    614,
    615,  # cubchoo, beartic, cryogonal
    616,
    617,
    618,  # shelmet, accelgor, stunfisk
    619,
    620,
    621,  # mienfoo, mienshao, druddigon
    622,
    623,
    624,  # golett, golurk, pawniard
    625,
    626,
    627,  # bisharp, bouffalant, rufflet
    628,
    629,
    630,  # braviary, vullaby, mandibuzz
    631,
    632,
    633,  # heatmor, durant, deino
    634,
    635,
    636,  # zweilous, hydreigon, larvesta
    637,  # volcarona
    # Generation 6
    659,
    660,
    661,  # bunnelby, diggersby, fletchling
    662,
    663,
    664,  # fletchinder, talonflame, scatterbug
    665,
    666,
    667,  # spewpa, vivillon, litleo
    668,
    669,
    670,  # pyroar, flabebe, floette
    671,
    672,
    673,  # florges, skiddo, gogoat
    674,
    675,
    676,  # pancham, pangoro, furfrou
    677,
    678,
    679,  # espurr, meowstic-male, honedge
    680,
    681,
    682,  # doublade, aegislash-shield, spritzee
    683,
    684,
    685,  # aromatisse, swirlix, slurpuff
    686,
    687,
    688,  # inkay, malamar, binacle
    689,
    690,
    691,  # barbaracle, skrelp, dragalge
    692,
    693,
    694,  # clauncher, clawitzer, helioptile
    695,
    700,
    701,  # heliolisk, sylveon, hawlucha
    702,
    703,
    704,  # dedenne, carbink, goomy
    705,
    706,
    707,  # sliggoo, goodra, klefki
    708,
    709,
    710,  # phantump, trevenant, pumpkaboo-average
    711,
    712,
    713,  # gourgeist-average, bergmite, avalugg
    714,
    715,  # noibat, noivern
    # Generation 7
    731,
    732,
    733,  # pikipek, trumbeak, toucannon
    734,
    735,
    736,  # yungoos, gumshoos, grubbin
    737,
    738,
    739,  # charjabug, vikavolt, crabrawler
    740,
    741,
    742,  # crabominable, oricorio-baile, cutiefly
    743,
    744,
    745,  # ribombee, rockruff, lycanroc-midday
    746,
    747,
    748,  # wishiwashi-solo, mareanie, toxapex
    749,
    750,
    751,  # mudbray, mudsdale, dewpider
    752,
    753,
    754,  # araquanid, fomantis, lurantis
    755,
    756,
    757,  # morelull, shiinotic, salandit
    758,
    759,
    760,  # salazzle, stufful, bewear
    761,
    762,
    763,  # bounsweet, steenee, tsareena
    764,
    765,
    766,  # comfey, oranguru, passimian
    767,
    768,
    769,  # wimpod, golisopod, sandygast
    770,
    771,
    774,  # palossand, pyukumuku, minior-red-meteor
    775,
    776,
    777,  # komala, turtonator, togedemaru
    778,
    779,
    780,  # mimikyu-disguised, bruxish, drampa
    781,
    782,
    783,  # dhelmise, jangmo-o, hakamo-o
    784,  # kommo-o
    # Generation 8
    819,
    820,
    821,  # skwovet, greedent, rookidee
    822,
    823,
    824,  # corvisquire, corviknight, blipbug
    825,
    826,
    827,  # dottler, orbeetle, nickit
    828,
    829,
    830,  # thievul, gossifleur, eldegoss
    831,
    832,
    833,  # wooloo, dubwool, chewtle
    834,
    835,
    836,  # drednaw, yamper, boltund
    837,
    838,
    839,  # rolycoly, carkol, coalossal
    840,
    841,
    842,  # applin, flapple, appletun
    843,
    844,
    845,  # silicobra, sandaconda, cramorant
    846,
    847,
    849,  # arrokuda, barraskewda, toxtricity-amped
    850,
    851,
    852,  # sizzlipede, centiskorch, clobbopus
    853,
    854,
    855,  # grapploct, sinistea, polteageist
    856,
    857,
    858,  # hatenna, hattrem, hatterene
    859,
    860,
    861,  # impidimp, morgrem, grimmsnarl
    862,
    863,
    864,  # obstagoon, perrserker, cursola
    865,
    866,
    867,  # sirfetchd, mr-rime, runerigus
    868,
    869,
    870,  # milcery, alcremie, falinks
    871,
    872,
    873,  # pincurchin, snom, frosmoth
    874,
    875,
    876,  # stonjourner, eiscue-ice, indeedee-male
    877,
    878,
    879,  # morpeko-full-belly, cufant, copperajah
    884,
    885,
    886,  # duraludon, dreepy, drakloak
    887,
    899,
    900,  # dragapult, wyrdeer, kleavor
    901,
    902,
    903,  # ursaluna, basculegion-male, sneasler
    904,  # overqwil
    # Generation 9
    915,
    916,
    917,  # lechonk, oinkologne, tarountula
    918,
    919,
    920,  # spidops, nymble, lokix
    921,
    922,
    923,  # pawmi, pawmo, pawmot
    924,
    925,
    926,  # tandemaus, maushold, fidough
    927,
    928,
    929,  # dachsbun, smoliv, dolliv
    930,
    931,
    932,  # arboliva, squawkabilly, nacli
    933,
    934,
    935,  # naclstack, garganacl, charcadet
    936,
    937,
    938,  # armarouge, ceruledge, tadbulb
    939,
    940,
    941,  # bellibolt, wattrel, kilowattrel
    942,
    943,
    944,  # maschiff, mabosstiff, shroodle
    945,
    946,
    947,  # grafaiai, bramblin, brambleghast
    948,
    949,
    950,  # toedscool, toedscruel, klawf
    951,
    952,
    953,  # capsakid, scovillain, rellor
    954,
    955,
    956,  # rabsca, flittle, espathra
    957,
    958,
    959,  # tinkatink, tinkatuff, tinkaton
    960,
    961,
    962,  # wiglett, wugtrio, bombirdier
    963,
    964,
    965,  # finizen, palafin, varoom
    966,
    967,
    968,  # revavroom, cyclizar, orthworm
    969,
    970,
    971,  # glimmet, glimmora, greavard
    972,
    973,
    974,  # houndstone, flamigo, cetoddle
    975,
    976,
    977,  # cetitan, veluza, dondozo
    978,
    979,
    980,  # tatsugiri, annihilape, clodsire
    981,
    982,
    983,  # farigiraf, dudunsparce, kingambit
    984,
    985,
    986,  # great-tusk, scream-tail, brute-bonnet
    987,
    988,
    989,  # flutter-mane, slither-wing, sandy-shocks
    990,
    991,
    992,  # iron-treads, iron-bundle, iron-hands
    993,
    994,
    995,  # iron-jugulis, iron-moth, iron-thorns
    996,
    997,
    998,  # frigibax, arctibax, baxcalibur
    999,
    1000,
    1005,  # gimmighoul, gholdengo, roaring-moon
    1006,
    1011,
    1012,  # iron-valiant, dipplin, poltchageist
    1013,
    1018,
    1019,  # sinistcha, archaludon, hydrapple
]

STARTERS = [
    # Generation 1
    1,
    2,
    3,  # bulbasaur, ivysaur, venusaur
    4,
    5,
    6,  # charmander, charmeleon, charizard
    7,
    8,
    9,  # squirtle, wartortle, blastoise
    # Generation 2
    152,
    153,
    154,  # chikorita, bayleef, meganium
    155,
    156,
    157,  # cyndaquil, quilava, typhlosion
    158,
    159,
    160,  # totodile, croconaw, feraligatr
    # Generation 3
    252,
    253,
    254,  # treecko, grovyle, sceptile
    255,
    256,
    257,  # torchic, combusken, blaziken
    258,
    259,
    260,  # mudkip, marshtomp, swampert
    # Generation 4
    387,
    388,
    389,  # turtwig, grotle, torterra
    390,
    391,
    392,  # chimchar, monferno, infernape
    393,
    394,
    395,  # piplup, prinplup, empoleon
    # Generation 5
    495,
    496,
    497,  # snivy, servine, serperior
    498,
    499,
    500,  # tepig, pignite, emboar
    501,
    502,
    503,  # oshawott, dewott, samurott
    # Generation 6
    650,
    651,
    652,  # chespin, quilladin, chesnaught
    653,
    654,
    655,  # fennekin, braixen, delphox
    656,
    657,
    658,  # froakie, frogadier, greninja
    # Generation 7
    722,
    723,
    724,  # rowlet, dartrix, decidueye
    725,
    726,
    727,  # litten, torracat, incineroar
    728,
    729,
    730,  # popplio, brionne, primarina
    # Generation 8
    810,
    811,
    812,  # grookey, thwackey, rillaboom
    813,
    814,
    815,  # scorbunny, raboot, cinderace
    816,
    817,
    818,  # sobble, drizzile, inteleon
    # Generation 9
    906,
    907,
    908,  # sprigatito, floragato, meowscarada
    909,
    910,
    911,  # fuecoco, crocalor, skeledirge
    912,
    913,
    914,  # quaxly, quaxwell, quaquaval
]

# =============================================================
# HARD-CODED MEGA / GMAX LISTS
# Generated from pokedex.json forme field — 95 Mega, 34 Gmax.
# =============================================================
MEGA_AND_SPECIAL = [
    # =============================================================
    # 1. TRUE MEGAS
    # =============================================================
    10033,  # venusaurmega           (species=3)
    10034,  # charizardmegax         (species=6)
    10035,  # charizardmegay         (species=6)
    10036,  # blastoisemega          (species=9)
    10037,  # alakazammega           (species=65)
    10038,  # gengarmega             (species=94)
    10039,  # kangaskhanmega         (species=115)
    10040,  # pinsirmega             (species=127)
    10041,  # gyaradosmega           (species=130)
    10042,  # aerodactylmega         (species=142)
    10043,  # mewtwomegax            (species=150)
    10044,  # mewtwomegay            (species=150)
    10045,  # ampharosmega           (species=181)
    10046,  # scizormega             (species=212)
    10047,  # heracrossmega          (species=214)
    10048,  # houndoommega           (species=229)
    10049,  # tyranitarmega          (species=248)
    10050,  # blazikenmega           (species=257)
    10051,  # gardevoirmega          (species=282)
    10052,  # mawilemega             (species=303)
    10053,  # aggronmega             (species=306)
    10054,  # medichammega           (species=308)
    10055,  # manectricmega          (species=310)
    10056,  # banettemega            (species=354)
    10057,  # absolmega              (species=359)
    10058,  # garchompmega           (species=445)
    10059,  # lucariomega            (species=448)
    10060,  # abomasnowmega          (species=460)
    10062,  # latiasmega             (species=380)
    10063,  # latiosmega             (species=381)
    10064,  # swampertmega           (species=260)
    10065,  # sceptilemega           (species=254)
    10066,  # sableyemega            (species=302)
    10067,  # altariamega            (species=334)
    10068,  # gallademega            (species=475)
    10069,  # audinomega             (species=531)
    10070,  # sharpedomega           (species=319)
    10071,  # slowbromega            (species=80)
    10072,  # steelixmega            (species=208)
    10073,  # pidgeotmega            (species=18)
    10074,  # glaliemega             (species=362)
    10075,  # dianciemega            (species=719)
    10076,  # metagrossmega          (species=376)
    10079,  # rayquazamega           (species=384)
    10087,  # cameruptmega           (species=323)
    10088,  # lopunnymega            (species=428)
    10089,  # salamencemega          (species=373)
    10090,  # beedrillmega           (species=15)
    10278,  # clefablemega           (species=36)
    10279,  # victreebelmega         (species=71)
    10280,  # starmiemega            (species=121)
    10281,  # dragonitemega          (species=149)
    10282,  # meganiummega           (species=154)
    10283,  # feraligatrmega         (species=160)
    10284,  # skarmorymega           (species=227)
    10285,  # froslassmega           (species=478)
    10286,  # emboarmega             (species=500)
    10287,  # excadrillmega          (species=530)
    10288,  # scolipedemega          (species=545)
    10289,  # scraftymega            (species=560)
    10290,  # eelektrossmega         (species=604)
    10291,  # chandeluremega         (species=609)
    10292,  # chesnaughtmega         (species=652)
    10293,  # delphoxmega            (species=655)
    10294,  # greninjamega           (species=658)
    10295,  # pyroarmega             (species=668)
    10296,  # floettemega            (species=670)
    10297,  # malamarmega            (species=687)
    10298,  # barbaraclemega         (species=689)
    10299,  # dragalgemega           (species=691)
    10300,  # hawluchamega           (species=701)
    10301,  # zygardemega            (species=718)
    10302,  # drampamega             (species=780)
    10303,  # falinksmega            (species=870)
    10304,  # raichumegax            (species=26)
    10305,  # raichumegay            (species=26)
    10306,  # chimechomega           (species=358)
    10307,  # absolmegaz             (species=359)
    10308,  # staraptormega          (species=398)
    10309,  # garchompmegaz          (species=445)
    10310,  # lucariomegaz           (species=448)
    10311,  # heatranmega            (species=485)  [Sub-Legendary tag — still Mega]
    10312,  # darkraimega            (species=491)  [Mythical tag — still Mega]
    10313,  # golurkmega             (species=623)
    10315,  # crabominablemega       (species=740)
    10316,  # golisopodmega          (species=768)
    10317,  # magearnamega           (species=801)
    10318,  # magearnaoriginalmega   (species=801)
    10319,  # zeraoramega            (species=807)  [Mythical tag — still Mega]
    10320,  # scovillainmega         (species=952)
    10321,  # glimmoramega           (species=970)
    10322,  # tatsugiricurlymega     (species=978)
    10323,  # tatsugiridroopymega    (species=978)
    10324,  # tatsugiristretchymega  (species=978)
    10325,  # baxcaliburmega         (species=998)
    # =============================================================
    # 2. PHASE 1 SPECIAL FORMS (LEGENDARY & MYTHICAL FUSIONS, ORIGINS, ETC.)
    # =============================================================
    # Primal Reversions & Eternamax (Not true Megas)
    10077,  # kyogreprimal           (species=382)
    10078,  # groudonprimal          (species=383)
    10190,  # eternatuseternamax     (species=890)
    # Zygarde Forms
    10120,  # zygardecomplete        (species=718)
    # Necrozma Forms & Fusions
    10155,  # necrozmaduskmane       (species=800)
    10156,  # necrozmadawnwings      (species=800)
    10157,  # necrozmaultra          (species=800)
    # Kyurem Fusions
    10022,  # kyuremblack            (species=646)
    10023,  # kyuremwhite            (species=646)
    # Zacian & Zamazenta Crowned Forms
    10188,  # zaciancrowned          (species=888)
    10189,  # zamazentacrowned       (species=889)
    # Calyrex Fusions
    10193,  # calyrexice             (species=898)
    10194,  # calyrexshadow          (species=898)
    # Terapagos Forms
    10276,  # terapagosterastal      (species=1024)
    10277,  # terapagosstellar       (species=1024)
    # Other Mythical/Legendary Special Forms
    10086,  # hoopaunbound           (species=720)
]

MEGA = MEGA_AND_SPECIAL

GMAX = [
    10195,  # venusaurgmax              (species=3)
    10196,  # charizardgmax             (species=6)
    10197,  # blastoisegmax             (species=9)
    10198,  # butterfreegmax            (species=12)
    10199,  # pikachugmax               (species=25)
    10200,  # meowthgmax                (species=52)
    10201,  # machampgmax               (species=68)
    10202,  # gengargmax                (species=94)
    10203,  # kinglergmax               (species=99)
    10204,  # laprasgmax                (species=131)
    10205,  # eeveegmax                 (species=133)
    10206,  # snorlaxgmax               (species=143)
    10207,  # garbodorgmax              (species=569)
    10208,  # melmetalgmax              (species=809)
    10209,  # rillaboomgmax             (species=812)
    10210,  # cinderacegmax             (species=815)
    10211,  # inteleongmax              (species=818)
    10212,  # corviknightgmax           (species=823)
    10213,  # orbeetlegmax              (species=826)
    10214,  # drednawgmax               (species=834)
    10215,  # coalossalgmax             (species=839)
    10216,  # flapplegmax               (species=841)
    10217,  # appletungmax              (species=842)
    10218,  # sandacondagmax            (species=844)
    10219,  # toxtricitygmax            (species=849)
    10220,  # centiskorchgmax           (species=851)
    10221,  # hatterenegmax             (species=858)
    10222,  # grimmsnarlgmax            (species=861)
    10223,  # alcremiegmax              (species=869)
    10224,  # copperajahgmax            (species=879)
    10225,  # duraludongmax             (species=884)
    10226,  # urshifugmax               (species=892)
    10227,  # urshifurapidstrikegmax    (species=892)
    10228,  # toxtricitylowkeygmax      (species=849)
]

UNAVAILABLE = [
    # Fossil Pokemon & Evolutions
    138,
    139,
    140,  # omanyte, omastar, kabuto
    141,
    142,
    345,  # kabutops, aerodactyl, lileep
    346,
    347,
    348,  # cradily, anorith, armaldo
    408,
    409,
    410,  # cranidos, rampardos, shieldon
    411,
    564,
    565,  # bastiodon, tirtouga, carracosta
    566,
    567,
    696,  # archen, archeops, tyrunt
    697,
    698,
    699,  # tyrantrum, amaura, aurorus
    880,
    881,
    882,  # dracozolt, arctozolt, dracovish
    883,  # arctovish
    # Alolan Forms (10093 = raticatealolatotem, 10149 = marowakalolatotem: stay UNAVAILABLE)
    10093,
    10149,
    10099,  # raticatealolatotem, marowakalolatotem, pikachualola
    # Special & Battle-Only Forms
    10117,
    10121,  # greninjaash, gumshoostotem
    10122,
    10128,
    10129,  # vikavolttotem, lurantistotem, salazzletotem
    10144,
    10145,
    10146,  # mimikyutotem, mimikyubustedtotem, kommoototem
    10150,
    10153,
    10154,  # ribombeetotem, araquanidtotem, togedemarutotem
    10256,  # palafinhero
    # Alternate Species Forms
    10013,
    10014,
    10015,  # castform sunny, castformrainy, castformsnowy
    10016,
    10017,  # basculinbluestriped, darmanitanzen
    10025,
    10027,  # meowsticf, aegislashblade, pumpkaboosmall
    10028,
    10029,
    10030,  # pumpkaboolarge, pumpkaboosuper, gourgeistsmall
    10031,
    10032,
    10061,  # gourgeistlarge, gourgeistsuper, floetteeternal
    10080,  # pikachurockstar
    10081,
    10082,
    10083,  # pikachubelle, pikachupopstar, pikachuphd
    10084,
    10085,  # pikachulibre, pikachucosplay
    10094,
    10095,
    10096,  # pikachuoriginal, pikachuhoenn, pikachusinnoh
    10097,
    10098,
    10116,  # pikachuunova, pikachukalos, greninjabond
    10123,
    10124,
    10125,  # oricoriopompom, oricoriopau, oricoriosensu
    10126,
    10127,
    10136,  # lycanrocmidnight, wishiwashischool, minior
    10137,
    10138,
    10139,  # miniororange, minioryellow, miniorgreen
    10140,
    10141,
    10142,  # miniorblue, miniorindigo, miniorviolet
    10143,
    10148,  # mimikyubusted, pikachupartner
    10152,  # lycanrocdusk
    10158,
    10159,
    10160,  # pikachustarter, eeveestarter, pikachuworld
    10182,
    10183,
    10184,  # cramorantgulping, cramorantgorging, toxtricitylowkey
    10185,
    10186,
    10187,  # eiscuenoice, indeedeef, morpekohangry
    10191,  # urshifurapidstrike
    10192,  # zarudedada
    10247,  # basculinwhitestriped
    10248,
    10254,  # basculegionf, oinkolognef
    10255,
    10257,
    10258,  # dudunsparcethreesegment, maushold, tatsugiridroopy
    10259,
    10260,
    10261,  # tatsugiristretchy, squawkabillyblue, squawkabillyyellow
    10262,
    10263,
    10272,  # squawkabillywhite, gimmighoulroaming, ursalunabloodmoon
    10273,
    10274,
    10275,  # ogerponwellspringtera, ogerponhearthflametera, ogerponcornerstonetera
    10178,  # darmanitan-galar-zen
]

# =============================================================
# REGIONAL FORMS
# Maps region name -> list of encounterable regional form actual_ids.
# Totem variants (10093, 10149) are intentionally excluded.
# =============================================================
REGIONAL_FORMS: dict[str, list[int]] = {
    "alola": [
        10091,
        10092,  # rattata-alola, raticate-alola
        10100,
        10101,
        10102,  # raichu-alola, sandshrew-alola, sandslash-alola
        10103,
        10104,
        10105,  # vulpix-alola, ninetales-alola, diglett-alola
        10106,
        10107,
        10108,  # dugtrio-alola, meowth-alola, persian-alola
        10109,
        10110,
        10111,  # geodude-alola, graveler-alola, golem-alola
        10112,
        10113,
        10114,  # grimer-alola, muk-alola, exeggutor-alola
        10115,  # marowak-alola
    ],
    "galar": [
        10161,
        10162,
        10163,  # meowth-galar, ponyta-galar, rapidash-galar
        10164,
        10165,
        10166,  # slowpoke-galar, slowbro-galar, farfetchd-galar
        10167,
        10168,
        10169,  # weezing-galar, mr-mime-galar, articuno-galar
        10170,
        10171,
        10172,  # zapdos-galar, moltres-galar, slowking-galar
        10173,
        10174,
        10175,  # corsola-galar, zigzagoon-galar, linoone-galar
        10176,
        10177,
        10179,  # darumaka-galar, darmanitan-galar-standard, yamask-galar
        10180,  # stunfisk-galar
    ],
    "hisui": [
        10229,
        10230,
        10231,  # growlithe-hisui, arcanine-hisui, voltorb-hisui
        10232,
        10233,
        10234,  # electrode-hisui, typhlosion-hisui, qwilfish-hisui
        10235,
        10236,
        10237,  # sneasel-hisui, samurott-hisui, lilligant-hisui
        10238,
        10239,
        10240,  # zorua-hisui, zoroark-hisui, braviary-hisui
        10241,
        10242,
        10243,  # sliggoo-hisui, goodra-hisui, avalugg-hisui
        10244,  # decidueye-hisui
    ],
    "paldea": [
        10250,
        10251,
        10252,  # tauros-paldea-combat-breed, tauros-paldea-blaze-breed, tauros-paldea-aqua-breed
        10253,  # wooper-paldea
    ],
}

# Maps forme string (as it appears in pokedex.json) -> required intro gen number
REGIONAL_FORME_GEN: dict[str, int] = {
    "Alola": 7,
    "Galar": 8,
    "Hisui": 8,
    "Paldea": 9,
}

# Flat reverse map: actual_id -> region string
REGIONAL_FORM_REGION: dict[int, str] = {
    aid: region for region, aids in REGIONAL_FORMS.items() for aid in aids
}

# species_id -> {region_name -> [actual_id, ...]}
# Multiple variants per species+region supported (e.g. Tauros-Paldea x3).
REGIONAL_FORM_LOOKUP: dict[int, dict[str, list[int]]] = {}


# REGIONAL_FORM_LOOKUP is populated lazily (on first use) by
# _build_regional_lookup() in functions/encounter_functions.py.


# =============================================================
# PREREQUISITE CHAINS (partly from encounter.txt)
# Key   = Pokémon species_id that requires prerequisites.
# Value = set of species_ids that must ALL be in the player's collection.
# =============================================================
PREREQUISITES = {
    # Generation 1
    150: {151},  # Mewtwo requires Mew
    # Generation 2
    249: {144, 145, 146},  # Lugia requires Articuno + Zapdos + Moltres
    250: {243, 244, 245},  # Ho-Oh requires Raikou + Entei + Suicune
    # Generation 3
    384: {382, 383},  # Rayquaza requires Kyogre + Groudon
    10001: {386},  # Deoxys Attack requires Deoxys
    10002: {386},  # Deoxys Defense requires Deoxys
    10003: {386},  # Deoxys Speed requires Deoxys
    # Generation 4
    487: {483, 484},  # Giratina requires Dialga + Palkia
    483: {480, 481, 482},  # Dialga requires Uxie + Mesprit + Azelf
    484: {480, 481, 482},  # Palkia requires Uxie + Mesprit + Azelf
    10245: {483},  # Dialga Origin requires Dialga
    10246: {484},  # Palkia Origin requires Palkia
    10007: {487},  # Giratina Origin requires Giratina
    10006: {492},  # Shaymin Sky requires Shaymin
    493: {487},  # Arceus requires Lake Trio + Dialga + Palkia + Giratina
    490: {489},  # Phione requires Manaphy
    486: {894, 895, 377, 378, 379},  # Regigigas requires Eleki + Drago + Regi Trio


    10004: {413},  # Wormadam-Sandy requires Wormadam-Plant
    10005: {413},  # Wormadam-Trash requires Wormadam-Plant
    10008: {479},  # Rotom-Heat requires Rotom
    10009: {479},  # Rotom-Wash requires Rotom
    10010: {479},  # Rotom-Frost requires Rotom
    10011: {479},  # Rotom-Fan requires Rotom
    10012: {479},  # Rotom-Mow requires Rotom
    # Generation 5
    647: {638, 639, 640},  # Keldeo requires Swords of Justice
    10024: {647},  # Keldeo Resolute requires Keldeo
    645: {641, 642},  # Landorus requires Tornadus + Thundurus
    646: {643, 644},  # Kyurem requires Reshiram + Zekrom
    10022: {646, 644},  # Kyurem-Black requires Kyurem + Zekrom
    10023: {646, 643},  # Kyurem-White requires Kyurem + Reshiram
    10019: {641},  # Tornadus Therian requires Tornadus
    10020: {642},  # Thundurus Therian requires Thundurus
    10021: {645},  # Landorus Therian requires Landorus
    10018: {648},  # Meloetta Pirouette requires Meloetta
    # Generation 6
    718: {716, 717},  # Zygarde requires Xerneas + Yveltal
    10181: {10119},  # Zygarde 10% requires Zygarde 50%
    # Generation 7
    800: {791, 792},  # Necrozma requires Solgaleo + Lunala
    773: {772},  # Silvally requires Type: Null
    10155: {800, 791},  # Necrozma-Dusk-Mane requires Necrozma + Solgaleo
    10156: {800, 792},  # Necrozma-Dawn-Wings requires Necrozma + Lunala
    10157: {800, 791, 792},  # Necrozma-Ultra requires Necrozma + Solgaleo + Lunala
    # Generation 8
    890: {888, 889},  # Eternatus requires Zacian + Zamazenta
    896: {898},  # Glastrier requires Calyrex
    897: {898},  # Spectrier requires Calyrex
    905: {645},  # Enamorus requires Tornadus + Thundurus + Landorus
    10249: {905},  # Enamorus Therian requires Enamorus
    10193: {898, 896},  # Calyrex-Ice requires Calyrex + Glastrier
    10194: {898, 897},  # Calyrex-Shadow requires Calyrex + Spectrier
    10191: {892},  # Urshifu Rapid Strike requires Urshifu Single Strike
    # Generation 9
    1025: {1014, 1015, 1016},  # Pecharunt requires Loyal Three
    # ("OR", {...}) = any one collected suffices (_meets_prerequisites);
    # a plain set would demand BOTH, but encounter.txt specifies "need either".
    1024: ("OR", {1007, 1008}),  # Terapagos requires Koraidon OR Miraidon
    # Starter Evolutions
    2: {1},
    3: {2},  # Ivysaur requires Bulbasaur, Venusaur requires Ivysaur
    5: {4},
    6: {5},  # Charmeleon requires Charmander, Charizard requires Charmeleon
    8: {7},
    9: {8},  # Wartortle requires Squirtle, Blastoise requires Wartortle
    153: {152},
    154: {153},  # Chikorita family
    156: {155},
    157: {156},  # Cyndaquil family
    159: {158},
    160: {159},  # Totodile family
    253: {252},
    254: {253},  # Treecko family
    256: {255},
    257: {256},  # Torchic family
    259: {258},
    260: {259},  # Mudkip family
    388: {387},
    389: {388},  # Turtwig family
    391: {390},
    392: {391},  # Chimchar family
    394: {393},
    395: {394},  # Piplup family
    496: {495},
    497: {496},  # Snivy family
    499: {498},
    500: {499},  # Tepig family
    502: {501},
    503: {502},  # Oshawott family
    651: {650},
    652: {651},  # Chespin family
    654: {653},
    655: {654},  # Fennekin family
    657: {656},
    658: {657},  # Froakie family
    723: {722},
    724: {723},  # Rowlet family
    726: {725},
    727: {726},  # Litten family
    729: {728},
    730: {729},  # Popplio family
    811: {810},
    812: {811},  # Grookey family
    814: {813},
    815: {814},  # Scorbunny family
    817: {816},
    818: {817},  # Sobble family
    907: {906},
    908: {907},  # Sprigatito family
    910: {909},
    911: {910},  # Fuecoco family
    913: {912},
    914: {913},  # Quaxly family
}
