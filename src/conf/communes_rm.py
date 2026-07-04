# Communes de Rennes Métropole avec leurs codes postaux
# Source officielle (commune_agglo=1) :
# https://public.sig.rennesmetropole.fr/geoserver/wfs?SERVICE=WFS&REQUEST=GetFeature&VERSION=2.0.0&TYPENAMES=ladm_autres%3Av_code_postal&OUTPUTFORMAT=csv&SRSNAME=EPSG%3A3948

# Clé = nom de la commune (minuscules), valeur = code postal
COMMUNES_RM = {
    "acigné":                       "35690",
    "betton":                       "35830",
    "bourgbarré":                   "35230",
    "brécé":                        "35530",
    "bruz":                         "35170",
    "bécherel":                     "35190",
    "cesson-sévigné":               "35510",
    "chantepie":                    "35135",
    "chartres-de-bretagne":         "35131",
    "chavagne":                     "35310",
    "chevaigné":                    "35250",
    "cintré":                       "35310",
    "clayes":                       "35590",
    "corps-nuds":                   "35150",
    "gévezé":                       "35850",
    "la chapelle-chaussée":         "35630",
    "la chapelle-thouarault":       "35590",
    "la chapelle-des-fougeretz":    "35520",
    "laillé":                       "35890",
    "langan":                       "35850",
    "le rheu":                      "35650",
    "le verger":                    "35160",
    "l'hermitage":                  "35590",
    "miniac-sous-bécherel":         "35190",
    "montgermont":                  "35760",
    "mordelles":                    "35310",
    "nouvoitou":                    "35410",
    "noyal-châtillon-sur-seiche":   "35230",
    "orgères":                      "35230",
    "pacé":                         "35740",
    "parthenay-de-bretagne":        "35850",
    "pont-péan":                    "35131",
    "rennes":                       "35000",
    "romillé":                      "35850",
    "saint-armel":                  "35230",
    "saint-erblon":                 "35230",
    "saint-gilles":                 "35590",
    "saint-grégoire":               "35760",
    "saint-jacques-de-la-lande":    "35136",
    "saint-sulpice-la-forêt":       "35250",
    "thorigné-fouillard":           "35235",
    "vern-sur-seiche":              "35770",
    "vezin-le-coquet":              "35132",
}

# Rennes a trois codes postaux
CODES_POSTAUX_RENNES = ["35000", "35200", "35700"]

# Ensemble des codes postaux RM (pour un premier filtre rapide)
CODES_POSTAUX_RM = set(COMMUNES_RM.values()) | set(CODES_POSTAUX_RENNES)

# Codes INSEE des 43 communes RM
# Source : https://geo.api.gouv.fr/epcis/243500139/communes
CODES_INSEE_RM = {
    "35001",  # Acigné
    "35022",  # Bécherel
    "35024",  # Betton
    "35032",  # Bourgbarré
    "35039",  # Brécé
    "35047",  # Bruz
    "35051",  # Cesson-Sévigné
    "35055",  # Chantepie
    "35058",  # La Chapelle-Chaussée
    "35059",  # La Chapelle-des-Fougeretz
    "35065",  # La Chapelle-Thouarault
    "35066",  # Chartres-de-Bretagne
    "35076",  # Chavagne
    "35079",  # Chevaigné
    "35080",  # Cintré
    "35081",  # Clayes
    "35088",  # Corps-Nuds
    "35120",  # Gévezé
    "35131",  # L'Hermitage
    "35139",  # Laillé
    "35144",  # Langan
    "35180",  # Miniac-sous-Bécherel
    "35189",  # Montgermont
    "35196",  # Mordelles
    "35204",  # Nouvoitou
    "35206",  # Noyal-Châtillon-sur-Seiche
    "35208",  # Orgères
    "35210",  # Pacé
    "35216",  # Parthenay-de-Bretagne
    "35238",  # Rennes
    "35240",  # Le Rheu
    "35245",  # Romillé
    "35250",  # Saint-Armel
    "35266",  # Saint-Erblon
    "35275",  # Saint-Gilles
    "35278",  # Saint-Grégoire
    "35281",  # Saint-Jacques-de-la-Lande
    "35315",  # Saint-Sulpice-la-Forêt
    "35334",  # Thorigné-Fouillard
    "35351",  # Le Verger
    "35352",  # Vern-sur-Seiche
    "35353",  # Vezin-le-Coquet
    "35363",  # Pont-Péan
}

