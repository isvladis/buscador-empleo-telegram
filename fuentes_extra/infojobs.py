"""
Scraper de InfoJobs: el portal líder en España para perfiles junior de IT/soporte y
ciberseguridad, que python-jobspy no soporta. Se integra al mismo contrato de datos
que el resto de fuentes de buscador.py.

No requiere login ni API privada: los resultados de búsqueda se sirven ya
renderizados en el HTML de la propia página pública (sin llamadas XHR adicionales),
así que basta con pedir esa página y parsear las tarjetas de oferta.

Umbral de bloqueo (ver salud.py): se usa el umbral general (3 bloqueos consecutivos,
BLOQUEOS_PARA_DESACTIVAR_POR_DEFECTO), no el especial de 1 que lleva SmartRecruiters.
InfoJobs no tiene ninguna señal equivalente a la de SmartRecruiters (robots.txt con
`Disallow: /` para todo salvo LinkedInBot) ni a la de Tecnoempleo (bloqueo explícito a
bots de IA): es el portal que ya se consulta con más volumen de las fuentes propias
(9 de 1.497 ofertas del histórico) sin que se haya registrado nunca un bloqueo. No hay
motivo para tratarlo con más cautela que a EURES, así que lleva el mismo umbral.

CLIENTE CON HUELLA DE CHROME (2026-10-27, por decisión explícita del usuario). Desde el
26-10 hacia las 18:00 UTC, InfoJobs sirve a python-requests una página anti-bot («No
podemos identificar tu navegador… Comprueba que JavaScript esté habilitado») con HTTP 200,
en vez de los resultados. Medido el 27-10, una petición de cada cliente desde la misma
conexión: python-requests recibe esa página (29 KB, 0 ofertas); tls_client con la huella
TLS de Chrome 120 recibe la página real (1,2 MB, 5 ofertas). Es la misma librería que
python-jobspy ya usa para LinkedIn e Indeed, así que no añade ninguna dependencia.

Esto esquiva la detección de bots de InfoJobs. Se le advirtió al usuario de que choca con
su propia regla 6 («scraping educado») y lo decidió igualmente. Las salvaguardas que se
mantienen: el mismo ritmo de antes (sin más peticiones ni más páginas); si aun así sale la
página anti-bot, cuenta como BLOQUEO (no como «página vacía») y a los 3 seguidos la fuente
se desactiva y avisa; y nada más allá: sin resolver CAPTCHAs, sin navegador sin cabeza y
sin proxies.
"""

from __future__ import annotations

import logging
import re
import random
import time
from datetime import date, datetime, timedelta
from threading import Lock

import threading

import pandas as pd
import requests
import tls_client
from bs4 import BeautifulSoup
from tls_client.exceptions import TLSClientExeption

from fuentes_extra import base
from fuentes_extra import salud

logger = logging.getLogger(__name__)

FUENTE = "infojobs"
BASE_URL = "https://www.infojobs.net/jobsearch/search-results/list.xhtml"

# InfoJobs identifica cada provincia por un id numérico interno (el value del
# checkbox del filtro "Provincia" en su propia búsqueda). Solo hace falta el de
# Barcelona: para el resto de ámbitos se busca en toda España sin restringir provincia.
PROVINCIA_IDS = {"barcelona": 9}

# El User-Agent tiene que decir la misma versión que la huella TLS (Chrome 120): un
# «Chrome 124» con huella de 120 es justo la incoherencia que delata a un bot.
CLIENTE_TLS = "chrome_120"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
}
MARCA_ANTIBOT = "No podemos identificar tu navegador"

TIMEOUT = 15
RESULTADOS_POR_PAGINA = 5
MAX_PAGINAS = 10  # tope de cortesía: 50 ofertas es de sobra y evita abusar del portal

RELATIVO_RE = re.compile(r"hace\s+(\d+)\s*(min|h|d)\b", re.IGNORECASE)
FECHA_CORTA_RE = re.compile(r"\b(\d{1,2})\s+([a-z]{3})\b", re.IGNORECASE)
MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

# "1.500 € - 1.600 € Bruto/mes", "18.000 € - 26.000 € Bruto/año", "Más de 18.000 €"...
SALARIO_RE = re.compile(
    r"([\d.,]+)\s*€(?:\s*-\s*([\d.,]+)\s*€)?(?:.*?Bruto/(año|mes|hora|día|semana))?",
    re.IGNORECASE,
)
INTERVALO_ES = {
    "año": "yearly", "mes": "monthly", "hora": "hourly",
    "día": "daily", "semana": "weekly",
}


