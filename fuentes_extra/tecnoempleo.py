"""
Tecnoempleo, el portal español solo de informática, leído por su FEED RSS de la provincia
de Barcelona. Añadido el 2026-10-26 por decisión explícita del usuario.

POR QUÉ ASÍ, Y NO DE OTRA FORMA (está todo en CAMBIOS-FASE4.md):

- Su `robots.txt` bloquea por nombre a los agentes de IA (ClaudeBot, Claude, anthropic-ai…)
  en TODO el sitio. Por eso Claude no ha abierto nunca tecnoempleo.com desde las sesiones
  de trabajo: la estructura del feed se tomó de una copia que guardó el usuario.
- El bloque `User-agent: *` incluye `Disallow: /alertas-empleo-rss.php`, que es la ruta de
  los 52 feeds provinciales. `robots.txt` es una instrucción para RASTREADORES (programas
  que recorren e indexan el sitio); ese Disallow tiene toda la pinta de evitar que los
  buscadores indexen 52 páginas casi duplicadas. Un lector que consulta UN feed al que el
  usuario está suscrito es el uso para el que existe un RSS, y por convención los lectores
  de feeds no aplican robots.txt a sus suscripciones. El usuario lo razonó así y decidió
  leer el feed con salvaguardas antes que leer las páginas HTML de resultados, que cargan
  más al servidor y son un uso menos previsto.

LAS SALVAGUARDAS (no son opcionales; si se quitan, la justificación de arriba no vale):

1. Un solo feed (Barcelona), una sola petición por corrida, y como mucho una cada
   `HORAS_ENTRE_DESCARGAS` horas aunque las corridas sean horarias.
2. User-Agent HONESTO, propio de este módulo. El común del proyecto (base.USER_AGENT) se
   hace pasar por Chrome; aquí no.
3. Al primer «no» del servidor (403, 429…) la fuente se desactiva y no se vuelve a
   consultar hasta que una persona lo decida (salud.BLOQUEOS_PARA_DESACTIVAR = 1).
4. Canario: si el feed responde pero sin ninguna oferta en dos descargas seguidas, aviso
   por Telegram (un feed de toda una provincia nunca está vacío: es un cambio de formato).
5. Apagada por defecto: solo se consulta si "tecnoempleo" está en los sitios del ámbito.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from threading import Lock

import pandas as pd
import requests
from bs4 import BeautifulSoup

from fuentes_extra import base
from fuentes_extra import salud

logger = logging.getLogger(__name__)

FUENTE = "tecnoempleo"
URL_FEED = "https://www.tecnoempleo.com/alertas-empleo-rss.php"
PARAMS_BARCELONA = {"pr": ",240,"}  # 240 = provincia de Barcelona en Tecnoempleo
HORAS_ENTRE_DESCARGAS = 3
TIMEOUT = 20
USER_AGENT_PERSONAL = (
    "buscador-empleo-personal/1.0 "
    "(uso privado y no comercial; lector RSS de un solo feed, como mucho cada 3 h)"
)

# ----------------------------------------------------------------------
# Estado por corrida. buscar() llama a buscar_tecnoempleo() una vez por término y ámbito,
# pero el feed es el mismo para todos: se descarga en la primera llamada de la corrida y
# las demás no hacen nada. Las búsquedas van en paralelo, así que todo lleva candado.
# ----------------------------------------------------------------------
_candado = Lock()
_CORRIDA = {"atendida": False, "respuestas": 0, "ofertas": 0}
_ULTIMA_DESCARGA = {"cuando": None}  # time.monotonic(); en memoria a propósito (ver abajo)


def empezar_corrida() -> None:
    with _candado:
        _CORRIDA.update(atendida=False, respuestas=0, ofertas=0)


def cerrar_corrida() -> dict:
    """Canario: con el feed respondiendo y cero ofertas, cuenta como corrida vacía. Si en
    esta corrida no tocaba descargar (por el intervalo mínimo), no se anota nada."""
    with _candado:
        resumen = dict(_CORRIDA)
    if resumen["respuestas"] == 0:
        return resumen
    if resumen["ofertas"] == 0:
        salud.registrar_corrida_vacia(FUENTE, "el feed respondió sin ninguna <item>")
    else:
        salud.registrar_corrida_con_resultados(FUENTE)
    return resumen


def _toca_descargar() -> bool:
    """El intervalo mínimo vive en memoria: tras reiniciar el contenedor se descarga una
    vez de más, y a cambio no hace falta escribir en disco en cada corrida."""
    ultima = _ULTIMA_DESCARGA["cuando"]
    return ultima is None or time.monotonic() - ultima >= HORAS_ENTRE_DESCARGAS * 3600


# ----------------------------------------------------------------------
# Lectura del feed (RSS 2.0)
# ----------------------------------------------------------------------
def _texto(elemento, nombre: str) -> str:
    """Texto de un hijo, aceptando cualquier espacio de nombres (dc:creator, etc.)."""
    for hijo in elemento:
        etiqueta = hijo.tag.rsplit("}", 1)[-1].lower()
        if etiqueta == nombre:
            return (hijo.text or "").strip()
    return ""


def _sin_html(fragmento: str) -> str:
    if not fragmento:
        return ""
    return BeautifulSoup(fragmento, "html.parser").get_text(" ", strip=True)


def _fecha(texto: str):
    if not texto:
        return None
    try:
        return parsedate_to_datetime(texto)
    except (TypeError, ValueError, IndexError):
        return None


# ----------------------------------------------------------------------
# Los campos de cada oferta (ajustado el 26-10 con la copia real del feed que guardó el
# usuario: 80 ofertas, del 16 al 23 de octubre).
#
# Tecnoempleo no usa campos estándar de RSS para el detalle: lo mete todo en
# <description> como «<b>Empresa:</b>&nbsp;Nextret<br /><b>Provincia:</b>…». Los diez que
# trae: Empresa, Provincia, Población, Descripción, Tecnologías, Tipo de Contrato, Salario,
# Experiencia, Funciones y (en la mitad) Formación mínima.
#
# OJO, «Provincia» NO es la provincia: es la modalidad. En la muestra: «100% En remoto»
# (49), «hibrido» (22), «Barcelona» (4), «Madrid» (3) y «presencial» (2). Sin traducirlo,
# todas llegarían como «Barcelona» y el filtro geográfico no tocaría ni las de Madrid.
# ----------------------------------------------------------------------
# Los dos puntos son obligatorios: en la copia real los 10 rótulos los llevan dentro de la
# negrita (80 de 80) y no hay más negritas. Sin ellos, cualquier «<b>Soporte</b>» de una
# descripción en otro formato se tomaría por rótulo y se perdería el resto del texto.
_RE_CAMPO = re.compile(r"<b>\s*([^<:]+?)\s*:\s*</b>", re.IGNORECASE)
_RE_SALARIO = re.compile(r"(\d[\d.]*)\s*€?\s*-\s*(\d[\d.]*)\s*€?\s*(bruto/año|bruto/mes)?", re.IGNORECASE)


def campos_de_la_descripcion(descripcion_html: str) -> dict[str, str]:
    """{"Empresa": "Nextret", "Provincia": "hibrido", ...}, ya sin HTML."""
    partes = _RE_CAMPO.split(descripcion_html or "")
    campos = {}
    for i in range(1, len(partes) - 1, 2):
        valor = re.sub(r"<br\s*/?>", "\n", partes[i + 1])
        valor = _sin_html(valor)
        # «Formación mínima: FP2/Grado Superior Descubre más: https://…» trae ese enlace
        # promocional pegado; se corta.
        valor = re.split(r"\s*Descubre m[aá]s\s*:", valor)[0]
        campos[partes[i].strip()] = valor.strip()
    return campos


def _modalidad_y_ubicacion(provincia: str, poblacion: str) -> tuple[str, bool | None, str]:
    """(modalidad, is_remote, ubicación) a partir del campo «Provincia», que es la modalidad."""
    p = (provincia or "").strip().lower()
    sitio = poblacion.strip() if poblacion and poblacion.strip() else ""
    if "remoto" in p or "teletrabajo" in p:
        return "Remoto", True, "Barcelona, Spain"
    if "hibrid" in p or "híbrid" in p:
        return "Híbrido", False, (f"{sitio}, Barcelona, Spain" if sitio else "Barcelona, Spain")
    if p in ("presencial", "barcelona", ""):
        return "Presencial", False, (f"{sitio}, Barcelona, Spain" if sitio else "Barcelona, Spain")
    # Cualquier otro valor es el nombre de otra provincia («Madrid»): presencial allí. El
    # filtro geográfico la descarta, como a cualquier presencial fuera del área.
    return "Presencial", False, f"{provincia.strip()}, Spain"


def _salario(texto: str) -> tuple[float | None, float | None, str | None]:
    m = _RE_SALARIO.search(texto or "")
    if not m:
        return None, None, None
    minimo, maximo = (float(x.replace(".", "")) for x in m.group(1, 2))
    intervalo = "monthly" if (m.group(3) or "").lower().endswith("mes") else "yearly"
    return minimo, maximo, intervalo


def parsear_feed(contenido: bytes) -> list[dict]:
    """
    Convierte el XML del feed en ofertas con el contrato de base.COLUMNAS.

    Pensado para no romper nunca la búsqueda: un XML ilegible se registra y devuelve una
    lista vacía, que el canario convierte en aviso si se repite.

    La descripción que se guarda reconstruye los campos en texto plano empezando por
    «Modalidad: …» y «Experiencia: …»: así buscador.modalidad() y
    extraer_anos_experiencia() los leen igual que en cualquier otra fuente, sin un caso
    especial para Tecnoempleo.
    """
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError as e:
        logger.warning("Tecnoempleo: el feed no es XML válido (%s)", e)
        return []
    ofertas = []
    for item in raiz.iter():
        if item.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        enlace = _texto(item, "link") or _texto(item, "guid")
        titulo = _texto(item, "title")
        if not enlace or not titulo:
            continue
        crudo = _texto(item, "description")
        campos = campos_de_la_descripcion(crudo)
        modalidad, remoto, ubicacion = _modalidad_y_ubicacion(
            campos.get("Provincia", ""), campos.get("Población", ""))
        minimo, maximo, intervalo = _salario(campos.get("Salario", ""))
        experiencia = campos.get("Experiencia", "")
        if experiencia and experiencia[0].isdigit():
            # «3 años», «3-5 años» -> «3 años de experiencia», que es la forma que
            # reconoce extraer_anos_experiencia(); «Más de 5 años» ya la reconoce tal cual.
            experiencia_texto = f"{experiencia} de experiencia"
        elif experiencia.lower().startswith("no requerida"):
            experiencia_texto = "sin experiencia"
        else:
            experiencia_texto = experiencia
        cabecera = [f"Modalidad: {modalidad}"]
        for clave, valor in (("Experiencia", experiencia_texto),
                             ("Tipo de contrato", campos.get("Tipo de Contrato", "")),
                             ("Salario", campos.get("Salario", "")),
                             ("Tecnologías", campos.get("Tecnologías", "")),
                             ("Formación mínima", campos.get("Formación mínima", ""))):
            if valor:
                cabecera.append(f"{clave}: {valor}")
        cuerpo = [campos.get("Descripción", ""), campos.get("Funciones", "")]
        descripcion = "\n".join(cabecera) + "\n\n" + "\n\n".join(c for c in cuerpo if c)
        if not campos:
            descripcion = _sin_html(crudo)  # formato desconocido: al menos el texto
        publicada = _fecha(_texto(item, "pubdate"))
        ofertas.append({
            "title": titulo,
            "company": campos.get("Empresa") or _texto(item, "creator") or None,
            "location": ubicacion,
            "job_url": enlace,
            "date_posted": publicada.date().isoformat() if publicada else None,
            "_publicada": publicada,
            "description": descripcion,
            "job_type": campos.get("Tipo de Contrato") or None,
            "min_amount": minimo, "max_amount": maximo,
            "currency": "EUR" if minimo is not None else None,
            "interval": intervalo,
            "is_remote": remoto,
            "site": FUENTE,
        })
    return ofertas


def _peticion() -> requests.Response | None:
    return base.peticion(FUENTE, lambda: requests.get(
        URL_FEED,
        params=PARAMS_BARCELONA,
        headers={"User-Agent": USER_AGENT_PERSONAL,
                 "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.1"},
        timeout=TIMEOUT,
    ))


def buscar_tecnoempleo(
    termino: str,
    location: str = "",
    hours_old: int | None = None,
    is_remote: bool = False,
) -> pd.DataFrame:
    """
    Mismo contrato que el resto de fuentes de fuentes_extra. El término se ignora a
    propósito: el feed es de toda la provincia y solo de informática, y lo que no encaje
    con el perfil lo quitan los filtros y la puntuación, como con cualquier otra fuente.
    """
    if not salud.esta_activa(FUENTE):
        if salud.anunciar_desactivada(FUENTE):
            logger.info("Tecnoempleo: fuente desactivada (%s); no se consulta.",
                        salud.motivo_desactivacion(FUENTE))
        return base.vacio()
    # Solo el ámbito de Barcelona: el feed es de esa provincia y en los ámbitos remotos
    # solo traería las mismas ofertas otra vez.
    if is_remote or "barcelona" not in (location or "").lower():
        return base.vacio()
    with _candado:
        if _CORRIDA["atendida"] or not _toca_descargar():
            return base.vacio()
        _CORRIDA["atendida"] = True
        _ULTIMA_DESCARGA["cuando"] = time.monotonic()

    r = _peticion()
    if r is None:
        return base.vacio()
    ofertas = parsear_feed(r.content)
    with _candado:
        _CORRIDA["respuestas"] += 1
        _CORRIDA["ofertas"] += len(ofertas)

    if hours_old:
        limite = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        ofertas = [o for o in ofertas
                   if o["_publicada"] is None or o["_publicada"].astimezone(timezone.utc) >= limite]
    for o in ofertas:
        o.pop("_publicada", None)
    logger.info("Tecnoempleo: %d oferta(s) en el feed de Barcelona dentro de la ventana.", len(ofertas))
    if not ofertas:
        return base.vacio()
    df = pd.DataFrame(ofertas, columns=base.COLUMNAS)
    return df.astype({c: "float64" for c in base.COLUMNAS_NUMERICAS})
