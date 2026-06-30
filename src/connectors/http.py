import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Session HTTP partagée par tous les connecteurs : pool de connexions (évite une
# poignée TCP/TLS par appel sur les ~200+ JDD moissonnés) + retry/backoff sur les
# erreurs transitoires (429, 5xx, erreurs réseau). Pas de retry sur les 4xx
# (404/403...), qui doivent propager immédiatement.
_retry = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=False,
)
_adapter = HTTPAdapter(max_retries=_retry, pool_maxsize=10)

session = requests.Session()
session.mount("https://", _adapter)
session.mount("http://", _adapter)
