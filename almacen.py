"""
Persistencia: base de ofertas acumulada y estado de cada perfil (favoritas / ocultas /
aplicadas). Por debajo, perfiles.py y `perfiles.db`; aquí vive la lógica de qué se guarda
y qué no. En todas las funciones `chat_id=None` significa el titular.

La clave de identidad de una oferta es SIEMPRE su enlace (job_url): es lo único
que identifica un anuncio de forma exacta entre búsquedas distintas.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock, RLock

import config as config_mod
import perfiles
from dedupe_difuso import (
    claves_bloque,
    fusionar_duplicados_difusos,
    normalizar_texto,
    son_duplicados,
)
from perfiles import CAMPOS_DEL_DESCUBRIMIENTO

logger = logging.getLogger(__name__)

# Ver la misma variable en buscador.py: sin JOBSPY_DATA_DIR, se comporta igual que
# siempre (los JSON junto al código); con ella, apunta al volumen montado en Docker.
DIR_BASE = Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent)))
# El caché de descripciones es `descripciones_vistas.db` desde el 11-11 (ver `ruta_cache`). Esta ruta es
# la del JSON de antes, que se importa una vez y se deja como está, y de ella sale el nombre del nuevo.
DESCRIPCIONES_PATH = DIR_BASE / "descripciones_vistas.json"
# Descripciones de ofertas ocultas hace tiempo, sacadas de la base para que no pesen en ella
# (ver podar_descripciones_ocultas). Comprimidas y fuera del camino caliente: solo se
# escriben cuando hay algo nuevo que podar.
PODADAS_PATH = DIR_BASE / "descripciones_podadas.json.gz"

# Cuántos días se conserva la descripción de una oferta que NO llegó a guardarse en la
# base. La ventana de búsqueda son 38 h: una oferta que lleva una semana sin volver a
# salir en ningún portal no va a reaparecer, así que pasado ese plazo la entrada solo
# ocupa espacio y tiempo de parseo.
DIAS_DESCRIPCIONES_VISTAS = 7

_lock = Lock()

# Protege el caché de descripciones: su única conexión se usa siempre con esto cogido. Reentrante
# porque una avería a mitad de una consulta cierra la conexión desde dentro.
_lock_cache = RLock()


def firma_ofertas(chat_id: int | None = None) -> str | None:
    """Identifica la versión actual de las ofertas del perfil. La usa app.py para saber si
    puede reutilizar el listado que ya tenía puntuado (ver /api/datos)."""
    return perfiles.firma_ofertas(perfiles.resolver(chat_id))


def _leer_json(path: Path, por_defecto):
    if not path.exists():
        return por_defecto
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return por_defecto


# Huella de lo último escrito en cada archivo, para no reescribir lo que no ha cambiado.
# El disco del Toshiba es un HDD de portátil que falló el 2026-10-21 (sectores dañados,
# sistema de ficheros en solo lectura): cada búsqueda horaria reescribía ofertas.json
# entero (13,8 MB) y estado.json aunque no hubiera nada nuevo. Ver README, «Escrituras
# en disco».
_HUELLAS: dict[Path, bytes] = {}


def _huella(contenido: bytes) -> bytes:
    return hashlib.blake2b(contenido, digest_size=16).digest()


def _escribir_json(path: Path, datos) -> None:
    # Escritura atómica: si el proceso muere a mitad, no se corrompe el archivo bueno.
    # default=str evita que un tipo inesperado del scraping (fechas, tipos de numpy...)
    # tire por la borda una búsqueda de varios minutos: se guarda como texto y ya está.
    contenido = json.dumps(datos, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    huella = _huella(contenido)
    if path not in _HUELLAS and path.exists():
        # Primera escritura de este proceso: la referencia es lo que ya hay en disco.
        # Leer cuesta mucho menos que escribir, y solo se hace una vez por archivo.
        try:
            _HUELLAS[path] = _huella(path.read_bytes())
        except OSError:
            pass
    if _HUELLAS.get(path) == huella and path.exists():
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(contenido)
    tmp.replace(path)
    _HUELLAS[path] = huella


# ----------------------------------------------------------------------
# ESTADO (favoritas / ocultas / aplicadas)
# ----------------------------------------------------------------------


def cargar_estado(chat_id: int | None = None) -> dict:
    chat = perfiles.resolver(chat_id)
    return {**perfiles.marcas(chat), "ultima_busqueda": perfiles.ultima_busqueda(chat)}


def urls_excluidas(chat_id: int | None = None) -> set[str]:
    """
    Enlaces que NO deben volver a aparecer en búsquedas nuevas: los que el usuario
    ocultó y los que ya aplicó. Las favoritas sí se conservan y se siguen mostrando.
    """
    return perfiles.urls_excluidas(perfiles.resolver(chat_id))


_CLAVE_DE_ACCION = {"favorito": "favoritas", "oculta": "ocultas", "aplicada": "aplicadas"}


def marcar(url: str, accion: str, datos_oferta: dict | None = None,
           chat_id: int | None = None) -> dict:
    """
    Alterna el estado de una oferta. Acciones: favorito | oculta | aplicada.

    Favorita significa «me interesa mucho pero, por lo que sea, no he aplicado (todavía
    o nunca)»: es un interés pendiente, no una decisión tomada. Aplicar u ocultar SÍ son
    decisiones tomadas, así que las tres marcas son mutuamente excluyentes: marcar una
    oferta como aplicada o como oculta la saca de favoritas, porque deja de estar
    pendiente — o se convirtió en candidatura enviada, o se descartó. Esto importa
    también para el modelo de relevancia (ver entrenar_modelo.py): cada oferta aporta
    como mucho una señal de entrenamiento, y la decisión tomada (aplicada=positiva fuerte,
    oculta=negativa) siempre pesa más que el interés pendiente (favorita=positiva débil),
    así que tiene que reemplazarla, no coexistir con ella.
    """
    if accion not in _CLAVE_DE_ACCION:
        raise ValueError(f"Acción desconocida: {accion}")
    chat = perfiles.resolver(chat_id)
    with _lock:
        perfiles.alternar_marca(chat, url, accion, datos_oferta)
        return cargar_estado(chat)


def fijar_marca(url: str, accion: str, datos_oferta: dict | None = None,
                extra: dict | None = None, chat_id: int | None = None) -> dict:
    """
    Deja la oferta EN ese estado, sin alternar (para los botones de Telegram, Fase 5).

    `marcar()` es un interruptor: pulsar dos veces deshace. En un móvil una doble
    pulsación es fácil, y que «Aplicada» se desmarque sola sería peor que no tener botón.
    Aquí pulsar dos veces deja lo mismo. Las tres marcas siguen siendo excluyentes, y
    además del todo: fijar una quita las otras dos (pasar de «me interesa» a «aplicada»
    es justo el uso esperado). `extra` añade campos al registro (origen, motivo).
    """
    if accion not in _CLAVE_DE_ACCION:
        raise ValueError(f"Acción desconocida: {accion}")
    chat = perfiles.resolver(chat_id)
    with _lock:
        perfiles.fijar_marca(chat, url, accion, datos_oferta, extra)
        return cargar_estado(chat)


def quitar_marcas(url: str, chat_id: int | None = None) -> dict:
    """La deja sin ninguna marca (el «Deshacer» de Telegram)."""
    chat = perfiles.resolver(chat_id)
    with _lock:
        perfiles.quitar_marcas(chat, url)
        return cargar_estado(chat)


# ----------------------------------------------------------------------
# DESCRIPCIONES YA DESCARGADAS QUE NO LLEGARON A LA BASE
#
# Los filtros que necesitan leer la descripción (nivel, idioma, fuera de la UE,
# presencial lejos) van DESPUÉS de descargarla, porque sin ella no pueden decidir. Lo que
# descartan no se guarda en la base, así que la corrida siguiente vuelve a descargarlo
# entero para volver a descartarlo.
#
# Medido en el Toshiba el 15 de agosto de 2026, dos corridas manuales seguidas: la
# primera descargó 300 descripciones y guardó 3 ofertas; la segunda, lanzada justo
# después, volvió a pedir 309. A una corrida por hora eso son unas 7.000 peticiones
# diarias a LinkedIn (~3,5 s cada una, en tandas de 4) para redescubrir que las mismas
# ofertas siguen siendo presenciales en Madrid.
#
# Guardar la descripción por enlace lo corta de raíz sin tocar ninguna decisión: los
# filtros se siguen aplicando enteros en cada corrida —así que cambiar un patrón desde el
# panel sigue surtiendo efecto— pero sobre texto que ya está en disco. Es la misma idea
# que descripciones_conocidas, que ya evita redescargar lo que sí está en la base;
# aquí se extiende a lo que se descartó.
#
# NO es un dato del usuario: es caché. Se puede borrar sin perder nada (solo hace que la
# corrida siguiente vaya lenta una vez), y por eso no entra en los backups de DESPLIEGUE.md.
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# DÓNDE VIVE EL CACHÉ: UN SQLITE APARTE, PREGUNTADO ENLACE A ENLACE (11-11-2026)
#
# Hasta el 11-11 era `descripciones_vistas.json`, entero en memoria durante toda la corrida, parseado
# otra vez dentro de `recordar_descripciones` y reescrito entero al acabar. MP1 lo dejó así a propósito
# (D1) cuando pesaba 5 MB y costaba +10 MiB. Con tres perfiles ya no es ese fichero. Medido el 11-11 con
# el del servidor (12,7 MB, 3.896 entradas; `REVISION-2026-11-11.md` §3):
#
# - **+136 MiB de pico de RSS por corrida** en el PC (de 92 a 229), 142 MiB de `tracemalloc`. No es el
#   tamaño del fichero, es cómo se lee: 393 descripciones llevan emoji, así que el texto del fichero entero
#   es UNA cadena de 4 bytes por letra (48 MiB), y se construye dos veces (cargar y recordar), más otra
#   del mismo tamaño para escribirlo. En el Toshiba eso es `VmHWM` 459 MiB y un pico del cgroup de
#   **498,1 MiB de los 512** con tres perfiles y ningún arranque de por medio (07-11: 423 y 454).
# - **12,7 MB escritos en cada corrida**, cambie una entrada o ninguna: ~305 MB al día en el disco que
#   ya falló, 60 veces lo que escribe `perfiles.db`.
# - Y de las 3.896 entradas una corrida solo consulta las de las ofertas que han salido en ella.
#
# Ahora cada enlace es una fila y la corrida pregunta solo por los que tiene delante. No cambia ninguna
# decisión: sale la misma descripción y los mismos campos del descubrimiento para el mismo enlace
# (`pruebas/test_cache.py` lo compara con el código de antes), y la poda sigue siendo de 7 días.
#
# **Fichero aparte y no una tabla de `perfiles.db`**, por tres razones: es caché y no entra en las copias
# diarias (las doblaría de tamaño); no toca el esquema de la base, así que volver al código de antes no
# pide nada; y si se estropea se aparta y se empieza vacío, sin que la base de nadie se entere.
#
# **Dos tablas** porque la fecha de «visto por última vez» cambia una vez al día en cada enlace que sigue
# saliendo, y con el texto en la misma fila eso reescribiría el texto entero: `vistas` son filas de ~100
# bytes y `textos` solo se escribe cuando una descripción entra o cambia.
#
# **El JSON de antes se importa una vez y no se toca** (regla 7): si hay que volver atrás, el código
# viejo lo encuentra donde estaba, con lo que tenía el día del cambio. Sin importarlo, la primera corrida
# volvería a pedir a LinkedIn todo lo descartado de la semana, unas 300 peticiones de golpe.
# ----------------------------------------------------------------------
_ESQUEMA_CACHE = (
    "CREATE TABLE IF NOT EXISTS vistas (url TEXT PRIMARY KEY, visto TEXT NOT NULL, extra TEXT) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS textos (url TEXT PRIMARY KEY, texto TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT) WITHOUT ROWID",
)
# Una conexión para todo el proceso, siempre bajo `_lock_cache`: el caché lo usa la corrida (una a la
# vez, por el trabajador) y nadie más, así que no hace falta una por hilo como en perfiles.py.
#
# `sin_servicio`: el caché ha fallado en esta corrida por algo que no se arregla apartándolo (el disco
# lleno, la carpeta sin permiso). Se deja de intentar hasta que la corrida acabe y lo cierre: una corrida
# pregunta por cientos de enlaces, y sin esto serían cientos de intentos y cientos de líneas en el log.
_cache: dict = {"conn": None, "ruta": None, "sin_servicio": False}


def ruta_cache() -> Path:
    """Junto al JSON de antes y con su nombre: quien mueve `DESCRIPCIONES_PATH` (las pruebas) mueve los dos."""
    return DESCRIPCIONES_PATH.with_suffix(".db")


def _soltar_cache() -> None:
    conn, _cache["conn"], _cache["ruta"] = _cache["conn"], None, None
    if conn is not None:
        with contextlib.suppress(sqlite3.Error):
            conn.close()


def cerrar_cache() -> None:
    """Suelta la conexión del caché y olvida una avería: la siguiente corrida lo vuelve a intentar."""
    with _lock_cache:
        _soltar_cache()
        _cache["sin_servicio"] = False


def _importar_json(conn: sqlite3.Connection) -> int:
    """
    El caché de antes, una sola vez. El fichero se lee y se deja como está.

    Es el único parseo entero que queda, y pasa una vez en la vida del caché: en la primera corrida
    tras el despliegue, con el proceso recién arrancado (~123 MiB), no encima del régimen.
    """
    try:
        datos = _leer_json(DESCRIPCIONES_PATH, {})
    except (ValueError, OSError, MemoryError) as e:
        # Un JSON que no se deja leer no puede impedir la corrida: se empieza sin él.
        logger.warning("No se ha podido importar %s (%s): el caché empieza vacío.", DESCRIPCIONES_PATH.name, e)
        datos = {}
    hoy = date.today().isoformat()
    entradas = 0
    if isinstance(datos, dict):
        for url, entrada in datos.items():
            if not url or not isinstance(entrada, dict):
                continue
            campos = {c: entrada[c] for c in CAMPOS_DEL_DESCUBRIMIENTO if entrada.get(c) is not None}
            if not entrada.get("d") and not campos:
                continue
            try:
                visto = date.fromisoformat(str(entrada.get("v"))[:10]).isoformat()
            except (TypeError, ValueError):
                visto = hoy  # sin fecha legible: se conserva y se le pone la de hoy, como hacía la poda
            conn.execute("INSERT OR REPLACE INTO vistas VALUES (?, ?, ?)",
                         (url, visto, perfiles.a_json(campos) if campos else None))
            if entrada.get("d"):
                conn.execute("INSERT OR REPLACE INTO textos VALUES (?, ?)", (url, str(entrada["d"])))
            entradas += 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('importado_del_json', ?)", (f"{hoy}: {entradas}",))
    return entradas


def _conectar_cache(destino: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(destino), timeout=30, isolation_level=None, check_same_thread=False)
    try:
        # WAL y NORMAL, como perfiles.db: una corrida escribe las páginas que cambian y nada más. Perder
        # la última transacción en un apagón solo cuesta volver a bajar esas descripciones.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sentencia in _ESQUEMA_CACHE:
                conn.execute(sentencia)
            if conn.execute("SELECT 1 FROM meta WHERE clave = 'importado_del_json'").fetchone() is None:
                importadas = _importar_json(conn)
                if importadas:
                    logger.info("Caché de descripciones: %d entradas importadas de %s, que se queda como estaba.",
                                importadas, DESCRIPCIONES_PATH.name)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    except BaseException:
        conn.close()
        raise
    return conn


def _apartar_cache(destino: Path, error: Exception) -> None:
    """Un caché ilegible se aparta (no se borra) y se empieza uno vacío: solo cuesta una corrida lenta."""
    _soltar_cache()
    apartado = destino.with_name(destino.name + ".roto-" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    try:
        os.replace(destino, apartado)
    except OSError:
        # Sigue ahí y sigue roto: no se insiste en esta corrida (la siguiente lo vuelve a intentar).
        _cache["sin_servicio"] = True
        logger.exception("El caché de descripciones está ilegible (%s) y no se ha podido apartar.", error)
        return
    for sufijo in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.replace(str(destino) + sufijo, str(apartado) + sufijo)
    logger.error("El caché de descripciones estaba ilegible (%s): apartado en %s y empezado de nuevo.",
                 error, apartado.name)


def _es_cache_roto(error: Exception) -> bool:
    texto = str(error).lower()
    return "malformed" in texto or "not a database" in texto or "corrupt" in texto


def _abrir_cache() -> sqlite3.Connection:
    """La conexión del caché, abriéndola (y creando o importando) si hace falta. Siempre bajo `_lock_cache`."""
    destino = ruta_cache()
    if _cache["conn"] is not None and _cache["ruta"] == destino:
        return _cache["conn"]
    if _cache["sin_servicio"]:
        raise sqlite3.OperationalError("el caché de descripciones no está disponible en esta corrida")
    _soltar_cache()
    try:
        conn = _conectar_cache(destino)
    except sqlite3.DatabaseError as e:
        if not _es_cache_roto(e):
            raise
        _apartar_cache(destino, e)
        conn = _conectar_cache(destino)
    _cache.update(conn=conn, ruta=destino)
    return conn


def _averia_del_cache(error: Exception) -> None:
    """
    El caché nunca tumba una corrida: si falla, es como si el enlace no estuviera (se vuelve a descargar),
    queda UNA línea en el log y, si el fichero está roto, se aparta y se empieza otro. Si no es eso (no
    se puede abrir, el disco está lleno), no se vuelve a intentar hasta la corrida siguiente.
    """
    if _cache["sin_servicio"]:
        return  # ya está dicho, y es la misma avería
    logger.warning("El caché de descripciones no responde (%s): se sigue sin él.", error)
    destino = _cache["ruta"] or ruta_cache()
    if _es_cache_roto(error):
        _apartar_cache(destino, error)
    else:
        _soltar_cache()
        _cache["sin_servicio"] = True


def _buscar_en_cache(memoria: dict, url) -> tuple[str | None, dict | None]:
    """(descripción, campos del descubrimiento) de un enlace; cada enlace se pregunta una vez por corrida."""
    if url in memoria:
        return memoria[url]
    par: tuple[str | None, dict | None] = (None, None)
    if isinstance(url, str) and url:
        with _lock_cache:
            try:
                fila = _abrir_cache().execute(
                    "SELECT t.texto, v.extra FROM vistas v LEFT JOIN textos t ON t.url = v.url WHERE v.url = ?",
                    (url,)).fetchone()
            except sqlite3.Error as e:
                _averia_del_cache(e)
                fila = None
        if fila:
            try:
                campos = json.loads(fila[1]) if fila[1] else None
            except ValueError:
                campos = None
            par = (fila[0] or None, campos or None)
        memoria[url] = par
    return par


class _VistaDelCache(Mapping):
    """
    Una de las dos caras del caché, **sin cargarlo**: `.get(enlace)` es una consulta por clave.

    Se comporta como el diccionario que era (`in`, `[]`, `.get`, y comparado con un `dict` da igual si
    tienen lo mismo), porque así lo usan el buscador y los espías de las pruebas. Recorrerlo entero
    (`dict(vista)`, `len`) sigue pudiéndose, pero ya no lo hace nadie en una corrida.

    Las dos vistas que devuelve `cargar_cache_vistas()` comparten `memoria`: una consulta por enlace sirve
    a las dos, y lo que responden no cambia a mitad de corrida aunque el caché se escriba entretanto.
    """

    def __init__(self, memoria: dict, indice: int):
        self._memoria, self._indice = memoria, indice

    def __getitem__(self, url):
        valor = _buscar_en_cache(self._memoria, url)[self._indice]
        if not valor:
            raise KeyError(url)
        return valor

    def _urls(self) -> list[str]:
        # Las mismas filas que encontraría `_buscar_en_cache`: recorrer la vista y preguntarle enlace a
        # enlace tienen que decir lo mismo.
        consulta = ("SELECT t.url FROM textos t JOIN vistas v ON v.url = t.url ORDER BY t.url",
                    "SELECT url FROM vistas WHERE extra IS NOT NULL ORDER BY url")
        with _lock_cache:
            try:
                return [u for (u,) in _abrir_cache().execute(consulta[self._indice])]
            except sqlite3.Error as e:
                _averia_del_cache(e)
                return []

    def __iter__(self):
        return iter(self._urls())

    def __len__(self) -> int:
        return len(self._urls())

    def __bool__(self) -> bool:
        # Verdadero sin contar filas: `conocidas or {}` y `if conocidas:` no pueden costar una consulta
        # a la tabla entera, y un caché vacío contesta «no lo tengo» igual de bien.
        return True

    def __repr__(self) -> str:
        return "<%s del caché de descripciones>" % ("descripciones", "campos del descubrimiento")[self._indice]


class Conocidas(Mapping):
    """
    Lo que ya se tiene descargado, mirando primero en la base y después en el caché, sin juntarlos.

    Antes era `{**en_cache, **en_base}`: un diccionario con las dos cosas, que obligaba a tener el caché
    entero en memoria. El orden es el de entonces: si un enlace está en los dos, manda la base.
    """

    def __init__(self, en_base: Mapping, en_cache: Mapping):
        self._en_base, self._en_cache = en_base, en_cache

    def __getitem__(self, url):
        valor = self._en_base.get(url)
        if valor:
            return valor
        return self._en_cache[url]

    def __iter__(self):
        yield from self._en_base
        yield from (u for u in self._en_cache if u not in self._en_base)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __bool__(self) -> bool:
        return True


def cargar_cache_vistas() -> tuple[Mapping, Mapping]:
    """
    Las descripciones y los campos del descubrimiento del caché, como dos vistas que preguntan por enlace.

    Devuelve ({enlace: descripción}, {enlace: campos del descubrimiento}). Hasta el 11-11 eran dos
    diccionarios con el caché entero (ver arriba lo que costaba); el nombre y la forma se conservan
    porque quien los usa solo hace `.get(enlace)`. Una entrada sin descripción no sirve de caché, pero
    su dato de competencia sigue valiendo, igual que antes.
    """
    with _lock_cache:
        try:
            # Se abre ya, y no en la primera pregunta: si toca importar el JSON de antes (una vez), que
            # sea al empezar la corrida y no a mitad, con las ofertas ya en memoria.
            _abrir_cache()
        except sqlite3.Error as e:
            _averia_del_cache(e)
    memoria: dict = {}
    return _VistaDelCache(memoria, 0), _VistaDelCache(memoria, 1)


def cargar_descripciones_vistas() -> Mapping:
    """{enlace: descripción} de ofertas ya descargadas que no están en la base."""
    return cargar_cache_vistas()[0]


def cargar_competencia_vista() -> Mapping:
    """
    {enlace: campos del descubrimiento} de ofertas ya descargadas que no están en la base.

    Viaja pegado a la descripción por una razón concreta: cuando una descripción se
    reutiliza del caché NO se pide la página, así que no hay de dónde sacar cuánta gente
    había aplicado. Y una oferta puede estar en el caché y entrar en la base más tarde —
    la misma URL la devuelven búsquedas de ámbitos distintos, y `filtrar_presenciales_lejos`
    no decide igual en todos—, con lo que sin esto entraría sin el dato para siempre.
    Pasó de verdad en la primera corrida tras desplegarlo.

    El valor es el del momento en que se descargó la página, que es justo lo que el campo
    quiere decir: cuánta competencia había cuando el sistema vio la oferta por primera vez.
    """
    return cargar_cache_vistas()[1]


def recordar_descripciones(
    descripciones: dict[str, str], competencia: dict[str, dict] | None = None
) -> int:
    """
    Añade descripciones al caché y poda las que llevan mucho sin verse.

    La fecha se refresca en cada corrida que vuelve a ver el enlace, así que la poda mide
    «cuánto lleva sin aparecer en ningún portal», no «cuándo se descargó por primera vez».
    Devuelve cuántas entradas quedan.

    CONTRATO CON QUIEN LLAMA: hay que pasarle también los enlaces que YA están en el
    caché, no solo los recién descargados. Es lo único que hace que la frase de arriba
    sea cierta — esta función solo puede refrescar la fecha de lo que recibe.

    No es una precaución teórica: hasta agosto de 2026 tanto app.py como test.py
    filtraban contra «base + caché» antes de llamar, así que a una entrada del caché no
    se le tocaba la fecha jamás y la poda medía justo lo contrario de lo que dice medir.
    Lo que sí conviene dejar fuera es lo que ya está en la base: eso ya está guardado, y
    duplicarlo aquí solo cuesta espacio.

    Desde el 11-11 escribe **solo lo que cambia**: la fila de un enlace nuevo, la fecha de uno que no
    se había visto hoy, y el texto únicamente si es otro. Una corrida que vuelve a ver lo mismo que la
    anterior no escribe nada; antes reescribía el fichero entero.
    """
    if not descripciones:
        return 0
    hoy = date.today()
    limite = (hoy - timedelta(days=DIAS_DESCRIPCIONES_VISTAS)).isoformat()
    hoy_iso = hoy.isoformat()
    competencia = competencia or {}
    with _lock_cache:
        try:
            conn = _abrir_cache()
            conn.execute("BEGIN IMMEDIATE")
            try:
                for url, texto in descripciones.items():
                    if not (url and texto):
                        continue
                    previa = conn.execute("SELECT visto, extra FROM vistas WHERE url = ?", (url,)).fetchone()
                    try:
                        campos = json.loads(previa[1]) if previa and previa[1] else {}
                    except ValueError:
                        campos = {}
                    # Los campos del descubrimiento se escriben una sola vez: si la entrada
                    # ya los tenía, el valor bueno es el viejo (ver cargar_competencia_vista).
                    for campo in CAMPOS_DEL_DESCUBRIMIENTO:
                        if campos.get(campo) is None:
                            valor = (competencia.get(url) or {}).get(campo)
                            if valor is not None:
                                campos[campo] = valor
                            else:
                                campos.pop(campo, None)
                    extra = perfiles.a_json(campos) if campos else None
                    if previa is None:
                        conn.execute("INSERT INTO vistas VALUES (?, ?, ?)", (url, hoy_iso, extra))
                    elif previa[0] != hoy_iso or previa[1] != extra:
                        conn.execute("UPDATE vistas SET visto = ?, extra = ? WHERE url = ?", (hoy_iso, extra, url))
                    # El texto solo se escribe si no estaba o es otro: comparar es leer, y leer no gasta disco.
                    conn.execute(
                        "INSERT INTO textos VALUES (?, ?) ON CONFLICT(url) DO UPDATE SET texto = excluded.texto"
                        " WHERE textos.texto IS NOT excluded.texto", (url, str(texto)))
                conn.execute("DELETE FROM textos WHERE url IN (SELECT url FROM vistas WHERE visto < ?)", (limite,))
                # Una entrada sin descripción no es caché de nada: se va con la poda, como antes.
                conn.execute("DELETE FROM vistas WHERE visto < ? OR url NOT IN (SELECT url FROM textos)", (limite,))
                total = conn.execute("SELECT COUNT(*) FROM textos").fetchone()[0]
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return total
        except sqlite3.Error as e:
            _averia_del_cache(e)
            return 0


# ----------------------------------------------------------------------
# BASE DE OFERTAS
# ----------------------------------------------------------------------


def cargar_ofertas(chat_id: int | None = None) -> list[dict]:
    """
    Las ofertas del perfil, en el orden de la lista. Cada llamada construye una lista nueva
    y se puede modificar sin miedo, pero parsea la base entera: quien solo necesite parte
    tiene iterar_ofertas(), ofertas_por_url(), ofertas_recientes() o descripciones_en_base().
    """
    return perfiles.ofertas_de(perfiles.resolver(chat_id))


def iterar_ofertas(chat_id: int | None = None):
    """Las mismas que cargar_ofertas(), pero de una en una: sin tenerlas todas en memoria."""
    return perfiles.iterar_ofertas(perfiles.resolver(chat_id))


def ofertas_por_url(urls, chat_id: int | None = None) -> dict[str, dict]:
    return perfiles.ofertas_por_url(perfiles.resolver(chat_id), urls)


def ofertas_recientes(dias: int, chat_id: int | None = None) -> list[dict]:
    """
    Las vistas en los últimos `dias`, sin descripción, con un día de margen: el corte fino
    lo hace quien las pide (app._indice_de_avisos_previos), aquí solo se evita leer el resto.
    """
    desde = (datetime.now() - timedelta(days=dias + 1)).isoformat(timespec="seconds")
    return perfiles.ofertas_recientes(perfiles.resolver(chat_id), desde)


def descripciones_en_base(chat_id: int | None = None) -> dict[str, str]:
    """{enlace: descripción} de las que la conservan: lo que no hay que volver a descargar."""
    return perfiles.descripciones_de(perfiles.resolver(chat_id))


def descripcion_de(url: str, chat_id: int | None = None) -> str | None:
    """El texto de una oferta del perfil, o None si el perfil no tiene esa oferta."""
    return perfiles.descripcion_de(perfiles.resolver(chat_id), url)


def guardar_ofertas(ofertas: list[dict], chat_id: int | None = None) -> None:
    """Deja al perfil exactamente estas ofertas. Lo que sobra se desenlaza, no se pisa a otros."""
    perfiles.reemplazar_ofertas(perfiles.resolver(chat_id), ofertas)


# ----------------------------------------------------------------------
# CATEGORÍAS SIEMPRE VISIBLES
#
# Excepción deliberada a la protección de republicaciones de más abajo (ver
# _es_republicacion): decisión explícita del usuario para el perfil de ciberseguridad
# junior (SOC/analista N1, ver config.categorias_siempre_visibles). Para esas ofertas,
# que una empresa la vuelva a publicar con URL nueva SÍ debe volver a aparecer y a
# notificarse, aunque la copia anterior esté oculta o aplicada — el usuario prefiere
# asumir el riesgo de que se le avise varias veces de la misma vacante a que una
# reaparición real quede enterrada para siempre por la decisión tomada sobre un
# anuncio anterior, en un segmento donde hay poca oferta y mucha rotación de anuncios.
#
# Acotado a propósito: NO toca urls_excluidas() (el enlace EXACTO ya visto se sigue sin
# volver a añadir nunca, ver el matiz al final de fusionar_ofertas) ni el dedupe difuso
# entre ofertas nuevas (dedupe_difuso.fusionar_duplicados_difusos), que sigue fusionando
# igual una misma vacante nueva publicada a la vez en varios portales. Solo cambia si
# una republicación se compara o no contra lo que YA está en ocultas/aplicadas — ver
# _indice_descartadas.
# ----------------------------------------------------------------------


def _patrones_siempre_visibles(config: dict | None = None) -> list[re.Pattern]:
    if config is None:
        config = config_mod.cargar_config()
    reglas = config.get("categorias_siempre_visibles") or []
    patrones = []
    for regla in reglas:
        if not isinstance(regla, dict) or not regla.get("activa", True):
            continue
        patron = regla.get("patron")
        if not patron:
            continue
        try:
            patrones.append(re.compile(patron, re.IGNORECASE))
        except re.error:
            continue  # patrón inválido escrito desde la web: se ignora, no rompe la búsqueda
    return patrones


def _es_categoria_siempre_visible(oferta: dict, patrones: list[re.Pattern]) -> bool:
    titulo = oferta.get("title") or ""
    return any(p.search(titulo) for p in patrones)


def es_categoria_siempre_visible(oferta: dict, config: dict | None = None) -> bool:
    """
    Versión pública, para quien no tiene los patrones ya compilados (app.py la usa al
    decidir si un aviso repetido se manda igualmente).
    """
    return _es_categoria_siempre_visible(oferta, _patrones_siempre_visibles(config))


def _indice_descartadas(
    previas: list[dict], excluidas: set[str], patrones_siempre_visibles: list[re.Pattern] = ()
) -> dict[str, list[dict]]:
    """
    Ofertas ya ocultadas o aplicadas, agrupadas por las claves de bloque del dedupe.

    Es el mismo bloqueo que usa dedupe_difuso para no comparar todo contra todo (empresa
    normalizada y título normalizado); sirve para reconocer una republicación sin
    recorrer la base entera por oferta.

    Las de una categoría "siempre visible" (ver arriba) quedan FUERA del índice a
    propósito: si no entran aquí, _es_republicacion nunca las encuentra, así que una
    republicación suya con URL nueva no se reconoce como tal y se guarda como oferta
    nueva de verdad, en vez de descartarse.
    """
    indice: dict[str, list[dict]] = {}
    for oferta in previas:
        if oferta.get("job_url") in excluidas and not _es_categoria_siempre_visible(
            oferta, patrones_siempre_visibles
        ):
            for clave in claves_bloque(oferta):
                indice.setdefault(clave, []).append(oferta)
    return indice


def _es_republicacion(oferta: dict, indice: dict[str, list[dict]]) -> bool:
    """
    ¿Esta oferta es la misma que una que el usuario ya ocultó o aplicó, republicada
    con otro enlace?

    urls_excluidas() solo reconoce el enlace EXACTO, y los portales republican el mismo
    anuncio con job_url nuevo cada pocos días (y uno por ciudad cuando la vacante es
    multi-sede). El dedupe difuso tampoco lo detecta, porque deja fuera de la
    comparación justo lo que está oculto o aplicado, que es con lo que habría que
    comparar. Medido sobre el histórico de la instalación original: casi un 5 % de las
    ocultaciones eran volver a descartar algo ya descartado; el caso peor, la misma
    oferta ocultada cinco veces.

    Se reutiliza son_duplicados() en vez de comparar títulos a mano para heredar sus
    salvaguardas: títulos que solo se diferencian en un número ("N1" vs "N2") o en el
    idioma pedido ("with Latvian" vs "with Lithuanian") NO se consideran la misma oferta.

    Verificado contra ese histórico: ninguna candidatura enviada ni ninguna favorita se
    habría perdido por este filtro.
    """
    vistas: set[int] = set()
    for clave in claves_bloque(oferta):
        for otra in indice.get(clave, ()):
            # Una misma oferta cae en los dos bloques cuando coinciden empresa y título;
            # compararla dos veces costaría el fuzz sobre la descripción por gusto.
            if id(otra) in vistas:
                continue
            vistas.add(id(otra))
            if son_duplicados(oferta, otra)[0]:
                return True
    return False


def _competencia_original(previa: dict, nueva: dict) -> dict:
    """Los campos del descubrimiento que ya tenía la oferta guardada y no hay que pisar."""
    return {
        campo: previa[campo]
        for campo in CAMPOS_DEL_DESCUBRIMIENTO
        if previa.get(campo) is not None and nueva.get(campo) is None
    }


def podar_descripciones_ocultas(chat_id: int) -> int:
    """
    Borra la descripción de las ofertas ocultas hace más de N días ("dias_descripcion_
    ocultas" en config.json, 30 por defecto; 0 la desactiva). Devuelve cuántas ha podado.

    Con varios perfiles (D1), solo si TODOS los que enlazan la oferta la tienen en ese
    estado: el texto se guarda una vez y es de todos. Ver perfiles.podables().

    Por qué: el 2026-10-24, 8,7 de los 13,8 MB de ofertas.json eran descripciones de
    ofertas ocultas o aplicadas, que entonces se reescribían enteras en cada búsqueda
    horaria sobre un disco que ya ha fallado. Solo se poda el TEXTO y solo de las ocultas: el
    registro se queda (deshacer el ocultado, detectar republicaciones por título y
    empresa), y las aplicadas y favoritas conservan siempre la descripción, porque son
    las que se vuelven a leer (una entrevista, una respuesta de la empresa).

    Nada se vuelve a descargar por esto: una oferta oculta ya no entra en ninguna
    búsqueda (urls_excluidas), así que su descripción no hace falta para el caché.

    Y nada se pierde: el texto podado se ARCHIVA en descripciones_podadas.json.gz antes
    de quitarlo. Los análisis del README (p. ej. «Lo que NO se filtra por experiencia
    exigida») se hicieron leyendo las descripciones de las ocultas, y tienen que poder
    repetirse cuando el historial crezca. El archivo solo se reescribe cuando hay algo
    nuevo que podar (unas pocas veces al mes, no en cada búsqueda), y comprimido ocupa
    una cuarta parte.
    """
    try:
        dias = int(config_mod.cargar_config().get("dias_descripcion_ocultas", 30))
    except (TypeError, ValueError):
        dias = 30
    if dias <= 0:
        return 0
    limite = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    podadas = perfiles.podables(chat_id, limite)
    if podadas:
        # Primero el archivo: si algo falla aquí, el texto sigue en la base y se reintenta
        # en la corrida siguiente. Y fuera de la transacción, porque comprimir 2,9 MB en el
        # Atom no puede tener a la base esperando.
        _archivar_podadas(podadas)
        perfiles.podar(chat_id, podadas)
    return len(podadas)


def _archivar_podadas(nuevas: dict[str, str]) -> None:
    """Añade descripciones al archivo comprimido (escritura atómica, como el resto)."""
    archivo = {}
    if PODADAS_PATH.exists():
        try:
            archivo = json.loads(gzip.decompress(PODADAS_PATH.read_bytes()).decode("utf-8"))
        except (OSError, ValueError):
            # Un archivo ilegible no puede impedir guardar la búsqueda; se conserva
            # aparte para poder mirarlo y se empieza uno nuevo.
            PODADAS_PATH.replace(PODADAS_PATH.with_suffix(".gz.ilegible"))
            archivo = {}
    archivo.update(nuevas)
    tmp = PODADAS_PATH.with_suffix(".gz.tmp")
    tmp.write_bytes(gzip.compress(json.dumps(archivo, ensure_ascii=False).encode("utf-8")))
    tmp.replace(PODADAS_PATH)


def fusionar_ofertas(nuevas: list[dict], reemplazar: bool = False, chat_id: int | None = None,
                     config: dict | None = None, ahora: str | None = None) -> dict:
    """
    Incorpora las ofertas nuevas a la base existente.

    Un enlace que el usuario ocultó o aplicó NUNCA se vuelve a añadir desde una
    búsqueda nueva. Si ya estaba en la base se conserva (para poder deshacer el
    ocultado y para consultar las aplicadas bajo su filtro), pero la interfaz no
    lo muestra en el listado normal.

    Las ofertas ya conocidas se actualizan (por si cambió el salario o la
    descripción) sin perder su antigüedad; las favoritas, las ocultas y las
    aplicadas se conservan siempre, incluso con reemplazar=True.

    Después del dedupe exacto por job_url, se aplica un dedupe DIFUSO adicional
    (dedupe_difuso.py) para juntar la misma oferta publicada en portales distintos con
    URLs distintas (p.ej. la misma vacante en indeed y en linkedin). Las ofertas en
    favoritas/ocultas/aplicadas quedan fuera de ese paso: nunca se fusionan ni se
    descartan, son decisiones del usuario y no le corresponde tocarlas a este proceso.

    Excepción: una REPUBLICACIÓN (mismo título+empresa, URL nueva) de algo ya oculto o
    aplicado tampoco se añade — salvo que la oferta caiga en una categoría "siempre
    visible" (ver config.categorias_siempre_visibles y CATEGORÍAS SIEMPRE VISIBLES más
    abajo), en cuyo caso sí se guarda como oferta nueva y puede volver a notificarse.

    NO SE CARGA LA BASE ENTERA (MP1): solo las ofertas que entran, las desmarcadas desde la
    última fusión y las candidatas de sus bloques del dedupe. Da los mismos grupos que
    comparar todas, por esto: si las candidatas ya no tienen duplicados entre sí (punto
    fijo, medido el 30-10: 0 grupos en las 98 reales), cualquier duplicado nuevo tiene que
    tocar a una oferta que acaba de cambiar, y los duplicados solo se buscan dentro de un
    bloque. Lo que podría romper ese punto de partida fuerza una pasada completa con
    `dedupe_pendiente`: la migración, un guardar_ofertas() que cambie registros y un
    cambio de VERSION_BLOQUES. pruebas/test_perfiles.py lo compara contra la versión JSON.

    Con varios perfiles, el dedupe de uno quita SU enlace, nunca la oferta (D1): la fila
    compartida solo se borra si nadie más la enlaza (perfiles._quitar_enlace).

    `config` es la efectiva del perfil: las categorías siempre visibles son de quien las tenga en
    su juego, y sin ella se leerían las del titular. `ahora` es la marca de la corrida, la misma
    para todos: con una por perfil, cada uno volvería a escribir el `visto_ultima` de la fila
    compartida que acaba de escribir el anterior, y eso es disco por nada.
    """
    chat = perfiles.resolver(chat_id)
    with _lock:
        estado = cargar_estado(chat)
        excluidas = set(estado["ocultas"]) | set(estado["aplicadas"])
        conservar = set(estado["favoritas"]) | excluidas
        patrones_siempre_visibles = _patrones_siempre_visibles(config)

        # La lista del perfil sin el contenido de las ofertas: quién está y en qué sitio.
        posicion = perfiles.posiciones(chat)
        if reemplazar:
            # Aunque se reemplace todo, no se tira lo que el usuario ya ha decidido
            conocidas = {u for u in posicion if u in conservar}
        else:
            conocidas = set(posicion)

        entrantes = [o for o in nuevas if o.get("job_url") and o["job_url"] not in excluidas]
        por_url = perfiles.ofertas_por_url(chat, {o["job_url"] for o in entrantes} & conocidas)
        ahora = ahora or datetime.now().isoformat(timespec="seconds")
        añadidas = 0
        republicadas = 0
        urls_nuevas: dict[str, int] = {}  # en orden de llegada, que es su orden en la lista
        descartadas = _indice_descartadas(
            perfiles.ofertas_en_bloques(chat, {c for o in entrantes if o["job_url"] not in conocidas
                                               for c in claves_bloque(o)}),
            excluidas, patrones_siempre_visibles,
        )

        for oferta in entrantes:
            url = oferta["job_url"]
            if url in por_url:
                visto = por_url[url].get("visto_por_primera_vez", ahora)
                # visto_por_ultima_vez: la última corrida en la que el portal seguía
                # publicando este anuncio. No lo había, y hace falta para saber si una
                # oferta sigue viva o es un resto de hace semanas: con `hours_old` a 38 h,
                # un anuncio que deja de aparecer está retirado o enterrado en el ranking.
                por_url[url] = {**oferta, "visto_por_primera_vez": visto,
                                "visto_por_ultima_vez": ahora,
                                **_competencia_original(por_url[url], oferta)}
            else:
                # Solo se comprueba en las que llegan por primera vez: una oferta que ya
                # está en la base es una actualización de algo que el usuario ya vio.
                if _es_republicacion(oferta, descartadas):
                    republicadas += 1
                    continue
                por_url[url] = {**oferta, "visto_por_primera_vez": ahora,
                                "visto_por_ultima_vez": ahora}
                añadidas += 1
                urls_nuevas[url] = len(urls_nuevas)

        # Quién ha cambiado de tramo desde la última fusión. Se mira contra `protegida`, no
        # contra las marcas de la anterior: lo que hay guardado es dónde está en la lista.
        def por_orden(urls):
            return sorted(urls, key=lambda u: posicion[u][1])

        a_protegidas = por_orden(u for u in conocidas if not posicion[u][0] and u in conservar)
        a_candidatas = [] if reemplazar else por_orden(
            u for u, (protegida, _) in posicion.items() if protegida and u not in conservar)

        # El dedupe difuso nunca toca lo que ya está en favoritas/ocultas/aplicadas:
        # se separan primero y se reincorporan tal cual, sin pasar por la comparación.
        afectadas = {**perfiles.ofertas_por_url(chat, a_candidatas),
                     **{u: o for u, o in por_url.items() if u not in conservar}}
        pasada_completa = perfiles.dedupe_pendiente(chat)
        if reemplazar:
            universo = afectadas
        elif pasada_completa:
            universo = {**perfiles.ofertas_por_url(chat, (u for u in conocidas if u not in conservar)),
                        **afectadas}
        else:
            vecinas = perfiles.ofertas_en_bloques(
                chat, {c for o in afectadas.values() for c in claves_bloque(o)})
            universo = {**{o["job_url"]: o for o in vecinas if o["job_url"] not in conservar},
                        **afectadas}

        # En el orden de la lista: es lo que decide un empate entre dos duplicados. Las
        # desmarcadas venían del tramo protegido, que va delante, y las nuevas al final.
        def lugar(oferta):
            url = oferta["job_url"]
            if url in urls_nuevas:
                return (2, urls_nuevas[url])
            return (0 if posicion[url][0] else 1, posicion[url][1])

        candidatas = sorted(universo.values(), key=lugar)
        originales = {id(o) for o in candidatas}
        supervivientes, fusiones = fusionar_duplicados_difusos(
            candidatas, campos_a_rescatar=CAMPOS_DEL_DESCUBRIMIENTO
        )

        # Lo que hay que escribir, y dónde queda cada una en la lista. La cuenta reproduce
        # `protegidas + candidatas`: lo que pasa a protegida va al final de su tramo, lo que
        # sale de él al principio del de candidatas, y las nuevas y las fusionadas al final.
        tramos: dict[int, list[int]] = {0: [], 1: []}
        for protegida, orden in posicion.values():
            tramos[protegida].append(orden)
        siguiente_protegida = max(tramos[1], default=-1) + 1
        primera_candidata = min(tramos[0], default=0)
        siguiente_candidata = max(tramos[0], default=-1) + 1

        sitio: dict[str, tuple[int, int]] = {}
        for url in a_protegidas + [u for u in urls_nuevas if u in conservar]:
            sitio[url] = (1, siguiente_protegida)
            siguiente_protegida += 1
        for i, url in enumerate(a_candidatas):
            sitio[url] = (0, primera_candidata - len(a_candidatas) + i)

        escribir = [(o, *(sitio.get(u) or posicion[u])) for u, o in por_url.items() if u in conservar]
        vivas = set()
        for oferta in supervivientes:
            url = oferta["job_url"]
            vivas.add(url)
            if url in urls_nuevas or id(oferta) not in originales:
                # id distinto: es la copia que devuelve el dedupe para la que sobrevive a una
                # fusión, y en la lista esa va al final.
                sitio.pop(url, None)
                escribir.append((oferta, 0, siguiente_candidata))
                siguiente_candidata += 1
            elif url in por_url:
                escribir.append((oferta, *sitio.pop(url, posicion[url])))

        quitar = {u for u in universo if u not in vivas and u in posicion}
        if reemplazar:
            # También las que vuelven a entrar: para esta pasada son ofertas nuevas, sin
            # nada de su vida anterior, y eso incluye los campos del descubrimiento.
            quitar |= {u for u in posicion if u not in conservar}
        ya_escritas = {o["job_url"] for o, _, _ in escribir}
        mover = [(u, *s) for u, s in sitio.items() if u in posicion and u not in quitar
                 and u not in ya_escritas]

        # Se guarda la marca de tiempo de esta pasada para poder distinguir después
        # "lo que ha traído la última actualización" de lo que ya estaba. Una ventana
        # fija (p.ej. 24h) no sirve: tras la primera carga marcaría todo como nuevo.
        perfiles.aplicar_cambios(chat, escribir, quitar, mover, ultima_busqueda=ahora,
                                 dedupe_hecho=pasada_completa or reemplazar)
        podadas = podar_descripciones_ocultas(chat)

        # Ofertas realmente nuevas que han sobrevivido al dedupe difuso (una oferta
        # nueva puede fusionarse con otra nueva de otro portal y desaparecer de aquí).
        # Solo se guardan los campos que necesita quien decide si notificar por
        # Telegram (ver app.py/notificaciones.py); el resto de este módulo no sabe
        # nada de relevancia ni de Telegram, eso vive en la capa de arriba.
        ofertas_nuevas = [
            {
                "job_url": o.get("job_url"),
                "title": o.get("title"),
                "company": o.get("company"),
                "location": o.get("location"),
            }
            for o in [por_url[u] for u in urls_nuevas if u in conservar] + supervivientes
            if o.get("job_url") in urls_nuevas
        ]

        return {
            "total": perfiles.contar_ofertas(chat),
            "nuevas": añadidas,
            "duplicados_fusionados": len(fusiones),
            "republicaciones_descartadas": republicadas,
            "descripciones_podadas": podadas,
            "marca": ahora,
            "ofertas_nuevas": ofertas_nuevas,
        }
