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