def _fecha_desde_texto(texto: str) -> date | None:
    """'Hace 3d' / 'Hace 12h' / '28 jul' -> date. None si no se reconoce el formato."""
    texto = texto.strip().lower()

    m = RELATIVO_RE.search(texto)
    if m:
        cantidad, unidad = int(m.group(1)), m.group(2)
        delta = {
            "min": timedelta(minutes=cantidad),
            "h": timedelta(hours=cantidad),
            "d": timedelta(days=cantidad),
        }[unidad]
        return (datetime.now() - delta).date()

    m = FECHA_CORTA_RE.search(texto)
    if m:
        mes = MESES.get(m.group(2))
        if not mes:
            return None
        dia = int(m.group(1))
        hoy = date.today()
        try:
            fecha = date(hoy.year, mes, dia)
        except ValueError:
            return None
        if fecha > hoy:  # p.ej. "28 dic" visto en enero es del año anterior
            fecha = fecha.replace(year=hoy.year - 1)
        return fecha
    return None


def _salario_desde_texto(texto: str | None):
    """'18.000 € - 26.000 € Bruto/año' -> (min, max, currency, interval); si no hay
    dato reconocible, cuatro None (no se adivina)."""
    if not texto or "no disponible" in texto.lower():
        return None, None, None, None
    m = SALARIO_RE.search(texto)
    if not m or not m.group(1):
        return None, None, None, None
    minimo = float(m.group(1).replace(".", "").replace(",", "."))
    maximo = float(m.group(2).replace(".", "").replace(",", ".")) if m.group(2) else minimo
    intervalo = INTERVALO_ES.get((m.group(3) or "").lower())
    return minimo, maximo, "EUR", intervalo


def _tipo_contrato(contrato_txt: str, jornada_txt: str) -> str | None:
    """Aproxima el job_type de jobspy (fulltime/parttime/contract/internship/temporary)
    a partir de los dos textos que InfoJobs muestra en la tarjeta. No es exacto -
    InfoJobs separa tipo de contrato y jornada como conceptos distintos, jobspy los
    mezcla en uno solo- pero es solo para etiquetar, no para filtrar."""
    contrato = (contrato_txt or "").lower()
    jornada = (jornada_txt or "").lower()
    if "parcial" in jornada:
        return "parttime"
    if "formativo" in contrato:
        return "internship"
    if "determinada" in contrato or "temporal" in contrato or "obra" in contrato:
        return "temporary"
    if "autónomo" in contrato or "autonomo" in contrato:
        return "contract"
    if "indefinido" in contrato or "completa" in jornada:
        return "fulltime"
    return None


# ----------------------------------------------------------------------
# CANARIO (2026-10-26): ¿sigue sabiendo leer la página?
#
# La búsqueda NO filtra por fecha en la petición (pide todo lo activo ordenado por fecha
# y recorta en local), así que una página de resultados trae tarjetas casi siempre,
# aunque no haya nada de las últimas 24 h. Una corrida entera —unas 22 búsquedas— con
# InfoJobs respondiendo bien y CERO tarjetas no es «no hay nada»: es que el lector ya no
# reconoce la página. Las búsquedas van en paralelo, así que el recuento lleva candado.
# ----------------------------------------------------------------------
_CORRIDA = {"respuestas": 0, "tarjetas": 0}
_candado_corrida = Lock()


def _anotar_respuesta(tarjetas: int) -> None:
    with _candado_corrida:
        _CORRIDA["respuestas"] += 1
        _CORRIDA["tarjetas"] += tarjetas


def empezar_corrida() -> None:
    """Pone a cero el recuento. La llama buscador.buscar() antes de las búsquedas."""
    with _candado_corrida:
        _CORRIDA.update(respuestas=0, tarjetas=0)


def cerrar_corrida() -> dict:
    """
    Decide al final de la corrida. Sin ninguna respuesta buena no se dice nada: o no se
    consultó InfoJobs, o se bloqueó, y eso ya lo registra base.peticion por su cuenta.
    """
    with _candado_corrida:
        resumen = dict(_CORRIDA)
    if resumen["respuestas"] == 0:
        return resumen
    if resumen["tarjetas"] == 0:
        salud.registrar_corrida_vacia(
            FUENTE, "%d páginas respondidas, 0 tarjetas" % resumen["respuestas"])
    else:
        salud.registrar_corrida_con_resultados(FUENTE)
    return resumen


