"""
Páginas de empleo de empresas concretas, consultadas directamente en su ATS.

Es la fuente con más potencial de traer lo que no llega a ningún agregador: muchas
empresas publican en su propio tablón y solo replican en LinkedIn/Indeed una parte (o
nada). Los ATS que se usan aquí exponen el tablón como API pública precisamente para
que la empresa pueda incrustarlo en su web, así que no hay login ni nada que saltarse.

Qué empresas se consultan sale de fuentes_extra/empresas.json, que es editable sin
tocar código (ver el comentario de ese archivo). Los tokens semilla no son inventados:
se sacaron de `job_url_direct` de las ofertas ya guardadas en ofertas.json —es decir,
de empresas que YA han publicado este perfil— y se verificaron uno a uno contra el
tablón real, descartando los homónimos (el token "iris" de Greenhouse, por ejemplo,
resultó no ser IRiS Grupo Tecnológico).

Sobre SmartRecruiters: su robots.txt dice `User-agent: * -> Disallow: /`, con una
excepción solo para LinkedInBot, aunque la API sea pública y documentada y sea el canal
con el que las propias empresas difunden sus vacantes. Se usa por decisión explícita
del usuario, y por eso lleva un trato aparte: la espera entre peticiones es un orden de
magnitud mayor que la del resto (ver ESPERA_POR_ATS) y basta UN solo código de bloqueo
para que se desactive sola (ver salud.BLOQUEOS_PARA_DESACTIVAR), en vez de los tres
habituales. Si eso pasa, se avisa por Telegram.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import requests

from fuentes_extra import base
from fuentes_extra import salud

logger = logging.getLogger(__name__)

FUENTE = "empresa"
REGISTRO_PATH = Path(__file__).parent / "empresas.json"

HEADERS = {
    "User-Agent": base.USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

TIMEOUT = 25

# Espera MÍNIMA entre dos peticiones al mismo ATS. Lever pide Crawl-delay: 1 en su
# robots.txt y se respeta. SmartRecruiters va a 6 s a propósito: se está consultando en
# contra de la preferencia de su robots.txt, así que como mínimo se hace despacio.
ESPERA_POR_ATS = {
    "greenhouse": 0.4,
    "lever": 1.0,
    "ashby": 0.4,
    "recruitee": 0.5,
    "smartrecruiters": 6.0,
}
ESPERA_POR_DEFECTO = 1.0

# Igual que en eures.py: un tablón de empresa no publica todos los días, así que aplicar
# un hours_old de horas (38 en la config actual) lo dejaría casi siempre vacío. La
# ventana es por tanto la MAYOR entre hours_old y estos días: nunca más estrecha que
# esto —o la fuente no aportaría nada— pero tampoco sin límite, que es lo que dejaba
# entrar vacantes de hace ocho meses todavía colgadas del tablón.
DIAS_MINIMOS = 30

# El resultado de cada tablón se cachea: buscar() llama a esta fuente una vez por cada
# (categoría × término × ámbito) —hasta 32 veces en una búsqueda normal— y el tablón de
# una empresa es el mismo para todas. Sin esto serían 32 peticiones por empresa.
TTL_CACHE_SEGUNDOS = 900

ETIQUETA_HTML_RE = re.compile(r"<[^>]+>")
PAISES_UE_RE = re.compile(
    r"spain|españa|espanha|portugal|france|francia|german|alemania|deutschland|"
    r"italy|italia|netherlands|holanda|países bajos|belgium|b[ée]lgica|ireland|irlanda|"
    r"poland|polonia|polska|romania|ruman[ií]a|bulgaria|greece|grecia|austria|sweden|"
    r"suecia|denmark|dinamarca|finland|finlandia|estonia|latvia|letonia|lithuania|"
    r"lituania|luxembourg|luxemburgo|hungary|hungr[ií]a|slovakia|eslovaquia|slovenia|"
    r"eslovenia|croatia|croacia|cyprus|chipre|malta|czech|chequia|norway|noruega|"
    r"iceland|islandia|\beu\b|\bemea\b|europe|europa",
    re.IGNORECASE,
)
ESPANA_RE = re.compile(r"spain|españa|barcelona|madrid|valencia|sevilla|bilbao|"
                       r"m[áa]laga|zaragoza|catalu|remote.*spain|spain.*remote",
                       re.IGNORECASE)
BARCELONA_RE = re.compile(r"barcelona|bcn|hospitalet|badalona|sant cugat|cornell",
                          re.IGNORECASE)
REMOTO_RE = re.compile(r"remote|remoto|teletrabajo|distributed|anywhere", re.IGNORECASE)

_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_lock_cache = Lock()
_ultima_peticion: dict[str, float] = {}
_lock_ritmo = Lock()


# ----------------------------------------------------------------------
# REGISTRO
# ----------------------------------------------------------------------


def cargar_registro() -> dict:
    """Lee empresas.json. Si falta o está roto, devuelve un registro vacío y lo dice:
    quedarse sin esta fuente no debe impedir que la búsqueda siga con las demás."""
    try:
        datos = json.loads(REGISTRO_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("No se ha podido leer %s (%s); la fuente 'empresas' no aportará nada.",
                       REGISTRO_PATH.name, e)
        return {"empresas": [], "patron_perfil": ""}
    if not isinstance(datos, dict):
        return {"empresas": [], "patron_perfil": ""}
    datos.setdefault("empresas", [])
    datos.setdefault("patron_perfil", "")
    return datos


# ----------------------------------------------------------------------
# ADAPTADORES POR ATS
#
# Cada adaptador recibe el JSON del tablón y devuelve una lista de ofertas ya en el
# contrato del proyecto. Añadir un ATS nuevo es añadir una entrada a ADAPTADORES.
# ----------------------------------------------------------------------


def _texto_plano(html: str | None) -> str | None:
    if not html:
        return None
    plano = ETIQUETA_HTML_RE.sub(" ", str(html))
    plano = (plano.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
             .replace("&quot;", '"'))
    return re.sub(r"[ \t]+", " ", plano).strip() or None


def _fecha_iso(valor) -> date | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(valor)[:10])
        except ValueError:
            return None


def _fecha_ms(valor) -> date | None:
    if not isinstance(valor, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(valor / 1000).date()
    except (OverflowError, OSError, ValueError):
        return None


def _greenhouse(datos: dict, empresa: str) -> list[dict]:
    filas = []
    for j in datos.get("jobs") or []:
        ubicacion = ((j.get("location") or {}).get("name") or "").strip() or None
        filas.append({
            "title": (j.get("title") or "").strip(),
            "company": empresa,
            "location": ubicacion,
            "job_url": j.get("absolute_url"),
            "date_posted": _fecha_iso(j.get("first_published") or j.get("updated_at")),
            "description": _texto_plano(j.get("content")),
            "job_type": None,
            "min_amount": None, "max_amount": None, "currency": None, "interval": None,
            "is_remote": bool(ubicacion and REMOTO_RE.search(ubicacion)),
        })
    return filas


def _lever(datos: list, empresa: str) -> list[dict]:
    filas = []
    for j in datos or []:
        cat = j.get("categories") or {}
        ubicacion = (cat.get("location") or "").strip() or None
        salario = j.get("salaryRange") or {}
        filas.append({
            "title": (j.get("text") or "").strip(),
            "company": empresa,
            "location": ubicacion,
            "job_url": j.get("hostedUrl") or j.get("applyUrl"),
            "date_posted": _fecha_ms(j.get("createdAt")),
            "description": j.get("descriptionPlain") or _texto_plano(j.get("description")),
            "job_type": (cat.get("commitment") or "").lower().replace("-", "") or None,
            "min_amount": salario.get("min"),
            "max_amount": salario.get("max"),
            "currency": salario.get("currency"),
            "interval": (salario.get("interval") or "").replace("per-", "").replace(
                "annum", "yearly") or None,
            "is_remote": (j.get("workplaceType") or "").lower() == "remote"
            or bool(ubicacion and REMOTO_RE.search(ubicacion)),
        })
    return filas


def _ashby(datos: dict, empresa: str) -> list[dict]:
    filas = []
    for j in datos.get("jobs") or []:
        ubicacion = (j.get("location") or "").strip() or None
        filas.append({
            "title": (j.get("title") or "").strip(),
            "company": empresa,
            "location": ubicacion,
            "job_url": j.get("jobUrl") or j.get("applyUrl"),
            "date_posted": _fecha_iso(j.get("publishedAt") or j.get("updatedAt")),
            "description": j.get("descriptionPlain") or _texto_plano(j.get("descriptionHtml")),
            "job_type": (j.get("employmentType") or "").lower() or None,
            # El resumen de retribución de Ashby es texto libre ("€40K – €50K"), no un
            # rango estructurado; no se intenta adivinar un número a partir de él.
            "min_amount": None, "max_amount": None,
            "currency": None, "interval": None,
            "is_remote": bool(j.get("isRemote"))
            or bool(ubicacion and REMOTO_RE.search(ubicacion)),
        })
    return filas


def _recruitee(datos: dict, empresa: str) -> list[dict]:
    filas = []
    for j in datos.get("offers") or []:
        ubicacion = (j.get("location") or j.get("city") or "").strip() or None
        filas.append({
            "title": (j.get("title") or "").strip(),
            "company": empresa,
            "location": ubicacion,
            "job_url": j.get("careers_url") or j.get("careers_apply_url") or j.get("url"),
            "date_posted": _fecha_iso(j.get("published_at") or j.get("created_at")),
            "description": _texto_plano(j.get("description")),
            "job_type": (j.get("employment_type") or "").lower() or None,
            "min_amount": None, "max_amount": None, "currency": None, "interval": None,
            "is_remote": bool(j.get("remote"))
            or bool(ubicacion and REMOTO_RE.search(ubicacion)),
        })
    return filas


def _smartrecruiters(datos: dict, empresa: str) -> list[dict]:
    filas = []
    for j in datos.get("content") or []:
        loc = j.get("location") or {}
        partes = [loc.get("city"), loc.get("region"), loc.get("country")]
        ubicacion = ", ".join(p for p in partes if p) or None
        filas.append({
            "title": (j.get("name") or "").strip(),
            "company": empresa,
            "location": ubicacion,
            "job_url": (f"https://jobs.smartrecruiters.com/{j.get('company', {}).get('identifier', '')}"
                        f"/{j.get('id')}" if j.get("id") else None),
            "date_posted": _fecha_iso(j.get("releasedDate")),
            # El listado de SmartRecruiters no trae descripción; pedirla serían N
            # peticiones más contra un host al que ya se le está pidiendo despacio.
            "description": None,
            "job_type": ((j.get("typeOfEmployment") or {}).get("label") or "").lower() or None,
            "min_amount": None, "max_amount": None, "currency": None, "interval": None,
            "is_remote": bool(loc.get("remote")),
        })
    return filas


ADAPTADORES = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true", _greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{t}?mode=json", _lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}", _ashby),
    "recruitee": ("https://{t}.recruitee.com/api/offers/", _recruitee),
    "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=100",
                        _smartrecruiters),
}


# ----------------------------------------------------------------------
# DESCARGA
# ----------------------------------------------------------------------


def _fuente_salud(ats: str) -> str:
    """Un bloqueo de un ATS no debe desactivar los demás: cada uno lleva su contador."""
    return f"empresas:{ats}"


def _esperar_turno(ats: str) -> None:
    """Ritmo mínimo por ATS. El lock es global a propósito: las búsquedas van en varios
    hilos, y sin él cada hilo respetaría la espera por su cuenta y entre todos se
    saltarían el límite."""
    espera = ESPERA_POR_ATS.get(ats, ESPERA_POR_DEFECTO)
    with _lock_ritmo:
        ultima = _ultima_peticion.get(ats, 0.0)
        pendiente = espera - (time.monotonic() - ultima)
        if pendiente > 0:
            time.sleep(pendiente)
        _ultima_peticion[ats] = time.monotonic()


def _descargar(ats: str, token: str, empresa: str) -> list[dict]:
    """Baja el tablón de una empresa. Lista vacía (nunca excepción) si algo falla.

    El 404 ("esta empresa no tiene tablón con este token") se deja pasar a través de
    `ignorar_estados`: no es un bloqueo (no cuenta para desactivar la fuente) ni un
    fallo de red que valga la pena reintentar, así que se resuelve aquí mismo con su
    propio mensaje en vez del genérico "fallo tras N intentos" de base.peticion().
    """
    plantilla, adaptador = ADAPTADORES[ats]
    url = plantilla.format(t=token)
    fuente = _fuente_salud(ats)

    def hacer():
        _esperar_turno(ats)
        return requests.get(url, headers=HEADERS, timeout=TIMEOUT)

    r = base.peticion(fuente, hacer, ignorar_estados=frozenset({404}))
    if r is None:
        return []
    if r.status_code == 404:
        logger.warning("empresas: %s no tiene tablón en %s con el token '%s' (404).",
                       empresa, ats, token)
        return []
    try:
        datos = r.json()
        return [f for f in adaptador(datos, empresa) if f.get("title") and f.get("job_url")]
    except (ValueError, AttributeError, TypeError) as e:
        # JSON inesperado o cambio de estructura del ATS: se registra y se sigue con
        # el resto de empresas, no se tira la búsqueda entera.
        logger.warning("empresas: respuesta inesperada de %s/%s (%s): %s",
                       ats, token, empresa, e)
        return []


def _tablon(ats: str, token: str, empresa: str) -> list[dict]:
    """_descargar con caché por TTL (ver TTL_CACHE_SEGUNDOS)."""
    clave = (ats, token)
    ahora = time.monotonic()
    with _lock_cache:
        entrada = _cache.get(clave)
        if entrada and ahora - entrada[0] < TTL_CACHE_SEGUNDOS:
            return entrada[1]

    filas = _descargar(ats, token, empresa)

    with _lock_cache:
        _cache[clave] = (time.monotonic(), filas)
    return filas


def limpiar_cache() -> None:
    """Fuerza que la próxima búsqueda vuelva a pedir los tablones (útil en pruebas)."""
    with _lock_cache:
        _cache.clear()


# ----------------------------------------------------------------------
# FILTRADO POR ÁMBITO
# ----------------------------------------------------------------------


def _encaja_ambito(oferta: dict, location: str) -> bool:
    """
    ¿Esta oferta pertenece al ámbito geográfico que se está buscando?

    Hace falta porque un tablón de empresa devuelve sus vacantes de TODO el mundo, no
    las de la ciudad que se está buscando: sin esto, el ámbito "Barcelona" se llenaría
    de puestos en Varsovia o Singapur.

    Aquí no se exige que la oferta sea remota aunque el ámbito lo sea (por eso no se usa
    el is_remote de la búsqueda): de eso ya se encarga después filtrar_presenciales_lejos
    en buscador.py, que aplica el mismo criterio a todas las fuentes por igual.
    """
    ubicacion = oferta.get("location") or ""
    remota = bool(oferta.get("is_remote")) or bool(REMOTO_RE.search(ubicacion))
    loc = (location or "").lower()

    if "barcelona" in loc:
        return bool(BARCELONA_RE.search(ubicacion))
    if "spain" in loc or "españa" in loc:
        return bool(ESPANA_RE.search(ubicacion)) or (remota and not ubicacion)
    if "european union" in loc or "europe" in loc:
        return bool(PAISES_UE_RE.search(ubicacion)) or (remota and not ubicacion)
    # Ámbito sin ubicación (remoto mundial): solo tiene sentido lo explícitamente remoto
    return remota


def buscar_empresas(
    termino: str,
    location: str = "",
    hours_old: int | None = None,
    is_remote: bool = False,
) -> pd.DataFrame:
    """
    Recorre los tablones de empresas.json y devuelve las ofertas del ámbito pedido, con
    las columnas del contrato del proyecto.

    `termino` NO se usa para filtrar: el tablón de una empresa no es un buscador, se
    lista entero y decide después el pipeline (reglas de descarte + relevancia), igual
    que haría alguien mirando la web de la empresa. Lo único que se recorta aquí es el
    ámbito geográfico y el patrón de volumen (ver empresas.json).

    Nunca lanza: cualquier fallo devuelve un DataFrame vacío y queda en el log.
    """
    registro = cargar_registro()
    empresas = [e for e in registro.get("empresas", []) if e.get("activa", True)]
    if not empresas:
        return base.vacio()

    try:
        patron = re.compile(registro.get("patron_perfil") or ".", re.IGNORECASE)
    except re.error:
        logger.warning("empresas: patron_perfil no es una expresión regular válida; se ignora.")
        patron = re.compile(".")

    dias = max(DIAS_MINIMOS, (hours_old or 0) / 24)
    limite_fecha = date.today() - timedelta(days=dias)

    filas: list[dict] = []
    try:
        for entrada in empresas:
            ats = entrada.get("ats")
            token = (entrada.get("token") or "").strip()
            nombre = (entrada.get("nombre") or token or "?").strip()
            if ats not in ADAPTADORES or not token:
                logger.warning("empresas: entrada inválida en el registro (%s)", entrada)
                continue
            if not salud.esta_activa(_fuente_salud(ats)):
                continue

            for oferta in _tablon(ats, token, nombre):
                if not patron.search(oferta.get("title") or ""):
                    continue
                if not _encaja_ambito(oferta, location):
                    continue
                # Sin fecha no se descarta: algunos ATS no la publican, y perder por eso
                # una vacante abierta sería peor que enseñar una que quizá esté vieja.
                if oferta.get("date_posted") and oferta["date_posted"] < limite_fecha:
                    continue
                # El `site` es "empresa" a secas y no "empresa:greenhouse": aguas abajo
                # solo se pinta como etiqueta en el listado, y al usuario lo que le
                # importa es que viene de la web de la empresa, no de qué ATS usa. El
                # ATS sí queda en los logs y en el registro.
                filas.append({**oferta, "site": FUENTE})
    except Exception:
        logger.exception("empresas: fallo inesperado recorriendo los tablones")
        return base.vacio()

    if not filas:
        return base.vacio()
    return pd.DataFrame(filas, columns=base.COLUMNAS).astype(
        {c: "float64" for c in base.COLUMNAS_NUMERICAS}
    )
