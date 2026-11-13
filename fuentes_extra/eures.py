"""
Scraper de EURES: el portal oficial de movilidad laboral de la Unión Europea.

Interesa por una razón concreta y comprobada: los servicios públicos de empleo vuelcan
ahí sus ofertas, así que EURES trae vacantes de **Feina Activa (Generalitat de
Catalunya)** y del SEPE que no se republican en LinkedIn, Indeed ni InfoJobs. Es decir,
aporta cobertura exclusiva de verdad, no otro agregador de lo mismo. Eso hace que
construir SEPE o Barcelona Activa por separado sea redundante: ya llegan por aquí.

Cómo se encontró la API: el buscador de EURES es una SPA de Angular, así que la URL
pública no sirve para nada sin JavaScript. El endpoint real es el que usa la propia
página (POST a jv-search/search) y devuelve JSON limpio, sin login y sin anti-bot.

Dos avisos importantes sobre esta fuente, medidos, no supuestos:

1. El JSON del cuerpo es ESTRICTO: cualquier campo que no conozca (p.ej. "lang") hace
   que conteste 400 {"key":"invalid-json"}. Por eso el cuerpo se construye con
   exactamente los campos de _cuerpo() y no se le añaden "por si acaso".

2. Su corpus se actualiza despacio: con publicationPeriod=LAST_DAY o LAST_WEEK devuelve
   CERO resultados para todos los términos de este perfil, y la antigüedad mediana de
   lo que sí trae ronda los 17-56 días. Por eso ignora a propósito un hours_old más
   estricto que un mes (ver PERIODO y la nota del README): aplicarlo literalmente
   convertiría la fuente en un no-op silencioso. Como el dedupe y `visto_por_primera_vez`
   hacen que cada oferta se vea una sola vez, enseñar cosas de hace tres semanas que
   nadie más publica sale a cuenta.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests

from fuentes_extra import base
from fuentes_extra import salud

logger = logging.getLogger(__name__)

FUENTE = "eures"
BASE_URL = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
URL_DETALLE = "https://europa.eu/eures/portal/jv-se/jv-details/{id}"

HEADERS = {
    "User-Agent": base.USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
}

TIMEOUT = 30
ESPERA_ENTRE_PAGINAS = 0.5  # cortesía con un portal institucional
RESULTADOS_POR_PAGINA = 25
MAX_PAGINAS = 4  # 100 ofertas por término es de sobra: los conteos reales van de 0 a 13

# El único valor de publicationPeriod que EURES tiene realmente poblado para este perfil
# (ver el punto 2 del docstring). LAST_DAY y LAST_WEEK devuelven 0 en todos los términos.
PERIODO = "LAST_MONTH"
DIAS_DEL_PERIODO = 31

# Ámbito del proyecto -> códigos de ubicación de EURES. Acepta ISO-2 de país y NUTS2;
# comprobado que NUTS3 (ES511) devuelve 0, así que para Barcelona lo más fino que
# funciona es Cataluña entera (ES51) y ya recorta después el filtro de distancia real.
CATALUNA = ["ES51"]
ESPANA = ["es"]

# NUTS3 español -> provincia, para poder construir "Barcelona, Spain" y que extraer_pais
# y el filtro de distancia funcionen igual que con el resto de fuentes.
NUTS3_ES = {
    "ES111": "A Coruña", "ES112": "Lugo", "ES113": "Ourense", "ES114": "Pontevedra",
    "ES120": "Asturias", "ES130": "Cantabria", "ES211": "Álava", "ES212": "Guipúzcoa",
    "ES213": "Vizcaya", "ES220": "Navarra", "ES230": "La Rioja", "ES241": "Huesca",
    "ES242": "Teruel", "ES243": "Zaragoza", "ES300": "Madrid", "ES411": "Ávila",
    "ES412": "Burgos", "ES413": "León", "ES414": "Palencia", "ES415": "Salamanca",
    "ES416": "Segovia", "ES417": "Soria", "ES418": "Valladolid", "ES419": "Zamora",
    "ES421": "Albacete", "ES422": "Ciudad Real", "ES423": "Cuenca", "ES424": "Guadalajara",
    "ES425": "Toledo", "ES431": "Badajoz", "ES432": "Cáceres", "ES511": "Barcelona",
    "ES512": "Girona", "ES513": "Lleida", "ES514": "Tarragona", "ES521": "Alicante",
    "ES522": "Castellón", "ES523": "Valencia", "ES531": "Eivissa y Formentera",
    "ES532": "Mallorca", "ES533": "Menorca", "ES611": "Almería", "ES612": "Cádiz",
    "ES613": "Córdoba", "ES614": "Granada", "ES615": "Huelva", "ES616": "Jaén",
    "ES617": "Málaga", "ES618": "Sevilla", "ES620": "Murcia", "ES630": "Ceuta",
    "ES640": "Melilla", "ES704": "Fuerteventura", "ES705": "Gran Canaria",
    "ES706": "La Gomera", "ES707": "La Palma", "ES708": "Lanzarote", "ES709": "Tenerife",
}
# ISO-2 -> nombre en inglés, que es lo que etiquetas.extraer_pais sabe traducir.
PAIS_ISO2 = {
    "ES": "Spain", "DE": "Germany", "FR": "France", "IT": "Italy", "PT": "Portugal",
    "NL": "Netherlands", "IE": "Ireland", "BE": "Belgium", "PL": "Poland",
    "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "EL": "Greece", "AT": "Austria",
    "SE": "Sweden", "DK": "Denmark", "FI": "Finland", "EE": "Estonia", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "HU": "Hungary", "SK": "Slovakia",
    "SI": "Slovenia", "HR": "Croatia", "CY": "Cyprus", "MT": "Malta", "CZ": "Czechia",
    "NO": "Norway", "IS": "Iceland", "LI": "Liechtenstein", "CH": "Switzerland",
}

# positionOfferingCode de EURES -> job_type de jobspy. Aproximado, igual que en
# infojobs.py: sirve para etiquetar, no para filtrar.
TIPO_CONTRATO = {
    "contract": "contract", "temporary": "temporary", "seasonal": "temporary",
    "permanent": "fulltime", "apprenticeship": "internship", "traineeship": "internship",
    "internship": "internship",
}
JORNADA = {"parttime": "parttime", "partTime": "parttime", "fullTime": "fulltime",
           "fulltime": "fulltime"}

REMOTO_RE = re.compile(
    r"teletrabajo|teletreball|tele-?working|remot[oa]|remote|a distancia|"
    r"h[íi]brid[oa]|hybrid",
    re.IGNORECASE,
)
ETIQUETA_HTML_RE = re.compile(r"<[^>]+>")

# Coletillas de nivel que en las ofertas de los servicios públicos de empleo no aparecen
# nunca en el título. Como EURES combina las palabras con AND, un solo "n1" deja la
# búsqueda en cero: medido, "tecnico soporte n1" devuelve 1 resultado y "tecnico
# soporte" devuelve 33. Solo se quitan estas: "junior" NO entra aquí porque quitarla sí
# dispara el ruido ("junior soc" pasa de 2 resultados a los 746 de "soc", que casan con
# SOCial, SOCorrista y SOCiosanitari).
NIVEL_RE = re.compile(r"^(?:n[1-3]|l[1-3]|nivel|niveau|lvl)$", re.IGNORECASE)

def _codigos_ubicacion(location: str, is_remote: bool) -> list[str]:
    """Traduce el `location` del ámbito a los códigos que entiende EURES."""
    loc = (location or "").lower()
    if "barcelona" in loc:
        return CATALUNA
    if "spain" in loc or "españa" in loc:
        return ESPANA
    # "European Union" o vacío: sin restricción, que en EURES ya significa "toda la UE"
    return []


def _limpiar_html(texto: str | None) -> str | None:
    """El campo description viene con <br> y algún <a>; se deja en texto plano porque el
    resto del pipeline (detección de idioma, filtros por texto) trabaja sobre texto."""
    if not texto:
        return None
    plano = ETIQUETA_HTML_RE.sub(" ", texto)
    plano = plano.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t]+", " ", plano).strip() or None


def _ubicacion(location_map: dict | None) -> str | None:
    """{'ES': ['ES511']} -> 'Barcelona, Spain'."""
    if not isinstance(location_map, dict) or not location_map:
        return None
    iso2 = next(iter(location_map))
    pais = PAIS_ISO2.get((iso2 or "").upper(), iso2)
    codigos = location_map.get(iso2) or []
    provincias = [NUTS3_ES[c] for c in codigos if c in NUTS3_ES]
    if provincias:
        # Con varias provincias se usa la primera: el pipeline solo necesita una
        # ubicación para geocodificar, y la lista completa iría al texto sin aportar nada.
        return f"{provincias[0]}, {pais}"
    return pais


def _consultas(termino: str) -> list[list[str]]:
    """
    Las listas de palabras que se van a probar, en orden, para un término.

    La primera es el término tal cual. La segunda —si aporta algo distinto— es el mismo
    término sin las coletillas de nivel (ver NIVEL_RE), y solo se llega a ella si la
    primera no devuelve nada: así "tecnico soporte n1", que en EURES da 1 resultado,
    acaba consultando "tecnico soporte", que da 33 y es donde están los técnicos de
    soporte informático de Feina Activa.

    No hace falta traducir nada al catalán aunque Feina Activa publique en catalán:
    comprobado que EURES normaliza los acentos y la raíz de la palabra, así que
    "tecnico soporte" (33 resultados) y "tecnic suport" (32) devuelven prácticamente el
    mismo conjunto, con los títulos en catalán incluidos en ambos.
    """
    palabras = [p for p in re.split(r"\s+", termino.strip()) if p]
    if not palabras:
        return []
    sin_nivel = [p for p in palabras if not NIVEL_RE.match(p)]
    if sin_nivel and sin_nivel != palabras:
        return [palabras, sin_nivel]
    return [palabras]


def _cuerpo(palabras: list[str], codigos: list[str], pagina: int) -> dict:
    """
    Construye el cuerpo EXACTO que acepta la API (ver aviso 1 del docstring del módulo).

    Cada palabra va como una keyword propia sobre el título, y EURES las combina con
    AND. Medido: buscar sobre el texto completo (EVERYWHERE) devuelve ruido a espuertas
    ("analista soc" -> 10.329 resultados), mientras que sobre el título con todas las
    palabras da conjuntos pequeños y de perfil ("helpdesk" -> 5, "analista soc" -> 1,
    todos ellos realmente de soporte/ciberseguridad).

    El ruido que aun así se cuela (EURES hace coincidencia por prefijo, así que "soc"
    también casa con "SOCial") no se filtra aquí a propósito: de eso ya se encargan las
    reglas de descarte y el orden por relevancia de buscador.py, que es donde vive esa
    decisión para todas las fuentes por igual.
    """
    return {
        "keywords": [{"keyword": p, "specificSearchCode": "TITLE"} for p in palabras],
        "locationCodes": codigos,
        "page": pagina,
        "resultsPerPage": RESULTADOS_POR_PAGINA,
        "sortSearch": "MOST_RECENT",
        "publicationPeriod": PERIODO,
    }


def _peticion(cuerpo: dict) -> dict | None:
    """POST con reintentos, backoff y salud ya integrados (ver fuentes_extra/base.py).
    None si falla, si está bloqueada, o si la respuesta no trae JSON válido."""
    r = base.peticion(FUENTE, lambda: requests.post(
        BASE_URL, json=cuerpo, headers=HEADERS, timeout=TIMEOUT
    ))
    if r is None:
        return None
    try:
        return r.json()
    except ValueError as e:  # respuesta 200 que no era JSON
        logger.warning("EURES: respuesta sin JSON válido: %s", e)
        return None


def _extraer_oferta(jv: dict) -> dict | None:
    identificador = jv.get("id")
    titulo = (jv.get("title") or "").strip()
    if not identificador or not titulo:
        return None

    descripcion = _limpiar_html(jv.get("description"))
    ubicacion = _ubicacion(jv.get("locationMap"))

    fecha = None
    marca = jv.get("creationDate")
    if isinstance(marca, (int, float)):
        try:
            fecha = datetime.fromtimestamp(marca / 1000).date()
        except (OverflowError, OSError, ValueError):
            fecha = None

    # El empleador viene casi siempre a None en los volcados de los servicios públicos
    # de empleo españoles (Feina Activa/SEPE publican la oferta, no la empresa). No se
    # inventa nada: se deja vacío y el resto del pipeline lo trata como tal.
    empleador = jv.get("employer")
    if isinstance(empleador, dict):
        empresa = (empleador.get("name") or "").strip() or None
    elif isinstance(empleador, str):
        empresa = empleador.strip() or None
    else:
        empresa = None

    jornadas = jv.get("positionScheduleCodes") or []
    tipo = None
    for j in jornadas:
        if j in JORNADA:
            tipo = JORNADA[j]
            break
    if tipo is None:
        tipo = TIPO_CONTRATO.get(jv.get("positionOfferingCode") or "")

    return {
        "title": titulo,
        "company": empresa,
        "location": ubicacion,
        "job_url": URL_DETALLE.format(id=quote(str(identificador))),
        "date_posted": fecha,
        "description": descripcion,
        "job_type": tipo,
        # EURES no publica salario estructurado en la respuesta de búsqueda; no se
        # adivina a partir del texto (mismo criterio que infojobs.py).
        "min_amount": None,
        "max_amount": None,
        "currency": None,
        "interval": None,
        # Solo el TÍTULO. Buscarlo también en la descripción da falsos positivos que
        # además son caros: una oferta presencial en Zaragoza decía "atención directa
        # al cliente, tanto presencial como remota" —lo remoto es la atención, no el
        # puesto— y al marcarla como teletrabajo se colaba por filtrar_presenciales_lejos.
        # Leer la descripción con criterio ya lo hace buscador._es_remota, que exige
        # frases inequívocas y descarta si hay una indicación clara de presencialidad.
        "is_remote": bool(REMOTO_RE.search(titulo)),
        "site": FUENTE,
    }


def buscar_eures(
    termino: str,
    location: str = "",
    hours_old: int | None = None,
    is_remote: bool = False,
) -> pd.DataFrame:
    """
    Busca ofertas en EURES y las devuelve con las columnas del contrato del proyecto.

    Cualquier fallo se registra y se devuelve un DataFrame vacío: nunca debe romper una
    búsqueda que también incluye linkedin/indeed/infojobs.
    """
    if not salud.esta_activa(FUENTE):
        # Una vez por corrida, no una por búsqueda (ver salud.anunciar_desactivada).
        if salud.anunciar_desactivada(FUENTE):
            logger.info("EURES: fuente desactivada (%s); no se consulta.",
                        salud.motivo_desactivacion(FUENTE))
        return base.vacio()

    codigos = _codigos_ubicacion(location, is_remote)

    # La ventana efectiva es la MAYOR entre hours_old y DIAS_DEL_PERIODO: por debajo de
    # un mes EURES no tiene nada publicado (ver docstring del módulo), así que aplicar
    # hours_old al pie de la letra convertiría la fuente en un no-op silencioso.
    dias = max(DIAS_DEL_PERIODO, (hours_old or 0) / 24)
    limite_fecha = date.today() - timedelta(days=dias)

    filas: list[dict] = []
    try:
        for palabras in _consultas(termino):
            for pagina in range(1, MAX_PAGINAS + 1):
                if pagina > 1:
                    time.sleep(ESPERA_ENTRE_PAGINAS)

                datos = _peticion(_cuerpo(palabras, codigos, pagina))
                if datos is None:
                    break

                jvs = datos.get("jvs") or []
                if not jvs:
                    break

                for jv in jvs:
                    try:
                        oferta = _extraer_oferta(jv)
                    except Exception:
                        logger.exception("EURES: fallo parseando una oferta")
                        continue
                    if oferta is None:
                        continue
                    if oferta["date_posted"] and oferta["date_posted"] < limite_fecha:
                        continue
                    filas.append(oferta)

                if len(jvs) < RESULTADOS_POR_PAGINA:
                    break

            # Solo se prueba la consulta más amplia si la anterior no ha dado nada;
            # si ya hay resultados, ensancharla solo traería ruido (ver _consultas).
            if filas:
                break
    except Exception:
        logger.exception("EURES: fallo inesperado buscando '%s'", termino)
        return base.vacio()

    if not filas:
        return base.vacio()
    return pd.DataFrame(filas, columns=base.COLUMNAS).astype(
        {c: "float64" for c in base.COLUMNAS_NUMERICAS}
    )
