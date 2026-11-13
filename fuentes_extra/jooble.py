"""
Jooble, por su API oficial (es.jooble.org). Añadido el 2026-10-26 con la clave gratuita
que registró el usuario.

LO QUE SE COMPROBÓ ANTES DE ESCRIBIRLO (26-10, con 4 peticiones de prueba):

- La clave es de España: contra jooble.org contesta 403 «Access is available only for
  registered users»; contra es.jooble.org, 200 con JSON.
- Petición: POST https://es.jooble.org/api/<clave> con JSON {keywords, location,
  ResultOnPage, datecreatedfrom}. ResultOnPage=50 devuelve 50 (sin él, 30), y
  datecreatedfrom (AAAA-MM-DD) deja casi solo ofertas recientes: 47 de 50 desde esa
  fecha, 3 del día anterior (zona horaria). La documentación pública no se pudo leer
  (Cloudflare); esto es lo que respondió la API, no lo que dice un manual.
- Respuesta: {"totalCount": N, "jobs": [{title, location, snippet, salary, source, type,
  link, company, updated, id}]}. NO trae la descripción entera, solo un resumen de unos
  300 caracteres, y el salario como texto («de 30000 a 35000 €/año»).
- Lo que aporta: de 30 resultados de «helpdesk» en Barcelona, 13 avisarían y unas 5 no
  estaban en ninguna otra fuente (CAMBIOS-FASE4.md). Con solo el resumen, la puntuación
  sale generosa: no ve años de experiencia ni idiomas que la descripción entera sí diría.

EL LÍMITE DE 500 PETICIONES manda en todo el diseño. Jooble no dice si es total o por
periodo, así que se trata como TOTAL hasta que el usuario lo aclare con ellos:

1. Pocas peticiones: PETICIONES_POR_DESCARGA (4) una vez cada HORAS_ENTRE_DESCARGAS (12 h),
   solo en ámbitos que no sean remotos, una página de 50 por término. Son 8 al día: unos 60 días.
2. El contador vive en disco (jooble_uso.json, en data/), no en memoria: un reinicio del
   contenedor no puede gastar peticiones de más. Cuenta cada intento, reintentos incluidos,
   antes de enviarlo.
3. Se para sola al llegar a LIMITE - RESERVA, y avisa por Telegram al 80 % y al pararse.
   La reserva cubre las 4 peticiones de prueba del 26-10 y deja margen para comprobar a mano.
   Si Jooble sube el límite: JOBSPY_JOOBLE_LIMITE en el .env.

EL CUPO SE REPARTE ENTRE PERFILES (decidido por el titular el 30-10). Cada perfil trae sus
`jooble_terminos`, y el ritmo de arriba es el de TODO el sistema, no el de cada uno: cada tanda
pide los términos que más tiempo llevan sin pedirse, y si dos perfiles comparten un término (y
su ubicación) se pide una vez y las ofertas son de los dos. Hoy, con el titular y el adicional
con los mismos 4 términos, no cambia nada: 8 peticiones al día, y las reciben los dos. Con 8
términos distintos sale a unas 4 al día por perfil, y con 3 perfiles a 2-3. Un contador por
perfil se descartó: añade estado y no compartiría nada. Lo que decide qué se pide es
buscador.planificar_jooble, y aquí llega como plan con fijar_plan(). Sin plan (pruebas, `test.py`)
se usan TERMINOS y UBICACION, como siempre.

LA CLAVE va en la URL, así que puede acabar en un log sin darse cuenta: el mensaje de un
error de red y el de raise_for_status() incluyen la URL completa. Por eso _peticion() la
tacha antes de que base.peticion() pueda escribir nada. Se lee de JOBSPY_JOOBLE_KEY (el
.env del servidor); sin ella, la fuente no hace nada.
"""

from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import pandas as pd
import requests

from fuentes_extra import base
from fuentes_extra import salud
from reglas.titular_2026_10 import JOOBLE_TERMINOS, JOOBLE_UBICACION

logger = logging.getLogger(__name__)

FUENTE = "jooble"
URL_API = "https://es.jooble.org/api/"
VARIABLE_CLAVE = "JOBSPY_JOOBLE_KEY"
VARIABLE_LIMITE = "JOBSPY_JOOBLE_LIMITE"

