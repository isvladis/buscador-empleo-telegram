"""
Motor de búsqueda de ofertas. Toda la configuración llega como diccionario
(editable desde la web), en vez de estar fijada en constantes del código.

Lo usan tanto app.py (interfaz web) como test.py (línea de comandos).
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Callable

import pandas as pd
from jobspy import scrape_jobs

from fuentes_extra.empresas import buscar_empresas
from fuentes_extra.eures import buscar_eures
from fuentes_extra import infojobs as infojobs_mod
from fuentes_extra import tecnoempleo as tecnoempleo_mod
from fuentes_extra.infojobs import buscar_infojobs
from fuentes_extra.tecnoempleo import buscar_tecnoempleo
from fuentes_extra import jooble as jooble_mod
from fuentes_extra.jooble import buscar_jooble
# Las reglas de una persona ya no viven aquí: son un juego con nombre (ver reglas/). Estos
# nombres se siguen exponiendo desde buscador para que nadie tenga que cambiar sus imports.
from reglas import TITULAR_2026_10, UMBRAL_ENCAJE_ALTO
from reglas import obtener as obtener_juego
from reglas.titular_2026_10 import (
    PENALIZACIONES_RELEVANCIA,
    REGLAS_POR_DEFECTO,
    SENALES_RELEVANCIA,
    SENIOR_EXCLUDE,
)

try:
    from geopy.distance import geodesic
    from geopy.geocoders import Nominatim

    GEOCODING_DISPONIBLE = True
except ImportError:
    GEOCODING_DISPONIBLE = False

# JOBSPY_DATA_DIR permite redirigir los JSON persistentes a un volumen de Docker (ver
# docker-compose.yml) sin tocar nada más; sin la variable de entorno, el comportamiento
# en local es idéntico al de siempre (los JSON viven junto al código).
DIR_BASE = Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent)))
GEOCODE_CACHE_PATH = DIR_BASE / "geocode_cache.json"
# Duplicado a propósito de modelo_relevancia.MODELO_PATH: sirve para mirar la fecha del
# archivo sin tener que importar el módulo cuando el modelo no se está usando.
MODELO_REL_PATH = DIR_BASE / "modelo_relevancia.json"

logger = logging.getLogger(__name__)

# Heurística para avisar de ofertas remotas que puedan excluir candidatos desde España
# (no es 100% fiable: siempre hay que confirmar en la propia oferta)
RESTRICCION_GEO_TEXTO = re.compile(
    r"must be (?:based|located|authorized to work) in the (?:u\.?s\.?|united states|uk|united kingdom)|"
    r"us[\s-]?based only|us citizens? only|no visa sponsorship|"
    r"only (?:accepting|considering) candidates? (?:based|located) in|"
    r"security clearance required",
    re.IGNORECASE,
)

# LinkedIn/Google sin location devuelven sobre todo ofertas "remote" ligadas a un único
# país (mayoritariamente EE.UU.): si el campo location apunta a uno de estos países/estados,
# lo más probable es que esa oferta NO acepte candidatos desde España aunque diga "remote".
RESTRICCION_GEO_UBICACION = re.compile(
    r"united states|\busa\b|\bu\.s\.\b|canada|united kingdom|\buk\b|australia|\bindia\b|"
    r",\s*[A-Z]{2}$",  # formato típico EE.UU. "Ciudad, TX"
)

# Señales de que una oferta de EE. UU. contrata también fuera del país. Sin alguna de
# ellas se asume que no acepta candidatos desde España, que es el caso mayoritario.
#
# Solo valen frases sobre DÓNDE puede estar el candidato. Palabras como "worldwide",
# "Europe" o "EMEA" sueltas no sirven: aparecen constantemente en la publicidad de la
# empresa ("77 oficinas en 40 países", "empowering brands worldwide") y colaban ofertas
# que en realidad exigen residencia en EE. UU.
# El "anywhere" suelto no vale: se cualifica localmente ("remote from anywhere in CO",
# que es Colorado, no el mundo). Solo cuentan las frases que nombran explícitamente el
# mundo entero, Europa, la UE o España.
SENAL_GLOBAL_POR_DEFECTO = (
    r"(?:from |work )?anywhere in the world|live and work anywhere in the world"
    r"|anywhere in (?:europe|the eu\b|the european union)|any european country"
    r"|we hire globally|hire from anywhere in the world|globally distributed team"
    r"|open to candidates (?:based |located )?(?:in|from) (?:europe|the eu\b|spain|espa[ñn]a)"
    r"|candidates? (?:based |located )?in (?:europe|the eu\b|spain)"
    r"|eligible to work in (?:spain|the eu\b|europe|the european union)"
    r"|emea[- ]based|based in (?:europe|the eu\b|spain)"
    r"|(?:legally )?authoriz(?:ed|ation) to work in (?:spain|the eu\b|europe)"
)

# Países desde los que se puede trabajar sin permiso adicional siendo residente en
# España: los 27 de la UE más el Espacio Económico Europeo. Todo lo demás (EE. UU.,
# Reino Unido, Suiza, Canadá, India…) queda fuera salvo verificación explícita.
# Los nombres son los que devuelve etiquetas.extraer_pais.
PAISES_UE = [
    "España", "Alemania", "Austria", "Bélgica", "Bulgaria", "Chequia", "Chipre",
    "Croacia", "Dinamarca", "Eslovaquia", "Eslovenia", "Estonia", "Finlandia",
    "Francia", "Grecia", "Hungría", "Irlanda", "Italia", "Letonia", "Lituania",
    "Luxemburgo", "Malta", "Países Bajos", "Polonia", "Portugal", "Rumanía", "Suecia",
    # Espacio Económico Europeo
    "Noruega", "Islandia", "Liechtenstein",
]

# Muchas ofertas llegan sin ubicación (el campo va vacío en 1 de cada 3), así que el
# país no basta. Estas señales solo aparecen en un contrato laboral estadounidense:
# planes 401(k), seguros médicos privados, visados o exigencia de permiso de trabajo
# en EE. UU. Nótese que el salario en dólares NO sirve: hay empresas globales que
# pagan en USD y contratan desde cualquier país.
CONTRATO_NO_UE_POR_DEFECTO = (
    r"401\s?\(?k\)?"
    r"|\bH1[- ]?B\b|green card|\bEAD\b"
    r"|(?:us|u\.s\.) (?:citizens?|work authorization|person)"
    r"|authoriz(?:ed|ation) to work in the (?:us|u\.s\.|united states)"
    r"|must (?:be|reside) (?:located |based )?in the (?:us|u\.s\.|united states)"
    r"|\bFSA\b|\bHSA\b|medical, dental,? (?:and |& )?vision"
    # "PTO" y "paid time off" quedan fuera a propósito: son beneficios que también
    # ofrecen empresas europeas y marcaban ofertas de Barcelona como estadounidenses.
)

# Muchas ofertas sin ubicación dicen el país en el propio título:
# "Entry-Level IT Helpdesk Technician (Remote - United States)".
PAIS_NO_UE_EN_TITULO = (
    r"\bunited states\b|\bu\.?s\.?a\b|\bus[- ]only\b|\bus[- ]based\b|\(remote\s*[-–]\s*us\b"
    r"|\bunited kingdom\b|\bu\.?k\.?[- ]only\b|\bcanada\b|\bindia\b|\baustralia\b"
    r"|\bbrazil\b|\bbrasil\b|\bmexico\b|\bm[ée]xico\b|\bsingapore\b|\bjapan\b"
    r"|\bphilippines\b|\bargentina\b|\bcolombia\b|\bchile\b|\bper[uú]\b"
    r"|\bswitzerland\b|\bsuiza\b|\bturkey\b|\bt[uü]rkiye\b|\bisrael\b|\bukraine\b"
    r"|\begypt\b|\bmorocco\b|\bsouth africa\b|\bnigeria\b|\bkenya\b"
)

# Marcas de que el puesto se puede hacer a distancia.
#
# «híbrido» NO está aquí, y es deliberado (corregido el 2026-10-25). Lo estaba, y por
# eso `filtrar_presenciales_lejos` daba por remota una oferta cuyo título decía
# «Operador de Sistemas Junior -Turnos 24X7 (Hibrido) Madrid»: la conservaba y llegaba
# a Telegram con 8,0. Un híbrido obliga a ir a la oficina, así que fuera del área de
# Barcelona no sirve. Dentro del área sí vale, y ahí no se aplica este filtro.
# Medido sobre toda la base: 161 ofertas de fuera del área se salvaban por el título.
REMOTO = re.compile(
    r"\bremote\b|remoto|teletrabajo|home ?office|work from home|\bwfh\b"
    r"|desde casa|en remoto",
    re.IGNORECASE,
)

# Híbrido y semipresencial: ni remoto ni (del todo) presencial, pero atan a una oficina.
HIBRIDO = re.compile(r"h[ií]brid\w*|hybrid|semipresencial", re.IGNORECASE)


def parchear_ubicacion_linkedin() -> bool:
    """
    Arregla la extracción de ubicación de LinkedIn en jobspy.

    Su `_get_location` solo interpreta cadenas de 2 o 3 partes separadas por comas
    ("Barcelona, Catalonia" o "Barcelona, Catalonia, Spain"). Cuando LinkedIn devuelve
    una sola parte —muy común: "Greater Barcelona Metropolitan Area", "Spain",
    "United States"— o cuatro o más, cae al valor por defecto «worldwide», que se
    muestra vacío. Resultado: 4 de cada 10 ofertas de LinkedIn llegaban sin ubicación,
    y con ellas se perdían tanto las de Barcelona como las de EE. UU.

    Se parchea en vez de post-procesar porque la cadena original se pierde dentro de
    jobspy: recuperarla después obligaría a volver a descargar cada oferta.
    """
    try:
        from jobspy.linkedin import LinkedIn
        from jobspy.model import Country, Location
    except ImportError:
        return False

    def _get_location(self, metadata_card):
        por_defecto = Location(country=Country.from_string(self.country))
        if metadata_card is None:
            return por_defecto
        etiqueta = metadata_card.find("span", class_="job-search-card__location")
        if etiqueta is None:
            return por_defecto
        cadena = etiqueta.text.strip()
        if not cadena or cadena == "N/A":
            return por_defecto

        def a_pais(texto):
            """Country si se reconoce; si no, el texto tal cual (Location lo admite)."""
            try:
                return Country.from_string(texto)
            except (ValueError, Exception):
                return texto

        partes = [p.strip() for p in cadena.split(",") if p.strip()]
        if len(partes) == 1:
            unica = partes[0]
            try:
                # "Spain", "United States"… son el país entero
                return Location(country=Country.from_string(unica))
            except (ValueError, Exception):
                # "Greater Barcelona Metropolitan Area": es una zona, no un país
                return Location(city=unica)
        if len(partes) == 2:
            return Location(city=partes[0], state=partes[1],
                            country=Country.from_string(self.country))
        # Tres o más: la última suele ser el país y las dos primeras ciudad y región
        return Location(city=partes[0], state=partes[1], country=a_pais(partes[-1]))

    LinkedIn._get_location = _get_location
    return True


# Se aplica al importar el módulo, antes de cualquier búsqueda
PARCHE_UBICACION_APLICADO = parchear_ubicacion_linkedin()


def _texto(valor) -> str:
    """
    Convierte a texto un campo que puede venir vacío.

    En un DataFrame los huecos son NaN, que es un float **y además es truthy**: por eso
    `valor or ""` devuelve el NaN en vez de la cadena vacía y luego revienta al llamar
    a .strip(). Hay que comprobar el tipo, no la veracidad.
    """
    if valor is None or not isinstance(valor, str):
        return "" if valor is None or pd.isna(valor) else str(valor)
    return valor


def _compilar(patron: str):
    try:
        return re.compile(patron, re.IGNORECASE) if patron else None
    except re.error:
        return None  # patrón inválido escrito desde la web: se ignora en vez de romper


def _id_linkedin(url) -> str | None:
    m = re.search(r"/jobs/view/(\d+)", _texto(url))
    return m.group(1) if m else None


# ----------------------------------------------------------------------
# COMPETENCIA: cuánta gente ha aplicado ya, y desde cuándo está publicada
#
# La página de oferta de LinkedIn trae los dos datos en su cabecera, y el pipeline ya
# la descarga entera para sacar la descripción (ver completar_descripciones). Hasta
# agosto de 2026 se leían de ella seis campos y se tiraba el resto, incluidos estos.
#
# Por qué importan, y por qué no son "una señal más de texto": las tres rondas de
# investigación anteriores midieron que la descripción no aporta nada que el título no
# diga ya, y que con unas decenas de candidaturas no hay con qué ajustar un modelo. Estos dos campos
# no son texto de la oferta: son un hecho del mercado, y no se pueden deducir de nada
# que el sistema tenga. Medido sobre 190 páginas reales: correlación de Spearman con la
# relevancia de las reglas +0,058 y con la edad de la publicación +0,060 — o sea,
# información nueva de verdad, no una recodificación de lo que ya se sabe.
#
# Se extraen con expresiones regulares sobre el HTML crudo y NO con BeautifulSoup: la
# página ya la parsea jobspy una vez, y volver a parsearla entera para dos campos
# costaría otro tanto en el Atom del Toshiba. Comprobado contra el parseo con soup sobre
# 58 páginas reales: 58/58 idénticos en los dos campos y en las tres maquetas distintas
# que sirve LinkedIn.
# ----------------------------------------------------------------------

# Las dos clases se buscan con str.find y no con una expresión regular: la página son
# unos 300 KB y en el Atom del Toshiba un re.search sobre todo ese texto cuesta 8,5 ms
# por campo, mientras que str.find va en C. Si la clase no aparece tal cual (LinkedIn
# cambiando mayúsculas, por ejemplo) se reintenta con el regex, que sí es insensible.
_MARCA_CANDIDATOS = "num-applicants__"
_MARCA_PUBLICADA = "posted-time-ago__text"
_RE_MARCA_CANDIDATOS = re.compile(_MARCA_CANDIDATOS, re.IGNORECASE)
_RE_MARCA_PUBLICADA = re.compile(_MARCA_PUBLICADA, re.IGNORECASE)
_RE_ETIQUETAS = re.compile(r"<[^>]*>")


def _trozo_tras(html: str, marca: str, patron, largo: int) -> str:
    """
    El texto que sigue a la etiqueta que lleva `marca` en su clase, sin marcado.

    Se salta hasta el ">" que cierra esa etiqueta antes de recortar: si no, la ventana se
    la comerían el resto de clases del atributo y, en el caso de la antigüedad, la treintena
    de líneas en blanco que LinkedIn mete entre la etiqueta y el texto.
    """
    i = html.find(marca)
    if i < 0:
        m = patron.search(html)
        if m is None:
            return ""
        i = m.start()
    cierre = html.find(">", i)
    if cierre < 0:
        return ""
    return _sin_etiquetas(html[cierre + 1: cierre + 1 + largo])
# LinkedIn sirve la página en el idioma que negocie la sesión: la de jobspy la recibe en
# inglés y un navegador español, en castellano. Los dos tienen que valer.
_RE_MAS_DE = re.compile(r"(?:over|m[aá]s de)\s*([\d.,]+)", re.IGNORECASE)
_RE_PRIMEROS = re.compile(r"(?:first|primeros)\s*([\d.,]+)", re.IGNORECASE)
_RE_CUANTOS = re.compile(r"([\d.,]+)\s*(?:applicant|solicitud|candidat)", re.IGNORECASE)
_RE_ANTIGUEDAD = re.compile(r"(\d+)\s+([a-zá-úñ]+)", re.IGNORECASE)
_HORAS_POR_UNIDAD = {
    "minute": 1 / 60, "minutes": 1 / 60, "minuto": 1 / 60, "minutos": 1 / 60,
    "hour": 1, "hours": 1, "hora": 1, "horas": 1,
    "day": 24, "days": 24, "dia": 24, "dias": 24, "día": 24, "días": 24,
    "week": 168, "weeks": 168, "semana": 168, "semanas": 168,
    "month": 720, "months": 720, "mes": 720, "meses": 720,
}


def _sin_etiquetas(html: str) -> str:
    return re.sub(r"\s+", " ", _RE_ETIQUETAS.sub(" ", html)).strip()


def _numero(texto: str) -> int:
    return int(re.sub(r"[.,]", "", texto))


def extraer_candidatos(html: str) -> tuple[int | None, bool]:
    """
    (cuántos han aplicado, si LinkedIn dejó de contar) leídos de la página de la oferta.

    El segundo valor marca el tope: con "Over 200 applicants" el número real es >= 200 y
    LinkedIn ya no lo precisa. Al revés, "Be among the first 25 applicants" devuelve
    (25, False) y significa <= 25: por debajo de ese umbral tampoco da el número exacto.
    Así que el campo es una banda, no una medida fina — y aun así separa un 25 de un
    200+, que es la diferencia que importa.

    El número llega en dos maquetas distintas: un <span> suelto ("26 applicants") o un
    <figure> con un <figcaption> dentro. Por eso se lee el trozo que sigue a la clase y
    se le quitan las etiquetas, en vez de cortar en el siguiente "<".
    """
    texto = _trozo_tras(html or "", _MARCA_CANDIDATOS, _RE_MARCA_CANDIDATOS, 300)
    for patron, es_tope in ((_RE_MAS_DE, True), (_RE_PRIMEROS, False), (_RE_CUANTOS, False)):
        mm = patron.search(texto)
        if mm:
            try:
                return _numero(mm.group(1)), es_tope
            except ValueError:
                return None, False
    return None, False


def extraer_horas_publicada(html: str) -> float | None:
    """
    Horas que llevaba publicada la oferta según la propia página.

    OJO con la precisión: LinkedIn engorda la unidad según envejece el anuncio, así que
    "1 week ago" cubre de 7 a 13 días. Solo es fino en horas y días — que es justo el
    rango en el que este pipeline lee la página, porque la ventana de búsqueda es de
    38 h. Comprobado contra las ofertas que sí traen date_posted: con texto fino
    coincide en 32 de 33 (97 %); con texto grueso, en 1 de 14.
    """
    mm = _RE_ANTIGUEDAD.search(
        _trozo_tras(html or "", _MARCA_PUBLICADA, _RE_MARCA_PUBLICADA, 400)
    )
    if not mm:
        return None
    horas = _HORAS_POR_UNIDAD.get(mm.group(2).lower())
    return int(mm.group(1)) * horas if horas is not None else None


def _recordar_html(scraper) -> None:
    """
    Hace que el scraper de LinkedIn guarde el HTML de la última página que pidió.

    Es la forma de leer estos dos campos SIN una petición extra: jobspy descarga la
    página, se queda con seis campos y tira la respuesta. Se envuelve session.get en vez
    de reescribir _get_job_details entero (que es lo que hace parchear_ubicacion_linkedin)
    porque aquí no hay que cambiar lo que jobspy hace, solo no perder lo que ya trajo:
    así un cambio suyo en el parseo no rompe nada de esto.

    Cada hilo tiene su propia instancia (ver scraper_del_hilo), así que el atributo no se
    comparte entre hilos.
    """
    get_original = scraper.session.get

    def get(*args, **kwargs):
        respuesta = get_original(*args, **kwargs)
        try:
            scraper._html_ultima = respuesta.text
        except Exception:
            scraper._html_ultima = None
        return respuesta

    scraper.session.get = get


def completar_descripciones(
    df: pd.DataFrame,
    config: dict,
    progreso: Callable | None = None,
    conocidas: dict[str, str] | None = None,
    competencia_conocida: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """
    Descarga la descripción (y con ella nivel de experiencia, tipo de contrato e
    industria) solo de las ofertas de LinkedIn que han sobrevivido a los filtros.

    Pedírselas a jobspy durante la búsqueda gasta una petición por cada resultado de
    cada búsqueda: con varios términos la misma oferta se descarga muchas veces y
    además se pagan las que luego se descartan por duplicadas, senior o fuera de
    perfil. Haciéndolo al final se descarga cada oferta una sola vez.
    """
    if df.empty:
        return df

    try:
        from jobspy.linkedin import LinkedIn
        from jobspy.model import DescriptionFormat, ScraperInput, Site
    except ImportError:
        return df

    df = df.copy()

    # Las que ya teníamos guardadas de una búsqueda anterior se reutilizan: en las
    # actualizaciones la mayoría de ofertas se repiten, y volver a descargarlas
    # costaría ~3,5 s cada una para obtener exactamente lo mismo.
    conocidas = conocidas or {}
    competencia_conocida = competencia_conocida or {}
    # Las columnas se crean antes de tocarlas: df.at[i, "columna"] no vale para estrenar
    # una columna que no existe. Van con dtype object para poder mezclar número y None
    # sin que pandas los convierta a NaN (ver el comentario de etiquetas.enriquecer).
    for columna in ("candidatos", "candidatos_tope", "publicada_en"):
        if columna not in df.columns:
            df[columna] = pd.Series([None] * len(df), index=df.index, dtype=object)

    reutilizadas = 0
    if conocidas:
        for i, fila in df.iterrows():
            # _texto y no `or`: un hueco de pandas es NaN, que es truthy y haría
            # creer que la oferta ya tiene descripción cuando en realidad está vacía.
            if not _texto(fila.get("description")):
                previa = conocidas.get(fila.get("job_url"))
                if previa:
                    df.at[i, "description"] = previa
                    reutilizadas += 1
                    # Reutilizar la descripción significa NO pedir la página, así que la
                    # competencia hay que sacarla también del caché o se pierde: una
                    # oferta puede estar ahí y entrar en la base más tarde (la misma URL
                    # la devuelven ámbitos distintos y los filtros no deciden igual en
                    # todos). Ver almacen.cargar_competencia_vista.
                    for campo, valor in (competencia_conocida.get(fila.get("job_url")) or {}).items():
                        if valor is not None:
                            df.at[i, campo] = valor

    pendientes = [
        (i, _id_linkedin(fila.get("job_url")))
        for i, fila in df.iterrows()
        if fila.get("site") == "linkedin" and not _texto(fila.get("description"))
    ]
    pendientes = [(i, jid) for i, jid in pendientes if jid]
    if progreso and reutilizadas:
        progreso(
            f"{reutilizadas} descripciones reutilizadas, faltan {len(pendientes)}",
            0, max(len(pendientes), 1),
        )
    if not pendientes:
        return df

    # Una instancia (y por tanto una Session) por hilo: LinkedIn crea la sesión con
    # borrado de cookies en cada petición, así que compartirla entre hilos provoca
    # reintentos con espera y la descarga se vuelve diez veces más lenta.
    local = threading.local()

    def scraper_del_hilo():
        if not hasattr(local, "scraper"):
            s = LinkedIn()
            s.scraper_input = ScraperInput(
                site_type=[Site.LINKEDIN], description_format=DescriptionFormat.MARKDOWN
            )
            _recordar_html(s)
            local.scraper = s
        return local.scraper

    total = len(pendientes)
    hechas = 0
    lock_desc = Lock()
    ahora = datetime.now()

    def traer(par):
        i, jid = par
        scraper = scraper_del_hilo()
        scraper._html_ultima = None
        try:
            detalles = scraper._get_job_details(jid)
        except Exception:
            return i, {}, None
        # El HTML es el de la MISMA petición que acaba de hacer jobspy, no una segunda
        return i, detalles, getattr(scraper, "_html_ultima", None)

    def volcar(i, detalles, html) -> bool:
        """Escribe los detalles en la fila; devuelve si traía descripción."""
        if not detalles or not detalles.get("description"):
            return False
        df.at[i, "description"] = detalles["description"]
        if detalles.get("job_level"):
            df.at[i, "job_level"] = detalles["job_level"].lower()
        if detalles.get("company_industry"):
            df.at[i, "company_industry"] = detalles["company_industry"]
        tipos = detalles.get("job_type")
        if tipos:
            df.at[i, "job_type"] = (
                ", ".join(t.value[0] for t in tipos if t is not None) or None
            )
        # Competencia y antigüedad real, de la misma respuesta (ver extraer_candidatos).
        # Un fallo aquí no puede costar la descripción, que es lo que se venía a buscar.
        try:
            candidatos, tope = extraer_candidatos(html or "")
            if candidatos is not None:
                df.at[i, "candidatos"] = candidatos
                df.at[i, "candidatos_tope"] = tope
            horas = extraer_horas_publicada(html or "")
            if horas is not None:
                df.at[i, "publicada_en"] = (
                    ahora - timedelta(hours=horas)
                ).isoformat(timespec="seconds")
        except Exception:
            logger.debug("No se han podido leer competencia/antigüedad de %s", i)
        return True

    def pasada(items, obreros, contar=True):
        fallidas = []
        nonlocal hechas
        # Aquí sí compensa el paralelismo: son peticiones sueltas a páginas de oferta,
        # sin la espera de 3-7 s que LinkedIn impone entre páginas de resultados.
        with ThreadPoolExecutor(max_workers=max(1, obreros)) as ex:
            for (i, detalles, html), par in zip(ex.map(traer, items), items):
                if not volcar(i, detalles, html):
                    fallidas.append(par)
                if not contar:
                    continue
                with lock_desc:
                    hechas += 1
                    # La barra pasa a medir esta segunda fase, que es la parte larga
                    if progreso and (hechas % 10 == 0 or hechas == total):
                        progreso(f"Descargando descripciones ({hechas}/{total})", hechas, total)
        return fallidas

    obreros = int(config.get("descripciones_en_paralelo", 4))
    fallidas = pasada(pendientes, obreros)

    # LinkedIn corta algunas peticiones cuando van muy seguidas. Se reintentan una vez
    # con la mitad de hilos: son pocas y recuperan buena parte de las descripciones.
    if fallidas and obreros > 1:
        if progreso:
            progreso(f"Reintentando {len(fallidas)} descripciones...", hechas, total)
        time.sleep(3)
        pasada(fallidas, max(1, obreros // 2), contar=False)

    return df


@functools.lru_cache(maxsize=512)
def _idiomas_de(texto: str) -> tuple[tuple[str, float], ...]:
    """
    Los candidatos de langdetect para un texto, una vez por texto (MP3).

    Es lo más caro por oferta (60,43 ms en el Atom, ver evaluar_caro) y con varios perfiles se
    repite: medida una corrida simulada sobre la copia del 01-11, la mitad de lo que añade el
    segundo perfil era volver a detectar los mismos textos. Y las mismas ofertas vuelven cada hora.
    Con la semilla fija la detección es determinista, así que guardar el resultado no cambia
    ninguna decisión. 512 textos de 2.000 caracteres son unos 2 MB como mucho.
    """
    from langdetect import DetectorFactory, detect_langs

    DetectorFactory.seed = 0  # la detección es aleatoria salvo que se fije la semilla
    return tuple((i.lang, i.prob) for i in detect_langs(texto))


def filtrar_por_idioma(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Descarta las ofertas escritas en un idioma que no lees.

    Solo juzga cuando hay descripción suficiente: con un título suelto la detección
    se equivoca a menudo (p.ej. "Técnico/a Service Desk / Soporte Usuario N1", que es
    español, se detecta como portugués). Ante la duda, se conserva la oferta.
    """
    if df.empty or not config.get("descartar_otros_idiomas", True):
        return df
    try:
        import langdetect  # noqa: F401
    except ImportError:
        return df  # sin la librería instalada, no se filtra por idioma

    permitidos = set(config.get("idiomas_permitidos", ["es", "ca", "en", "ru"]))
    min_texto = int(config.get("idioma_min_caracteres", 200))
    umbral = float(config.get("idioma_confianza_minima", 0.90))

    def se_conserva(fila) -> bool:
        desc = _texto(fila.get("description")).strip()
        if len(desc) < min_texto:
            return True
        try:
            idiomas = _idiomas_de(f"{_texto(fila.get('title'))}. {desc}"[:2000])
        except Exception:
            return True
        # Basta con que alguno de los candidatos plausibles sea un idioma que lees
        if {idioma for idioma, prob in idiomas if prob >= 0.05} & permitidos:
            return True
        return idiomas[0][1] < umbral

    return df[df.apply(se_conserva, axis=1)]