# Circonscriptions législatives (Assemblée nationale) recoupant Rennes Métropole
# Source : https://public.sig.rennesmetropole.fr/geoserver/wfs?SERVICE=WFS&REQUEST=GetFeature&VERSION=2.0.0&TYPENAMES=ladm_terri%3Acirconscription&OUTPUTFORMAT=csv
# Chaque commune RM correspond à exactement une circonscription, SAUF Rennes qui en
# recoupe 4 (035-01, 035-02, 035-03, 035-08). ATTENTION : une circonscription est un
# territoire plus large que RM (ex: la 1re circonscription inclut aussi des communes
# hors RM, au sud de Rennes) — voir filters/geographic.py::est_circonscription_rm()
# et CLAUDE.md "Known limitations" pour la conséquence sur la précision du filtrage.
CIRCONSCRIPTIONS_PAR_COMMUNE = {
    "35001": ("035-02",),  # Acigné
    "35022": ("035-03",),  # Bécherel
    "35024": ("035-02",),  # Betton
    "35032": ("035-01",),  # Bourgbarré
    "35039": ("035-05",),  # Brécé
    "35047": ("035-01",),  # Bruz
    "35051": ("035-02",),  # Cesson-Sévigné
    "35055": ("035-01",),  # Chantepie
    "35058": ("035-03",),  # La Chapelle-Chaussée
    "35059": ("035-02",),  # La Chapelle-des-Fougeretz
    "35065": ("035-03",),  # La Chapelle-Thouarault
    "35066": ("035-01",),  # Chartres-de-Bretagne
    "35076": ("035-08",),  # Chavagne
    "35079": ("035-06",),  # Chevaigné
    "35080": ("035-08",),  # Cintré
    "35081": ("035-03",),  # Clayes
    "35088": ("035-05",),  # Corps-Nuds
    "35120": ("035-03",),  # Gévezé
    "35131": ("035-08",),  # L'Hermitage
    "35139": ("035-04",),  # Laillé
    "35144": ("035-03",),  # Langan
    "35180": ("035-03",),  # Miniac-sous-Bécherel
    "35189": ("035-02",),  # Montgermont
    "35196": ("035-08",),  # Mordelles
    "35204": ("035-05",),  # Nouvoitou
    "35206": ("035-01",),  # Noyal-Châtillon-sur-Seiche
    "35208": ("035-01",),  # Orgères
    "35210": ("035-03",),  # Pacé
    "35216": ("035-03",),  # Parthenay-de-Bretagne
    "35238": ("035-01", "035-02", "035-03", "035-08"),  # Rennes (répartie sur 4 circo)
    "35240": ("035-08",),  # Le Rheu
    "35245": ("035-03",),  # Romillé
    "35250": ("035-05",),  # Saint-Armel
    "35266": ("035-01",),  # Saint-Erblon
    "35275": ("035-08",),  # Saint-Gilles
    "35278": ("035-02",),  # Saint-Grégoire
    "35281": ("035-08",),  # Saint-Jacques-de-la-Lande
    "35315": ("035-02",),  # Saint-Sulpice-la-Forêt
    "35334": ("035-02",),  # Thorigné-Fouillard
    "35351": ("035-03",),  # Le Verger
    "35352": ("035-01",),  # Vern-sur-Seiche
    "35353": ("035-08",),  # Vezin-le-Coquet
    "35363": ("035-01",),  # Pont-Péan
}

# Ensemble plat des circonscriptions dont le territoire recoupe RM (7 sur les 8 que
# compte le département 35 — la 035-07 ne touche aucune commune RM)
CIRCONSCRIPTIONS_RM = {c for circos in CIRCONSCRIPTIONS_PAR_COMMUNE.values() for c in circos}