# Pocos y amplios: cada término es una petición. El puntuador decide después qué encaja,
# como con el resto de fuentes. Son los de quien no diga los suyos: cada perfil lleva los suyos
# en `jooble_terminos` (ver la nota del cupo arriba).
TERMINOS = JOOBLE_TERMINOS
UBICACION = JOOBLE_UBICACION
PETICIONES_POR_DESCARGA = 4
RESULTADOS_POR_PAGINA = 50
HORAS_ENTRE_DESCARGAS = 12
LIMITE_POR_DEFECTO = 500
RESERVA = 20
AVISO_AL = 0.8
TIMEOUT = 30

DIR_BASE = Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent.parent)))
USO_PATH = DIR_BASE / "jooble_uso.json"


@dataclass
class TerminoJooble:
    """Una petición de la tanda y para quién es."""

    palabra: str
    ubicacion: str
    clave: str = ""
    perfiles: list = field(default_factory=list)


_candado = Lock()
_CORRIDA = {"atendida": False, "respuestas": 0, "ofertas": 0}
# Lo de cada corrida que NO va en _CORRIDA: esa se copia al resumen que sale en el log, y esto
# lleva chat_id (regla 3). `plan` a None es «sin plan»: los TERMINOS de siempre.
_REPARTO = {"plan": None, "duenos": {}, "pedidos": []}


def empezar_corrida() -> None:
    with _candado:
        _CORRIDA.update(atendida=False, respuestas=0, ofertas=0)
        _REPARTO.update(plan=None, duenos={}, pedidos=[])


def fijar_plan(plan: list[TerminoJooble]) -> None:
    """Qué se pide en esta corrida, si toca descargar. Después de empezar_corrida()."""
    with _candado:
        _REPARTO["plan"] = list(plan)


def duenos_de_la_corrida() -> dict[str, set]:
    """
    {enlace: perfiles} de lo traído en esta corrida. Una oferta es de quien pidió el término
    que la trajo, y no de quien lanzara la consulta en la que cayó la tanda: esa solo es la
    primera que llegó aquí. Si la traen dos términos, es de los perfiles de los dos.
    """
    with _candado:
        return {url: set(quienes) for url, quienes in _REPARTO["duenos"].items()}


def terminos_pedidos() -> list[str]:
    """Las claves de los términos que han llegado a responder en esta corrida."""
    with _candado:
        return list(_REPARTO["pedidos"])


def cerrar_corrida() -> dict:
    """Canario, igual que Tecnoempleo: la API respondió en toda la corrida y no trajo nada
    en ninguno de los términos. Si en esta corrida no tocaba descargar, no se anota."""
    with _candado:
        resumen = dict(_CORRIDA)
    resumen["peticiones_gastadas"] = _leer_uso()["peticiones"]
    if resumen["respuestas"] == 0:
        return resumen
    if resumen["ofertas"] == 0:
        salud.registrar_corrida_vacia(FUENTE, "la API respondió sin ninguna oferta en %d término/s"
                                      % resumen["respuestas"])
    else:
        salud.registrar_corrida_con_resultados(FUENTE)
    return resumen


# ----------------------------------------------------------------------
# El presupuesto de peticiones, en disco
# ----------------------------------------------------------------------
def limite() -> int:
    try:
        return int(os.environ.get(VARIABLE_LIMITE, "").strip() or LIMITE_POR_DEFECTO)
    except ValueError:
        return LIMITE_POR_DEFECTO


def _leer_uso() -> dict:
    try:
        datos = json.loads(USO_PATH.read_text(encoding="utf-8"))
        if isinstance(datos, dict):
            datos.setdefault("peticiones", 0)
            return datos
    except (OSError, json.JSONDecodeError):
        pass
    return {"peticiones": 0}


def _escribir_uso(datos: dict) -> None:
    tmp = USO_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USO_PATH)