# RITMO (2026-10-29). La primera corrida automática con tls_client (27-10, 23:45 UTC)
# recibió la página anti-bot 3 veces seguidas y la fuente se desactivó. Una petición
# suelta, antes y después, pasaba (10 tarjetas desde el contenedor; 5 desde el PC el 29-10,
# así que la IP no estaba marcada). Lo que delataba era el patrón de la corrida: 3
# búsquedas en paralelo, cada una con su sesión nueva y sin cookies, páginas a 0,3 s, unas
# 22 peticiones en minuto y medio. Ahora, como un navegador: UNA sesión (las cookies que
# pone InfoJobs se conservan entre peticiones), las peticiones de InfoJobs de una en una
# aunque las búsquedas vayan en paralelo, y una pausa al azar entre ellas. Mismo número de
# peticiones; más tiempo.
PAUSA_ENTRE_PETICIONES = (4.0, 9.0)  # segundos, al azar dentro del intervalo
_turno = threading.Lock()
_ESTADO_SESION = {"sesion": None, "ultima": None}


def _sesion() -> tls_client.Session:
    """La sesión única de InfoJobs. Se usa siempre con _turno tomado."""
    if _ESTADO_SESION["sesion"] is None:
        _ESTADO_SESION["sesion"] = tls_client.Session(
            client_identifier=CLIENTE_TLS, random_tls_extension_order=True)
    return _ESTADO_SESION["sesion"]


def _esperar_turno() -> None:
    """Con _turno tomado: deja pasar la pausa desde la petición anterior."""
    ultima = _ESTADO_SESION["ultima"]
    if ultima is not None:
        falta = random.uniform(*PAUSA_ENTRE_PETICIONES) - (time.monotonic() - ultima)
        if falta > 0:
            time.sleep(falta)


class _Respuesta:
    """Lo que base.peticion() usa de una Response de requests, sobre la de tls_client
    (que no trae raise_for_status)."""

    def __init__(self, r):
        self.status_code, self.text, self.url = r.status_code, r.text or "", r.url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError("%s en %s" % (self.status_code, BASE_URL))


def _get(params: dict) -> _Respuesta:
    with _turno:
        _esperar_turno()
        try:
            r = _sesion().get(BASE_URL, params=params, headers=HEADERS,
                              timeout_seconds=TIMEOUT, allow_redirects=True)
        except TLSClientExeption as e:
            # Como un fallo de red de requests: base.peticion lo reintenta con espera.
            raise requests.ConnectionError("tls_client: %s" % e) from None
        finally:
            _ESTADO_SESION["ultima"] = time.monotonic()
    return _Respuesta(r)


def es_pagina_antibot(r) -> str | None:
    if MARCA_ANTIBOT in (r.text or ""):
        return ("InfoJobs ha servido su página anti-bot («%s») con HTTP %s en vez de los "
                "resultados" % (MARCA_ANTIBOT, r.status_code))
    return None


def _peticion(params: dict) -> _Respuesta | None:
    """GET con reintentos, backoff y salud ya integrados (ver fuentes_extra/base.py:
    un bloqueo no se reintenta, se registra y se corta ahí mismo). La página anti-bot
    cuenta como bloqueo aunque llegue con 200."""
    return base.peticion(FUENTE, lambda: _get(params), es_bloqueo=es_pagina_antibot)


def _extraer_oferta(card) -> dict | None:
    title_a = card.select_one(".ij-OfferCardContent-description-title a")
    if title_a is None:
        return None
    href = title_a.get("href", "")
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.infojobs.net" + href
    job_url = href.split("?")[0] if href else None
    if not job_url:
        return None

    company_a = card.select_one(".ij-OfferCardContent-description-subtitle a")

    # La modalidad (Presencial/Híbrido/Teletrabajo) no siempre aparece -algunas
    # ofertas no la indican-, así que no se puede asumir una posición fija: la
    # ubicación es siempre el primer elemento y la fecha el último; lo que quede
    # en medio (si queda algo) es la modalidad.
    listas = card.select(".ij-OfferCardContent-description-list")
    cabecera = [li.get_text(" ", strip=True) for li in listas[0].select("li")] if listas else []
    ubicacion = cabecera[0] if len(cabecera) > 0 else None
    modalidad = " ".join(cabecera[1:-1]) if len(cabecera) > 2 else ""
    fecha_txt = cabecera[-1] if len(cabecera) > 1 else ""

    pie = (
        [li.get_text(" ", strip=True) for li in listas[1].select("li")]
        if len(listas) > 1
        else []
    )
    contrato_txt = pie[0] if len(pie) > 0 else ""
    jornada_txt = pie[1] if len(pie) > 1 else ""
    salario_txt = pie[2] if len(pie) > 2 else None

    desc_el = card.select_one(".ij-OfferCardContent-description-description")
    descripcion = desc_el.get_text("\n", strip=True) if desc_el else None

    minimo, maximo, moneda, intervalo = _salario_desde_texto(salario_txt)

    return {
        "title": title_a.get_text(strip=True),
        "company": company_a.get_text(strip=True) if company_a else None,
        "location": f"{ubicacion}, Spain" if ubicacion else "Spain",
        "job_url": job_url,
        "date_posted": _fecha_desde_texto(fecha_txt),
        "description": descripcion,
        "job_type": _tipo_contrato(contrato_txt, jornada_txt),
        "min_amount": minimo,
        "max_amount": maximo,
        "currency": moneda,
        "interval": intervalo,
        "is_remote": "teletrabajo" in modalidad.lower() or "remoto" in modalidad.lower(),
        "site": FUENTE,
    }


