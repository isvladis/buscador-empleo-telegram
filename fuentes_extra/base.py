"""
Contrato compartido entre las fuentes propias (eures.py, infojobs.py, empresas.py):
mismas columnas del DataFrame, mismo User-Agent, y una función `peticion()` con el
registro de salud ya integrado.

Por qué existe: antes de este módulo, COLUMNAS/COLUMNAS_NUMERICAS/HEADERS/_vacio() y el
patrón de reintentos+backoff+salud vivían copiados en las tres fuentes. Esa duplicación
no era solo repetición: fue la causa de que infojobs.py se quedara sin conectar a
salud.py durante un tiempo sin que nadie lo notara (dos de las tres fuentes lo tenían,
la tercera no, y no había ningún sitio único donde verlo). Centralizar `peticion()` aquí,
con la comprobación de `esta_activa()` ya dentro, hace que una fuente nueva la herede
automáticamente aunque quien la escriba se olvide de mirar salud.py.

Lo que NO se centraliza a propósito, porque es una diferencia real entre fuentes:
TIMEOUT (varía por fuente), el ritmo entre peticiones de empresas.py (distinto por ATS,
ver ESPERA_POR_ATS en empresas.py), y el resto de HEADERS más allá del User-Agent (cada
fuente pide un Content-Type/Accept distinto según lo que espera su API).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import pandas as pd
import requests

from fuentes_extra import salud

logger = logging.getLogger(__name__)

# Idéntico en las tres fuentes (verificado por igualdad, no a ojo: ver INVESTIGACION.md).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Las 13 columnas del contrato: idénticas en las tres fuentes propias.
COLUMNAS = [
    "title", "company", "location", "job_url", "date_posted", "description",
    "job_type", "min_amount", "max_amount", "currency", "interval", "is_remote", "site",
]
# Ninguna fuente propia publica salario estructurado de forma fiable (o, como InfoJobs,
# solo a veces), así que min_amount/max_amount salen a menudo enteros a None. Sin dtype
# explícito, pandas los infiere como columna "object" todo-nula, que choca (FutureWarning)
# con el "float64" de una fuente que sí trae algún salario numérico al concatenar los
# DataFrames de una misma búsqueda (o con el de buscador._tipar_numericas).
COLUMNAS_NUMERICAS = ["min_amount", "max_amount"]

REINTENTOS = 3
ESPERA_BASE = 1.5  # segundos; se dobla en cada reintento (backoff simple)


def vacio() -> pd.DataFrame:
    """DataFrame vacío pero con las columnas y los dtypes del contrato, para que
    concatenarlo con resultados de otras fuentes no dispare un FutureWarning de pandas."""
    return pd.DataFrame(columns=COLUMNAS).astype({c: "float64" for c in COLUMNAS_NUMERICAS})


def peticion(
    fuente: str,
    hacer: Callable[[], requests.Response],
    *,
    reintentos: int = REINTENTOS,
    espera_base: float = ESPERA_BASE,
    ignorar_estados: frozenset[int] = frozenset(),
    es_bloqueo: Callable[[requests.Response], str | None] | None = None,
) -> requests.Response | None:
    """
    Ejecuta `hacer()` (una petición GET o POST concreta) con reintentos, backoff y el
    registro de salud ya integrado: comprueba `esta_activa()` antes de intentar nada,
    clasifica cada respuesta como bloqueo/éxito, y solo reintenta fallos de red de
    verdad (timeouts, DNS, conexión rota) — nunca un bloqueo, que es justo lo que
    salud.py existe para no insistir.

    `ignorar_estados` deja pasar códigos que un bloqueo no es y un error genérico
    tampoco: la fuente los interpreta ella misma sin que esta función los reintente ni
    los cuente como "fallo tras N intentos" (p.ej. el 404 "esta empresa no tiene tablón
    en este ATS" de empresas.py, que no tiene sentido reintentar).

    `es_bloqueo` reconoce un bloqueo que llega con 200: una página anti-bot en lugar de
    los resultados (InfoJobs, desde el 26-10-2026). Devuelve el motivo, o None si la
    página es buena. Se mira ANTES de anotar el éxito: si no, cada página anti-bot
    reiniciaría el contador de bloqueos seguidos y la fuente no se pararía nunca.

    Devuelve la Response si ha ido bien (o si su código está en `ignorar_estados`), o
    None si la fuente estaba desactivada, se ha bloqueado, o han fallado todos los
    intentos.
    """
    if not salud.esta_activa(fuente):
        if salud.anunciar_desactivada(fuente):
            logger.info("%s: fuente desactivada (%s); no se consulta.",
                        fuente, salud.motivo_desactivacion(fuente))
        return None

    ultimo_error = None
    for intento in range(reintentos):
        try:
            r = hacer()
            if r.status_code in salud.RESPUESTAS_DE_BLOQUEO:
                salud.registrar_bloqueo(fuente, r.status_code, r.text[:200])
                return None
            if r.status_code in ignorar_estados:
                return r
            r.raise_for_status()
            motivo = es_bloqueo(r) if es_bloqueo else None
            if motivo:
                salud.registrar_bloqueo(fuente, r.status_code, motivo)
                return None
            salud.registrar_exito(fuente)
            return r
        except requests.RequestException as e:
            # Un timeout o un corte de red no son un bloqueo: no cuentan para
            # desactivar la fuente (ver salud.py). Solo estos se reintentan con backoff.
            ultimo_error = e
            if intento < reintentos - 1:
                time.sleep(espera_base * (2**intento))
    logger.warning("%s: fallo tras %d intentos: %s", fuente, reintentos, ultimo_error)
    return None