# Frases que de verdad indican que el puesto se hace a distancia. Buscar solo la
# palabra "remote" en toda la descripción daba falsos positivos constantes: casi
# cualquier oferta la menciona de pasada ("we support remote teams", "hybrid/remote
# options"), y colaba presenciales de otras ciudades como remotas.
# Declaración de modalidad en la descripción, no una mención de pasada. La diferencia
# importa: con el patrón antiguo (que valía con un «teletrabajo» suelto en cualquier
# parte) se colaban como remotas frases como «resolver presencialmente y en remoto los
# tickets» (describe la tarea), «trabajando en remoto (aunque se requiere vivir...)» (dice
# lo contrario) o «Modalidad: Híbrida (40-60% teletrabajo)» (es híbrida).
REMOTO_EXPLICITO = re.compile(
    r"(?:100\s*%|fully|full|completamente|totalmente)\s*[- ]?(?:en\s*)?(?:remot[eo]|teletrabajo)"
    r"|\bremote[- ]first\b|\bfull[- ]remote\b|\bremote position\b|\bremote role\b|\bremote job\b"
    r"|\bremot[oa]\s*(?:total|completo)\b|\bteletrabajo\s*(?:total|completo|100)"
    r"|\bwork from home\b|\bwork from anywhere\b|\bwfh\b|\bhome ?office\b",
    re.IGNORECASE,
)
# Contradicen lo anterior: si el puesto ata a una oficina concreta, no es remoto.
# «híbrido» entra aquí por el mismo motivo por el que sale de REMOTO.
PRESENCIAL_EXPLICITO = re.compile(
    r"\b(?:\d+\s*(?:days?|d[ií]as?)\s*(?:a week\s*)?(?:in|en)\s*(?:the\s*)?(?:office|oficina))"
    r"|\bon[- ]?site\b|\bpresencial\b|\bin[- ]office\b|\boffice[- ]based\b"
    r"|h[ií]brid\w*|hybrid|semipresencial",
    re.IGNORECASE,
)

# Campo estructurado de modalidad que traen bastantes descripciones: «Modalidad:
# Híbrido», «**Trabajo a distancia:** Híbrido», «Ubicación del trabajo: Empleo
# presencial». Medido sobre la base del 25-10-2026: lo traen 41 de las 307 ofertas que
# conservan descripción (27 LinkedIn, 11 Indeed, 3 InfoJobs), y en 7 casos dice lo
# CONTRARIO que la bandera is_remote del portal —entre ellas «Técnico/a de Sistemas
# Junior – Granada», que llegaba a Telegram con 7,5—. Cuando existe, se le cree a él.
# El [:*\s] del medio absorbe el ruido de markdown que deja la conversión a texto.
_CAMPO_MODALIDAD = re.compile(
    r"(?:modalidad|trabajo a distancia|ubicaci[oó]n del trabajo|tipo de trabajo"
    r"|work ?(?:place|site) ?type|remote work)[:*\s]{1,8}"
    r"(?P<valor>empleo presencial|presencial|h[ií]brid\w*|hybrid|semipresencial"
    r"|teletrabajo|remot[oa]|remote|on[- ]?site)",
    re.IGNORECASE,
)

# Remoto anclado en Barcelona: el portal ubica la oferta en otra ciudad, pero el texto
# dice que se teletrabaja desde aquí. Caso real: un «Técnico/a Service Desk (LT1)» que
# LinkedIn sitúa en Madrid y cuya descripción dice «100 % teletrabajo desde Barcelona».
REMOTO_DESDE_BARCELONA = re.compile(
    r"(?:remot[oa]|teletrabajo|deslocalizad\w*)[^.]{0,60}barcelona"
    r"|barcelona[^.]{0,40}(?:remot[oa]|teletrabajo)",
    re.IGNORECASE,
)


def modalidad(fila) -> str:
    """
    «Remoto», «Híbrido», «Presencial» o «No indicado».

    Única fuente de verdad sobre la modalidad: la usan el filtro geográfico
    (filtrar_presenciales_lejos) y la etiqueta que ve el usuario en la web y en Telegram
    (etiquetas.enriquecer). Antes eran dos criterios distintos —el filtro leía el texto,
    la etiqueta solo la bandera `is_remote` del portal— y se contradecían: había ofertas
    presenciales en Madrid etiquetadas «Remoto» porque el portal lo decía. Esa
    discrepancia hizo que durante la auditoría se etiquetaran mal 5 ofertas de la muestra.

    El orden de precedencia está medido (ver DESIGN.md §2.2); no es arbitrario:
    la descripción manda sobre la bandera del portal, porque la bandera falla en los dos
    sentidos (marca remotas las presenciales y presenciales las remotas).
    """
    titulo = _texto(fila.get("title"))
    ubicacion = _texto(fila.get("location"))
    descripcion = _texto(fila.get("description"))

    # 1. Campo estructurado: lo más fiable que hay cuando está.
    campo = _CAMPO_MODALIDAD.search(descripcion[:4000])
    if campo:
        valor = campo.group("valor").lower()
        if HIBRIDO.search(valor):
            return "Híbrido"
        if "presencial" in valor or "site" in valor:
            return "Presencial"
        return "Remoto"

    # 2. El título manda sobre el resto del texto.
    if HIBRIDO.search(titulo):
        return "Híbrido"
    if PRESENCIAL_EXPLICITO.search(titulo) or re.search(r"\boffice\b|oficina", titulo, re.I):
        return "Presencial"
    if REMOTO.search(titulo) or REMOTO.search(ubicacion):
        return "Remoto"

    # 3. Remoto desde Barcelona, aunque el portal la ubique en otra ciudad.
    recorte = descripcion[:1500]
    if REMOTO_DESDE_BARCELONA.search(recorte):
        return "Remoto"

    # 4. Una indicación clara de presencialidad o de híbrido en la descripción.
    pres = PRESENCIAL_EXPLICITO.search(recorte)
    if pres:
        return "Híbrido" if HIBRIDO.search(pres.group(0)) else "Presencial"

    # 5. La bandera del portal, solo si nada de lo anterior la contradice.
    if fila.get("is_remote") is True:
        return "Remoto"

    # 6. Declaración inequívoca de modalidad remota en la descripción.
    if REMOTO_EXPLICITO.search(recorte):
        return "Remoto"

    # 7. Sin ninguna señal en el texto, pero el portal dice que NO es remota: es lo único
    #    que se sabe, y es más de lo que dice «No indicado». Se devuelve la etiqueta
    #    ambigua de siempre, que es la afirmación honesta: no es remota, pero no se sabe
    #    si presencial o híbrida. (Corregido el 2026-10-25: al unificar la modalidad dejé
    #    estos casos en «No indicado», que informa menos que antes del cambio.)
    #    No afecta al filtro geográfico: ni esto ni «No indicado» son «Remoto».
    if fila.get("is_remote") is False:
        return "Presencial/Híbrido"

    return "No indicado"


def _es_remota(fila) -> bool:
    """¿Se puede hacer este puesto enteramente a distancia? Híbrido NO cuenta."""
    return modalidad(fila) == "Remoto"