def buscar_infojobs(
    termino: str,
    location: str = "",
    hours_old: int | None = None,
    is_remote: bool = False,
) -> pd.DataFrame:
    """
    Busca ofertas en InfoJobs y las devuelve con las mismas columnas que usa el resto
    del pipeline (buscador.py / etiquetas.py).

    Cualquier fallo (red, timeout, cambio de estructura del HTML...) se registra con
    logging y se devuelve un DataFrame vacío: nunca debe romper una búsqueda que
    también incluye indeed/linkedin/google.
    """
    if not salud.esta_activa(FUENTE):
        # Una vez por corrida, no una por búsqueda (ver salud.anunciar_desactivada).
        if salud.anunciar_desactivada(FUENTE):
            logger.info("InfoJobs: fuente desactivada (%s); no se consulta.",
                        salud.motivo_desactivacion(FUENTE))
        return base.vacio()

    params_base: dict = {"keyword": termino, "sortBy": "PUBLICATION_DATE"}
    if "barcelona" in (location or "").lower():
        params_base["provinceIds"] = PROVINCIA_IDS["barcelona"]
    if is_remote:
        params_base["teleworkingIds"] = 2  # "Solo teletrabajo"

    limite_fecha = (date.today() - timedelta(hours=hours_old)) if hours_old else None

    filas: list[dict] = []
    try:
        for pagina in range(1, MAX_PAGINAS + 1):
            # Sin pausa aquí: la pone _get() entre TODAS las peticiones de InfoJobs, también
            # entre las de búsquedas distintas que van en paralelo.
            r = _peticion({**params_base, "page": pagina})
            if r is None:
                break  # ya se ha logueado el error dentro de _peticion

            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".ij-OfferCardContent")
            # Se anota SIEMPRE, también con cero tarjetas: es lo que permite distinguir al
            # cerrar la corrida «no hay nada» de «ya no sé leer esta página». Antes, este
            # `if not cards: break` era mudo, y un cambio de marcado de InfoJobs habría
            # dejado la fuente sin ofertas para siempre sin que nada lo notara.
            _anotar_respuesta(len(cards))
            if not cards:
                break

            en_ventana = 0
            for card in cards:
                try:
                    oferta = _extraer_oferta(card)
                except Exception:
                    logger.exception("InfoJobs: fallo parseando una tarjeta de oferta")
                    continue
                if oferta is None:
                    continue
                # Fuera de la ventana: se SALTA, no se corta la página.
                #
                # `sortBy=PUBLICATION_DATE` no ordena de verdad: InfoJobs intercala sus
                # ofertas promocionadas arriba pase lo que pase. Medido el 15 de agosto
                # de 2026 sobre la página 1 de cinco términos reales, tres venían
                # desordenadas — «helpdesk» devolvía [08-12, 08-03, 08-14, 08-14,
                # 08-14]. El código anterior daba por buena la ordenación y cortaba en
                # la primera antigua, así que en ese ejemplo se quedaba con CERO ofertas
                # y tiraba las tres del día que sí entraban en la ventana. Eso explica
                # que InfoJobs aportara 10 ofertas de 1.606 en todo el histórico.
                if limite_fecha and oferta["date_posted"] and oferta["date_posted"] < limite_fecha:
                    continue
                en_ventana += 1
                filas.append(oferta)

            # Se para cuando una página ENTERA no trae nada dentro de la ventana (ahí sí
            # se ha agotado lo reciente), no en cuanto aparece la primera antigua. Cuesta
            # una petición más por búsqueda que cortar a la primera; a cambio, deja de
            # perderse el grueso de lo que el portal sí publica.
            if not en_ventana or len(cards) < RESULTADOS_POR_PAGINA:
                break
    except Exception:
        logger.exception("InfoJobs: fallo inesperado buscando '%s'", termino)
        return base.vacio()

    if not filas:
        return base.vacio()
    return pd.DataFrame(filas, columns=base.COLUMNAS).astype(
        {c: "float64" for c in base.COLUMNAS_NUMERICAS}
    )