def _gastar_una() -> bool:
    """Anota una petición ANTES de hacerla. False si ya no queda presupuesto.

    Si no se puede guardar el contador, no se hace la petición: gastar sin contar es
    justo lo que esto existe para evitar."""
    tope = limite() - RESERVA
    with _candado:
        datos = _leer_uso()
        if datos["peticiones"] >= tope:
            return False
        datos["peticiones"] += 1
        hoy = datetime.now(timezone.utc).date().isoformat()
        por_dia = datos.setdefault("por_dia", {})
        por_dia[hoy] = por_dia.get(hoy, 0) + 1
        for dia in sorted(por_dia)[:-60]:  # el historial no crece sin fin
            del por_dia[dia]
        avisos = []
        if datos["peticiones"] >= AVISO_AL * limite() and not datos.get("aviso_80_enviado"):
            datos["aviso_80_enviado"] = True
            avisos.append("Lleva %d de %d peticiones de la API (%d %%). Al llegar a %d se para "
                          "sola. Si Jooble te sube el límite, ponlo en %s en el .env."
                          % (datos["peticiones"], limite(), round(100 * datos["peticiones"] / limite()),
                             tope, VARIABLE_LIMITE))
        if datos["peticiones"] >= tope and not datos.get("aviso_tope_enviado"):
            datos["aviso_tope_enviado"] = True
            avisos.append("Ha gastado %d peticiones de %d y SE PARA (quedan %d de reserva). "
                          "Para seguir, pide a Jooble que suban el límite y ponlo en %s."
                          % (datos["peticiones"], limite(), limite() - datos["peticiones"],
                             VARIABLE_LIMITE))
        try:
            _escribir_uso(datos)
        except OSError:
            logger.exception("Jooble: no se puede guardar el contador de peticiones; no se pide nada")
            return False
    for motivo in avisos:
        salud.registrar_aviso_presupuesto(FUENTE, motivo)
    return True


def _toca_descargar() -> bool:
    ultima = _leer_uso().get("ultima_descarga")
    if not ultima:
        return True
    try:
        hace = datetime.now(timezone.utc) - datetime.fromisoformat(ultima)
    except ValueError:
        return True
    return hace >= timedelta(hours=HORAS_ENTRE_DESCARGAS)