def filtrar_fuera_de_ue(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Descarta las ofertas de fuera de la UE salvo que digan explícitamente que se puede
    trabajar desde otro país.

    Desde España, una oferta de fuera de la UE casi siempre exige residencia o permiso
    de trabajo allí, y comprobarlo una por una cuesta más tiempo del que vale: sobre
    178 ofertas de EE. UU. reales, ninguna resultó contratar desde Europa. Así que se
    invierte la carga de la prueba: fuera fuera de la UE, salvo verificación explícita.

    Cuando la oferta no trae ubicación —pasa en una de cada tres— se mira si el texto
    delata un contrato estadounidense (401k, seguro médico privado, visados).
    """
    if df.empty or not config.get("descartar_fuera_de_ue", True):
        return df
    senal = _compilar(config.get("patron_senal_global", SENAL_GLOBAL_POR_DEFECTO))
    contrato_fuera = _compilar(
        config.get("patron_contrato_no_ue", CONTRATO_NO_UE_POR_DEFECTO)
    )
    pais_en_titulo = _compilar(
        config.get("patron_pais_no_ue_titulo", PAIS_NO_UE_EN_TITULO)
    )
    permitidos = {p.strip().lower() for p in config.get("paises_permitidos", PAISES_UE) if p}
    if not permitidos:
        return df

    from etiquetas import extraer_pais  # import local: evita dependencia circular

    def se_conserva(fila) -> bool:
        titulo = _texto(fila.get("title"))
        texto = f"{titulo} {_texto(fila.get('description'))[:4000]}"
        verificada = bool(senal and senal.search(texto))

        # El título manda: si dice "(Remote - United States)" no hay más que discutir
        if pais_en_titulo and pais_en_titulo.search(titulo):
            return verificada

        pais = extraer_pais(_texto(fila.get("location")))
        if pais:
            # Fuera de la UE solo se conserva con una frase inequívoca sobre dónde
            # puede estar el candidato; sin ella se descarta sin más.
            return pais.lower() in permitidos or verificada

        # Sin país: se conserva salvo que el texto delate un contrato de fuera de la UE
        if contrato_fuera and contrato_fuera.search(texto):
            return verificada
        return True

    return df[df.apply(se_conserva, axis=1)]


# Municipios del área a la que se puede ir a diario (radio de unos 8 km de Plaça
# Catalunya) más las cadenas con las que los portales nombran la zona. «Greater
# Barcelona Metropolitan Area» entra a propósito aunque el área metropolitana llegue
# bastante más lejos: es lo único que dice LinkedIn en muchas ofertas de la propia
# ciudad, y descartarla costaría más de lo que ahorra.
#
# OJO CON LA PROVINCIA (corregido el 2026-10-25, lo rompí el día anterior). La primera
# versión incluía `\bct, es\b` para reconocer el formato de Indeed («Barcelona, CT, ES»),
# y eso daba por área de Barcelona CUALQUIER sitio de Cataluña: «Lleida, CT, ES» (160 km),
# «Tarragona, CT, ES» (100 km), «Mataró, CT, ES» (30 km). Por ahí entraron 14 ofertas, y
# un «Responsable Helpdesk» de Mataró llegó a cruzar el umbral de aviso con 5,0.
# Ahora se mira el MUNICIPIO (el primer trozo de la ubicación) contra la lista.
AREA_BARCELONA = re.compile(
    r"barcelona|\bbcn\b"
    r"|hospitalet|badalona|santa coloma|sant adri|esplugues|cornell[aà]"
    r"|sant just desvern|sant joan desp[ií]|el prat|prat de llobregat|montgat",
    re.IGNORECASE,
)

# Ubicaciones en las que NO SE PUEDE SABER si el sitio está en el área: o nombran solo
# Cataluña («CT, ES»), donde el municipio podría ser Barcelona o Lleida, o nombran solo
# el país («ES», «Spain»). Se tratan igual que una ubicación vacía: se conservan.
#
# Aquí NO vale un comodín de dos letras. La primera versión llevaba `\w{2}`, y entonces
# «PV, ES» (País Vasco) o «MD, ES» (Madrid) pasaban por «no se sabe dónde es» y se
# conservaban: justo al revés de lo que toca, porque esos códigos sí dicen la región y la
# región no es Barcelona. Lo cazó pruebas/test_modalidad.py con un caso real de Indeed.
_SOLO_REGION = re.compile(
    r"^\s*(?:ct|cat|catalu[nñ]a|catalunya|catalonia)\s*(?:,\s*(?:es|esp|spain|espa[nñ]a)\s*)?$"
    r"|^\s*(?:es|esp|spain|espa[nñ]a)\s*$",
    re.IGNORECASE,
)


# Áreas escritas a mano, por nombre: es lo que una config efectiva puede pedir con
# {"modo": "regex", "nombre": ...}. La config son datos y una regex compilada no lo es.
AREAS_REGEX = {"barcelona": AREA_BARCELONA}


def esta_en_el_area(ubicacion, area=None, geo=None) -> bool | None:
    """
    ¿Está esta ubicación en el área del perfil?

    True = sí; False = no; None = no se puede saber (ubicación vacía, o que solo nombra
    la provincia o el país). Quien llama decide qué hacer con el None; el filtro
    geográfico conserva la oferta, que es lo que se ha hecho siempre.

    Con `area=None` o en modo regex es el área de Barcelona. Se mira el municipio además
    del texto completo: los portales escriben «Mataró, CT, ES», y buscar la provincia en la
    cadena entera da por bueno todo Cataluña.

    En modo distancia, `area` lleva `lat`, `lon` y `radio_km`, y se compara la distancia al
    municipio. Ver `Geocodificador`, que es quien decide qué se pregunta y cuánto. Un
    `radio_km` de 0 es «solo remoto»: todo lo que se sabe dónde es queda fuera, sin gastar
    ninguna consulta.
    """
    texto = _texto(ubicacion).strip()
    if not texto or _SOLO_REGION.match(texto):
        return None
    if area is not None and area.get("modo") == "distancia":
        radio = float(area.get("radio_km") or 0)
        if radio <= 0:
            return False
        coords = (geo or Geocodificador()).ubicacion(texto)
        if coords is None:
            return None
        return geodesic((area["lat"], area["lon"]), coords).km <= radio
    patron = AREA_BARCELONA if area is None else AREAS_REGEX[area.get("nombre", "barcelona")]
    municipio = texto.split(",")[0].strip()
    if patron.search(municipio):
        return True
    # «Greater Barcelona Metropolitan Area» y variantes llevan el nombre fuera del primer
    # trozo en algunos formatos, así que se mira también la cadena completa; lo que no
    # vale es que la coincidencia venga solo de la provincia.
    return bool(patron.search(texto))


def filtrar_presenciales_lejos(df: pd.DataFrame, config: dict, geo=None) -> pd.DataFrame:
    """
    Fuera del área de Barcelona solo tienen sentido las ofertas en remoto de verdad: a
    una presencial o híbrida en Madrid o Dublín no puedes ir.

    Las ofertas sin ubicación se conservan: no se puede saber dónde son, y descartarlas
    perdería remotas legítimas (los portales dejan ese campo vacío a menudo).

    Qué cambió el 2026-10-25 (decisión explícita del usuario, ver DESIGN.md §2.2):

    - Un HÍBRIDO fuera del área ya no se salva. Antes «híbrido» estaba dentro del patrón
      REMOTO, así que «Operador de Sistemas Junior -Turnos 24X7 (Hibrido) Madrid» pasaba
      el filtro y llegaba a Telegram con 8,0.
    - La pertenencia al área se decide por la UBICACIÓN, no por el ámbito de la búsqueda.
      Antes bastaba con que la oferta viniera del ámbito "barcelona_local" para
      conservarla, y eso daba por bueno que el portal hubiera respetado el `location` de
      la consulta, que no lo hace: por ahí entraban ofertas de Madrid buscando en
      Barcelona.

    Medido: sube la precisión del 63 % al 71 % sin perder recall, y descarta 453 de las
    2.614 ofertas del histórico (17 %). Tiene un coste asumido: alrededor del 10 % de las
    ofertas marcadas como interesantes eran presenciales fuera del área y habrían quedado
    fuera. Para volver atrás, `exigir_remoto_fuera_de_barcelona: false`.

    Desde MP2 «el área» es la del perfil (`config["area"]`); sin esa clave, Barcelona.
    """
    if df.empty or not config.get("exigir_remoto_fuera_de_barcelona", True):
        return df
    area = config.get("area")
    if geo is None and area is not None and area.get("modo") == "distancia":
        geo = Geocodificador(tope=config.get("geocodificaciones_por_corrida"))

    def se_conserva(fila) -> bool:
        en_el_area = esta_en_el_area(fila.get("location"), area, geo)
        if en_el_area is not False:
            # True (es del área) o None (no se sabe dónde es): se conserva.
            return True
        return _es_remota(fila)

    conservadas = df[df.apply(se_conserva, axis=1)]
    if geo is not None:
        geo.guardar()
    return conservadas


def reglas_activas(config: dict) -> list[dict]:
    """Reglas de descarte de la configuración, cayendo a las de fábrica si no hay."""
    reglas = config.get("reglas_descarte")
    # Una lista vacía cae a las de fábrica, salvo que la config diga que no: son reglas de IT en
    # Barcelona, y a un perfil de catálogo sin descartes le tumbarían su sector entero.
    if not isinstance(reglas, list) or (not reglas and config.get("reglas_de_fabrica", True)):
        reglas = REGLAS_POR_DEFECTO
    return [r for r in reglas if isinstance(r, dict) and r.get("activa", True)]


# ----------------------------------------------------------------------
# EMPRESAS EXCLUIDAS (2026-10-25, a petición del usuario)
#
# Empresas cuyas ofertas se descartan SIEMPRE, sea cual sea el título. Caso de uso:
# anuncios que se presentan como puestos de ciberseguridad pero son formación («Programa
# Talento Joven», cursos de pago), que ninguna regla por título puede separar de un puesto
# real porque el título es exactamente el de un puesto real: «Analista de Ciberseguridad
# Junior». Lo que las delata es quién las publica.
#
# Se compara el NOMBRE NORMALIZADO (minúsculas, sin acentos ni signos) por PALABRAS
# COMPLETAS, no por subcadena: «evolve» no debe casar con «Revolve Labs». Por eso las
# variantes de escritura de una misma empresa («Tech Nova», «TechNova») van como entradas
# separadas en la lista.
#
# Coste conocido: no distingue entre sus ofertas buenas y malas. Al añadir TECH NOVA se
# descarta también «Analista SOC Junior», a la que el usuario llegó a aplicar el 4-08.
# Ver `empresas_excluidas` en config.py y DESIGN.md.
# ----------------------------------------------------------------------
def _normalizar_empresa(valor) -> str:
    texto = unicodedata.normalize("NFKD", _texto(valor).lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def _patron_empresas(empresas) -> "re.Pattern | None":
    nombres = [_normalizar_empresa(e) for e in (empresas or []) if _normalizar_empresa(e)]
    if not nombres:
        return None
    # Los espacios internos casan con uno o más espacios del nombre normalizado.
    alternativas = [r"\s+".join(map(re.escape, n.split())) for n in nombres]
    return re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(alternativas))


def filtrar_empresas_excluidas(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Quita las ofertas de las empresas de `empresas_excluidas` en la config."""
    if df.empty or "company" not in df.columns:
        return df
    patron = _patron_empresas(config.get("empresas_excluidas"))
    if patron is None:
        return df
    excluir = df["company"].apply(lambda c: bool(patron.search(_normalizar_empresa(c))))
    if excluir.any():
        logger.info(
            "Empresas excluidas: se descartan %s oferta(s) de %s.",
            int(excluir.sum()),
            ", ".join(sorted({str(c) for c in df.loc[excluir, "company"]})),
        )
    return df[~excluir]


def marcar_ruido(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Marca (no borra) las ofertas que no encajan con el perfil, anotando en
    `motivo_descarte` cuál de las reglas la ha señalado. Guardar el motivo permite
    revisar después si una regla está descartando de más.
    """
    df = df.copy()
    if df.empty:
        df["fuera_de_perfil"] = False
        df["motivo_descarte"] = None
        return df

    titulos = df["title"].fillna("")
    fuera = pd.Series(False, index=df.index)
    motivo = pd.Series(None, index=df.index, dtype=object)

    for regla in reglas_activas(config):
        patron = _compilar(regla.get("patron", ""))
        if patron is None:
            continue
        coincide = titulos.str.contains(patron)
        excepcion = _compilar(regla.get("excepcion") or "")
        if excepcion is not None:
            coincide &= ~titulos.str.contains(excepcion)
        # Solo se anota la primera regla que la descarta, para no pisar el motivo
        nuevas = coincide & ~fuera
        motivo[nuevas] = regla.get("nombre", "sin nombre")
        fuera |= coincide

    df["fuera_de_perfil"] = fuera
    df["motivo_descarte"] = motivo
    return df


# ----------------------------------------------------------------------
# RELEVANCIA
#
# Puntúa cada oferta según lo que el historial dice que interesa. No sirve para
# descartar (alguna oferta buena puntúa bajo), sino para ORDENAR: validado contra el
# histórico de la instalación original, el 20 % mejor puntuado contiene el 83 % de las
# ofertas a las que se acabó aplicando (AUC 0,88).
#
# Hay dos sistemas y se elige con "usar_modelo_entrenado" en config.json:
#
#   false (por defecto)  estos pesos escritos a mano.
#   true                 el modelo de modelo_relevancia.json, entrenado sobre el mismo
#                        historial con entrenar_modelo.py.
#
# Por defecto van las reglas porque, medidos con la misma validación cruzada sobre los
# mismos datos, el modelo entrenado NO las supera (ver README, «Orden por relevancia»).
# El interruptor existe para poder repetir la comparación cuando el historial crezca:
# el modelo mejora con cada oferta marcada, las reglas no.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# EXPERIENCIA EXIGIDA (2026-10-24)
#
# El título dice «SOC Analyst N1» o «Técnico/a Helpdesk», pero es la descripción la que
# dice si piden 1 año o 5. Se extrae de ahí el MÍNIMO de años que exige la oferta y se
# usa para dos cosas: mostrarlo (tarjeta y Telegram) y ajustar la relevancia cuando pide
# más de lo que se tiene ("anos_experiencia_max" en config.json, 3 por defecto).
#
# Por qué el mínimo y no el máximo: una oferta suele listar varios requisitos («2 años
# en Windows; valorable 5 en redes») y el que decide si puedes optar es el más bajo.
# Los valores de más de 15 se ignoran: casi siempre son la antigüedad de la empresa
# («más de 30 años de experiencia en el sector»), no un requisito.
#
# Medido sobre la base del 2026-10-24 (2.709 ofertas con descripción): da un número en el
# 30 %. Entre las ofertas marcadas como interesantes, muy pocas piden más de 3 años
# (1,5 %); entre las descartadas, el 7,6 %. Revisadas a mano 25 coincidencias al azar: todas
# correctas una vez admitidos los rangos con barra («3 \- 5 años», escape de Markdown que
# deja la conversión de LinkedIn).
#
# El mismo escape en el «+»: «3\+ years of experience» no se leía, y la oferta parecía no pedir
# nada. Medido el 02-11 sobre 2.817 ofertas con texto: 153 cambian, casi todas de no tener dato a
# tenerlo (955 -> 1.105), y todas de LinkedIn e Indeed. Jooble no lo trae: no le cierra el hueco
# del tope de años (D8). Sigue sin leerse «3+ years in» sin la palabra «experience» (28 ofertas) y
# «3 years’ experience» (17): lo primero, a propósito, porque «30 years in» suele ser la empresa.
# ----------------------------------------------------------------------
_EXP_NUM = r"(\d{1,2})(?:[.,]5)?"
_EXP_RANGO = _EXP_NUM + r"(?:\s*\\?(?:-|–|a|to|o|or|/|y|and)\s*\d{1,2})?\s*\\?\+?"
_EXP_ANOS = r"(?:años?|anos?|anys?|years?|yrs?)"  # también en singular: «1 año de experiencia»
_EXP_PALABRA = r"(?:experiencia|experiència|experience|exp\.?)"
_EXP_PATRONES = [
    # «3 años de experiencia», «2-3 years of experience», «5+ years experience»,
    # «3 anys d'experiència», «2 años de experiencia profesional»
    re.compile(r"(?<![\d.,])" + _EXP_RANGO + r"\s*" + _EXP_ANOS
               + r"\s+(?:de\s+|of\s+)?(?:\w+\s+){0,3}?(?:de\s+|d['’]\s*|of\s+)?" + _EXP_PALABRA, re.I),
    # «experiencia mínima de 2 años», «experience of at least 3 years»
    re.compile(_EXP_PALABRA + r"\s+(?:\w+\s+){0,4}?(?:de\s+|of\s+)?(?:al menos\s+|m[ií]nimo\s+|at least\s+"
               r"|minimum\s+|más de\s+|over\s+)?" + _EXP_RANGO + r"\s*" + _EXP_ANOS, re.I),
    # «mínimo 3 años», «al menos 2 años en», «at least 3 years in», «minimum of 4 years»
    re.compile(r"(?:al menos|m[ií]nimo(?: de)?|at least|minimum(?: of)?|más de|over)\s+"
               + _EXP_RANGO + r"\s*" + _EXP_ANOS, re.I),
]
_EXP_NINGUNA = re.compile(
    r"sin experiencia|no se requiere experiencia|no (?:es )?necesaria experiencia|sense experiència"
    r"|no (?:prior )?experience (?:is )?(?:required|needed)|no prior experience", re.I)
ANOS_EXPERIENCIA_MAX_POR_DEFECTO = 3

# «Al menos tres años de experiencia» (InfoJobs, «Técnico/a it», descartada por el usuario
# el 27-10 con «pide experiencia») no daba ningún número: los patrones solo leen cifras.
# Se cambia la palabra por la cifra antes de buscar, y solo delante de «años/years».
_NUMEROS_EN_LETRA = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "dues": 2,  # catalán, «dues anys»
}
_PALABRA_NUMERO = r"(" + "|".join(_NUMEROS_EN_LETRA) + r")"
# Con el rango entero («two to four years» -> «2 to 4 years»): si solo se cambiara la
# palabra pegada a «years», saldría 4 en vez del mínimo, 2.
_RE_NUMERO_EN_LETRA = re.compile(
    r"\b" + _PALABRA_NUMERO + r"(?:(\s*(?:-|–|to|a|o|or|y|and)\s*)(" + "|".join(_NUMEROS_EN_LETRA)
    + r"|\d{1,2}))?(?=\s+(?:años?|anos?|anys?|years?)\b)", re.I)
# «No necesitas tener diez años de experiencia» (una oferta JUNIOR de la base) no es un
# requisito: tras una negación cercana la palabra se deja como está y no se lee.
_RE_NEGACION_PREVIA = re.compile(
    r"\b(?:no|ni|sin|without|not|don['’]t|no need)\b[^.\n]{0,30}$", re.I)


def _cifras_en_vez_de_letras(texto: str) -> str:
    def cambiar(m):
        if _RE_NEGACION_PREVIA.search(texto[max(0, m.start() - 40):m.start()]):
            return m.group(0)
        hasta = m.group(3) or ""
        hasta = str(_NUMEROS_EN_LETRA.get(hasta.lower(), hasta))
        return str(_NUMEROS_EN_LETRA[m.group(1).lower()]) + (m.group(2) or "") + hasta

    return _RE_NUMERO_EN_LETRA.sub(cambiar, texto)


def extraer_anos_experiencia(descripcion) -> int | None:
    """Años mínimos de experiencia que exige la descripción; 0 si dice «sin experiencia»;
    None si no lo dice o no hay descripción."""
    texto = _texto(descripcion)
    if not texto:
        return None
    texto = _cifras_en_vez_de_letras(texto)
    valores = [
        n for rx in _EXP_PATRONES for m in rx.finditer(texto)
        if 0 < (n := int(m.group(1))) <= 15
    ]
    if valores:
        return min(valores)
    return 0 if _EXP_NINGUNA.search(texto) else None


def ajuste_por_experiencia(anos, maximo=ANOS_EXPERIENCIA_MAX_POR_DEFECTO) -> float:
    """
    Corrección de la relevancia por la experiencia exigida.

    Pedir más de lo que se tiene resta 1,5 por año de diferencia, hasta -4,5: una oferta
    de «Encaje alto» que pide 5 años con un máximo de 3 baja 3 puntos y deja de llegar a
    Telegram salvo que el título encaje de lleno. Pedir 1 año o ninguno suma 0,5. Sin el
    dato (70 % de las ofertas) no se toca nada: la ausencia no es una señal.
    """
    if anos is None:
        return 0.0
    if anos > maximo:
        return -min(4.5, 1.5 * (anos - maximo))
    return 0.5 if anos <= 1 else 0.0


def _motivo_de_experiencia(anos, maximo) -> list[tuple[str, float]]:
    """La corrección por años exigidos, como motivo; vacío si no corrige nada."""
    ajuste = ajuste_por_experiencia(anos, maximo)
    if not ajuste:
        return []
    etiqueta = ("sin experiencia o 1 año" if anos <= 1
                else "pide %s años (tu tope: %s)" % (anos, maximo))
    return [(etiqueta, ajuste)]


class Puntuador:
    """
    Lo que hace falta para puntuar a UN perfil, sin globales: MP2 lo saca de `_REL`, que
    guardaba pesos y tope de años para todo el proceso, y con varios perfiles el último en
    cargarse habría puntuado a todos.

    Quien lo crea es `crear_puntuador`, desde la config efectiva. No se modifica una vez
    creado, así que se puede compartir entre hilos.
    """

    anos_max: int | None = ANOS_EXPERIENCIA_MAX_POR_DEFECTO
    # Alertas que a este perfil no le dicen nada, como «inglés alto» a quien lo tiene.
    alertas_omitidas: tuple[str, ...] = ()
    # Si explicar() necesita la fila entera (salario, jornada) y no solo el título. Convertir
    # el DataFrame a dicts cuesta, así que solo se hace para quien lo pide.
    usa_oferta = False

    def motivos(self, titulo, ubicacion, anos_experiencia, descripcion,
                oferta) -> list[tuple[str, float]]:
        raise NotImplementedError

    def termino_aprendido(self, titulo, ubicacion, descripcion, oferta,
                          anos_experiencia=None) -> tuple[str, float] | None:
        """
        El enganche de la capa aprendida de MP6: un término más, con su motivo, igual que
        cualquier regla. Sin nada aprendido devuelve None, que no añade ningún motivo: un
        «+0,0» saldría en los avisos y rompería la equivalencia. Lo que aprende cada perfil
        lo pone `PuntuadorConCapa` (aprendizaje.py); los años van aparte porque uno de sus
        rasgos es cuántos pide la oferta, y `explicar` ya los tiene calculados.
        """
        return None

    def explicar(self, titulo, ubicacion=None, anos_experiencia=None, descripcion=None,
                 oferta=None) -> tuple[float, list[tuple[str, float]]]:
        motivos = self.motivos(titulo, ubicacion, anos_experiencia, descripcion, oferta)
        aprendido = self.termino_aprendido(titulo, ubicacion, descripcion, oferta,
                                           anos_experiencia=anos_experiencia)
        if aprendido is not None:
            motivos.append(aprendido)
        return round(sum(p for _, p in motivos), 1), motivos


class PuntuadorReglas(Puntuador):
    """Un juego de reglas a mano, con sus pesos ajustados y, si toca, el modelo entrenado."""

    def __init__(self, juego=TITULAR_2026_10, pesos=None,
                 anos_max=ANOS_EXPERIENCIA_MAX_POR_DEFECTO, modelo=None):
        self.juego = juego
        self.pesos = pesos or {}
        self.anos_max = anos_max
        self.modelo = modelo

    def reglas_del_titulo(self, titulo, ubicacion=None, pesos=None) -> list[tuple[str, float]]:
        texto = _texto(titulo)
        if ubicacion:
            texto = f"{texto} {_texto(ubicacion)}"
        pesos = self.pesos if pesos is None else pesos
        return [(n, float(pesos.get(n, p))) for n, p, rx in self.juego.reglas_titulo
                if rx.search(texto)]

    def reglas_de_descripcion(self, titulo, descripcion, pesos=None) -> list[tuple[str, float]]:
        if not descripcion:
            return []
        pesos = self.pesos if pesos is None else pesos
        texto = _texto(descripcion)
        encontradas = []
        for regla in self.juego.reglas_descripcion:
            if regla["titulo"].search(_texto(titulo)):
                # `fuertes`, si la regla los trae: los que tienen que estar entre los que casan,
                # para reglas cuyos términos sueltos dicen poco (reglas/titular_ciber.py).
                if (sum(1 for rx in regla["terminos"] if rx.search(texto)) >= regla["minimo"]
                        and any(rx.search(texto) for rx in regla.get("fuertes", regla["terminos"]))):
                    encontradas.append(
                        (regla["nombre"], float(pesos.get(regla["nombre"], regla["peso"]))))
        return encontradas

    def _por_el_modelo(self, titulo, ubicacion) -> float:
        if self.modelo.usa_reglas:
            # Modelo híbrido: aprendió con la puntuación de las reglas como una señal más, así
            # que hay que dársela también aquí.
            reglas = round(sum(p for _, p in self.reglas_del_titulo(titulo, ubicacion)), 1)
            return self.modelo.puntuar(titulo, ubicacion, reglas)
        return self.modelo.puntuar(titulo, ubicacion)

    def motivos(self, titulo, ubicacion, anos_experiencia, descripcion, oferta):
        if self.modelo is None:
            motivos = self.reglas_del_titulo(titulo, ubicacion)
        else:
            motivos = [("modelo entrenado", float(self._por_el_modelo(titulo, ubicacion)))]
        motivos += self.reglas_de_descripcion(titulo, descripcion)
        if anos_experiencia is not None:
            motivos += _motivo_de_experiencia(anos_experiencia, self.anos_max)
        return motivos


class PuntuadorConCapa(Puntuador):
    """
    El puntuador de un perfil más lo que ha aprendido de sus botones (MP6, aprendizaje.py).

    Envuelve al de siempre sin tocarlo: los motivos son los suyos, y la capa entra por el enganche
    como un motivo más («+1,2 lo aprendido (soc, linkedin)»). Solo existe si la capa del perfil
    está activa (buscador.puntuador_para): sin ella se usa el de siempre, y nada cambia. Pide la
    fila entera porque la empresa, la fuente y la modalidad son rasgos.
    """

    usa_oferta = True

    def __init__(self, base: Puntuador, capa):
        self.base = base
        self.capa = capa
        self.anos_max = base.anos_max
        self.alertas_omitidas = base.alertas_omitidas

    def __getattr__(self, nombre):
        # juego, pesos, reglas_del_titulo…: lo que se le pida que no sea de la capa es del de siempre.
        return getattr(self.__dict__["base"], nombre)

    def motivos(self, titulo, ubicacion, anos_experiencia, descripcion, oferta):
        return self.base.motivos(titulo, ubicacion, anos_experiencia, descripcion, oferta)

    def termino_aprendido(self, titulo, ubicacion, descripcion, oferta, anos_experiencia=None):
        return self.capa.termino(titulo, ubicacion, descripcion, oferta, anos_experiencia)


def _sin_acentos(texto) -> str:
    texto = unicodedata.normalize("NFKD", _texto(texto).lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


# Lo que el catálogo no puede sacar de ningún puesto: son iguales para todos los sectores.
_CAT_JUNIOR = re.compile(r"\bjunior\b|\bjr\b|j[úu]nior|\btrainee\b|sin experiencia|entry[- ]level"
                         r"|primer empleo", re.IGNORECASE)
_CAT_PRACTICAS = re.compile(r"\bintern\b|internship|becari|\bbecas?\b|pr[áa]cticas|apprentice",
                            re.IGNORECASE)
# El mismo que `perfiles._SENIOR_GENERICO` (el filtro), por las mismas razones: ver allí (MP7).
_CAT_SENIOR = re.compile(r"\bsenior\b|\bsr\.?\b|(?<!tour )\blead(?:er)?\b(?! gen)"
                         r"|(?<!community )(?<!account )(?<!social media )(?<!content )(?<!traffic )\bmanager\b"
                         r"|\bdirector\b|\bhead of\b|\bjefe\b(?!(?:/a)? de (?:partida|rango))|\bresponsable\b",
                         re.IGNORECASE)
_CAT_IDIOMAS = {
    "en": r"\benglish\b|\bingl[ée]s\b", "ca": r"\bcatal[àa]n?\b", "fr": r"\bfrench\b|franc[ée]s",
    "de": r"\bgerman\b|alem[áa]n", "it": r"\bitalian\b|italiano", "pt": r"portuguese|portugu[ée]s",
    "nl": r"\bdutch\b|neerland[ée]s|flamenco",
}
# Los dos grupos de `plan.jornada`: decir «completa» no dice nada de indefinido o temporal, así
# que una oferta solo resta dentro de un grupo en el que la persona ha elegido algo.
_CAT_JORNADA = {"fulltime": "completa", "completa": "completa", "parttime": "parcial",
                "parcial": "parcial", "temporary": "temporal", "contract": "autonomo"}
_CAT_GRUPOS_JORNADA = ({"completa", "parcial"}, {"indefinido", "temporal", "autonomo"})
_CAT_MESES_POR_INTERVALO = {"yearly": 1, "monthly": 12}


def _con_tope(motivos, tope) -> list[tuple[str, float]]:
    """Los motivos hasta sumar `tope`; el último se recorta para que la suma siga siendo el número."""
    salida, suma = [], 0.0
    for nombre, peso in motivos:
        if suma >= tope:
            break
        peso = min(peso, tope - suma)
        salida.append((nombre, peso))
        suma += peso
    return salida


class PuntuadorCatalogo(Puntuador):
    """
    La puntuación genérica de EVOLUCION-MULTIPERFIL.md §5.2, sobre un plan de perfil (§5.5).

    Misma escala que las reglas a mano, con «Encaje alto» ≥ 5,0. Un plan genera regex por
    puesto, así que aquí no hay nada de ningún sector. Cada peso es un motivo con nombre, y dos
    puestos que comparten una señal la cuentan una vez: ser administrativo y contable no es
    tener dos veces «contabilidad».
    """

    usa_oferta = True

    def __init__(self, plan: dict):
        self.plan = plan
        self.anos = int(plan.get("experiencia_anos") or 0)
        self.anos_max = plan.get("experiencia_max_exigida")
        self.estricta = bool(plan.get("experiencia_estricta"))
        self.alertas_omitidas = ("inglés alto",) if plan.get("ingles") == "alto" else ()

        # Los negativos de un puesto solo restan cuando el título ES de ese puesto (casa su núcleo):
        # están para bajar sus falsos positivos, no para castigar los otros puestos de la persona.
        # Sin esto, el «diseño industrial o técnico» de diseño gráfico restaba 3 puntos a «Mecánico/a de
        # automoción» a quien buscaba las dos cosas (MP7, medido con pruebas/catalogo_muestras.json: 113
        # choques entre negativos y títulos que otro puesto elegido debe aceptar). Por eso cada negativo
        # recuerda de qué puestos es: dos con el mismo nombre siguen contando una vez.
        self.nucleos, self.senales, self.negativos = [], [], []
        vistos, duenos = set(), {}
        for indice, puesto in enumerate(plan.get("puestos") or []):
            dueno = puesto.get("id") or puesto.get("nombre") or indice
            nucleo = _compilar(puesto.get("nucleo") or "")
            if nucleo is not None:
                self.nucleos.append((puesto.get("nombre") or puesto.get("id"), nucleo, dueno))
            for regla in puesto.get("senales") or []:
                patron = _compilar(regla.get("patron") or "")
                if patron is not None and regla.get("nombre") not in vistos:
                    vistos.add(regla.get("nombre"))
                    self.senales.append((regla["nombre"], float(regla["peso"]), patron))
            for regla in puesto.get("negativos") or []:
                patron = _compilar(regla.get("patron") or "")
                if patron is None:
                    continue
                if regla.get("nombre") in duenos:
                    duenos[regla["nombre"]].add(dueno)
                elif regla.get("nombre") not in vistos:
                    vistos.add(regla.get("nombre"))
                    duenos[regla["nombre"]] = {dueno}
                    self.negativos.append((regla["nombre"], float(regla["peso"]), patron,
                                           duenos[regla["nombre"]]))

        self.claves_mas = self._palabras(plan.get("claves_mas"))
        self.claves_menos = self._palabras(plan.get("claves_menos"))
        # Lo escrito en «Otro» es un puesto desde el 11-11 (`perfilado.generador.nucleo_de_otro`) y sigue
        # además entre las palabras clave, por su +0,5 «en la descripción». En el título ya cuenta como
        # puesto (+4,0): si sumara también su +1,5, lo escrito a mano valdría más que un puesto del catálogo.
        # Un plan sin puestos escritos, que son todos los de antes de esa fecha, puntúa exactamente igual.
        escritos = {_sin_acentos(p.get("nombre")).strip() for p in plan.get("puestos") or []
                    if str(p.get("id") or "").startswith("otro:")}
        self.claves_del_titulo = [(n, rx) for n, rx in self.claves_mas if _sin_acentos(n).strip() not in escritos]
        area = plan.get("area") or {}
        self.ciudad = _sin_acentos(area.get("nombre") or "").strip()
        # Solo remoto (radio 0, §5.5): no tiene ciudad que le sume. Decidido por el titular el 05-11
        # (MP7): una oferta en remoto le da el +1,0 que la ciudad da a los demás. Sin él, un título
        # exacto sin señal ni palabra clave se quedaba en 4,0 a quien no es junior («DevOps Engineer
        # AWS» a un desarrollador senior en remoto), y no llegaba nunca.
        self.solo_remoto = not float(area.get("radio_km") or 0)
        ajenos = [rx for codigo, rx in _CAT_IDIOMAS.items()
                  if codigo not in (plan.get("idiomas_permitidos") or ["es"])]
        self.idioma_ajeno = re.compile("|".join(ajenos), re.IGNORECASE) if ajenos else None
        self.salario_min = plan.get("salario_min")
        self.jornada = set(plan.get("jornada") or [])

    @staticmethod
    def _palabras(palabras) -> list[tuple[str, re.Pattern]]:
        """Sin acentos y por palabras completas: «ett» no puede casar con «marketing»."""
        salida, vistas = [], set()
        for palabra in palabras or []:
            limpia = _sin_acentos(palabra).strip()
            if limpia and limpia not in vistas:
                vistas.add(limpia)
                salida.append((str(palabra).strip(), re.compile(
                    r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(limpia))))
        return salida

    def reglas_del_titulo(self, titulo, ubicacion=None, pesos=None) -> list[tuple[str, float]]:
        titulo = _texto(titulo)
        normal = _sin_acentos(titulo)
        motivos = []
        suyos = {dueno for _, nucleo, dueno in self.nucleos if nucleo.search(titulo)}
        for nombre, nucleo, _ in self.nucleos:
            if nucleo.search(titulo):
                motivos.append(("puesto: %s" % nombre, 4.0))
                break
        motivos += _con_tope([(n, p) for n, p, rx in self.senales if rx.search(titulo)], 2.0)
        if self.anos <= 2 and _CAT_JUNIOR.search(titulo):
            motivos.append(("junior o sin experiencia", 2.0))
        if self.plan.get("practicas") and _CAT_PRACTICAS.search(titulo):
            motivos.append(("prácticas, que aceptas", 1.0))
        elif _CAT_PRACTICAS.search(titulo):
            motivos.append(("prácticas o becario", -1.5))
        motivos += _con_tope([("«%s» en el título" % n, 1.5) for n, rx in self.claves_del_titulo
                              if rx.search(normal)], 3.0)
        if self.ciudad and self.ciudad in _sin_acentos(ubicacion):
            motivos.append(("en %s" % self.ciudad.title(), 1.0))
        if self.anos < 6 and _CAT_SENIOR.search(titulo):
            motivos.append(("senior, lead o manager", -3.0))
        if self.anos >= 6 and _CAT_JUNIOR.search(titulo):
            motivos.append(("junior, por debajo de tu nivel", -1.5))
        motivos += [("«%s» en el título, a evitar" % n, -3.0) for n, rx in self.claves_menos
                    if rx.search(normal)]
        if self.idioma_ajeno is not None and self.idioma_ajeno.search(titulo):
            motivos.append(("idioma que no hablas", -4.0))
        motivos += [(n, p) for n, p, rx, de in self.negativos if de & suyos and rx.search(titulo)]
        return motivos

    def reglas_de_descripcion(self, titulo, descripcion, pesos=None) -> list[tuple[str, float]]:
        if not descripcion:
            return []
        normal, en_titulo = _sin_acentos(descripcion), _sin_acentos(titulo)
        # Lo que ya está en el título no puntúa otra vez por repetirse en la descripción.
        motivos = _con_tope([("«%s» en la descripción" % n, 0.5) for n, rx in self.claves_mas
                             if rx.search(normal) and not rx.search(en_titulo)], 1.5)
        motivos += [("«%s» en la descripción, a evitar" % n, -1.0) for n, rx in self.claves_menos
                    if rx.search(normal) and not rx.search(en_titulo)]
        return motivos

    def _por_condiciones(self, oferta: dict) -> list[tuple[str, float]]:
        """Solo actúa si la oferta lo dice: sin el dato, ni resta ni suma (§4.2, paso 10)."""
        motivos = []
        meses = _CAT_MESES_POR_INTERVALO.get(oferta.get("interval"))
        if self.salario_min and meses and oferta.get("currency") in (None, "EUR"):
            importes = [v for v in (oferta.get("min_amount"), oferta.get("max_amount"))
                        if v is not None and not pd.isna(v)]
            if importes and max(importes) * meses < float(self.salario_min):
                motivos.append(("salario por debajo de tu mínimo", -1.5))
        tipo = _CAT_JORNADA.get(_texto(oferta.get("job_type")).strip().lower())
        if tipo and self.jornada:
            grupo = next(g for g in _CAT_GRUPOS_JORNADA if tipo in g)
            if self.jornada & grupo and tipo not in self.jornada:
                motivos.append(("jornada que no quieres", -1.0))
        return motivos

    def motivos(self, titulo, ubicacion, anos_experiencia, descripcion, oferta):
        motivos = self.reglas_del_titulo(titulo, ubicacion)
        motivos += self.reglas_de_descripcion(titulo, descripcion)
        # Solo por encima del tope, y con estricta ni eso: esas ya las ha quitado el filtro. El
        # +0,5 de pedir poco no está en la tabla: premia a quien busca su primer empleo.
        if (anos_experiencia is not None and self.anos_max is not None and not self.estricta
                and anos_experiencia > self.anos_max):
            motivos += _motivo_de_experiencia(anos_experiencia, self.anos_max)
        if oferta:
            motivos += self._por_condiciones(oferta)
            # Con `modalidad()`, la misma que pone la etiqueta del aviso: lo que la corrida deja pasar
            # sin ubicación conocida («No indicado») no suma. Y una sola vez: si ya sumó su ciudad, no.
            if (self.solo_remoto and modalidad(oferta) == "Remoto"
                    and ("en %s" % self.ciudad.title()) not in dict(motivos)):
                motivos.append(("en remoto", 1.0))
        return motivos


# Las reglas sin ajustar de nadie: lo que devuelven las funciones de módulo sin `puntuador`.
_REGLAS_DE_LA_TABLA = PuntuadorReglas()


def _cargar_modelo():
    """
    El modelo entrenado, o None si no hay uno legible.

    Importado aquí y no arriba: con el interruptor apagado (lo normal) la app no necesita ni
    numpy ni el módulo, y un fallo suyo no puede dejar sin listado a quien no lo usa.
    """
    try:
        import modelo_relevancia

        modelo = modelo_relevancia.cargar()
    except Exception:
        logger.exception("No se ha podido cargar el modelo de relevancia")
        modelo = None
    if modelo is None:
        logger.warning(
            "usar_modelo_entrenado está activado pero no hay un %s legible; "
            "se puntúa con las reglas. Ejecuta entrenar_modelo.py.",
            MODELO_REL_PATH.name,
        )
    return modelo


def crear_puntuador(config: dict) -> Puntuador:
    """
    El puntuador de una config efectiva (`perfiles.config_efectiva`).

    Una config sin la clave `puntuador`, como la de `config.json`, es la del titular: juego
    `titular-2026-10` con los pesos y el tope de esa config. Los perfiles de catálogo no
    tienen pesos que sobrescribir: lo suyo lo dice el plan.
    """
    plantilla = config.get("puntuador") or {"tipo": "reglas", "juego": TITULAR_2026_10.nombre}
    if plantilla.get("tipo") == "catalogo":
        return PuntuadorCatalogo(plantilla["plan"])
    # El máximo de años y los pesos se leen aquí, una vez, y no por oferta: /api/datos puntúa
    # la base entera de golpe.
    try:
        anos_max = int(config.get("anos_experiencia_max", ANOS_EXPERIENCIA_MAX_POR_DEFECTO))
    except (TypeError, ValueError):
        anos_max = ANOS_EXPERIENCIA_MAX_POR_DEFECTO
    # Pesos que el usuario ha sobrescrito en la config, por nombre de regla. Solo se aceptan
    # números: un valor roto no puede tumbar la puntuación de toda la base.
    pesos = {}
    for nombre, peso in (config.get("pesos_relevancia") or {}).items():
        try:
            pesos[str(nombre)] = float(peso)
        except (TypeError, ValueError):
            logger.warning("pesos_relevancia: el peso de %r no es un número; se ignora", nombre)
    modelo = _cargar_modelo() if config.get("usar_modelo_entrenado", False) else None
    return PuntuadorReglas(obtener_juego(plantilla["juego"]), pesos, anos_max, modelo)


# El puntuador del titular se construye UNA vez, no en cada llamada: /api/datos puntúa las
# ~1.100 ofertas de la base en cada refresco. Se revalida como mucho una vez cada dos segundos
# comparando la fecha de config.json y del modelo, para que activar el interruptor o reentrenar
# surtan efecto sin reiniciar el servidor, pero sin un stat por oferta.
#
# Es el único global que queda con algo de una persona, y es seguro: no lleva chat_id, solo
# refleja `config.json`, y a él solo se llega con `puntuador=None`. Sale de `config.json` y no
# de perfiles.db a propósito: con la base caída se tiene que poder seguir puntuando, y
# pruebas/medir.py corre sin base.
_REL = {"comprobado": 0.0, "firma": None, "puntuador": None}
_lock_rel = Lock()
SEGUNDOS_REVALIDAR = 2.0


def _fecha(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def firma_relevancia() -> tuple[float, float]:
    """
    Identifica de qué depende la puntuación ahora mismo: el config.json (que elige
    reglas o modelo, y trae los patrones editables) y el modelo entrenado.

    Es la misma firma que usa _puntuador_del_titular() para decidir si tiene que recargar.
    La expone para que app.py sepa cuándo puede reutilizar el listado ya puntuado sin
    tener que volver a puntuar la base entera (ver /api/datos).
    """
    import config as _config  # import diferido: ver el docstring de config.py

    return (_fecha(_config.CONFIG_PATH), _fecha(MODELO_REL_PATH))


def _puntuador_del_titular() -> Puntuador:
    ahora = time.monotonic()
    if _REL["puntuador"] is not None and ahora - _REL["comprobado"] < SEGUNDOS_REVALIDAR:
        return _REL["puntuador"]

    import config as _config  # import diferido: ver el docstring de config.py

    with _lock_rel:
        firma = (_fecha(_config.CONFIG_PATH), _fecha(MODELO_REL_PATH))
        if _REL["puntuador"] is not None and firma == _REL["firma"]:
            _REL["comprobado"] = ahora
            return _REL["puntuador"]
        puntuador = crear_puntuador(_config.cargar_config())
        _REL.update(comprobado=ahora, firma=firma, puntuador=puntuador)
        return puntuador


def reglas_que_aplican(titulo, ubicacion=None, pesos=None, puntuador=None) -> list[tuple[str, float]]:
    """
    Las reglas del título que se cumplen, como [(nombre, peso)]. Es lo que explica una
    puntuación: su suma es el número que ve el usuario.

    `pesos` sobrescribe el peso de una regla por su nombre (`pesos_relevancia` en la
    config); sin él, o sin esa regla en él, vale el peso de la tabla. Con `puntuador`, sus
    reglas y sus pesos; `None` = las del titular, con los pesos de la tabla, como siempre.
    """
    if puntuador is None:
        return _REGLAS_DE_LA_TABLA.reglas_del_titulo(titulo, ubicacion, pesos)
    return puntuador.reglas_del_titulo(titulo, ubicacion, pesos)


def puntuar_con_reglas(titulo, ubicacion=None) -> float:
    """Puntúa una oferta por su parecido con el perfil buscado, con los pesos de arriba."""
    # Sin puntuador construido todavía, los de la tabla: entrenar_modelo.py la llama nada más
    # arrancar, y su medida depende de eso.
    titular = _REL["puntuador"]
    pesos = titular.pesos if titular is not None else None
    return round(sum(p for _, p in reglas_que_aplican(titulo, ubicacion, pesos)), 1)


def reglas_de_la_descripcion(titulo, descripcion, pesos=None, puntuador=None) -> list[tuple[str, float]]:
    """Las señales que solo se ven leyendo la descripción, como [(nombre, peso)]."""
    if puntuador is None:
        return _REGLAS_DE_LA_TABLA.reglas_de_descripcion(titulo, descripcion, pesos)
    return puntuador.reglas_de_descripcion(titulo, descripcion, pesos)


def explicar_relevancia(
    titulo, ubicacion=None, anos_experiencia=None, descripcion=None, puntuador=None, oferta=None
) -> tuple[float, list[tuple[str, float]]]:
    """
    La puntuación de una oferta Y por qué: [(motivo, peso)] cuya suma es el número.

    Es la única implementación. `puntuar_relevancia` es esto sin la explicación, así que
    lo que se enseña como motivo es, por construcción, lo que ha puntuado.

    Con el modelo entrenado activado (`usar_modelo_entrenado`) no hay reglas que
    enumerar: el modelo aporta un único motivo, y la experiencia y la descripción se
    siguen aplicando por fuera, igual que antes.

    `puntuador=None` es el del titular, para que ningún llamador de antes tenga que cambiar.
    `oferta` es la fila entera, para el puntuador de catálogo (salario y jornada).
    """
    puntuador = puntuador if puntuador is not None else _puntuador_del_titular()
    return puntuador.explicar(titulo, ubicacion, anos_experiencia, descripcion, oferta)


def texto_de_motivos(motivos: list[tuple[str, float]], maximo: int = 3) -> str:
    """Los motivos que más pesan, en una línea: «+4,0 soporte IT · +2,0 junior · −2,0 N2»."""
    mas_pesados = sorted(motivos, key=lambda m: -abs(m[1]))[:maximo]
    return " · ".join(
        "%s%s %s" % ("+" if p > 0 else "−", ("%.1f" % abs(p)).replace(".", ","), n)
        for n, p in mas_pesados
    )


def puntuar_relevancia(titulo, ubicacion=None, anos_experiencia=None, descripcion=None,
                       puntuador=None) -> float:
    """
    Puntúa una oferta por su parecido con el perfil buscado.

    Firma y escala estables pase lo que pase: devuelve un número en el mismo rango que
    han devuelto siempre las reglas (los cortes «Encaje alto» ≥ 5 y «Encaje medio» ≥ 3
    de templates/index.html siguen valiendo), tanto si detrás están los pesos a mano
    como si está el modelo entrenado, que se calibra a esa misma escala al guardarse.

    `anos_experiencia` y `descripcion` (opcionales) se aplican por fuera del puntuador,
    así que valen igual para las reglas y para el modelo entrenado. Quien no pasa la
    descripción obtiene la puntuación de siempre.
    """
    return explicar_relevancia(titulo, ubicacion, anos_experiencia, descripcion, puntuador)[0]


# ----------------------------------------------------------------------
# ALERTAS DE UNA OFERTA (2026-10-25)
#
# Cosas que no descartan la oferta pero que conviene saber ANTES de abrirla, para no
# gastar el clic. Son las que el usuario pidió que se avisaran, más una que salió de
# mirar la base: los anuncios que piden pagar por formarte o que pagan a comisión.
#
# Los turnos 24x7 NO son una alerta: el usuario dijo expresamente que le valen. Se
# muestran como dato, no como advertencia.
# ----------------------------------------------------------------------
_ALERTAS = [
    ("inglés alto", re.compile(
        r"ingl[ée]s\s*(?:alto|avanzado|fluido|nativo|c1|c2|b2\+)"
        r"|\b(?:advanced|fluent|native|proficient)\s+english"
        r"|english\s*(?:c1|c2|advanced|fluent|native)", re.IGNORECASE)),
    ("otro idioma", re.compile(
        r"\b(?:franc[ée]s|alem[áa]n|neerland[ée]s|italiano|portugu[ée]s|holand[ée]s)\s*"
        r"(?:alto|avanzado|fluido|nativo|c1|b2)"
        r"|\b(?:french|german|dutch|italian)\s*(?:c1|c2|advanced|fluent|native)"
        r"|(?:with|habla)\s+(?:french|german|dutch|italian|franc[ée]s|alem[áa]n)",
        re.IGNORECASE)),
    ("posible trampa (formación de pago o comisiones)", re.compile(
        r"formaci[óo]n\s*(?:de pago|previa de pago)|curso\s*(?:de pago|previo)"
        r"|(?:solo|únicamente|exclusivamente)\s*(?:a\s*)?comisi[óo]n"
        r"|a\s*comisi[óo]n\b|sin\s*(?:sueldo|salario)\s*fijo"
        r"|alta\s*(?:en|como)\s*aut[óo]nomo|darte de alta como aut[óo]nomo", re.IGNORECASE)),
]
TURNOS = re.compile(r"24\s*[x/]\s*7|turnos? rotativ|nocturn|fin(?:es)? de semana|\bguardias?\b",
                    re.IGNORECASE)


def alertas(fila, anos_max: int | None = None, puntuador=None) -> list[str]:
    """Avisos a pie de oferta. Se leen del título y de la descripción."""
    texto = f"{_texto(fila.get('title'))} {_texto(fila.get('description'))[:4000]}"
    omitidas = puntuador.alertas_omitidas if puntuador is not None else ()
    encontradas = [nombre for nombre, patron in _ALERTAS
                   if nombre not in omitidas and patron.search(texto)]

    if anos_max is not None:
        maximo = anos_max
    else:
        maximo = puntuador.anos_max if puntuador is not None else ANOS_EXPERIENCIA_MAX_POR_DEFECTO
    anos = fila.get("anos_experiencia")
    if anos is None:
        anos = extraer_anos_experiencia(fila.get("description"))
    if maximo is not None and isinstance(anos, (int, float)) and anos > maximo:
        encontradas.append(f"pide {int(anos)} años (tu tope: {maximo})")

    if fila.get("posible_restriccion_geografica"):
        encontradas.append("puede restringir el país desde el que se trabaja")
    return encontradas


def anotar_relevancia(df: pd.DataFrame, puntuador=None) -> pd.DataFrame:
    if df.empty:
        df = df.copy()
        df["relevancia"] = None
        return df
    df = df.copy()
    descripciones = df["description"] if "description" in df else [None] * len(df)
    anos = [extraer_anos_experiencia(d) for d in descripciones]
    # dtype object: con None y enteros mezclados pandas haría la columna float con NaN,
    # y el JSON guardaría 3.0 / NaN en vez de 3 / null.
    df["anos_experiencia"] = pd.Series(anos, index=df.index, dtype=object)
    filas = (df.to_dict(orient="records") if puntuador is not None and puntuador.usa_oferta
             else [None] * len(df))
    explicaciones = [
        explicar_relevancia(t, l, a, d, puntuador, fila)
        for t, l, a, d, fila in zip(df["title"], df.get("location", df["title"]), anos,
                                    descripciones, filas)
    ]
    df["relevancia"] = [puntos for puntos, _ in explicaciones]
    # Los motivos se guardan con la oferta tal y como eran al descubrirla. La puntuación
    # se recalcula al vuelo cada vez que se sirve la base (para que ajustar un peso surta
    # efecto sin repetir la búsqueda), pero los motivos guardados no: son el registro de
    # por qué se puntuó así EN ESE MOMENTO. Para los de ahora, `explicar_relevancia`.
    df["motivos_relevancia"] = pd.Series(
        [["%s (%s)" % (n, ("%+.1f" % p).replace(".", ",")) for n, p in motivos]
         for _, motivos in explicaciones],
        index=df.index, dtype=object,
    )
    return df


def filtrar_por_nivel(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Descarta los niveles de experiencia a los que no optas.

    Contrastado con el historial: de las ofertas aplicadas, ninguna es «Mid-Senior»;
    de las ocultadas, 169 lo eran. Solo se descarta cuando LinkedIn indica el nivel
    explícitamente: la mayoría vienen sin él y esas se conservan.
    """
    if df.empty or "job_level" not in df.columns:
        return df
    excluidos = {n.lower() for n in config.get("niveles_excluidos", []) if n}
    if not excluidos:
        return df
    nivel = df["job_level"].apply(lambda v: _texto(v).strip().lower())
    return df[~nivel.isin(excluidos)]


def filtrar_por_experiencia_exigida(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Con `experiencia_estricta`, fuera lo que pida más años que `anos_experiencia_max`.

    Sin el dato pasa: el 70 % de las ofertas no dice los años, y la ausencia no es una señal
    (ver ajuste_por_experiencia). Sin `experiencia_estricta` devuelve el mismo DataFrame, sin
    leer ninguna descripción: al titular no le cuesta nada, y los años solo restan puntos.
    """
    tope = config.get("anos_experiencia_max", ANOS_EXPERIENCIA_MAX_POR_DEFECTO)
    # Sin tope: quien ha elegido «sin límite» de años (§4.2) no descarta por esto.
    if df.empty or not config.get("experiencia_estricta") or tope is None:
        return df
    tope = int(tope)

    def se_conserva(descripcion) -> bool:
        anos = extraer_anos_experiencia(descripcion)
        return anos is None or anos <= tope

    return df[df["description"].apply(se_conserva)]


# "remoto_es" y "remoto_ue" se agrupan en el mismo ámbito final de cara al visor
AMBITO_FINAL = {
    "barcelona_local": "barcelona_local",
    "remoto_es": "remoto_es_ue",
    "remoto_ue": "remoto_es_ue",
    "remoto_mundial": "remoto_mundial",
}


def _ventana_temporal_google(hours_old: int | None) -> str | None:
    """
    Traduce hours_old a la sintaxis de fecha que entiende la búsqueda de empleo de
    Google ("since yesterday", "in the last week"...). Mismos cortes que usa jobspy
    internamente (jobspy.google._get_initial_cursor_and_jobs.get_time_range) cuando no
    se le pasa google_search_term explícito, para no inventar un criterio distinto.
    """
    if not hours_old:
        return None
    if hours_old <= 24:
        return "since yesterday"
    if hours_old <= 72:
        return "in the last 3 days"
    if hours_old <= 168:
        return "in the last week"
    return "in the last month"


def _google_search_term(termino: str, amb: dict, config: dict) -> str:
    """
    Construye el google_search_term explícito para un ámbito: Google Jobs ignora
    search_term (solo lo usan indeed/linkedin), así que sin esto la fuente "google"
    nunca devuelve resultados por mucho que search_term esté puesto.
    """
    query = f"{termino} jobs"
    if amb.get("location"):
        query += f" in {amb['location']}"
    if amb.get("is_remote"):
        query += " remote"
    ventana = _ventana_temporal_google(config.get("hours_old"))
    if ventana:
        query += f" {ventana}"
    return query


def construir_busquedas(config: dict) -> list[dict]:
    """Genera la lista de búsquedas (categoría × término × ámbito) a partir de la config."""
    busquedas = []
    for clave_cat, cat in config["categorias"].items():
        if not cat.get("activa", True):
            continue
        for termino in cat.get("terminos", []):
            if not termino.strip():
                continue
            for clave_amb, amb in config["ambitos"].items():
                if not amb.get("activo", True):
                    continue
                sitios = amb.get("sitios", ["linkedin"])
                params = {
                    "search_term": termino,
                    "site_name": sitios,
                    "is_remote": amb.get("is_remote", False),
                }
                if amb.get("location"):
                    params["location"] = amb["location"]
                if amb.get("usar_distancia"):
                    params["distance"] = config["distancia_portal_millas"]
                if amb.get("usar_country_indeed"):
                    params["country_indeed"] = config["country_indeed"]
                if "google" in sitios:
                    params["google_search_term"] = _google_search_term(termino, amb, config)

                busquedas.append(
                    {
                        "nombre": f"{cat['etiqueta']} · {amb['etiqueta']} · \"{termino}\"",
                        "categoria": clave_cat,
                        "ambito": AMBITO_FINAL.get(clave_amb, clave_amb),
                        "ambito_origen": clave_amb,
                        "params": params,
                    }
                )
    return busquedas


# ----------------------------------------------------------------------
# GEOCODIFICACIÓN
# ----------------------------------------------------------------------


def _cargar_cache_geocoding() -> dict:
    if GEOCODE_CACHE_PATH.exists():
        try:
            return json.loads(GEOCODE_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _guardar_cache_geocoding(cache: dict) -> None:
    GEOCODE_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# Lo que Nominatim devuelve para una región o un país: un punto en el centro, que no dice nada de
# dónde es la oferta. «Comunitat Valenciana» caería a 60 km de Valencia y descartaría, y aquí lo
# que toca es «no se sabe», como con «Spain» o «CT, ES».
_TIPOS_QUE_NO_SON_UN_SITIO = {"continent", "country", "state", "region", "province"}
_RE_AREA_METROPOLITANA = re.compile(
    r"^(?:greater|gran)\s+(.+?)(?:\s+metropolitan)?(?:\s+area)?$", re.IGNORECASE)


class Geocodificador:
    """
    Las coordenadas de una corrida, con `geocode_cache.json` y el ritmo de Nominatim.

    **Uno por corrida y compartido entre perfiles**, porque el tope lo es: 60 consultas nuevas a
    1 por segundo son un minuto, y dos perfiles con el mismo municipio no deben pagarlo dos veces.
    La clave `municipio, país` es lo que lo permite: «Valencia, VC, ES» y «Valencia, Valencian
    Community, Spain» son la misma consulta.

    El tope solo vale para `ubicacion()`, la del área por distancia. `direccion()` es lo de
    siempre, la cadena entera y sin tope: al titular no se le puede poner, porque tras un atasco
    de más de 60 ubicaciones nuevas dejaría de descartar por distancia y cambiaría decisiones.
    Lo que sí hace es contar para el tope de los demás: es la misma cuota de Nominatim.

    Lo que no entra en el tope se queda sin cachear: es «no se sabe» hoy, no para siempre.
    """

    def __init__(self, tope=None, consultar=None, dormir=time.sleep):
        self.tope = None if tope is None else int(tope)
        self.nuevas = 0
        self.cache = _cargar_cache_geocoding()
        self._cambio = False
        self._consultar = consultar
        self._dormir = dormir
        self._geolocator = None

    def _preguntar(self, direccion):
        """El resultado crudo de Nominatim. Deja que salten sus excepciones."""
        if self._consultar is not None:
            return self._consultar(direccion)
        if self._geolocator is None:
            self._geolocator = Nominatim(user_agent="jobspy_busqueda_personal_bcn")
        return self._geolocator.geocode(direccion, timeout=10)

    def _apuntar(self, clave, coords) -> None:
        self.cache[clave] = coords
        self._cambio = True

    def direccion(self, direccion):
        """Coordenadas de una cadena tal cual, o None. Sin tope."""
        if not direccion or not isinstance(direccion, str):
            return None
        if direccion in self.cache:
            return self.cache[direccion]
        try:
            self._dormir(1)  # política de uso de Nominatim: máx. 1 petición/segundo
            self.nuevas += 1
            resultado = self._preguntar(direccion)
        except Exception:
            resultado = None
        coords = [resultado.latitude, resultado.longitude] if resultado else None
        self._apuntar(direccion, coords)
        return coords

    @staticmethod
    def clave_de(ubicacion) -> str | None:
        """Lo que se pregunta por una ubicación de portal, o None si no nombra ningún sitio."""
        from etiquetas import extraer_pais  # import local: evita dependencia circular

        texto = _texto(ubicacion).strip()
        municipio = texto.split(",")[0].strip()
        zona = _RE_AREA_METROPOLITANA.match(municipio)
        if zona:
            municipio = zona.group(1).strip()
        if not municipio or REMOTO.search(municipio):
            return None
        pais = extraer_pais(texto)
        return f"{municipio}, {pais}" if pais and pais.lower() != municipio.lower() else municipio

    def ubicacion(self, ubicacion):
        """Coordenadas del municipio de una ubicación, o None si no se sabe. Con tope."""
        clave = self.clave_de(ubicacion)
        if clave is None or not GEOCODING_DISPONIBLE:
            return None
        if clave in self.cache:
            return self.cache[clave]
        if self.tope is not None and self.nuevas >= self.tope:
            return None
        try:
            self._dormir(1)  # política de uso de Nominatim: máx. 1 petición/segundo
            self.nuevas += 1
            resultado = self._preguntar(clave)
        except Exception:
            # Un fallo de red no es «ese sitio no existe»: no se apunta, y se reintenta.
            return None
        coords = None
        if resultado:
            tipo = (getattr(resultado, "raw", None) or {}).get("addresstype")
            if tipo not in _TIPOS_QUE_NO_SON_UN_SITIO:
                coords = [resultado.latitude, resultado.longitude]
        self._apuntar(clave, coords)
        return coords

    def guardar(self) -> None:
        """Escribe el caché solo si hay algo nuevo: en la mayoría de corridas no lo hay, y antes
        se reescribía entero igualmente en un disco que ya falló."""
        if self._cambio:
            _guardar_cache_geocoding(self.cache)
            self._cambio = False


def calcular_distancias(df: pd.DataFrame, config: dict, geo=None) -> pd.DataFrame:
    """Añade distancia_km (en km reales desde el centro configurado) a las ofertas locales."""
    if df.empty:
        df = df.copy()
        df["distancia_km"] = pd.Series(dtype=float)
        return df
    if not GEOCODING_DISPONIBLE:
        df = df.copy()
        df["distancia_km"] = None
        return df

    centro = (config["centro_lat"], config["centro_lon"])
    geo = geo or Geocodificador()
    area = config.get("area") or {}
    coordenadas = geo.ubicacion if area.get("modo") == "distancia" else geo.direccion

    distancias = []
    for loc in df["location"]:
        coords = coordenadas(loc)
        distancias.append(round(geodesic(centro, coords).km, 1) if coords else None)

    geo.guardar()
    df = df.copy()
    df["distancia_km"] = distancias
    return df


# ----------------------------------------------------------------------
# BÚSQUEDA PRINCIPAL
# ----------------------------------------------------------------------

# Fuentes que no vienen de jobspy y viven en fuentes_extra/. La clave es el nombre con
# el que aparecen en "sitios" dentro de config.json; todas comparten la misma firma
# (termino, location, hours_old, is_remote) y devuelven un DataFrame con las columnas
# del contrato, así que añadir una nueva es añadir una línea aquí y su migración.
FUENTES_PROPIAS = {
    "infojobs": buscar_infojobs,
    "eures": buscar_eures,
    "empresa": buscar_empresas,
    # Apagada por defecto: solo se consulta si se añade "tecnoempleo" a los sitios del
    # ámbito de Barcelona (ver fuentes_extra/tecnoempleo.py, «LAS SALVAGUARDAS»).
    "tecnoempleo": buscar_tecnoempleo,
    # API con 500 peticiones: sus propios términos, cada 12 h y solo en Barcelona (ver
    # fuentes_extra/jooble.py). Sin JOBSPY_JOOBLE_KEY en el entorno no hace nada.
    "jooble": buscar_jooble,
}

# Fuentes que llevan estado por corrida: buscar() les avisa al empezar y al terminar.
FUENTES_CON_CORRIDA = {
    "infojobs": infojobs_mod,
    "tecnoempleo": tecnoempleo_mod,
    "jooble": jooble_mod,
}

# Las que traen ofertas una vez por corrida, sin mirar el término de la consulta en la que caen. Lo
# que traen no es de quien pidió esa consulta: ver _duenos_de_corrida.
FUENTES_DE_UNA_VEZ = {"tecnoempleo", "jooble"}

# ----------------------------------------------------------------------
# BLOQUEOS DE LAS FUENTES DE JOBSPY (2026-10-25)
#
# El problema: `una_busqueda` solo captura excepciones de Python, y jobspy NO las lanza
# cuando un portal bloquea. Lo que hace (comprobado en el código de la 1.1.82 instalada,
# jobspy/linkedin/__init__.py:124) es registrar el error en su propio logger y devolver
# un DataFrame vacío:
#
#     if response.status_code == 429:
#         err = "429 Response - Blocked by LinkedIn for too many requests"
#     log.error(err)
#     return JobResponse(jobs=job_list)
#
# Para el sistema eso era indistinguible de «no hay nada nuevo»: la búsqueda terminaba
# bien y la barra de progreso decía «0 ofertas». Con 33 búsquedas por corrida cada hora
# contra la fuente que aporta el 86 % de las ofertas, un bloqueo podía durar días sin
# que nadie se enterara.
#
# NO se usa `jobs.attrs['errors']`: esa API no existe en la 1.1.82 (se comprobó
# buscándola en el paquete instalado; las coincidencias de "attrs" son de BeautifulSoup).
# Llegó en una versión posterior. Capturar el logger funciona en las dos, así que vale
# igual antes y después de subir de versión.
#
# Los logger de jobspy se llaman "JobSpy:LinkedIn", "JobSpy:Indeed"... y tienen
# propagate=False, así que no basta con escuchar en la raíz: hay que enganchar un
# handler en cada uno.
# ----------------------------------------------------------------------
_RE_CODIGO_HTTP = re.compile(r"\b([1-5]\d{2})\b")


class _CazadorDeBloqueos(logging.Handler):
    """Apunta los errores que jobspy manda a su logger, por fuente."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.sucesos: list[tuple[str, int | None, str]] = []
        self._candado = Lock()

    def emit(self, registro: logging.LogRecord) -> None:
        # "JobSpy:LinkedIn" -> "linkedin"; coincide con los nombres que usa la config.
        fuente = registro.name.partition(":")[2].lower() or registro.name.lower()
        try:
            mensaje = registro.getMessage()
        except Exception:  # un formateo roto no puede tumbar una búsqueda
            return
        encontrado = _RE_CODIGO_HTTP.search(mensaje)
        codigo = int(encontrado.group(1)) if encontrado else None
        with self._candado:
            self.sucesos.append((fuente, codigo, mensaje[:300]))

    def bloqueos_por_fuente(self) -> dict[str, tuple[int | None, str]]:
        """Solo lo que es un «no» del portal, no un fallo de red nuestro."""
        from fuentes_extra.salud import RESPUESTAS_DE_BLOQUEO

        encontrados: dict[str, tuple[int | None, str]] = {}
        with self._candado:
            sucesos = list(self.sucesos)
        for fuente, codigo, mensaje in sucesos:
            es_bloqueo = codigo in RESPUESTAS_DE_BLOQUEO or "blocked by" in mensaje.lower()
            if es_bloqueo:
                encontrados.setdefault(fuente, (codigo, mensaje))
        return encontrados


@contextmanager
def vigilar_bloqueos_de_jobspy():
    """Escucha los logger de jobspy mientras dure el bloque `with`."""
    cazador = _CazadorDeBloqueos()
    # Los ocho logger ("JobSpy:LinkedIn", "JobSpy:Indeed", ...) ya existen al importar
    # jobspy, así que engancharse aquí los pilla todos (comprobado). jobspy crea además
    # uno en minúsculas por sitio durante el scraping, pero solo para un INFO de
    # «finished scraping», que este handler no escucha (nivel WARNING).
    enganchados = [
        registrado
        for nombre, registrado in list(logging.Logger.manager.loggerDict.items())
        if nombre.startswith("JobSpy") and isinstance(registrado, logging.Logger)
    ]
    for registrado in enganchados:
        registrado.addHandler(cazador)
    try:
        yield cazador
    finally:
        for registrado in enganchados:
            try:
                registrado.removeHandler(cazador)
            except Exception:
                pass


def _registrar_salud_de_jobspy(cazador: _CazadorDeBloqueos, df: pd.DataFrame | None,
                               sitios_consultados: set[str]) -> None:
    """
    Traduce lo cazado a la salud de las fuentes: una fuente que no ha traído NADA en
    toda la corrida y ha respondido con un bloqueo cuenta como bloqueada; si ha traído
    algo, el bloqueo era una ráfaga suelta y se olvida la racha.

    Se mira por corrida y no por petición a propósito: con 33 búsquedas por hora, un 429
    aislado entre 33 peticiones correctas no es un problema, y contarlo haría saltar el
    aviso constantemente.
    """
    from fuentes_extra import salud

    bloqueos = cazador.bloqueos_por_fuente()
    con_resultados = set()
    if df is not None and not df.empty and "site" in df.columns:
        con_resultados = {str(s).lower() for s in df["site"].dropna().unique()}

    for fuente in sorted(sitios_consultados):
        if fuente in con_resultados:
            salud.registrar_exito(fuente)
        elif fuente in bloqueos:
            codigo, detalle = bloqueos[fuente]
            salud.registrar_bloqueo(fuente, codigo, detalle)
        # 0 ofertas y sin bloqueo: puede ser que de verdad no haya nada nuevo. No se
        # anota nada; inventarse un bloqueo aquí daría avisos falsos a las 4 de la
        # mañana, cuando no se publica nada.

# Las fuentes propias tipan min_amount/max_amount como float64 explícito (ver
# fuentes_extra/eures.py y empresas.py), pero JobSpy no: su DataFrame los deja en
# "object" incluso cuando no hay ni un solo salario en la búsqueda (columna entera a
# None). Mezclar ese "object" todo-nulo con el "float64" de una fuente propia que sí
# trae algún salario es justo el caso que pandas avisa que va a dejar de tratar como
# antes (FutureWarning al concatenar). Se castea el DataFrame de JobSpy a esta misma
# columna en el único punto por el que pasan todas las búsquedas mixtas (una_busqueda),
# así que no hace falta repetirlo en cada sitio que junta resultados.
COLUMNAS_NUMERICAS = ["min_amount", "max_amount"]


def _tipar_numericas(df: pd.DataFrame) -> pd.DataFrame:
    """Fuerza float64 en COLUMNAS_NUMERICAS. astype basta (no hace falta to_numeric):
    JobSpy y las fuentes propias ya dejan esas columnas en None/números, nunca texto."""
    presentes = [c for c in COLUMNAS_NUMERICAS if c in df.columns]
    if not presentes:
        return df
    return df.astype({c: "float64" for c in presentes})


# ----------------------------------------------------------------------
# EVALUAR: lo que depende de la persona (MP2)
#
# Son dos funciones y no una porque en medio está lo único que se comparte: la descarga de
# descripciones. Lo barato decide qué merece la pena descargar y lo caro necesita el texto, así
# que MP3 pasa a cada perfil por `evaluar_barato`, descarga UNA vez la unión de lo que sobrevive
# y pasa a cada uno por `evaluar_caro`. Reciben la config efectiva del perfil y no saben de
# quién es.
#
# `urls_excluidas` y el dedupe se quedan fuera a propósito: lo primero es previo y por perfil, y
# lo segundo no depende de nadie.
# ----------------------------------------------------------------------
def evaluar_barato(df: pd.DataFrame, config: dict, avisar: Callable[[str], None] | None = None,
                   geo=None) -> pd.DataFrame:
    """Lo que no necesita la descripción: empresas, senior, ruido y distancias."""
    # Antes de descargar descripciones y de geocodificar: es lo más barato que hay.
    todo = filtrar_empresas_excluidas(df, config)

    if config.get("filtrar_senior", True):
        patron_senior = _compilar(config.get("patron_senior")) or SENIOR_EXCLUDE
        todo = todo[~todo["title"].fillna("").str.contains(patron_senior)]

    # Puestos de otras áreas: se descartan aquí, antes de geocodificar, para no gastar
    # una petición por segundo a Nominatim en ofertas que no interesan.
    todo = marcar_ruido(todo, config)
    if config.get("descartar_fuera_de_perfil", True):
        todo = todo[~todo["fuera_de_perfil"]]

    # Distancia real solo para el ámbito local; el resto queda sin distancia.
    # Va antes de las descripciones para no descargar las de ofertas que caen por
    # quedar fuera del radio.
    ambito_local = config.get("ambito_local", "barcelona_local")
    locales = todo[todo["ambito"] == ambito_local]
    resto = todo[todo["ambito"] != ambito_local].copy()
    if avisar and not locales.empty:
        avisar("Calculando distancias reales...")
    locales = calcular_distancias(locales, config, geo)
    if not locales.empty:
        locales = locales[
            locales["distancia_km"].isna()
            | (locales["distancia_km"] <= config["max_distancia_km"])
        ]
    # float64 explícito, no None a secas: asignar None a una columna nueva la crea de
    # dtype "object", y esa columna entera a null choca con la "distancia_km" float64
    # (con valores reales) que trae locales -> mismo FutureWarning que con min_amount/
    # max_amount, mismo motivo (ver _tipar_numericas): dos dtypes distintos para la
    # misma columna, uno de ellos todo-nulo.
    resto["distancia_km"] = None
    resto["distancia_km"] = resto["distancia_km"].astype("float64")
    # Los trozos vacíos se dejan fuera del concat: un DataFrame sin filas tiene todas
    # sus columnas a nulo, y concatenarlo hace que pandas avise (FutureWarning) porque
    # va a cambiar cómo deduce el tipo del resultado. Pasa siempre que se busca en un
    # solo ámbito, y con pandas 3 dejaría de ser un aviso para cambiar los dtypes.
    con_filas = [parte for parte in (locales, resto) if not parte.empty]
    if con_filas:
        todo = pd.concat(con_filas, ignore_index=True)
    else:
        todo = locales.reset_index(drop=True)  # vacío pero con las columnas correctas
    return todo


def evaluar_caro(df: pd.DataFrame, config: dict, puntuador=None,
                 avisar: Callable[[str], None] | None = None, geo=None) -> pd.DataFrame:
    """
    Lo que necesita la descripción, hasta la relevancia. `puntuador=None` es el del titular.
    """
    # Filtros que necesitan leer la descripción, así que van después de descargarla.
    # Si uno falla se salta y se sigue: perder por un filtro los resultados de una
    # búsqueda que ha costado varios minutos sería mucho peor que mostrar de más.
    def aplicar(nombre, funcion, datos):
        try:
            resultado = funcion(datos, config)
            return resultado, len(datos) - len(resultado)
        except Exception as e:
            if avisar:
                avisar(f"Aviso: el filtro '{nombre}' ha fallado y se omite: {e}")
            return datos, 0

    # El nivel va aquí y no antes: LinkedIn solo lo entrega junto con la descripción
    # de la oferta, así que antes de la fase 2 el campo viene vacío y no filtraría nada.
    #
    # EL IDIOMA VA EL ÚLTIMO A PROPÓSITO, y no es cosmético. Los cuatro filtros son
    # predicados fila a fila —ninguno mira otras filas ni modifica el DataFrame—, así que
    # el conjunto que sobrevive es el mismo en cualquier orden: comprobado sobre la base
    # real del Toshiba, las 24 permutaciones dejan exactamente las mismas 1.223 ofertas.
    # Lo que sí cambia es lo que cuesta llegar hasta él.
    #
    # Coste por oferta de cada filtro, medido dentro del contenedor sobre las 1.640
    # ofertas reales de la base:
    #
    #   nivel              0,05 ms      presencial lejos    0,30 ms
    #   fuera de la UE     5,41 ms      idioma             60,43 ms   <- langdetect
    #
    # El idioma cuesta 200 veces lo que la presencialidad, y la presencialidad es la que
    # más tumba con diferencia. Tenerlos al revés significaba detectar el idioma de
    # cientos de ofertas para que el filtro siguiente las tirase de todas formas. El
    # orden de aquí es el óptimo: barato y selectivo primero, caro al final.
    #
    # Medido en corridas reales del 15 de agosto de 2026, antes y después del cambio:
    #
    #   antes   308 ofertas -> nivel 8 · idioma 89 · fuera UE 2 · presenciales lejos 204
    #   después 322 ofertas -> nivel 6 · presenciales lejos 302 · fuera UE 0 · idioma 1
    #
    # Con el idioma el segundo, llegaban 300 ofertas al idioma y 211 al de la UE (~19,2 s
    # entre los cuatro); el último, llegan 14 a cada uno (~1,0 s). Son unos 18 s menos por
    # corrida, y cuadra con el reloj: la fase de filtros pasó de 24 s a 5 s. 24 corridas
    # al día, sin que cambie ni una oferta del resultado.
    todo = df
    n_con_descripcion = len(todo)
    todo, n_nivel = aplicar("nivel", filtrar_por_nivel, todo)
    # Junto al de nivel: es igual de barato si está apagado, y solo mira la descripción.
    todo, n_experiencia = aplicar("experiencia exigida", filtrar_por_experiencia_exigida, todo)
    todo, n_lejos = aplicar("presencial lejos",
                            lambda datos, cfg: filtrar_presenciales_lejos(datos, cfg, geo), todo)
    todo, n_fuera_ue = aplicar("fuera de la UE", filtrar_fuera_de_ue, todo)
    todo, n_idioma = aplicar("idioma", filtrar_por_idioma, todo)
    # Los recuentos son "cuántas tumbó ESTE filtro con lo que le llegó", así que al
    # reordenar la atribución se mueve entre ellos (presencialidad sube, idioma baja);
    # el total descartado y las supervivientes no cambian.
    resumen_filtros = (
        f"Descartadas por nivel: {n_nivel} · presenciales lejos: {n_lejos} · "
        f"fuera de la UE: {n_fuera_ue} · idioma: {n_idioma}"
    )
    if config.get("experiencia_estricta"):
        resumen_filtros += f" · experiencia: {n_experiencia}"
    # Al log además de a la barra de progreso: este desglose solo vivía en la barra, que
    # se borra en cuanto la búsqueda termina, así que no había forma de mirarlo después.
    # Es exactamente el número que dejó ver que el idioma se estaba evaluando de más.
    logger.info("%s (de %s ofertas con descripción)", resumen_filtros, n_con_descripcion)
    if avisar:
        avisar(resumen_filtros)

    if todo.empty:
        return todo

    # Marca de posible restricción geográfica (necesita la descripción ya descargada). Sobre
    # una copia: si ningún filtro quitó nada, `todo` aún es el DataFrame de quien llama, y con
    # varios perfiles ese es el mismo para todos.
    todo = todo.copy()
    texto = todo["title"].fillna("") + " " + todo["description"].fillna("")
    todo["posible_restriccion_geografica"] = texto.str.contains(
        RESTRICCION_GEO_TEXTO
    ) | todo["location"].fillna("").str.contains(RESTRICCION_GEO_UBICACION)

    return anotar_relevancia(todo, puntuador)


# ----------------------------------------------------------------------
# CONSULTAS: lo que se comparte antes de la descripción (MP3, D5)
#
# Una consulta es una petición real a los portales, y es de quien la pida: si dos perfiles buscan
# lo mismo se hace una vez y el resultado es de los dos. Así la carga sobre LinkedIn depende del
# presupuesto de consultas por corrida y no del número de personas, y eso es lo que evita los
# bloqueos: añadir perfiles alarga la rotación, no sube el ritmo.
#
# Cada consulta recuerda las etiquetas de cada perfil porque la misma búsqueda puede llamarse
# distinto para cada uno, y la categoría y el ámbito son suyos: `evaluar_barato` decide por el
# ámbito qué ofertas son locales, y en la fila compartida se pisarían.
# ----------------------------------------------------------------------
PRESUPUESTO_POR_DEFECTO = 33


@dataclass
class Consulta:
    clave: str
    busqueda: dict  # lo que devuelve construir_busquedas, del primer perfil que la pide
    perfiles: list = field(default_factory=list)  # chat_id, por orden de quien la pidió
    etiquetas: dict = field(default_factory=dict)  # {chat_id: {categoria, ambito, busqueda}}


def _clave_de_consulta(params: dict) -> str:
    """Mismo término, ubicación, remoto, radio y fuentes: la misma petición. `country_indeed`
    está porque también cambia la petición, aunque hoy valga lo mismo para todos."""
    termino = " ".join(str(params.get("search_term") or "").casefold().split())
    return "|".join(str(valor) for valor in (
        termino, params.get("location") or "", bool(params.get("is_remote")), params.get("distance"),
        ",".join(sorted(params.get("site_name") or [])), params.get("country_indeed") or ""))


def _etiquetas_de(busqueda: dict) -> dict:
    return {"categoria": busqueda["categoria"], "ambito": busqueda["ambito"],
            "busqueda": busqueda["nombre"]}


def planificar_consultas(perfiles_activos: list[dict], config: dict) -> list[Consulta]:
    """
    Las consultas de todos los perfiles, sin repetir, en el orden en que se las encuentra: con un
    solo perfil es la lista de construir_busquedas, tal cual. Cada perfil sale de su config
    efectiva, sea de reglas a mano o de catálogo (que ya trae sus términos recortados a
    `max_terminos_por_perfil`). Un perfil con la config rota no pide nada: lo avisa quien lo evalúa.
    """
    import perfiles  # import diferido: `python -m perfiles` no debe cargar jobspy

    consultas: dict[str, Consulta] = {}
    for perfil in perfiles_activos:
        chat = perfil.get("chat_id")
        try:
            efectiva = perfiles.config_efectiva(perfil, config)
        except ValueError:
            continue
        for busqueda in construir_busquedas(efectiva):
            clave = _clave_de_consulta(busqueda["params"])
            consulta = consultas.setdefault(clave, Consulta(clave, busqueda))
            if chat not in consulta.etiquetas:
                consulta.perfiles.append(chat)
                consulta.etiquetas[chat] = _etiquetas_de(busqueda)
    return list(consultas.values())


def elegir_consultas(plan: list[Consulta], ultimas: dict, tope: int) -> list[Consulta]:
    """
    Las `tope` que más tiempo llevan sin hacerse, las que no se han hecho nunca primero. A
    igualdad, el orden del plan: una corrida pone la misma marca a todas las suyas, así que con
    menos consultas que presupuesto siempre salen las mismas y en el mismo orden.
    """
    orden = sorted(range(len(plan)), key=lambda i: (ultimas.get(plan[i].clave) or "", i))
    return [plan[i] for i in orden[:max(0, tope)]]


def consultas_de_la_corrida(perfiles_activos: list[dict], config: dict,
                            ultimas: dict | None = None) -> tuple[list[Consulta], list[Consulta]]:
    """(el plan entero, las que entran en el presupuesto de esta corrida)."""
    import perfiles  # import diferido: ver planificar_consultas

    plan = planificar_consultas(perfiles_activos, config)
    ultimas = perfiles.ultimas_consultas() if ultimas is None else ultimas
    tope = int(config.get("max_busquedas_por_corrida") or PRESUPUESTO_POR_DEFECTO)
    return plan, elegir_consultas(plan, ultimas, tope)


def planificar_jooble(perfiles_activos: list[dict], config: dict,
                      ultimas: dict | None = None) -> tuple[list, list]:
    """
    (todos los términos de Jooble, los de la siguiente tanda). Ver «EL CUPO SE REPARTE» en
    fuentes_extra/jooble.py: la tanda son los que más tiempo llevan sin pedirse, y un término que
    comparten dos perfiles es una sola petición. Solo piden los perfiles con "jooble" en un
    ámbito que no sea remoto, que es donde esta fuente responde.
    """
    import perfiles  # import diferido: ver planificar_consultas

    terminos: dict[str, jooble_mod.TerminoJooble] = {}
    for perfil in perfiles_activos:
        try:
            efectiva = perfiles.config_efectiva(perfil, config)
        except ValueError:
            continue
        ambitos = efectiva.get("ambitos") or {}
        if not any("jooble" in (a.get("sitios") or []) and a.get("activo", True)
                   and not a.get("is_remote") for a in ambitos.values()):
            continue
        ubicacion = efectiva.get("jooble_ubicacion") or jooble_mod.UBICACION
        for palabra in efectiva.get("jooble_terminos") or jooble_mod.TERMINOS:
            normal = " ".join(str(palabra).casefold().split())
            clave = "jooble|%s|%s" % (normal, " ".join(str(ubicacion).casefold().split()))
            termino = terminos.setdefault(clave, jooble_mod.TerminoJooble(palabra, ubicacion, clave))
            if perfil.get("chat_id") not in termino.perfiles:
                termino.perfiles.append(perfil.get("chat_id"))
    todos = list(terminos.values())
    ultimas = ultimas or {}
    orden = sorted(range(len(todos)), key=lambda i: (ultimas.get(todos[i].clave) or "", i))
    return todos, [todos[i] for i in orden[:jooble_mod.PETICIONES_POR_DESCARGA]]


def periodo_de_rotacion(consultas_unicas: int, config: dict) -> float:
    """Horas que tarda una consulta en repetirse, con el presupuesto y la frecuencia de hoy."""
    tope = int(config.get("max_busquedas_por_corrida") or PRESUPUESTO_POR_DEFECTO)
    horas = float(config.get("busqueda_automatica_horas") or 1)
    return math.ceil(consultas_unicas / max(1, tope)) * horas


def _avisar_una_vez_al_dia(clave: str, fuente: str, motivo: str) -> None:
    """Va por la cola de salud, que llega solo al titular. Sin esto, un aviso por hora."""
    import perfiles  # import diferido: ver planificar_consultas
    from fuentes_extra import salud

    hoy = datetime.now().date().isoformat()
    if perfiles.meta_get(clave) != hoy:
        perfiles.meta_set(clave, hoy)
        salud.registrar_aviso_operacion(fuente, motivo)


def _revisar_rotacion(unicas: int, config: dict) -> None:
    """D5: si una consulta tarda en repetirse más de media ventana, se pueden perder ofertas."""
    limite = (config.get("hours_old") or 0) / 2
    periodo = periodo_de_rotacion(unicas, config)
    if limite and periodo > limite:
        _avisar_una_vez_al_dia(
            "aviso_rotacion", "consultas",
            "Cada consulta tarda %g h en repetirse (%d únicas, %s por corrida) y la mitad de la "
            "ventana son %g h: se pueden perder ofertas." % (
                periodo, unicas, config.get("max_busquedas_por_corrida") or PRESUPUESTO_POR_DEFECTO,
                limite))


# ----------------------------------------------------------------------
# LA CORRIDA: se comparte la descarga, nada más (MP3)
#
# 1. Las consultas del presupuesto, una tanda para todos.
# 2. Por perfil, DE UNO EN UNO para que la RAM no crezca con el número de perfiles: sus filas,
#    menos lo que él ha excluido, por su evaluar_barato.
# 3. Descripciones: una sola descarga para la unión de lo que ha sobrevivido.
# 4. Por perfil: su evaluar_caro, y al_evaluar, que es quien guarda.
#
# Fallar con un perfil no puede costarle la corrida a los demás, salvo con el titular, que va
# el primero y falla como ha fallado siempre: es quien lo tiene que ver.
# ----------------------------------------------------------------------
# Lo que depende del perfil o de cómo llegó a él. El resto de la fila es del portal, y en el paso
# 4 se toma de la unión: si cada perfil escribiera su versión de una misma oferta (dos consultas
# pueden traerla un poco distinta), se pisarían la fila compartida en cada corrida.
_COLUMNAS_DEL_PERFIL = ("categoria", "ambito", "busqueda", "distancia_km", "fuera_de_perfil",
                        "motivo_descarte")


@dataclass
class _EnCorrida:
    perfil: dict
    chat_id: int | None
    efectiva: dict
    puntuador: object
    posicion: str  # «2/3»: es lo que sale en el log en vez del chat_id (regla 3)


def puntuador_para(perfil: dict, config: dict):
    """
    El titular con `"config": "global"` sigue yendo por `puntuador=None`, el de siempre.

    Desde MP6, si el perfil tiene la capa aprendida activa, su puntuador va envuelto en
    `PuntuadorConCapa`. Sin capa (menos de 15 etiquetas, apagada o empezada de cero) devuelve
    exactamente lo de antes, `None` incluido: por eso la equivalencia y `medir.py`, que no pasa
    por ningún perfil, no pueden moverse por esto.
    """
    import aprendizaje  # import diferido: arrastra perfiles
    import perfiles  # import diferido: ver planificar_consultas

    base = None if perfil["datos"].get("config") == "global" else perfiles.puntuador_de(perfil, config)
    capa = aprendizaje.capa_de(perfil, config) if perfil.get("chat_id") is not None else None
    if capa is None:
        return base
    return PuntuadorConCapa(base if base is not None else _puntuador_del_titular(), capa)


def _perfil_fallido(posicion: str, que: str) -> None:
    logger.exception("Perfil %s: ha fallado %s; los demás siguen.", posicion, que)
    _avisar_una_vez_al_dia(
        f"aviso_perfil:{posicion}", f"perfil {posicion}",
        f"Ha fallado {que} y este perfil se queda sin lo de esta corrida. Mira el log.")


def _preparar_perfiles(activos: list[dict], config: dict) -> list[_EnCorrida]:
    import perfiles  # import diferido: ver planificar_consultas

    listos = []
    for i, perfil in enumerate(activos):
        posicion = f"{i + 1}/{len(activos)}"
        try:
            listos.append(_EnCorrida(perfil, perfil.get("chat_id"), perfiles.config_efectiva(perfil, config),
                                     puntuador_para(perfil, config), posicion))
        except ValueError:
            if not i:
                raise
            _perfil_fallido(posicion, "su configuración")
    return listos


def _aislado(p: _EnCorrida, primero: _EnCorrida, que: str, funcion: Callable):
    """Devuelve lo que devuelva `funcion`, o None si falla con un perfil que no es el primero."""
    import perfiles  # import diferido: ver planificar_consultas

    try:
        return funcion()
    except perfiles.BaseNoDisponible:
        raise
    except Exception:
        if p is primero:
            raise
        _perfil_fallido(p.posicion, que)
        return None


def _descargar(consultas: list[Consulta], terminos_jooble: list, config: dict,
               progreso: Callable | None) -> tuple[pd.DataFrame | None, dict[str, int]]:
    """
    Paso 1: lo que antes era la mitad de buscar(). Devuelve (crudo, {clave: filas}), con el crudo
    a None si no ha llegado nada.

    La columna `_consulta` dice de dónde salió cada fila: la posición de su consulta, o `-1 - i`
    si la trajo una fuente que no busca por término. Jooble y Tecnoempleo se descargan una vez en
    la primera consulta de la corrida que llega a ellas y sus ofertas caen ahí por casualidad, así
    que no pueden ser «de quien pidió esa consulta» (ver _duenos_de_corrida).
    """
    busquedas = [c.busqueda for c in consultas]
    total = len(busquedas)
    resultados = []
    filas_por_consulta = {c.clave: 0 for c in consultas}
    hechas = 0
    lock = Lock()

    # En dos fases: la búsqueda no pide descripciones (lento y muy repetido) y luego
    # se descargan solo las de las ofertas que sobreviven a los filtros.
    dos_fases = config.get("descripciones_dos_fases", True)
    pedir_descripcion_ya = config["linkedin_fetch_description"] and not dos_fases

    def una_busqueda(i: int, b: dict):
        """
        Ejecuta una búsqueda; devuelve su DataFrame o None si falla o va vacía.

        Las fuentes de FUENTES_PROPIAS no las soporta jobspy, así que se separan del
        resto de sitios y se consultan con su módulo de fuentes_extra. Cada una va en su
        propio try/except: un fallo en una no debe tirar los resultados de las demás ni
        los de indeed/linkedin de la misma búsqueda, y viceversa.
        """
        from fuentes_extra import salud

        sitios = list(b["params"].get("site_name") or [])
        # Se respeta la desactivación también para las fuentes de jobspy: hasta ahora
        # solo la miraban las de fuentes_extra, así que poner "desactivada": true a
        # mano para linkedin no servía de nada. Ahora es un interruptor de verdad.
        sitios_jobspy = [
            s for s in sitios if s not in FUENTES_PROPIAS and salud.esta_activa(s)
        ]

        partes = []
        origen: list[int] = []
        error = None

        if sitios_jobspy:
            try:
                df = scrape_jobs(
                    results_wanted=config["results_wanted"],
                    hours_old=config["hours_old"] or None,
                    verbose=0,
                    linkedin_fetch_description=pedir_descripcion_ya,
                    **{**b["params"], "site_name": sitios_jobspy},
                )
                if df is not None and not df.empty:
                    partes.append(_tipar_numericas(df))
                    origen += [i] * len(df)
            except Exception as e:
                error = str(e)

        for nombre_fuente, buscar_en_fuente in FUENTES_PROPIAS.items():
            if nombre_fuente not in sitios:
                continue
            try:
                df_extra = buscar_en_fuente(
                    termino=b["params"]["search_term"],
                    location=b["params"].get("location", ""),
                    hours_old=config["hours_old"] or None,
                    is_remote=b["params"].get("is_remote", False),
                )
                if df_extra is not None and not df_extra.empty:
                    partes.append(df_extra)
                    una_vez = nombre_fuente in FUENTES_DE_UNA_VEZ
                    origen += [-1 - i if una_vez else i] * len(df_extra)
            except Exception as e:
                # Cada módulo de fuentes_extra ya captura y loguea sus propios fallos
                # devolviendo un DataFrame vacío; este except es un cinturón extra.
                error = error or f"{nombre_fuente}: {e}"

        if not partes:
            return i, b, None, error
        df = pd.concat(partes, ignore_index=True)
        df["categoria"] = b["categoria"]
        df["ambito"] = b["ambito"]
        df["busqueda"] = b["nombre"]
        df["_consulta"] = origen
        return i, b, df, error

    # Las búsquedas son independientes entre sí y el tiempo se va esperando a la red,
    # así que se lanzan varias a la vez. El tope lo marca config para poder bajarlo
    # si LinkedIn empieza a devolver 429.
    obreros = max(1, int(config.get("busquedas_en_paralelo", 3)))
    # Todas las fuentes de jobspy que esta corrida va a consultar, para poder decir
    # después cuáles no han traído nada (ver _registrar_salud_de_jobspy).
    sitios_de_jobspy = {
        s for b in busquedas for s in (b["params"].get("site_name") or [])
        if s not in FUENTES_PROPIAS
    }
    # Fuentes con estado POR CORRIDA (canario de «responde pero sin ofertas»; Tecnoempleo,
    # además, descarga su feed una sola vez por corrida). Ver FUENTES_CON_CORRIDA.
    con_corrida = [
        (nombre, modulo) for nombre, modulo in FUENTES_CON_CORRIDA.items()
        if any(nombre in (b["params"].get("site_name") or []) for b in busquedas)
    ]
    for nombre, modulo in con_corrida:
        modulo.empezar_corrida()
        if nombre == "jooble":
            modulo.fijar_plan(terminos_jooble)
    with vigilar_bloqueos_de_jobspy() as cazador:
        with ThreadPoolExecutor(max_workers=obreros) as executor:
            futuros = [executor.submit(una_busqueda, i, b) for i, b in enumerate(busquedas)]
            for futuro in as_completed(futuros):
                i, b, df, error = futuro.result()
                with lock:
                    hechas += 1
                    if df is not None:
                        resultados.append(df)
                        filas_por_consulta[consultas[i].clave] = len(df)
                    if progreso:
                        if error:
                            progreso(f"Aviso: fallo en '{b['nombre']}': {error}", hechas, total)
                        else:
                            n = 0 if df is None else len(df)
                            progreso(f"{b['nombre']} → {n} ofertas", hechas, total)

    if progreso:
        progreso("Procesando resultados...", total, total)

    # La salud se anota SIEMPRE, también cuando no ha llegado ni una oferta: ese es
    # justo el caso en el que hay que enterarse de si es que no hay nada o es que
    # están bloqueando. Aislado en su propio try: que falle anotando la salud no puede
    # costar una búsqueda de varios minutos.
    crudo = pd.concat(resultados, ignore_index=True) if resultados else None
    try:
        _registrar_salud_de_jobspy(cazador, crudo, sitios_de_jobspy)
    except Exception:
        logger.exception("No se ha podido anotar la salud de las fuentes de jobspy")
    for nombre, modulo in con_corrida:
        try:
            resumen_fuente = modulo.cerrar_corrida()
            logger.info("%s en esta corrida: %s", nombre,
                        ", ".join("%s=%s" % kv for kv in resumen_fuente.items()))
        except Exception:
            logger.exception("No se ha podido cerrar la corrida de %s", nombre)

    if not resultados:
        bloqueadas = cazador.bloqueos_por_fuente()
        if bloqueadas:
            detalle = ", ".join(
                f"{f} (HTTP {c})" for f, (c, _) in sorted(bloqueadas.items())
            )
            logger.error(
                "Ninguna oferta y con respuestas de bloqueo: %s. NO es que no haya "
                "nada nuevo: las fuentes están rechazando las peticiones.", detalle,
            )
            if progreso:
                progreso(f"Fuentes bloqueadas: {detalle}", total, total)
    return crudo, filas_por_consulta


def _duenos_de_corrida(crudo: pd.DataFrame, consultas: list[Consulta], plan: list[Consulta]) -> dict:
    """
    {enlace: perfiles} de lo que trajeron las fuentes que no buscan por término.

    Lo de Jooble es de quien pidió el término (jooble.duenos_de_la_corrida). El feed de Tecnoempleo
    es de provincia entera, así que es de todos los que lo tienen en un ámbito en el que responde,
    tanto si esa consulta entra en esta corrida como si no: lo decide el plan, no el presupuesto.
    """
    de_corrida = crudo[crudo["_consulta"] < 0]
    if de_corrida.empty:
        return {}
    jooble = jooble_mod.duenos_de_la_corrida()
    tecnoempleo = set()
    for consulta in plan:
        params = consulta.busqueda["params"]
        if ("tecnoempleo" in (params.get("site_name") or []) and not params.get("is_remote")
                and "barcelona" in (params.get("location") or "").lower()):
            tecnoempleo.update(consulta.perfiles)
    duenos = {}
    sitios = de_corrida["site"] if "site" in de_corrida.columns else [None] * len(de_corrida)
    for url, sitio, origen in zip(de_corrida["job_url"], sitios, de_corrida["_consulta"]):
        if sitio == "tecnoempleo":
            duenos[url] = tecnoempleo
        else:
            duenos[url] = jooble.get(url) or set(consultas[-1 - origen].perfiles)
    return duenos


def _filas_del_perfil(p: _EnCorrida, crudo: pd.DataFrame, consultas: list[Consulta],
                      plan: list[Consulta], duenos: dict) -> pd.DataFrame:
    """Lo que le ha llegado a este perfil, con SUS etiquetas. Nada que no sea suyo pasa de aquí."""
    chat = p.chat_id
    origen = crudo["_consulta"]
    mascara = origen.isin([i for i, c in enumerate(consultas) if chat in c.etiquetas])
    de_corrida = origen < 0
    if de_corrida.any():
        mascara = mascara.copy()
        mascara[de_corrida] = [chat in duenos.get(url, ()) for url in crudo.loc[de_corrida, "job_url"]]
    filas = crudo[mascara]

    # Lo de Jooble y Tecnoempleo le llega con las etiquetas de una consulta que puede no ser suya:
    # entonces valen las de su primera consulta local, que es de donde le habría llegado.
    local = next((c.etiquetas[chat] for c in plan
                  if chat in c.etiquetas and not c.busqueda["params"].get("is_remote")), None)
    cambios = {}
    for o in filas["_consulta"].unique():
        consulta = consultas[o if o >= 0 else -1 - o]
        suyas = consulta.etiquetas.get(chat) or local
        if suyas is not None and suyas != _etiquetas_de(consulta.busqueda):
            cambios[o] = suyas
    if cambios:
        filas = filas.copy()
        for columna in ("categoria", "ambito", "busqueda"):
            filas[columna] = [cambios[o][columna] if o in cambios else actual
                              for o, actual in zip(filas["_consulta"], filas[columna])]
    return filas.drop(columns="_consulta")


def _lo_barato_del_perfil(p: _EnCorrida, filas: pd.DataFrame, excluidas: set[str],
                          avisar: Callable[[str], None], geo: Geocodificador) -> pd.DataFrame:
    """Paso 2: el deduplicado de siempre, lo que él descartó y sus filtros baratos."""
    # Deduplicado y exclusión de lo ya descartado por el usuario (comparando el enlace)
    todo = filas.drop_duplicates(subset="job_url", keep="first")
    # El deduplicado por (título, empresa, ubicación) solo se aplica a lo que TIENE
    # empresa. pandas considera iguales entre sí a dos celdas nulas, así que sin esta
    # salvedad dos ofertas DISTINTAS sin empleador —los volcados de los servicios
    # públicos de empleo que llegan por EURES no lo traen— con el mismo título y la
    # misma provincia se fusionarían en una, y la otra desaparecería sin dejar rastro.
    # Lo que no tiene empresa ya ha pasado por el deduplicado exacto de job_url de
    # arriba, que es el que identifica un anuncio de forma inequívoca.
    con_empresa = todo["company"].notna() & (todo["company"].astype(str).str.strip() != "")
    repetidas = todo[con_empresa].duplicated(
        subset=["title", "company", "location"], keep="first"
    )
    todo = todo.drop(index=repetidas[repetidas].index)
    if excluidas:
        todo = todo[~todo["job_url"].isin(excluidas)]
    return evaluar_barato(todo, p.efectiva, avisar, geo)


def _con_lo_compartido(df: pd.DataFrame, compartido: pd.DataFrame) -> pd.DataFrame:
    """Paso 4: las filas de un perfil con la descripción, el nivel y demás, de la unión."""
    if df.empty:
        return df
    df = df.copy()
    enlaces = df["job_url"]
    for columna in compartido.columns:
        if columna not in _COLUMNAS_DEL_PERFIL:
            df[columna] = compartido[columna].reindex(enlaces).to_numpy()
    return df


def _atender(en_corrida: list[_EnCorrida], consultas: list[Consulta], plan: list[Consulta],
             terminos_jooble: list, config: dict, al_evaluar: Callable,
             excluidas: Callable[[int | None], set[str]], progreso: Callable | None,
             descripciones_conocidas: dict[str, str] | None,
             al_descargar_descripciones: Callable[..., None] | None,
             competencia_conocida: dict[str, dict] | None,
             tras_consultar: Callable[[dict[str, int]], None] | None = None) -> None:
    primero = en_corrida[0]
    total = len(consultas)

    def avisar(mensaje: str) -> None:
        if progreso:
            progreso(mensaje, total, total)

    crudo, filas_por_consulta = _descargar(consultas, terminos_jooble, config, progreso)
    if tras_consultar is not None:
        tras_consultar(filas_por_consulta)

    if crudo is None:
        # También sin nada que guardar: la fusión apunta la marca de la última búsqueda.
        for p in en_corrida:
            _aislado(p, primero, "la corrida", lambda: al_evaluar(p.perfil, pd.DataFrame()))
        return

    geo = Geocodificador(tope=config.get("geocodificaciones_por_corrida"))
    duenos = _duenos_de_corrida(crudo, consultas, plan)
    vivos: list = []
    for p in en_corrida:
        df = _aislado(p, primero, "los filtros", lambda: _lo_barato_del_perfil(
            p, _filas_del_perfil(p, crudo, consultas, plan, duenos), excluidas(p.chat_id), avisar, geo))
        if df is not None:
            vivos.append((p, df))
    del crudo, duenos

    # Ahora sí: descripciones solo de lo que ha sobrevivido a todos los filtros. Con un solo perfil
    # la unión es él; con varios, gana la primera fila de cada enlace, y el titular va el primero.
    solo = len(en_corrida) == 1
    if solo:
        union = vivos[0][1] if vivos else pd.DataFrame()
    else:
        con_filas = [df.drop(columns=[c for c in _COLUMNAS_DEL_PERFIL if c in df.columns])
                     for _, df in vivos if not df.empty]
        union = (pd.concat(con_filas, ignore_index=True).drop_duplicates(subset="job_url", keep="first")
                 .reset_index(drop=True) if con_filas else pd.DataFrame())

    dos_fases = config.get("descripciones_dos_fases", True)
    if config["linkedin_fetch_description"] and dos_fases:
        if progreso:
            progreso("Descargando descripciones...", total, total)
        union = completar_descripciones(
            union, config, progreso, conocidas=descripciones_conocidas,
            competencia_conocida=competencia_conocida,
        )
        # Antes de los filtros que descartan: aquí es donde están TODAS las descripciones
        # descargadas, incluidas las de las ofertas que no van a sobrevivir. Fallar
        # guardándolas no puede costar una búsqueda de varios minutos, así que se aísla.
        if al_descargar_descripciones is not None and not union.empty:
            try:
                con_texto = {
                    url: texto
                    for url, texto in zip(union["job_url"], union.get("description", []))
                    if isinstance(url, str) and _texto(texto).strip()
                }
                competencia = {}
                for fila in union.to_dict(orient="records"):
                    url = fila.get("job_url")
                    if not isinstance(url, str) or url not in con_texto:
                        continue
                    campos = {
                        campo: fila[campo]
                        for campo in ("candidatos", "candidatos_tope", "publicada_en")
                        if fila.get(campo) is not None
                    }
                    if campos:
                        competencia[url] = campos
                al_descargar_descripciones(con_texto, competencia)
            except Exception:
                logger.exception("No se han podido guardar las descripciones descargadas")

    compartido = None if solo or union.empty else union.set_index("job_url")
    for i, (p, df) in enumerate(vivos):
        vivos[i] = None  # lo de este perfil no sigue en memoria mientras se atiende al siguiente

        def evaluar():
            if solo:
                filas = union
            else:
                filas = df if compartido is None else _con_lo_compartido(df, compartido)
            al_evaluar(p.perfil, evaluar_caro(filas, p.efectiva, p.puntuador, avisar, geo))

        _aislado(p, primero, "la evaluación", evaluar)


def buscar_para_perfiles(
    activos: list[dict],
    config: dict,
    al_evaluar: Callable[[dict, pd.DataFrame], None],
    excluidas: Callable[[int | None], set[str]],
    progreso: Callable[[str, int, int], None] | None = None,
    descripciones_conocidas: dict[str, str] | None = None,
    al_descargar_descripciones: Callable[..., None] | None = None,
    competencia_conocida: dict[str, dict] | None = None,
) -> None:
    """
    Una corrida para varios perfiles (`perfiles.activos()`, con el titular el primero). Lo que
    sale de cada uno se entrega a `al_evaluar(perfil, df)` de uno en uno y no se devuelve: juntar
    los de todos sería tener en memoria lo que los pasos de arriba evitan. `excluidas(chat_id)`
    da lo que ese perfil ha descartado. El resto, como en `buscar`.
    """
    import perfiles  # import diferido: ver planificar_consultas

    en_corrida = _preparar_perfiles(activos, config)
    if not en_corrida:
        return
    ultimas = perfiles.ultimas_consultas()
    plan, elegidas = consultas_de_la_corrida(activos, config, ultimas)
    todos_los_terminos, terminos = planificar_jooble(activos, config, ultimas)
    _revisar_rotacion(len(plan), config)

    def tras_consultar(filas_por_consulta: dict[str, int]) -> None:
        pedidos = jooble_mod.terminos_pedidos()
        fichas = [(c.clave, perfiles.a_json(c.busqueda["params"]), perfiles.a_json(c.perfiles))
                  for c in plan]
        fichas += [(t.clave, perfiles.a_json({"termino": t.palabra, "ubicacion": t.ubicacion}),
                    perfiles.a_json(t.perfiles)) for t in todos_los_terminos]
        # Esto llega con la descarga hecha: no poder apuntarlo cuesta repetir unas consultas en la
        # corrida siguiente, y tirar lo descargado costaría mucho más.
        try:
            perfiles.anotar_consultas(fichas, {**filas_por_consulta, **{clave: None for clave in pedidos}},
                                      datetime.now().isoformat(timespec="seconds"))
        except perfiles.BaseNoDisponible:
            raise
        except Exception:
            logger.exception("No se han podido apuntar las consultas de esta corrida")

    _atender(en_corrida, elegidas, plan, terminos, config, al_evaluar, excluidas, progreso,
             descripciones_conocidas, al_descargar_descripciones, competencia_conocida, tras_consultar)


# La corrida de arranque de un perfil nuevo (MP8): como mucho estas consultas, solo suyas.
CONSULTAS_DE_ARRANQUE = 8


def buscar_arranque(
    perfil: dict,
    config: dict,
    al_evaluar: Callable[[dict, pd.DataFrame], None],
    excluidas: Callable[[int | None], set[str]],
    tope: int = CONSULTAS_DE_ARRANQUE,
    progreso: Callable[[str, int, int], None] | None = None,
    descripciones_conocidas: dict[str, str] | None = None,
    al_descargar_descripciones: Callable[..., None] | None = None,
    competencia_conocida: dict[str, dict] | None = None,
) -> int:
    """
    La corrida de arranque de quien acaba de hacer la encuesta (D8, EVOLUCION-MULTIPERFIL.md §4.2, paso 14):
    SOLO sus consultas, como mucho `tope`, para que tenga algo con lo que calibrar y no espere a que la
    rotación llegue a las suyas. Devuelve cuántas consultas ha hecho.

    Qué comparte con `buscar_para_perfiles` y qué no:

    - Es la misma descarga, al mismo ritmo (`_atender`: tres a la vez, las mismas fuentes y salvaguardas).
    - Las consultas que elige son las suyas que más tiempo llevan sin hacerse; al ser nuevo, las que nunca
      se han hecho. Se apuntan como hechas, así que la rotación siguiente no las repite: lo que cuesta de
      más en los portales son ESTAS `tope` consultas, una vez por perfil que entra (el tope son 3
      perfiles), y no suben el ritmo de las corridas de después.
    - **Jooble no se pide** (`terminos_jooble=[]`): su cupo de 500 es del sistema y su reloj de 12 h lo
      lleva la corrida normal, que le dará a este perfil sus términos los primeros (nunca se han pedido).
    - `anotar_consultas` deja la tabla «como dice el plan» y borra lo que no está: por eso se le pasa el
      plan de TODOS los activos y no el de este perfil. Con solo el suyo borraría las filas de los demás y
      les haría perder la cuenta de cuándo se hizo cada consulta.
    """
    import perfiles  # import diferido: ver planificar_consultas

    en_corrida = _preparar_perfiles([perfil], config)
    if not en_corrida:
        return 0
    ultimas = perfiles.ultimas_consultas()
    propio = planificar_consultas([perfil], config)
    elegidas = elegir_consultas(propio, ultimas, tope)
    activos = perfiles.activos()
    if not any(p["chat_id"] == perfil["chat_id"] for p in activos):
        activos = activos + [perfil]
    plan_de_todos = planificar_consultas(activos, config)
    jooble_de_todos, _ = planificar_jooble(activos, config, ultimas)

    def tras_consultar(filas_por_consulta: dict[str, int]) -> None:
        fichas = [(c.clave, perfiles.a_json(c.busqueda["params"]), perfiles.a_json(c.perfiles))
                  for c in plan_de_todos]
        fichas += [(t.clave, perfiles.a_json({"termino": t.palabra, "ubicacion": t.ubicacion}),
                    perfiles.a_json(t.perfiles)) for t in jooble_de_todos]
        try:
            perfiles.anotar_consultas(fichas, filas_por_consulta, datetime.now().isoformat(timespec="seconds"))
        except perfiles.BaseNoDisponible:
            raise
        except Exception:
            logger.exception("No se han podido apuntar las consultas de la corrida de arranque")

    _atender(en_corrida, elegidas, propio, [], config, al_evaluar, excluidas, progreso,
             descripciones_conocidas, al_descargar_descripciones, competencia_conocida, tras_consultar)
    return len(elegidas)


def buscar(
    config: dict | None = None,
    urls_excluidas: set[str] | None = None,
    progreso: Callable[[str, int, int], None] | None = None,
    descripciones_conocidas: dict[str, str] | None = None,
    al_descargar_descripciones: Callable[..., None] | None = None,
    competencia_conocida: dict[str, dict] | None = None,
) -> pd.DataFrame:
    """
    Ejecuta todas las búsquedas activas y devuelve un DataFrame de ofertas.

    urls_excluidas: enlaces ya ocultados/aplicados que no deben volver a aparecer.
    progreso: callback(mensaje, hecho, total) para informar del avance a la web.
    descripciones_conocidas: {enlace: descripción} ya guardadas, para no volver a
        descargarlas en cada actualización.
    al_descargar_descripciones: callback (descripciones, competencia) con TODO lo que
        tiene descripción al terminar la fase 2, incluidas las ofertas que los filtros de
        después van a descartar. Sirve para que quien llama lo guarde y la corrida
        siguiente no tenga que volver a descargarlo (ver almacen.recordar_descripciones):
        el descarte se repite igual, pero sin pagar la petición otra vez.
    competencia_conocida: {enlace: campos del descubrimiento} ya guardados, para las
        ofertas cuya descripción se reutiliza y por tanto no se vuelve a pedir la página.
    """
    if config is None:
        import config as _config  # import diferido: ver el docstring de config.py

        config = _config.cargar_config()
    # La config efectiva del titular es esta misma con las claves nuevas a sus valores de hoy.
    # Se construye desde `config` y no desde perfiles.db: buscar() no ha dependido nunca de la
    # base, y quien llama ya la ha comprobado. Por lo mismo no pasa por el presupuesto, que vive
    # en la base: hace todas las búsquedas, como siempre. La corrida del sistema es
    # buscar_para_perfiles; esto queda para test.py y para las pruebas de equivalencia.
    import perfiles  # import diferido: `python -m perfiles` no debe cargar jobspy

    excluidas = urls_excluidas or set()
    perfil = perfiles.PERFIL_GLOBAL
    plan = planificar_consultas([perfil], config)
    _, terminos = planificar_jooble([perfil], config)
    salida: list[pd.DataFrame] = []
    _atender(_preparar_perfiles([perfil], config), plan, plan, terminos, config,
             lambda _, df: salida.append(df), lambda _: excluidas, progreso,
             descripciones_conocidas, al_descargar_descripciones, competencia_conocida)
    return salida[0]