def _anotar_descarga() -> None:
    with _candado:
        datos = _leer_uso()
        datos["ultima_descarga"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            _escribir_uso(datos)
        except OSError:
            logger.exception("Jooble: no se puede guardar la hora de la última descarga")


# ----------------------------------------------------------------------
# La respuesta
# ----------------------------------------------------------------------
_RE_NUMERO = re.compile(r"\d[\d.\s]*")


def _limpio(texto: str | None) -> str:
    sin_etiquetas = re.sub(r"<[^>]+>", " ", html.unescape(texto or ""))
    return re.sub(r"\s+", " ", sin_etiquetas).strip(" .…")


def _salario(texto: str | None) -> tuple[float | None, float | None, str | None]:
    """«de 30000 a 35000 €/año», «18000 €/año», «1300 €/mensuales», «12 €/hora»."""
    t = (texto or "").lower()
    numeros = []
    for m in _RE_NUMERO.findall(t):
        cifra = re.sub(r"[.\s]", "", m)
        if cifra:
            numeros.append(float(cifra))
    if not numeros:
        return None, None, None
    if "hora" in t:
        intervalo = "hourly"
    elif "mes" in t or "mensual" in t:
        intervalo = "monthly"
    else:
        intervalo = "yearly"
    return min(numeros), max(numeros), intervalo


def parsear_respuesta(datos: dict) -> list[dict]:
    """El JSON de la API -> ofertas con el contrato de base.COLUMNAS. Nunca lanza: una
    oferta rara se salta, no tumba las demás."""
    ofertas = []
    for j in (datos or {}).get("jobs") or []:
        try:
            titulo = _limpio(j.get("title"))
            enlace = (j.get("link") or "").strip()
            if not titulo or not enlace:
                continue
            ubicacion = _limpio(j.get("location"))
            minimo, maximo, intervalo = _salario(j.get("salary"))
            resumen = _limpio(j.get("snippet"))
            origen = _limpio(j.get("source"))
            # Se dice claro que es un resumen: quien lea la oferta en la web tiene que saber
            # que no ve el anuncio entero (y por qué no hay años de experiencia).
            descripcion = resumen + ("\n\n(Resumen de Jooble; anuncio original en %s.)" % origen
                                     if origen else "\n\n(Resumen de Jooble.)")
            actualizada = (j.get("updated") or "")[:10] or None
            ofertas.append({
                "title": titulo,
                "company": _limpio(j.get("company")) or None,
                "location": f"{ubicacion}, Spain" if ubicacion and "spain" not in ubicacion.lower() else (ubicacion or "Spain"),
                "job_url": enlace,
                "date_posted": actualizada,
                "description": descripcion,
                "job_type": _limpio(j.get("type")) or None,
                "min_amount": minimo, "max_amount": maximo,
                "currency": "EUR" if minimo is not None else None,
                "interval": intervalo,
                "is_remote": None,
                "site": FUENTE,
            })
        except Exception:
            logger.exception("Jooble: oferta que no se ha podido leer; se salta")
    return ofertas


def _peticion(clave: str, cuerpo: dict) -> requests.Response | None:
    url = URL_API + clave
    tachada = URL_API + "<clave>"

    def hacer() -> requests.Response:
        if not _gastar_una():
            raise _SinPresupuesto()
        try:
            r = requests.post(url, json=cuerpo, timeout=TIMEOUT,
                              headers={"Accept": "application/json"})
        except requests.RequestException as e:
            # El mensaje de requests lleva la URL, y con ella la clave.
            raise requests.RequestException(str(e).replace(clave, "<clave>")) from None
        # raise_for_status() compone «404 ... for url: <r.url>»: se tacha aquí.
        r.url = tachada
        return r

    return base.peticion(FUENTE, hacer)


class _SinPresupuesto(Exception):
    pass


def buscar_jooble(
    termino: str,
    location: str = "",
    hours_old: int | None = None,
    is_remote: bool = False,
) -> pd.DataFrame:
    """
    Mismo contrato que el resto de fuentes. El término del ámbito se ignora: se usan los del
    plan (o TERMINOS, sin plan), todos en la primera llamada de la corrida, para gastar lo
    justo. Sin plan solo atiende a Barcelona; con plan, a la ubicación de cada término.
    """
    plan = _REPARTO["plan"]
    if is_remote or (plan is None and "barcelona" not in (location or "").lower()):
        return base.vacio()
    clave = os.environ.get(VARIABLE_CLAVE, "").strip()
    if not clave:
        if salud.anunciar_desactivada(FUENTE):
            logger.info("Jooble: no hay clave (%s en el .env); no se consulta.", VARIABLE_CLAVE)
        return base.vacio()
    with _candado:
        if _CORRIDA["atendida"]:
            return base.vacio()
        _CORRIDA["atendida"] = True
    if not _toca_descargar():
        return base.vacio()

    desde = (datetime.now(timezone.utc) - timedelta(hours=hours_old or 48)).date().isoformat()
    ofertas, respuestas = [], 0
    terminos = plan if plan is not None else [TerminoJooble(p, UBICACION) for p in TERMINOS]
    for i, termino in enumerate(terminos):
        palabra = termino.palabra
        if i:
            time.sleep(random.uniform(1.5, 4.0))
        cuerpo = {"keywords": palabra, "location": termino.ubicacion,
                  "ResultOnPage": RESULTADOS_POR_PAGINA, "datecreatedfrom": desde}
        try:
            r = _peticion(clave, cuerpo)
        except _SinPresupuesto:
            if salud.anunciar_desactivada(FUENTE):
                logger.warning("Jooble: sin presupuesto de peticiones (%d de %d); no se consulta.",
                               _leer_uso()["peticiones"], limite())
            break
        if r is None:
            # Bloqueo (la fuente ya se ha desactivado y avisado) o fallo de red: no se
            # insiste con los términos que quedan.
            break
        respuestas += 1
        try:
            datos = r.json()
        except ValueError:
            logger.warning("Jooble: «%s» respondió sin JSON válido", palabra)
            continue
        nuevas = parsear_respuesta(datos)
        if datos.get("totalCount", 0) > len(nuevas) >= RESULTADOS_POR_PAGINA:
            logger.info("Jooble: «%s» tiene %s y solo se lee la primera página (%d)",
                        palabra, datos.get("totalCount"), len(nuevas))
        ofertas += nuevas
        with _candado:
            _REPARTO["pedidos"].append(termino.clave)
            for oferta in nuevas:
                _REPARTO["duenos"].setdefault(oferta["job_url"], set()).update(termino.perfiles)

    if respuestas:
        _anotar_descarga()
    with _candado:
        _CORRIDA["respuestas"] += respuestas
        _CORRIDA["ofertas"] += len(ofertas)
    logger.info("Jooble: %d oferta(s) en %d petición(es); %d de %d gastadas.",
                len(ofertas), respuestas, _leer_uso()["peticiones"], limite())
    if not ofertas:
        return base.vacio()
    df = pd.DataFrame(ofertas, columns=base.COLUMNAS).drop_duplicates("job_url")
    return df.astype({c: "float64" for c in base.COLUMNAS_NUMERICAS})
