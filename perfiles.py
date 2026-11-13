"""
Almacén por perfil en SQLite (MP1, EVOLUCION-MULTIPERFIL.md §2 D1-D2 y §3.2).

Sustituye a estado.json, ofertas.json y telegram_avisos.json por `perfiles.db`, una sola
base con todo lo de cada chat_id. Tres cosas mandan en el diseño:

- **El disco.** El Toshiba tiene un disco que ya falló el 21-10-2026, y hoy un clic
  reescribe 502 KB y cada corrida 4,6 MB. Aquí un clic escribe una fila y solo se toca lo
  que cambió, comparando antes de escribir.
- **El aislamiento (regla 5).** La oferta del portal se guarda UNA vez (`ofertas`,
  `descripciones`) y cada perfil ve solo lo que tiene enlazado en `ofertas_perfil`. Toda
  función que lee ofertas exige el chat_id y hace ese JOIN: desde aquí no se puede pedir
  «todas las ofertas». Con None no devuelve lo del titular, sino un TypeError: eso es
  cosa de almacen.py, que es quien sabe que None significa «el titular».
- **La fidelidad (regla 4).** Con un solo perfil, lo que se lee es exactamente lo que
  devolvía el JSON, ausente frente a None y orden de la lista incluidos, porque de ese
  orden depende qué copia sobrevive a un duplicado (ver `orden` y `protegida`).

Lo que se aparta del §3.2 del plan, con el dato (aprobado por el titular el 30-10):

- `ofertas` y `descripciones` llevan rowid. Con las 2.787 ofertas reales, todo WITHOUT
  ROWID ocupa 7,09 MB y tarda 0,078 s en leerse; así, 6,09 MB y 0,027 s. Sus filas son de
  1-3 KB y SQLite recomienda WITHOUT ROWID solo por debajo de ~200 B.
- `marcas` tiene de clave (chat_id, url, accion) y no (chat_id, url): `almacen.marcar`
  permite aplicada + oculta y aplicada + favorita, y la clave del plan no podría guardarlo
  sin cambiar lo que hace la web. MP5 retira ese camino.
- `ofertas_perfil` lleva además `orden`, `protegida` y `extra`, y `marcas` lleva `extra`.

Hilos: una conexión por hilo y un candado para escribir. Con WAL los lectores no esperan
a nadie, y `BEGIN IMMEDIATE` más el timeout cubren a otro proceso (`python -m perfiles`,
un `docker compose run`).

Comandos:  python -m perfiles migrar | verificar | exportar <chat_id|titular> <carpeta>
           python -m perfiles crear --desde <perfil.json> | borrar <chat_id>

Desde MP5 las altas son por invitación (/invitar, telegram_bot.py): `JOBSPY_TELEGRAM_CHAT_IDS_EXTRA`
ya no crea perfiles, y `crear-adicionales` y `avisar-adicionales` se han ido con él.
"""

from __future__ import annotations

import contextlib
import copy
import functools
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

import reglas
from dedupe_difuso import VERSION_BLOQUES, clave_de_vacante, claves_bloque

logger = logging.getLogger(__name__)

# Sin JOBSPY_TELEGRAM_CHAT_ID (PC, pruebas) el titular es este chat_id ficticio. Telegram no
# usa nunca el 0, así que no se puede confundir con una persona real.
TITULAR_LOCAL = 0

# Campos que describen el MOMENTO EN QUE SE DESCUBRIÓ la oferta, no su estado de ahora:
# cuánta gente había aplicado ya y desde cuándo estaba publicada (ver
# buscador.extraer_candidatos). Se conserva siempre el primer valor capturado.
#
# No es una precaución teórica. La descripción de una oferta que ya está en la base se
# reutiliza del caché en vez de volver a pedir la página (ver completar_descripciones),
# así que en las corridas siguientes estos campos llegan vacíos: sin esto, la primera
# actualización de una oferta borraría el dato que solo se puede tomar una vez.
CAMPOS_DEL_DESCUBRIMIENTO = ("candidatos", "candidatos_tope", "publicada_en")

# De qué perfil es cada campo de la oferta. El resto es del portal y va en `ofertas.datos`,
# igual para todos. Categoría, ámbito y búsqueda dependen de la consulta que la trajo; la
# puntuación es el juicio del perfil; y lo que va a `extra` no estaba en el §3.2 pero es
# igual de suyo: las etiquetas salen de la categoría y el ámbito, `distancia_km` se mide
# desde SU centro y `fuentes_adicionales` es el resultado de SU dedupe. En la fila
# compartida, cada perfil pisaría el valor del otro y se filtraría de uno a otro.
_COLUMNAS_DEL_ENLACE = {
    "relevancia": "relevancia", "motivos_relevancia": "motivos", "categoria": "categoria",
    "ambito": "ambito", "busqueda": "busqueda", "visto_por_primera_vez": "visto_primera",
    "visto_por_ultima_vez": "visto_ultima",
}
_CAMPOS_EXTRA_DEL_ENLACE = ("categoria_label", "ambito_label", "distancia_km", "fuentes_adicionales")

_ACCIONES = {"favorito": "favorita", "favorita": "favorita", "oculta": "oculta", "aplicada": "aplicada"}
_CLAVE_DE_ACCION = {"favorita": "favoritas", "oculta": "ocultas", "aplicada": "aplicadas"}
_ESTADOS_DE_PERFIL = ("pendiente", "encuesta", "activo", "pausado", "bloqueado")

# Variables por sentencia: SQLite admite 999 en versiones antiguas y una corrida trae cientos
# de ofertas con dos claves de bloque cada una.
_TROZO = 400

ESQUEMA_V1 = (
    "CREATE TABLE perfiles (chat_id INTEGER PRIMARY KEY, estado TEXT NOT NULL, datos TEXT NOT NULL,"
    " version INTEGER NOT NULL DEFAULT 1, creado TEXT, actualizado TEXT, ultima_actividad TEXT)",
    # La oferta tal y como la da el portal: UNA fila, la use quien la use.
    "CREATE TABLE ofertas (url TEXT PRIMARY KEY, datos TEXT NOT NULL,"
    " bloque_empresa TEXT, bloque_titulo TEXT, visto_primera TEXT, visto_ultima TEXT)",
    "CREATE TABLE descripciones (url TEXT PRIMARY KEY, texto TEXT, podada INTEGER NOT NULL DEFAULT 0)",
    # El juicio de cada perfil sobre esa oferta. `orden` y `protegida` reproducen la lista
    # del JSON: es (protegidas, por orden) + (candidatas, por orden). Hace falta porque el
    # empate entre dos duplicados de la misma fuente y la misma completitud lo decide la
    # posición (dedupe_difuso._mejor). `protegida` dice en qué tramo estaba en la última
    # fusión, no si hoy tiene marca: así se sabe quién ha cambiado de tramo desde entonces.
    "CREATE TABLE ofertas_perfil (chat_id INTEGER NOT NULL, url TEXT NOT NULL, relevancia REAL,"
    " motivos TEXT, categoria TEXT, ambito TEXT, busqueda TEXT, visto_primera TEXT, visto_ultima TEXT,"
    " orden INTEGER NOT NULL, protegida INTEGER NOT NULL DEFAULT 0, extra TEXT,"
    " PRIMARY KEY (chat_id, url)) WITHOUT ROWID",
    "CREATE INDEX ofertas_perfil_url ON ofertas_perfil (url)",  # ¿la enlaza alguien más?
    # Con este índice la lista sale ordenada sin sorter, que con temp_store=FILE escribiría
    # en disco un temporal del tamaño de la base en cada lectura.
    "CREATE INDEX ofertas_perfil_orden ON ofertas_perfil (chat_id, protegida DESC, orden)",
    "CREATE TABLE marcas (chat_id INTEGER NOT NULL, url TEXT NOT NULL, accion TEXT NOT NULL,"
    " fecha TEXT, origen TEXT, motivo TEXT, titulo TEXT, empresa TEXT, extra TEXT,"
    " PRIMARY KEY (chat_id, url, accion)) WITHOUT ROWID",
    "CREATE TABLE avisos (chat_id INTEGER NOT NULL, clave TEXT NOT NULL, url TEXT, vacante TEXT,"
    " tipo TEXT, titulo TEXT, empresa TEXT, relevancia REAL, fuente TEXT, avisada TEXT,"
    " respuesta TEXT, motivo TEXT, respondida TEXT, PRIMARY KEY (chat_id, clave)) WITHOUT ROWID",
    "CREATE TABLE conversacion (chat_id INTEGER PRIMARY KEY, paso TEXT, borrador TEXT,"
    " mensaje_id INTEGER, actualizado TEXT)",
    "CREATE TABLE consultas (clave TEXT PRIMARY KEY, params TEXT, perfiles TEXT, ultima TEXT,"
    " resultados INTEGER)",
    "CREATE TABLE invitaciones (codigo TEXT PRIMARY KEY, creada TEXT, caduca TEXT, usada_por INTEGER)",
    "CREATE TABLE pendientes (chat_id INTEGER, url TEXT, relevancia REAL, creada TEXT,"
    " PRIMARY KEY (chat_id, url)) WITHOUT ROWID",
    "CREATE INDEX ofertas_bloque_empresa ON ofertas (bloque_empresa)",
    "CREATE INDEX ofertas_bloque_titulo ON ofertas (bloque_titulo)",
)

# Mismo espíritu que MIGRACIONES_SITIOS de config.py: una lista numerada que solo crece. Cada
# versión se aplica una vez y su número queda en meta.version_esquema.
MIGRACIONES = ((1, ESQUEMA_V1),)


class BaseNoDisponible(RuntimeError):
    """
    perfiles.db no se puede usar y no se va a fingir lo contrario.

    Una base vacía en su lugar sería el peor fallo posible: sin marcas ni ofertas, toda
    oferta sería nueva y la corrida siguiente mandaría por Telegram lo que el titular ya
    descartó. Decidido con él el 30-10: se aparta, se avisa y se para hasta que lo resuelva.
    La excepción salta antes de consultar ningún portal (ver app._ejecutar_busqueda).
    """


_local = threading.local()
_candado = threading.Lock()         # una escritura a la vez dentro del proceso
_preparando = threading.RLock()     # una sola preparación de la base por proceso
_preparadas: set[str] = set()       # rutas ya revisadas, migradas y al día
_avisadas: set[tuple[str, str]] = set()  # incidencias ya encoladas en este proceso
_generacion = 0                     # sube al apartar una base: invalida las conexiones de otros hilos


def ruta() -> Path:
    # Se lee en cada llamada, no al importar: las pruebas cambian JOBSPY_DATA_DIR entre
    # recargas de los demás módulos, y este no tiene por qué recargarse con ellos.
    return Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent))) / "perfiles.db"


def _abrir(destino: str) -> sqlite3.Connection:
    # isolation_level=None: las transacciones las abre _transaccion(), no el módulo sqlite3.
    conn = sqlite3.connect(destino, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-512")
        conn.execute("PRAGMA temp_store=FILE")
    except sqlite3.Error:
        # Una base corrupta falla justo aquí, y hay que soltar el fichero para poder apartarlo.
        conn.close()
        raise
    return conn


def cerrar() -> None:
    """Cierra la conexión de este hilo. La siguiente llamada abre otra."""
    conn = getattr(_local, "conn", None)
    _local.conn = None
    if conn is not None:
        with contextlib.suppress(sqlite3.Error):
            conn.close()


def _es_corrupcion(error: sqlite3.Error) -> bool:
    # Un disco lleno, una base bloqueada o un fallo de E/S NO son corrupción: apartar la
    # base por eso sería perder de vista datos buenos. Solo estos dos códigos.
    codigo = getattr(error, "sqlite_errorcode", None)
    if codigo is not None:
        return (codigo & 0xFF) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
    texto = str(error).lower()
    return "malformed" in texto or "not a database" in texto


def _hay_rastro_de_datos(destino: Path) -> bool:
    """
    ¿Consta que aquí hubo una base, aunque ahora no esté? Si es así, no se crea otra.

    Desde MP10 también cuentan las copias diarias de `copias/` (operacion/copia_perfiles.py): son la
    prueba más reciente de que hubo una base, y sin ellas quitar los `*.pre-mp1` (11 MB del 30-10 que
    la limpieza propone) dejaría que una base desaparecida se recreara vacía y reenviara por Telegram
    todo lo ya descartado de cada persona.
    """
    carpeta = destino.parent
    return (any(carpeta.glob("*.pre-mp1")) or any(carpeta.glob(destino.name + ".corrupta-*"))
            or any((carpeta / "copias").glob("copia-*.tar.gz")))


def _encolar_incidencia(destino: str, tipo: str, motivo: str) -> None:
    """Una por proceso y tipo: la corrida horaria no debe repetir el aviso cada hora."""
    if (destino, tipo) in _avisadas:
        return
    _avisadas.add((destino, tipo))
    try:
        from fuentes_extra import salud

        salud.registrar_incidencia_sistema("perfiles.db", motivo)
    except Exception:
        logger.exception("No se ha podido encolar la incidencia de perfiles.db")


def _apartar(destino: str) -> Path | None:
    """Mueve la base y sus -wal/-shm a un lado, sin borrar nada. Devuelve dónde ha quedado."""
    global _generacion
    cerrar()
    apartada = Path(destino + ".corrupta-" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    _preparadas.discard(destino)
    _generacion += 1
    try:
        os.replace(destino, apartada)
    except OSError:
        # En Windows no se puede mover un fichero abierto por otro hilo. Se queda donde
        # está y se volverá a detectar: lo importante es no seguir usándola.
        logger.exception("No se ha podido apartar %s", destino)
        return None
    for sufijo in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.replace(destino + sufijo, str(apartada) + sufijo)
    return apartada


def _base_corrupta(destino: str, error: Exception) -> BaseNoDisponible:
    apartada = _apartar(destino)
    donde = apartada.name if apartada else "su sitio (no se ha podido mover)"
    motivo = f"La base está corrupta ({error}). Se ha quedado en {donde}."
    _encolar_incidencia(destino, "corrupta", motivo)
    # Al apartarla deja de existir, y el siguiente intento diría que «falta»: es la misma avería.
    _avisadas.add((destino, "ausente"))
    return BaseNoDisponible(motivo)


def _vigilar(funcion):
    """Si la base resulta estar corrupta en plena marcha, se trata igual que al abrirla."""

    @functools.wraps(funcion)
    def envuelta(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        except sqlite3.DatabaseError as e:
            if _es_corrupcion(e):
                raise _base_corrupta(str(ruta()), e) from e
            raise

    return envuelta


@contextlib.contextmanager
def _en_transaccion(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    with _candado:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def _aplicar_esquema(conn: sqlite3.Connection) -> None:
    with _en_transaccion(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)")
        fila = conn.execute("SELECT valor FROM meta WHERE clave = 'version_esquema'").fetchone()
        version = int(fila[0]) if fila else 0
        if version > MIGRACIONES[-1][0]:
            # Código más viejo que la base (una vuelta atrás a medias). Tampoco es
            # corrupción: los datos están bien, es este programa el que no los entiende.
            raise BaseNoDisponible(
                f"perfiles.db va por el esquema {version} y este código solo conoce hasta el "
                f"{MIGRACIONES[-1][0]}. Exporta con el código nuevo antes de volver atrás."
            )
        for numero, sentencias in MIGRACIONES:
            if numero > version:
                for sentencia in sentencias:
                    conn.execute(sentencia)
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('version_esquema', ?)", (str(numero),))


def _bloques(oferta: dict) -> tuple[str | None, str | None]:
    empresa = titulo = None
    for clave in claves_bloque(oferta):
        if clave.startswith("e:"):
            empresa = clave
        else:
            titulo = clave
    return empresa, titulo


def _revisar_bloques(conn: sqlite3.Connection) -> None:
    """Si cambió la regla de los bloques, las columnas guardadas ya no valen (ver VERSION_BLOQUES)."""
    fila = conn.execute("SELECT valor FROM meta WHERE clave = 'version_bloques'").fetchone()
    if fila and fila[0] == str(VERSION_BLOQUES):
        return
    with _en_transaccion(conn):
        filas = conn.execute("SELECT url, datos FROM ofertas").fetchall()
        for url, datos in filas:
            conn.execute("UPDATE ofertas SET bloque_empresa = ?, bloque_titulo = ? WHERE url = ?",
                         (*_bloques(json.loads(datos)), url))
        if filas:
            # Con otros bloques, dos ofertas que nunca se compararon pasan a compararse.
            conn.execute("INSERT OR REPLACE INTO meta SELECT 'dedupe_pendiente:' || chat_id, '1'"
                         " FROM (SELECT DISTINCT chat_id FROM ofertas_perfil)")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('version_bloques', ?)", (str(VERSION_BLOQUES),))


def _preparar(destino: str) -> None:
    """Primera vez de este proceso con esta base: la revisa, la pone al día y migra si toca."""
    with _preparando:
        if destino in _preparadas:
            return
        camino = Path(destino)
        existia = camino.exists() and camino.stat().st_size > 0
        if not existia and _hay_rastro_de_datos(camino):
            motivo = ("Falta perfiles.db pero consta que existió (hay *.pre-mp1, una base apartada o "
                      "copias en data/copias). No se crea una vacía: se restaura una copia (DESPLIEGUE.md).")
            _encolar_incidencia(destino, "ausente", motivo)
            raise BaseNoDisponible(motivo)
        conn = None
        try:
            conn = _abrir(destino)
            if existia:
                resultado = conn.execute("PRAGMA quick_check(1)").fetchone()[0]
                if resultado != "ok":
                    raise sqlite3.DatabaseError(f"database disk image is malformed: {resultado[:200]}")
            _aplicar_esquema(conn)
            _comprobar_titular(conn)
            _migrar_si_toca(conn, camino.parent)
            _revisar_bloques(conn)
        except sqlite3.DatabaseError as e:
            with contextlib.suppress(sqlite3.Error):
                if conn is not None:
                    conn.close()
            if _es_corrupcion(e):
                raise _base_corrupta(destino, e) from e
            raise
        except (OSError, ValueError) as e:
            # Un JSON de origen ilegible, o uno que no se puede copiar. Migrar sin él sería
            # empezar sin esos datos, así que tampoco se sigue.
            with contextlib.suppress(sqlite3.Error):
                if conn is not None:
                    conn.close()
            motivo = f"No se ha podido migrar desde los JSON: {e}"
            _encolar_incidencia(destino, "migracion", motivo)
            raise BaseNoDisponible(motivo) from e
        conn.close()
        _preparadas.add(destino)


def conexion() -> sqlite3.Connection:
    """La conexión de este hilo, abriéndola y preparando la base si hace falta."""
    destino = str(ruta())
    conn = getattr(_local, "conn", None)
    if conn is not None and _local.ruta == destino and _local.generacion == _generacion:
        return conn
    cerrar()
    _preparar(destino)
    conn = _abrir(destino)
    _local.conn, _local.ruta, _local.generacion = conn, destino, _generacion
    return conn


def _transaccion():
    return _en_transaccion(conexion())


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json(valor) -> str:
    # sort_keys para que el mismo registro dé siempre el mismo texto y se pueda comparar
    # con el guardado sin reescribir la fila. default=str: lo mismo que el JSON de antes, un
    # tipo inesperado del scraping no tira por la borda una búsqueda de varios minutos.
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def a_json(valor) -> str:
    """El JSON con el que se guarda todo aquí, para quien tenga que dejar algo en la base."""
    return _json(valor)


def _exigir(chat_id) -> int:
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        raise TypeError("Hace falta el chat_id (int) del perfil: no se puede pedir lo de todos.")
    return chat_id


# ----------------------------------------------------------------------
# QUIÉN ES EL TITULAR
# ----------------------------------------------------------------------


def _titular_del_entorno() -> int | None:
    valor = os.environ.get("JOBSPY_TELEGRAM_CHAT_ID", "").strip()
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        # Sin el valor en el mensaje (regla 3). Seguir con otro chat_id migraría los datos
        # del titular a un dueño equivocado, así que se para aquí.
        raise ValueError("JOBSPY_TELEGRAM_CHAT_ID no es un número") from None


def _comprobar_titular(conn: sqlite3.Connection) -> None:
    fila = conn.execute("SELECT valor FROM meta WHERE clave = 'titular'").fetchone()
    entorno = _titular_del_entorno()
    if fila and entorno is not None and fila[0] != str(entorno):
        logger.warning("JOBSPY_TELEGRAM_CHAT_ID no coincide con el titular con el que se migró "
                       "perfiles.db: sus ofertas y marcas no se verán.")


def titular() -> int:
    """
    El chat_id del titular: el del entorno; si no hay, el guardado al migrar (así una copia
    de la base funciona en el PC sin saber el número); si tampoco, TITULAR_LOCAL.
    """
    entorno = _titular_del_entorno()
    if entorno is not None:
        return entorno
    guardado = meta_get("titular")
    return int(guardado) if guardado is not None else TITULAR_LOCAL


def resolver(chat_id: int | None) -> int:
    """El contrato de almacen.py: None significa el titular."""
    return titular() if chat_id is None else _exigir(chat_id)


# ----------------------------------------------------------------------
# META
# ----------------------------------------------------------------------


@_vigilar
def meta_get(clave: str, por_defecto: str | None = None) -> str | None:
    fila = conexion().execute("SELECT valor FROM meta WHERE clave = ?", (clave,)).fetchone()
    return fila[0] if fila else por_defecto


@_vigilar
def meta_set(clave: str, valor: str | None) -> None:
    with _transaccion() as conn:
        if valor is None:
            conn.execute("DELETE FROM meta WHERE clave = ?", (clave,))
        else:
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (clave, valor))


def ultima_busqueda(chat_id: int) -> str | None:
    return meta_get(f"ultima_busqueda:{_exigir(chat_id)}")


def firma_ofertas(chat_id: int) -> str | None:
    """Cambia cada vez que cambian las ofertas de este perfil (la usaba el listado de la web, hasta MP5)."""
    return meta_get(f"firma_ofertas:{_exigir(chat_id)}")


def dedupe_pendiente(chat_id: int) -> bool:
    return meta_get(f"dedupe_pendiente:{_exigir(chat_id)}") is not None


def _subir_firma(conn: sqlite3.Connection, chat_id: int) -> None:
    conn.execute(
        "INSERT INTO meta VALUES (?, '1') ON CONFLICT (clave)"
        " DO UPDATE SET valor = CAST(valor AS INTEGER) + 1", (f"firma_ofertas:{chat_id}",))


# ----------------------------------------------------------------------
# PERFILES
# ----------------------------------------------------------------------

_COLUMNAS_PERFIL = "chat_id, estado, datos, version, creado, actualizado, ultima_actividad"


def _fila_a_perfil(fila: tuple) -> dict:
    return {"chat_id": fila[0], "estado": fila[1], "datos": json.loads(fila[2]), "version": fila[3],
            "creado": fila[4], "actualizado": fila[5], "ultima_actividad": fila[6]}


@_vigilar
def obtener(chat_id: int) -> dict | None:
    fila = conexion().execute(f"SELECT {_COLUMNAS_PERFIL} FROM perfiles WHERE chat_id = ?",
                              (_exigir(chat_id),)).fetchone()
    return _fila_a_perfil(fila) if fila else None


@_vigilar
def guardar(chat_id: int, datos: dict, estado: str | None = None) -> int:
    """Crea o actualiza un perfil. Devuelve su `version`, que sube en cada cambio."""
    chat_id = _exigir(chat_id)
    if estado is not None and estado not in _ESTADOS_DE_PERFIL:
        raise ValueError(f"Estado de perfil desconocido: {estado}")
    ahora = _ahora()
    with _transaccion() as conn:
        conn.execute(
            "INSERT INTO perfiles (chat_id, estado, datos, version, creado, actualizado)"
            " VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT (chat_id) DO UPDATE SET"
            " estado = COALESCE(?, estado), datos = excluded.datos,"
            " version = version + 1, actualizado = excluded.actualizado",
            (chat_id, estado or "pendiente", _json(datos), ahora, ahora, estado))
        return conn.execute("SELECT version FROM perfiles WHERE chat_id = ?", (chat_id,)).fetchone()[0]


@_vigilar
def listar(estado: str | None = None) -> list[dict]:
    consulta = f"SELECT {_COLUMNAS_PERFIL} FROM perfiles"
    filas = (conexion().execute(consulta + " WHERE estado = ?", (estado,)) if estado
             else conexion().execute(consulta))
    return [_fila_a_perfil(f) for f in filas]


def activos() -> list[dict]:
    """
    Los perfiles que atiende una corrida, con el titular el primero: lo compartido de una fila
    sale del primero que la tiene, y su camino tiene que seguir siendo el de siempre (regla 4).

    Pausados, bloqueados, pendientes y en encuesta se quedan fuera: ni consultas ni avisos.
    Una base nueva no tiene fila para el titular hasta que hay algo que migrar, y aun así las
    corridas tienen que funcionar como siempre: si no hay fila, se le da la de la migración.
    Si la hay y está pausado, se respeta.
    """
    chat = titular()
    todos = listar("activo")
    primero = [p for p in todos if p["chat_id"] == chat]
    if not primero and obtener(chat) is None:
        primero = [{**PERFIL_GLOBAL, "chat_id": chat, "estado": "activo"}]
    return primero + [p for p in todos if p["chat_id"] != chat]


def recibe_avisos(perfil: dict) -> bool:
    """Ser `activo` no basta: el perfil sombra está activo y con el modo en "ninguno"."""
    if perfil.get("estado") != "activo":
        return False
    return (perfil["datos"].get("avisos") or {}).get("modo") != "ninguno"


# ----------------------------------------------------------------------
# CONFIG EFECTIVA (MP2, EVOLUCION-MULTIPERFIL.md §5.3)
#
# Devuelve las mismas claves que `buscador.py` lee hoy de config.json, más `area`, `puntuador`,
# `umbral` y `experiencia_estricta`: por eso los filtros sirven tal cual para cualquier perfil.
#
# Lo personal de un perfil con bloque propio o de catálogo NUNCA sale de config.json, que es la
# del titular: parte del juego o del plan, y de config.json solo se toma lo del sistema. Si no,
# lo que MP4 cambie ahí para el titular se le colaría al adicional sin que nadie lo decidiera.
# ----------------------------------------------------------------------

# El titular tal y como lo crea la migración, sin pasar por la base: buscar() y medir no pueden
# depender de ella. No lleva chat_id, y sin chat_id no se cachea.
PERFIL_GLOBAL = {"chat_id": None, "version": 0,
                 "datos": {"v": 1, "reglas": "titular-2026-10", "config": "global"}}
_AREA_BARCELONA = {"modo": "regex", "nombre": "barcelona"}
# Senior para sectores que no son IT. El de `titular-2026-10` lleva «arquitecto», «principal» o
# «staff», que en otros oficios son el puesto y no el nivel.
#
# MP7 (aprobado por el titular con la lista de puestos, 04-11): tampoco son nivel los puestos que
# llevan la palabra en el nombre. Con «\bmanager\b» a secas, alguien de marketing o de ventas con menos
# de 6 años no recibía nunca su propio puesto («Community Manager», «Key Account Manager», «Traffic
# manager»); con «\blead», tampoco «Lead Generation Specialist» ni «Tour Leader»; con «\bjefe\b», el
# «Jefe/a de partida» de cocina y el «jefe de rango» de sala, que son escalones del oficio. «Store
# Manager», «Responsable de tienda» o «Jefe/a de recepción» siguen siendo mando, a propósito. Tiene que
# ser igual que `buscador._CAT_SENIOR` (lo comprueba pruebas/test_generador.py).
_SENIOR_GENERICO = (r"\bsenior\b|\bsr\.?\b|(?<!tour )\blead(?:er)?\b(?! gen)"
                    r"|(?<!community )(?<!account )(?<!social media )(?<!content )(?<!traffic )\bmanager\b"
                    r"|\bdirector\b|\bhead of\b|\bjefe\b(?!(?:/a)? de (?:partida|rango))|\bresponsable\b")
_TERMINOS_POR_PERFIL = 6  # D5: que nadie se coma el presupuesto de consultas
# Un perfil de catálogo que no diga sus términos de Jooble usa sus primeros: cada uno es una
# petición de un cupo que se reparte entre todos (ver fuentes_extra/jooble.py).
_TERMINOS_JOOBLE_POR_DEFECTO = 2
_MILLAS_POR_KM = 0.621371
# Se pierde la clave, no la persona: una entrada por chat_id, y cambiar la clave la reemplaza. Así
# no crece con cada cambio de config, y el tope son los perfiles que haya (3, D4).
_CACHE_EFECTIVA: dict[int, list] = {}
_lock_efectiva = threading.Lock()


def _ruta_del_modelo() -> Path:
    return ruta().parent / "modelo_relevancia.json"


def _firma_del_modelo() -> float:
    try:
        return _ruta_del_modelo().stat().st_mtime
    except OSError:
        return 0.0


def _firma_de_config(config_global: dict) -> str:
    """
    Huella del CONTENIDO de la config que se ha pasado, no de la fecha del fichero: lo que se
    cachea depende de ese dict, y dos versiones del fichero en el mismo tick tendrían la misma
    fecha. Cuesta una serialización por perfil y corrida, no por oferta.
    """
    return hashlib.sha1(_json(config_global).encode("utf-8")).hexdigest()


def _efectiva_con_bloque(datos: dict, juego: reglas.Juego, bloque: dict, config_global: dict) -> dict:
    efectiva = copy.deepcopy(config_global)
    efectiva.update(copy.deepcopy(juego.config))
    # Una clave del sistema en el bloque se ignora: el ritmo y el paralelismo no son de nadie.
    efectiva.update({c: copy.deepcopy(v) for c, v in bloque.items()
                     if c in reglas.CLAVES_DE_LA_PERSONA})
    tope = bloque.get("anos_experiencia_max", datos.get("experiencia_max_exigida"))
    if tope is not None:
        efectiva["anos_experiencia_max"] = tope
    # El modelo salió de las marcas del titular: dárselo a otro perfil sería puntuarle con lo
    # aprendido de otra persona (regla 5).
    efectiva["usar_modelo_entrenado"] = False
    efectiva["reglas_de_fabrica"] = False
    efectiva["area"] = copy.deepcopy(bloque.get("area", _AREA_BARCELONA))
    efectiva["puntuador"] = {"tipo": "reglas", "juego": juego.nombre}
    efectiva["umbral"] = bloque.get("umbral", datos.get("umbral", reglas.UMBRAL_ENCAJE_ALTO))
    efectiva["experiencia_estricta"] = bool(
        bloque.get("experiencia_estricta", datos.get("experiencia_estricta", False)))
    return efectiva


def _efectiva_de_catalogo(plan: dict, config_global: dict) -> dict:
    """
    La config que sale de un plan de perfil. Estructura del plan: §5.5 del plan de evolución.
    Sin plan es un error y no un perfil vacío: no se busca para nadie con la config de otro.
    """
    if not plan:
        raise ValueError("perfil de catálogo sin plan")
    efectiva = copy.deepcopy(config_global)
    for clave in reglas.CLAVES_DE_LA_PERSONA:
        if clave not in ("dias_sin_repetir_aviso", "telegram_max_avisos_individuales"):
            efectiva.pop(clave, None)
    area = copy.deepcopy(plan["area"])
    anos = int(plan.get("experiencia_anos") or 0)
    radio = float(area.get("radio_km") or 0)
    terminos = list(plan.get("terminos") or [])[
        :int(config_global.get("max_terminos_por_perfil") or _TERMINOS_POR_PERFIL)]
    efectiva.update({
        "categorias": {"plan": {"etiqueta": "Mi búsqueda", "activa": True, "terminos": terminos}},
        "jooble_terminos": list(plan.get("jooble_terminos") or terminos[:_TERMINOS_JOOBLE_POR_DEFECTO]),
        "jooble_ubicacion": plan.get("jooble_ubicacion") or area.get("nombre"),
        "ambitos": copy.deepcopy(plan.get("ambitos") or {}),
        "ambito_local": "local",
        "centro_lat": area.get("lat"),
        "centro_lon": area.get("lon"),
        "centro_nombre": area.get("nombre"),
        "max_distancia_km": radio,
        "distancia_portal_millas": round(radio * _MILLAS_POR_KM),
        "exigir_remoto_fuera_de_barcelona": True,
        "anos_experiencia_max": plan.get("experiencia_max_exigida"),
        # Quien lleva 6 años o más sí opta a los puestos con mando (§5.2).
        "filtrar_senior": anos < 6,
        "patron_senior": _SENIOR_GENERICO,
        "niveles_excluidos": (["director", "executive"] if anos >= 6
                              else ["mid-senior level", "director", "executive"]),
        "descartar_otros_idiomas": True,
        "idiomas_permitidos": list(plan.get("idiomas_permitidos") or ["es"]),
        "descartar_fuera_de_perfil": True,
        "reglas_descarte": copy.deepcopy(plan.get("descartes") or []),
        "reglas_de_fabrica": False,
        "empresas_excluidas": list(plan.get("empresas_excluidas") or []),
        "pesos_relevancia": {},
        "categorias_siempre_visibles": [],
        "usar_modelo_entrenado": False,
        "area": area,
        "puntuador": {"tipo": "catalogo", "plan": copy.deepcopy(plan)},
        "umbral": plan.get("umbral", reglas.UMBRAL_ENCAJE_ALTO),
        "experiencia_estricta": bool(plan.get("experiencia_estricta", False)),
    })
    return efectiva


def _construir_efectiva(datos: dict, config_global: dict) -> dict:
    if datos.get("reglas") == "catalogo":
        return _efectiva_de_catalogo(datos.get("plan") or {}, config_global)
    bloque = datos.get("config")
    # Con "global", config.json decide el juego si lo dice. Es lo que lee la web
    # (buscador._puntuador_del_titular), y así un titular sin fila, o recién migrado tras perder la
    # base, no vuelve solo a las reglas viejas mientras la web sigue con las nuevas.
    nombre = (config_global.get("puntuador") or {}).get("juego") if bloque == "global" else None
    juego = reglas.obtener(nombre or datos.get("reglas"))
    if bloque == "global":
        efectiva = copy.deepcopy(config_global)
        efectiva.update(area=dict(_AREA_BARCELONA),
                        puntuador={"tipo": "reglas", "juego": juego.nombre},
                        umbral=reglas.UMBRAL_ENCAJE_ALTO, experiencia_estricta=False)
        return efectiva
    if not isinstance(bloque, dict):
        raise ValueError("el perfil no dice de dónde sale su config")
    return _efectiva_con_bloque(datos, juego, bloque, config_global)


def _entrada_de_cache(perfil: dict, config_global: dict) -> list:
    """[clave, config efectiva, puntuador o None] de ese perfil, al día."""
    clave = (perfil["chat_id"], perfil["version"], _firma_de_config(config_global),
             _firma_del_modelo())
    with _lock_efectiva:
        entrada = _CACHE_EFECTIVA.get(perfil["chat_id"])
        if entrada is None or entrada[0] != clave:
            entrada = [clave, _construir_efectiva(perfil["datos"], config_global), None]
            _CACHE_EFECTIVA[perfil["chat_id"]] = entrada
        return entrada


def config_efectiva(perfil: dict, config_global: dict) -> dict:
    """
    Lo que los filtros tienen que leer para este perfil, como lo devuelve `obtener()`.

    Cada llamada devuelve una copia: si un filtro tocara la config de un perfil, con un dict
    compartido se le quedaría a ese perfil hasta el siguiente cambio.
    """
    if perfil.get("chat_id") is None:
        return _construir_efectiva(perfil["datos"], config_global)
    return copy.deepcopy(_entrada_de_cache(perfil, config_global)[1])


def puntuador_de(perfil: dict, config_global: dict):
    """
    El puntuador de este perfil, con la misma clave de caché que su config. Se comparte porque
    no se modifica una vez creado (ver buscador.Puntuador).
    """
    import buscador  # import diferido: `python -m perfiles` no debe cargar jobspy

    if perfil.get("chat_id") is None:
        return buscador.crear_puntuador(_construir_efectiva(perfil["datos"], config_global))
    entrada = _entrada_de_cache(perfil, config_global)
    if entrada[2] is None:
        entrada[2] = buscador.crear_puntuador(entrada[1])
    return entrada[2]


# ----------------------------------------------------------------------
# MARCAS
# ----------------------------------------------------------------------


def _accion(accion: str) -> str:
    try:
        return _ACCIONES[accion]
    except KeyError:
        raise ValueError(f"Acción desconocida: {accion}") from None


def _info_a_columnas(info: dict) -> tuple:
    """
    (fecha, origen, motivo, titulo, empresa, extra) de una marca, sin perder nada.

    `titulo` y `empresa` están siempre en el registro y NULL se lee como None. `fecha`,
    `origen` y `motivo` pueden faltar, así que un None explícito se guarda en `extra`
    para no confundirlo con «no estaba». Lo que no tiene columna va también a `extra`.
    """
    resto = dict(info)
    columnas = []
    for campo in ("fecha", "origen", "motivo"):
        valor = resto.get(campo)
        columnas.append(valor)
        if valor is not None:
            del resto[campo]
    columnas.append(resto.pop("titulo", None))
    columnas.append(resto.pop("empresa", None))
    return (*columnas, _json(resto) if resto else None)


def _columnas_a_info(fecha, origen, motivo, titulo, empresa, extra) -> dict:
    info: dict = {}
    if fecha is not None:
        info["fecha"] = fecha
    info["titulo"], info["empresa"] = titulo, empresa
    if origen is not None:
        info["origen"] = origen
    if motivo is not None:
        info["motivo"] = motivo
    if extra:
        info.update(json.loads(extra))
    return info


def _poner_marca(conn: sqlite3.Connection, chat_id: int, url: str, accion: str, info: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO marcas (chat_id, url, accion, fecha, origen, motivo, titulo,"
        " empresa, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, url, accion, *_info_a_columnas(info)))


@_vigilar
def marcas(chat_id: int) -> dict:
    """{"favoritas": {url: info}, "ocultas": {...}, "aplicadas": {...}} de este perfil."""
    fuera: dict = {clave: {} for clave in _CLAVE_DE_ACCION.values()}
    for url, accion, *columnas in conexion().execute(
            "SELECT url, accion, fecha, origen, motivo, titulo, empresa, extra FROM marcas"
            " WHERE chat_id = ?", (_exigir(chat_id),)):
        clave = _CLAVE_DE_ACCION.get(accion)
        if clave:  # `encaja` y `no_encaja` son de MP6, y no van en este diccionario
            fuera[clave][url] = _columnas_a_info(*columnas)
    return fuera


@_vigilar
def urls_excluidas(chat_id: int) -> set[str]:
    """Las que este perfil ocultó o aplicó: no le vuelven a aparecer en búsquedas nuevas."""
    return {f[0] for f in conexion().execute(
        "SELECT url FROM marcas WHERE chat_id = ? AND accion IN ('oculta', 'aplicada')",
        (_exigir(chat_id),))}


@_vigilar
def alternar_marca(chat_id: int, url: str, accion: str, datos_oferta: dict | None = None) -> None:
    """
    Interruptor, con la semántica exacta de almacen.marcar: pulsar dos veces deshace.
    Marcar favorita saca de ocultas, y ocultar o aplicar saca de favoritas. Que aplicada y
    oculta puedan convivir no es un descuido de esta función: es lo que hace la web hoy.
    """
    chat_id, accion = _exigir(chat_id), _accion(accion)
    info = {"fecha": _ahora(), "titulo": (datos_oferta or {}).get("title"),
            "empresa": (datos_oferta or {}).get("company")}
    with _transaccion() as conn:
        borrada = conn.execute("DELETE FROM marcas WHERE chat_id = ? AND url = ? AND accion = ?",
                               (chat_id, url, accion)).rowcount
        if not borrada:
            _poner_marca(conn, chat_id, url, accion, info)
            quita = "oculta" if accion == "favorita" else "favorita"
            conn.execute("DELETE FROM marcas WHERE chat_id = ? AND url = ? AND accion = ?",
                         (chat_id, url, quita))


@_vigilar
def fijar_marca(chat_id: int, url: str, accion: str, datos_oferta: dict | None = None,
                extra: dict | None = None) -> None:
    """Deja la oferta EN ese estado, sin alternar, y quita las otras dos (ver almacen.fijar_marca)."""
    chat_id, accion = _exigir(chat_id), _accion(accion)
    with _transaccion() as conn:
        fila = conn.execute(
            "SELECT fecha, origen, motivo, titulo, empresa, extra FROM marcas"
            " WHERE chat_id = ? AND url = ? AND accion = ?", (chat_id, url, accion)).fetchone()
        previo = _columnas_a_info(*fila) if fila else {}
        info = {
            "fecha": previo.get("fecha") or _ahora(),
            "titulo": (datos_oferta or {}).get("title") or previo.get("titulo"),
            "empresa": (datos_oferta or {}).get("company") or previo.get("empresa"),
            **{k: v for k, v in previo.items() if k not in ("fecha", "titulo", "empresa")},
            **(extra or {}),
        }
        conn.execute("DELETE FROM marcas WHERE chat_id = ? AND url = ? AND accion <> ?",
                     (chat_id, url, accion))
        _poner_marca(conn, chat_id, url, accion, info)


@_vigilar
def quitar_marcas(chat_id: int, url: str) -> None:
    """La deja sin ninguna marca (el «Deshacer» de Telegram)."""
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        conn.execute("DELETE FROM marcas WHERE chat_id = ? AND url = ?", (chat_id, url))


# ----------------------------------------------------------------------
# ETIQUETAS (MP6, aprendizaje.py)
#
# Una etiqueta es una marca puesta con un botón: `origen` telegram (los avisos), descubrimiento
# (👍/👎) o calibracion (MP8). Las 2.626 marcas sin origen del titular son los barridos en lote de
# la web, que el proyecto ya midió que no sirven como etiqueta (DESIGN.md, Fase 5): no entran.
# Todo va por chat_id, como el resto de este módulo (regla 5).
# ----------------------------------------------------------------------

ORIGENES_DE_ETIQUETA = ("telegram", "descubrimiento", "calibracion")
_EN_ORIGENES = "(" + ", ".join("'%s'" % o for o in ORIGENES_DE_ETIQUETA) + ")"


@_vigilar
def etiquetas(chat_id: int) -> list[dict]:
    """[{url, accion, ...info}] de las marcas de este perfil que son etiquetas."""
    return [{"url": url, "accion": accion, **_columnas_a_info(*columnas)} for url, accion, *columnas in conexion().execute(
        "SELECT url, accion, fecha, origen, motivo, titulo, empresa, extra FROM marcas"
        f" WHERE chat_id = ? AND origen IN {_EN_ORIGENES}", (_exigir(chat_id),))]


@_vigilar
def firma_de_marcas(chat_id: int) -> tuple:
    """Cambia con cada marca que se pone, se cambia o se quita en este perfil: una consulta, sin leer filas."""
    return tuple(conexion().execute(
        "SELECT COUNT(*), MAX(fecha), TOTAL(LENGTH(accion) + LENGTH(COALESCE(origen, '')) + LENGTH(COALESCE(extra, '')))"
        " FROM marcas WHERE chat_id = ?", (_exigir(chat_id),)).fetchone())


@_vigilar
def urls_positivas(chat_id: int) -> list[str]:
    """Las que este perfil marcó ⭐ o ✅, venga de donde venga la marca: lo que la protección no deja caer."""
    return [f[0] for f in conexion().execute(
        "SELECT DISTINCT url FROM marcas WHERE chat_id = ? AND accion IN ('favorita', 'aplicada')",
        (_exigir(chat_id),))]


@_vigilar
def completar_rasgos(chat_id: int, rasgos_por_url: dict[str, dict]) -> int:
    """
    Apunta en `extra` de sus etiquetas lo que hace falta para sus rasgos (aprendizaje.datos_de_rasgos),
    para que la etiqueta no dependa de que la descripción sobreviva a la poda. Solo en las que no lo
    tienen: repetirlo no escribe nada. Devuelve cuántas filas ha tocado.
    """
    chat_id = _exigir(chat_id)
    tocadas = 0
    with _transaccion() as conn:
        for url, rasgos in rasgos_por_url.items():
            for accion, extra in conn.execute(
                    f"SELECT accion, extra FROM marcas WHERE chat_id = ? AND url = ? AND origen IN {_EN_ORIGENES}",
                    (chat_id, url)).fetchall():
                datos = json.loads(extra) if extra else {}
                if "rasgos" in datos:
                    continue
                datos["rasgos"] = rasgos
                conn.execute("UPDATE marcas SET extra = ? WHERE chat_id = ? AND url = ? AND accion = ?",
                             (_json(datos), chat_id, url, accion))
                tocadas += 1
    return tocadas


@_vigilar
def contar_avisos(chat_id: int, tipo: str, desde: str) -> int:
    return conexion().execute("SELECT COUNT(*) FROM avisos WHERE chat_id = ? AND tipo = ? AND avisada >= ?",
                              (_exigir(chat_id), tipo, desde)).fetchone()[0]


@_vigilar
def avisos_entre(chat_id: int, desde: str, hasta: str) -> list[dict]:
    """Los avisos y descubrimientos de este perfil avisados en [desde, hasta), con su respuesta."""
    return [dict(zip(("url", "tipo", "relevancia", "respuesta", "motivo", "avisada", "respondida"), f))
            for f in conexion().execute(
                "SELECT url, tipo, relevancia, respuesta, motivo, avisada, respondida FROM avisos"
                " WHERE chat_id = ? AND tipo IN ('aviso', 'descubrimiento') AND avisada >= ? AND avisada < ?",
                (_exigir(chat_id), desde, hasta))]


@_vigilar
def motivos_guardados(chat_id: int, urls: Iterable[str]) -> dict[str, list]:
    """{url: motivos} tal y como se puntuaron al descubrirlas, de las que este perfil enlaza."""
    chat_id, salida = _exigir(chat_id), {}
    for trozo in _trozos(urls):
        marcas_ = ",".join("?" * len(trozo))
        for url, motivos in conexion().execute(
                f"SELECT url, motivos FROM ofertas_perfil WHERE chat_id = ? AND url IN ({marcas_})", (chat_id, *trozo)):
            salida[url] = json.loads(motivos) if motivos else []
    return salida


@_vigilar
def actualizar_datos(chat_id: int, cambiar) -> dict:
    """
    Lee, cambia y guarda los datos de un perfil en UNA transacción: el bot y la corrida van en hilos
    distintos, y leer por un lado y guardar por otro perdería lo que el otro escribiera en medio.
    `cambiar(datos)` los modifica en su sitio. Un titular sin fila (recién migrado, o en el PC) la
    recibe con lo de la migración y activo. Si no cambia nada, no escribe.
    """
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        fila = conn.execute("SELECT datos FROM perfiles WHERE chat_id = ?", (chat_id,)).fetchone()
        if fila is None and chat_id != titular():
            raise ValueError("ese perfil no existe")
        antes = json.loads(fila[0]) if fila else copy.deepcopy(PERFIL_GLOBAL["datos"])
        datos = copy.deepcopy(antes)
        cambiar(datos)
        if datos == antes and fila is not None:
            return datos
        ahora = _ahora()
        if fila is None:
            conn.execute("INSERT INTO perfiles (chat_id, estado, datos, version, creado, actualizado)"
                         " VALUES (?, 'activo', ?, 1, ?, ?)", (chat_id, _json(datos), ahora, ahora))
        else:
            conn.execute("UPDATE perfiles SET datos = ?, version = version + 1, actualizado = ? WHERE chat_id = ?",
                         (_json(datos), ahora, chat_id))
        return datos


# ----------------------------------------------------------------------
# AVISOS
# ----------------------------------------------------------------------

_COLUMNAS_AVISO = "url, titulo, empresa, relevancia, fuente, avisada, respuesta, motivo, respondida"


def vacante_de(oferta: dict) -> str:
    """dedupe_difuso.clave_de_vacante en texto. Lo normalizado no lleva «|», así que no hay ambigüedad."""
    return "|".join(clave_de_vacante(oferta))


def _fila_a_aviso(fila: tuple) -> dict:
    aviso_ = dict(zip(("url", "titulo", "empresa", "relevancia", "fuente", "avisada"), fila))
    # Estos tres no existen hasta que se responde, y «Deshacer» los vuelve a quitar.
    for campo, valor in zip(("respuesta", "motivo", "respondida"), fila[6:]):
        if valor is not None:
            aviso_[campo] = valor
    return aviso_


@_vigilar
def registrar_aviso(chat_id: int, clave: str, url: str, titulo: str | None = None,
                    empresa: str | None = None, relevancia: float | None = None,
                    fuente: str | None = None, vacante: str | None = None, tipo: str = "aviso",
                    avisada: str | None = None) -> None:
    """
    Apunta un aviso. Volver a avisar de lo mismo conserva su fecha y su respuesta.

    `tipo` «top» es una oferta enseñada con /top (MP5): lleva sus botones, así que necesita su
    fila, pero no es un aviso y no cuenta en las cifras (`cifras_de_avisos`). Si la oferta ya se
    avisó, se queda como «aviso»: enseñarla otra vez no cambia lo que pasó.
    """
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        conn.execute(
            "INSERT INTO avisos (chat_id, clave, url, vacante, tipo, titulo, empresa, relevancia,"
            " fuente, avisada) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (chat_id, clave) DO UPDATE SET url = excluded.url,"
            " vacante = excluded.vacante,"
            " tipo = CASE WHEN avisos.tipo = 'aviso' THEN 'aviso' ELSE excluded.tipo END,"
            " titulo = excluded.titulo,"
            " empresa = excluded.empresa, relevancia = excluded.relevancia,"
            " fuente = excluded.fuente, avisada = COALESCE(avisos.avisada, excluded.avisada)",
            (chat_id, clave, url, vacante, tipo, titulo, empresa, relevancia, fuente, avisada or _ahora()))


@_vigilar
def aviso(chat_id: int, clave: str) -> dict | None:
    fila = conexion().execute(f"SELECT {_COLUMNAS_AVISO} FROM avisos WHERE chat_id = ? AND clave = ?",
                              (_exigir(chat_id), clave)).fetchone()
    return _fila_a_aviso(fila) if fila else None


@_vigilar
def avisos(chat_id: int) -> dict[str, dict]:
    """{clave: aviso} de este perfil: lo que era telegram_avisos.json."""
    return {f[0]: _fila_a_aviso(f[1:]) for f in conexion().execute(
        f"SELECT clave, {_COLUMNAS_AVISO} FROM avisos WHERE chat_id = ?", (_exigir(chat_id),))}


@_vigilar
def anotar_respuesta(chat_id: int, clave: str, respuesta: str | None, motivo: str | None = None) -> None:
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        if respuesta is None:
            conn.execute("UPDATE avisos SET respuesta = NULL, motivo = NULL, respondida = NULL"
                         " WHERE chat_id = ? AND clave = ?", (chat_id, clave))
        elif motivo or respuesta == "oculta":
            # Un 🙈 sin motivo conserva el que ya hubiera (pulsarlo tras elegir el motivo).
            conn.execute("UPDATE avisos SET respuesta = ?, respondida = ?, motivo = COALESCE(?, motivo)"
                         " WHERE chat_id = ? AND clave = ?", (respuesta, _ahora(), motivo, chat_id, clave))
        else:
            conn.execute("UPDATE avisos SET respuesta = ?, respondida = ?, motivo = NULL"
                         " WHERE chat_id = ? AND clave = ?", (respuesta, _ahora(), chat_id, clave))


@_vigilar
def cifras_de_avisos(chat_id: int, dias_semana: int = 7, dias_precision: int = 30) -> dict:
    """
    Los números de un perfil que el titular puede ver (D9): cuántos avisos en la última semana y,
    de los avisos de los últimos 30 días que tienen respuesta, cuántos fueron ⭐ o ✅. Nunca qué
    ofertas ni qué marcó: solo cuentas.
    """
    chat_id = _exigir(chat_id)
    ahora = datetime.now()
    semana = (ahora - timedelta(days=dias_semana)).isoformat(timespec="seconds")
    mes = (ahora - timedelta(days=dias_precision)).isoformat(timespec="seconds")
    n_semana = conexion().execute(
        "SELECT COUNT(*) FROM avisos WHERE chat_id = ? AND tipo = 'aviso' AND avisada >= ?",
        (chat_id, semana)).fetchone()[0]
    respondidos, positivos = conexion().execute(
        "SELECT COUNT(*), COALESCE(SUM(respuesta IN ('favorito', 'aplicada')), 0) FROM avisos"
        " WHERE chat_id = ? AND tipo = 'aviso' AND avisada >= ? AND respuesta IS NOT NULL",
        (chat_id, mes)).fetchone()
    return {"semana": n_semana, "respondidos": respondidos, "positivos": positivos}


@_vigilar
def paso_de(chat_id: int) -> str | None:
    """El paso de conversación en el que está este chat (encuesta, ajustes), si hay uno."""
    fila = conexion().execute("SELECT paso FROM conversacion WHERE chat_id = ?", (_exigir(chat_id),)).fetchone()
    return fila[0] if fila else None


# ----------------------------------------------------------------------
# CONVERSACIÓN (MP8): el borrador de la encuesta y de /ajustes
#
# Una fila por chat, con el paso en el que está, sus respuestas hasta ahora y el mensaje que se va
# editando. El esquema ya la traía desde MP1 y nadie la usaba: no hay migración. Se escribe en cada
# paso (un reinicio a mitad no pierde nada) y es del chat que la tiene: ninguna función de aquí mira
# la de otro.
# ----------------------------------------------------------------------
@_vigilar
def conversacion(chat_id: int) -> dict | None:
    """{paso, borrador, mensaje_id, actualizado} de este chat, o None si no tiene ninguna abierta."""
    fila = conexion().execute("SELECT paso, borrador, mensaje_id, actualizado FROM conversacion"
                              " WHERE chat_id = ?", (_exigir(chat_id),)).fetchone()
    if fila is None:
        return None
    try:
        borrador = json.loads(fila[1]) if fila[1] else {}
    except ValueError:
        borrador = {}
    return {"paso": fila[0], "borrador": borrador if isinstance(borrador, dict) else {},
            "mensaje_id": fila[2], "actualizado": fila[3]}


@_vigilar
def guardar_conversacion(chat_id: int, paso: str, borrador: dict, mensaje_id: int | None) -> None:
    """Crea o actualiza la conversación de este chat. `actualizado` es lo que mide el recordatorio."""
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        conn.execute(
            "INSERT INTO conversacion (chat_id, paso, borrador, mensaje_id, actualizado) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (chat_id) DO UPDATE SET paso = excluded.paso, borrador = excluded.borrador,"
            " mensaje_id = excluded.mensaje_id, actualizado = excluded.actualizado",
            (chat_id, paso, _json(borrador), mensaje_id, _ahora()))


@_vigilar
def anotar_en_borrador(chat_id: int, borrador: dict) -> None:
    """Cambia solo el borrador, sin tocar `actualizado`: lo que mide el reloj es lo que hace la persona."""
    with _transaccion() as conn:
        conn.execute("UPDATE conversacion SET borrador = ? WHERE chat_id = ?", (_json(borrador), _exigir(chat_id)))


@_vigilar
def borrar_conversacion(chat_id: int) -> bool:
    with _transaccion() as conn:
        return conn.execute("DELETE FROM conversacion WHERE chat_id = ?", (_exigir(chat_id),)).rowcount > 0


@_vigilar
def conversaciones_abiertas() -> list[dict]:
    """
    [{chat_id, actualizado, borrador}] de todas, para el recordatorio de las 24 h y el borrado de los
    7 días. Es la única lectura que cruza chats, y no enseña nada a nadie: solo la usa el servicio y
    solo para contar el tiempo.
    """
    salida = []
    for chat_id, actualizado, borrador in conexion().execute(
            "SELECT chat_id, actualizado, borrador FROM conversacion"):
        try:
            datos = json.loads(borrador) if borrador else {}
        except ValueError:
            datos = {}
        salida.append({"chat_id": chat_id, "actualizado": actualizado, "borrador": datos})
    return salida


@_vigilar
def relevancias(chat_id: int) -> list[tuple[str, float]]:
    """[(url, relevancia guardada)] de este perfil, de mayor a menor: para elegir la calibración (MP8)."""
    return [(f[0], f[1]) for f in conexion().execute(
        "SELECT url, relevancia FROM ofertas_perfil WHERE chat_id = ? AND relevancia IS NOT NULL"
        " ORDER BY relevancia DESC, url", (_exigir(chat_id),))]


@_vigilar
def vacantes_avisadas(chat_id: int, desde: str) -> list[tuple[str, str, str]]:
    """[(vacante, url, avisada)] de lo que se le ha avisado a este perfil desde esa marca."""
    return conexion().execute(
        "SELECT vacante, url, avisada FROM avisos WHERE chat_id = ? AND avisada >= ?"
        " AND vacante IS NOT NULL", (_exigir(chat_id), desde)).fetchall()


@_vigilar
def podar_avisos(dias: int = 90) -> int:
    """Borra los avisos más viejos que eso, sean de quien sean: el índice es del sistema."""
    corte = (datetime.now() - timedelta(days=dias)).isoformat()
    with _transaccion() as conn:
        return conn.execute("DELETE FROM avisos WHERE avisada IS NULL OR avisada < ?", (corte,)).rowcount


# ----------------------------------------------------------------------
# OFERTAS
# ----------------------------------------------------------------------


def _partir(oferta: dict) -> tuple:
    """
    Separa un registro en lo del portal, lo del perfil y la descripción, de forma que
    _componer() devuelva el mismo diccionario, ausente frente a None incluido.
    """
    portal = dict(oferta)
    url = portal.pop("job_url")
    enlace: dict = {}
    extra: dict = {}
    for campo, columna in _COLUMNAS_DEL_ENLACE.items():
        if campo not in portal:
            continue
        valor = portal.pop(campo)
        if valor is None:
            extra[campo] = None  # NULL en la columna significa que el campo no estaba
        else:
            enlace[columna] = _json(valor) if columna == "motivos" else valor
    for campo in _CAMPOS_EXTRA_DEL_ENLACE:
        if campo in portal:
            extra[campo] = portal.pop(campo)
    texto, podada = None, 0
    descripcion = portal.get("description")
    if descripcion is None and portal.get("descripcion_podada") is True:
        portal.pop("description", None)
        del portal["descripcion_podada"]
        podada = 1
    elif isinstance(descripcion, str) and descripcion:
        texto = portal.pop("description")
    return url, portal, enlace, extra, texto, podada


_LEER_OFERTA = (
    "SELECT op.url, o.datos, op.relevancia, op.motivos, op.categoria, op.ambito, op.busqueda,"
    " op.visto_primera, op.visto_ultima, op.extra, {descripcion} FROM {desde}"
)
_CON_TEXTO = "d.texto, d.podada"
_SIN_TEXTO = "NULL, NULL"
_DESDE_EL_ENLACE = ("ofertas_perfil op JOIN ofertas o ON o.url = op.url"
                    " LEFT JOIN descripciones d ON d.url = op.url")
_DESDE_EL_ENLACE_SIN_TEXTO = "ofertas_perfil op JOIN ofertas o ON o.url = op.url"
# CROSS JOIN fija el orden: se entra por el índice del bloque, que son unas pocas filas, y
# no por los enlaces del perfil. Sin estadísticas el planificador podría elegir al revés.
_DESDE_EL_BLOQUE = ("ofertas o CROSS JOIN ofertas_perfil op ON op.chat_id = ? AND op.url = o.url"
                    " LEFT JOIN descripciones d ON d.url = o.url")


def _componer(fila: tuple) -> dict:
    url, datos, relevancia, motivos, categoria, ambito, busqueda, primera, ultima, extra, texto, podada = fila
    oferta = json.loads(datos)
    oferta["job_url"] = url
    if relevancia is not None:
        oferta["relevancia"] = relevancia
    if motivos is not None:
        oferta["motivos_relevancia"] = json.loads(motivos)
    if categoria is not None:
        oferta["categoria"] = categoria
    if ambito is not None:
        oferta["ambito"] = ambito
    if busqueda is not None:
        oferta["busqueda"] = busqueda
    if primera is not None:
        oferta["visto_por_primera_vez"] = primera
    if ultima is not None:
        oferta["visto_por_ultima_vez"] = ultima
    if extra:
        oferta.update(json.loads(extra))
    if podada:
        oferta["description"] = None
        oferta["descripcion_podada"] = True
    elif texto is not None:
        oferta["description"] = texto
    return oferta


def iterar_ofertas(chat_id: int, con_texto: bool = True) -> Iterator[dict]:
    """
    Las ofertas de este perfil, de una en una y en el orden de la lista: leerlas no exige
    tener la base entera en memoria (ese era el coste del `_cache_ofertas` de antes).
    Sin `con_texto`, sin la descripción, que es el 80 % de lo que pesa (ver /misdatos).
    """
    consulta = (_LEER_OFERTA.format(descripcion=_CON_TEXTO, desde=_DESDE_EL_ENLACE) if con_texto else
                _LEER_OFERTA.format(descripcion=_SIN_TEXTO, desde=_DESDE_EL_ENLACE_SIN_TEXTO))
    return map(_componer, conexion().execute(
        consulta + " WHERE op.chat_id = ? ORDER BY op.protegida DESC, op.orden", (_exigir(chat_id),)))


@_vigilar
def ofertas_de(chat_id: int) -> list[dict]:
    return list(iterar_ofertas(chat_id))


@_vigilar
def contar_ofertas(chat_id: int) -> int:
    return conexion().execute("SELECT COUNT(*) FROM ofertas_perfil WHERE chat_id = ?",
                              (_exigir(chat_id),)).fetchone()[0]


def _trozos(valores: Iterable, tamaño: int = _TROZO) -> Iterator[list]:
    lista = list(valores)
    for i in range(0, len(lista), tamaño):
        yield lista[i:i + tamaño]


@_vigilar
def ofertas_por_url(chat_id: int, urls: Iterable[str]) -> dict[str, dict]:
    """{url: oferta} de las que este perfil tiene enlazadas; las demás, como si no existieran."""
    chat_id = _exigir(chat_id)
    consulta = _LEER_OFERTA.format(descripcion=_CON_TEXTO, desde=_DESDE_EL_ENLACE)
    fuera: dict[str, dict] = {}
    for trozo in _trozos(set(urls)):
        huecos = ",".join("?" * len(trozo))
        for fila in conexion().execute(f"{consulta} WHERE op.chat_id = ? AND op.url IN ({huecos})",
                                       (chat_id, *trozo)):
            fuera[fila[0]] = _componer(fila)
    return fuera


@_vigilar
def ofertas_en_bloques(chat_id: int, claves: Iterable[str]) -> list[dict]:
    """
    Las ofertas de este perfil en esos bloques del dedupe (dedupe_difuso.claves_bloque). Es
    lo que evita cargar la base entera para reconocer una republicación o un duplicado.
    """
    chat_id = _exigir(chat_id)
    consulta = _LEER_OFERTA.format(descripcion=_CON_TEXTO, desde=_DESDE_EL_BLOQUE)
    claves = set(claves)
    por_url: dict[str, dict] = {}
    for columna, prefijo in (("bloque_empresa", "e:"), ("bloque_titulo", "t:")):
        for trozo in _trozos(c for c in claves if c.startswith(prefijo)):
            huecos = ",".join("?" * len(trozo))
            for fila in conexion().execute(f"{consulta} WHERE o.{columna} IN ({huecos})",
                                           (chat_id, *trozo)):
                por_url[fila[0]] = _componer(fila)
    return list(por_url.values())


@_vigilar
def ofertas_recientes(chat_id: int, desde: str) -> list[dict]:
    """Las que este perfil vio por primera vez desde esa marca, sin la descripción."""
    consulta = _LEER_OFERTA.format(descripcion=_SIN_TEXTO, desde=_DESDE_EL_ENLACE_SIN_TEXTO)
    return [_componer(f) for f in conexion().execute(
        consulta + " WHERE op.chat_id = ? AND op.visto_primera >= ?", (_exigir(chat_id), desde))]


@_vigilar
def mejores_vigentes(chat_id: int, desde: str, limite: int, sin_marcar: bool = False) -> list[str]:
    """
    Las URL de las `limite` mejores ofertas de este perfil vistas desde esa marca, sin las que ha
    ocultado o aplicado, por la relevancia guardada (para /top, MP5). Es una preselección: quien
    llama las repuntúa con el puntuador de hoy, como los avisos.

    `sin_marcar` (MP9): ni siquiera las que guardó con ⭐. El /top completo es lo que aún no ha
    clasificado; una favorita ya está clasificada y tiene su sitio en «Me interesa».
    """
    acciones = "" if sin_marcar else " AND m.accion IN ('oculta', 'aplicada')"
    return [f[0] for f in conexion().execute(
        "SELECT op.url FROM ofertas_perfil op WHERE op.chat_id = ? AND op.visto_ultima >= ?"
        " AND NOT EXISTS (SELECT 1 FROM marcas m WHERE m.chat_id = op.chat_id AND m.url = op.url"
        + acciones + ") ORDER BY op.relevancia DESC, op.url LIMIT ?",
        (_exigir(chat_id), desde, limite))]


# ----------------------------------------------------------------------
# LA COLA DE AVISOS (MP9, tabla `pendientes`)
#
# Lo que le toca a una persona y todavía no se le ha enseñado: porque es de noche para ella, porque
# eligió un resumen o porque ya llegó a su tope del día. Guarda solo la URL y la nota con la que
# entró; lo demás (título, años, alertas…) se vuelve a leer de la oferta al enviarla, con el
# puntuador de ese día, igual que hace /top. La tabla existe desde MP1 y estaba vacía.
# ----------------------------------------------------------------------
@_vigilar
def encolar_pendientes(chat_id: int, filas: Iterable[tuple[str, float | None]], creada: str | None = None) -> int:
    """Añade a la cola de este perfil. Una oferta que ya espera conserva su sitio. Devuelve cuántas nuevas."""
    chat_id, creada = _exigir(chat_id), creada or _ahora()
    filas = list(filas)
    if not filas:
        return 0
    with _transaccion() as conn:
        return sum(conn.execute("INSERT OR IGNORE INTO pendientes (chat_id, url, relevancia, creada)"
                                " VALUES (?, ?, ?, ?)", (chat_id, url, relevancia, creada)).rowcount
                   for url, relevancia in filas)


@_vigilar
def pendientes(chat_id: int) -> list[dict]:
    """
    La cola de este perfil, la mejor primero y a igualdad la que entró más tarde:
    [{url, relevancia, creada, visto_ultima}]. `visto_ultima` es la última vez que un portal
    la devolvió a este perfil (None si ya no la enlaza): con eso caduca.

    El orden de envío lo decide `avisos_perfil.orden_de_salida`, con la nota de HOY (la cola se vuelve a
    puntuar al salir); el de aquí es el mismo criterio con la nota con la que se encoló.
    """
    return [dict(zip(("url", "relevancia", "creada", "visto_ultima"), f)) for f in conexion().execute(
        "SELECT p.url, p.relevancia, p.creada, op.visto_ultima FROM pendientes p"
        " LEFT JOIN ofertas_perfil op ON op.chat_id = p.chat_id AND op.url = p.url"
        " WHERE p.chat_id = ? ORDER BY p.relevancia DESC, p.creada DESC, p.url", (_exigir(chat_id),))]


@_vigilar
def contar_pendientes(chat_id: int) -> int:
    return conexion().execute("SELECT COUNT(*) FROM pendientes WHERE chat_id = ?", (_exigir(chat_id),)).fetchone()[0]


@_vigilar
def quitar_pendientes(chat_id: int, urls: Iterable[str]) -> int:
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        return sum(conn.execute(f"DELETE FROM pendientes WHERE chat_id = ? AND url IN ({','.join('?' * len(t))})",
                                (chat_id, *t)).rowcount for t in _trozos(urls))


@_vigilar
def urls_ya_tratadas(chat_id: int, urls: Iterable[str]) -> set[str]:
    """
    De esas URL, las que esta persona ya clasificó (una marca) o ya vio como oferta (una fila de
    `avisos` de cualquier tipo, también la de /top). Una de la cola que cae aquí no se vuelve a enseñar.
    """
    chat_id, hechas = _exigir(chat_id), set()
    for t in _trozos(urls):
        ph = ",".join("?" * len(t))
        for tabla in ("marcas", "avisos"):
            hechas |= {f[0] for f in conexion().execute(
                f"SELECT url FROM {tabla} WHERE chat_id = ? AND url IN ({ph})", (chat_id, *t))}
    return hechas


@_vigilar
def contar_enviados(chat_id: int, desde: str) -> int:
    """Lo que se le ha mandado a esta persona desde esa marca: avisos y ofertas para descubrir (MP9, tope diario)."""
    return conexion().execute("SELECT COUNT(*) FROM avisos WHERE chat_id = ? AND tipo IN ('aviso', 'descubrimiento')"
                              " AND avisada >= ?", (_exigir(chat_id), desde)).fetchone()[0]


@_vigilar
def quitar_avisos_sin_responder(chat_id: int, claves: Iterable[str]) -> int:
    """
    Deshace el apunte de un aviso cuyo envío ha fallado: sin él contaría para el tope y para «no repetir»
    como si se hubiera mandado. Nunca toca uno con respuesta ni uno que no sea un aviso.
    """
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        return sum(conn.execute(f"DELETE FROM avisos WHERE chat_id = ? AND respuesta IS NULL AND tipo = 'aviso'"
                                f" AND clave IN ({','.join('?' * len(t))})", (chat_id, *t)).rowcount
                   for t in _trozos(claves))


@_vigilar
def posiciones(chat_id: int) -> dict[str, tuple[int, int]]:
    """{url: (protegida, orden)} de este perfil: la lista sin el contenido de las ofertas."""
    return {f[0]: (f[1], f[2]) for f in conexion().execute(
        "SELECT url, protegida, orden FROM ofertas_perfil WHERE chat_id = ?", (_exigir(chat_id),))}


@_vigilar
def descripciones_de(chat_id: int) -> dict[str, str]:
    """{url: descripción} de las ofertas de este perfil que la conservan."""
    return {f[0]: f[1] for f in conexion().execute(
        "SELECT d.url, d.texto FROM ofertas_perfil op JOIN descripciones d ON d.url = op.url"
        " WHERE op.chat_id = ? AND d.texto IS NOT NULL", (_exigir(chat_id),))}


@_vigilar
def descripcion_de(chat_id: int, url: str) -> str | None:
    """El texto de UNA oferta. None si este perfil no la tiene; "" si la tiene sin texto."""
    fila = conexion().execute(
        "SELECT d.texto FROM ofertas_perfil op LEFT JOIN descripciones d ON d.url = op.url"
        " WHERE op.chat_id = ? AND op.url = ?", (_exigir(chat_id), url)).fetchone()
    if fila is None:
        return None
    return fila[0] or ""


def _la_enlaza_otro(conn: sqlite3.Connection, chat_id: int, url: str) -> bool:
    return conn.execute("SELECT 1 FROM ofertas_perfil WHERE url = ? AND chat_id <> ? LIMIT 1",
                        (url, chat_id)).fetchone() is not None


def _escribir_oferta(conn: sqlite3.Connection, chat_id: int, oferta: dict, protegida: int,
                     orden: int) -> bool:
    """
    Guarda una oferta y el enlace de este perfil tocando solo lo que ha cambiado, para no
    gastar disco en lo que sigue igual. Devuelve si ha cambiado algo.

    La fila de `ofertas` es de todos: actualizarla la actualiza para todos, y los campos
    del descubrimiento siguen la regla de almacen._competencia_original también aquí,
    porque la oferta puede venir de un perfil que no la conocía.
    """
    url, portal, enlace, extra, texto, podada = _partir(oferta)
    cambio = False
    primera, ultima = enlace.get("visto_primera"), enlace.get("visto_ultima")
    previa = conn.execute("SELECT datos, visto_primera, visto_ultima FROM ofertas WHERE url = ?",
                          (url,)).fetchone()
    if previa is None:
        conn.execute("INSERT INTO ofertas (url, datos, bloque_empresa, bloque_titulo, visto_primera,"
                     " visto_ultima) VALUES (?, ?, ?, ?, ?, ?)",
                     (url, _json(portal), *_bloques(oferta), primera, ultima))
        cambio = True
    else:
        anterior = json.loads(previa[0])
        for campo in CAMPOS_DEL_DESCUBRIMIENTO:
            if anterior.get(campo) is not None and portal.get(campo) is None:
                portal[campo] = anterior[campo]
        datos = _json(portal)
        en_el_sistema = (min(filter(None, (previa[1], primera)), default=None),
                         max(filter(None, (previa[2], ultima)), default=None))
        if datos != previa[0]:
            conn.execute("UPDATE ofertas SET datos = ?, bloque_empresa = ?, bloque_titulo = ?,"
                         " visto_primera = ?, visto_ultima = ? WHERE url = ?",
                         (datos, *_bloques(oferta), *en_el_sistema, url))
            cambio = True
        elif en_el_sistema != (previa[1], previa[2]):
            # Por separado: lo normal en una corrida es que solo cambie esto, y así no se
            # tocan las páginas de los índices de bloque.
            conn.execute("UPDATE ofertas SET visto_primera = ?, visto_ultima = ? WHERE url = ?",
                         (*en_el_sistema, url))
            cambio = True

    fila_texto = conn.execute("SELECT texto, podada FROM descripciones WHERE url = ?", (url,)).fetchone()
    if texto is None and not podada:
        # Llegar sin texto no es decir que ya no lo tiene: es una descarga que falló. Con un solo
        # perfil se borraba, y se sigue borrando; con otro enlazándola, le quitaría el suyo.
        if fila_texto is not None and not _la_enlaza_otro(conn, chat_id, url):
            conn.execute("DELETE FROM descripciones WHERE url = ?", (url,))
            cambio = True
    elif fila_texto != (texto, podada):
        conn.execute("INSERT OR REPLACE INTO descripciones (url, texto, podada) VALUES (?, ?, ?)",
                     (url, texto, podada))
        cambio = True

    columnas = ("relevancia", "motivos", "categoria", "ambito", "busqueda", "visto_primera",
                "visto_ultima", "orden", "protegida", "extra")
    valores = (*(enlace.get(c) for c in columnas[:7]), orden, protegida,
               _json(extra) if extra else None)
    actual = conn.execute(f"SELECT {', '.join(columnas)} FROM ofertas_perfil WHERE chat_id = ? AND url = ?",
                          (chat_id, url)).fetchone()
    if actual is None:
        conn.execute(f"INSERT INTO ofertas_perfil (chat_id, url, {', '.join(columnas)})"
                     f" VALUES (?, ?, {', '.join('?' * len(columnas))})", (chat_id, url, *valores))
        cambio = True
    elif actual != valores:
        distintas = [(c, v) for c, a, v in zip(columnas, actual, valores) if a != v]
        conn.execute(f"UPDATE ofertas_perfil SET {', '.join(c + ' = ?' for c, _ in distintas)}"
                     " WHERE chat_id = ? AND url = ?", (*(v for _, v in distintas), chat_id, url))
        cambio = True
    return cambio


def _quitar_enlace(conn: sqlite3.Connection, chat_id: int, url: str) -> bool:
    """
    Quita la oferta de ESTE perfil. La fila compartida solo se borra si no queda nadie que la
    enlace: es lo que hace que el dedupe o el borrado de uno no le quiten nada a otro.
    """
    borrados = conn.execute("DELETE FROM ofertas_perfil WHERE chat_id = ? AND url = ?",
                            (chat_id, url)).rowcount
    if borrados and conn.execute("SELECT 1 FROM ofertas_perfil WHERE url = ? LIMIT 1", (url,)).fetchone() is None:
        conn.execute("DELETE FROM ofertas WHERE url = ?", (url,))
        conn.execute("DELETE FROM descripciones WHERE url = ?", (url,))
    return bool(borrados)


@_vigilar
def quitar_enlace(chat_id: int, url: str) -> bool:
    chat_id = _exigir(chat_id)
    with _transaccion() as conn:
        quitado = _quitar_enlace(conn, chat_id, url)
        if quitado:
            _subir_firma(conn, chat_id)
        return quitado


@_vigilar
def aplicar_cambios(chat_id: int, escribir: Iterable[tuple[dict, int, int]] = (),
                    quitar: Iterable[str] = (), mover: Iterable[tuple[str, int, int]] = (),
                    ultima_busqueda: str | None = None, dedupe_hecho: bool = False) -> bool:
    """
    Aplica de una vez lo que decidió una fusión: todo o nada, para que un corte a mitad no
    deje enlaces quitados sin sus actualizaciones.

    `escribir` son (oferta, protegida, orden), `quitar` enlaces de este perfil y `mover`
    (url, protegida, orden) de las que solo cambian de sitio en la lista.
    """
    chat_id = _exigir(chat_id)
    cambio = False
    with _transaccion() as conn:
        for url in quitar:
            cambio = _quitar_enlace(conn, chat_id, url) or cambio
        for oferta, protegida, orden in escribir:
            cambio = _escribir_oferta(conn, chat_id, oferta, protegida, orden) or cambio
        for url, protegida, orden in mover:
            conn.execute("UPDATE ofertas_perfil SET protegida = ?, orden = ? WHERE chat_id = ? AND url = ?",
                         (protegida, orden, chat_id, url))
            cambio = True
        if cambio:
            _subir_firma(conn, chat_id)
        if ultima_busqueda is not None:
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                         (f"ultima_busqueda:{chat_id}", ultima_busqueda))
        if dedupe_hecho:
            conn.execute("DELETE FROM meta WHERE clave = ?", (f"dedupe_pendiente:{chat_id}",))
    return cambio


@_vigilar
def reemplazar_ofertas(chat_id: int, ofertas: list[dict]) -> None:
    """
    Deja a este perfil exactamente estas ofertas, en este orden (almacen.guardar_ofertas).
    Un registro sin job_url no se puede guardar: `fusionar_ofertas` tampoco los conservaba.
    """
    chat_id = _exigir(chat_id)
    actuales = posiciones(chat_id)
    lista = [o for o in ofertas if o.get("job_url")]
    urls = [o["job_url"] for o in lista]
    quedan = set(urls)
    orden_actual = sorted((u for u in actuales if u in quedan),
                          key=lambda u: (-actuales[u][0], actuales[u][1]))
    # Lo normal (web, «Limpiar») es filtrar sin reordenar: entonces no hay que renumerar nada.
    mismo_orden = orden_actual == urls
    with _transaccion() as conn:
        quitadas = False
        for url in actuales.keys() - quedan:
            quitadas = _quitar_enlace(conn, chat_id, url) or quitadas
        contenido = False
        for i, oferta in enumerate(lista):
            protegida, orden = actuales[oferta["job_url"]] if mismo_orden else (0, i)
            contenido = _escribir_oferta(conn, chat_id, oferta, protegida, orden) or contenido
        if quitadas or contenido:
            _subir_firma(conn, chat_id)
        if contenido:
            # Un registro que cambia puede convertirse en duplicado de otro sin que ninguna
            # de las dos pase por la fusión: la próxima mira todas las candidatas.
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, '1')", (f"dedupe_pendiente:{chat_id}",))


@_vigilar
def podables(chat_id: int, limite: str) -> dict[str, str]:
    """
    {url: texto} de las descripciones que este perfil ya puede podar: las tiene ocultas
    desde antes de `limite` (o sin fecha) y sin favorita ni aplicada que las proteja, y
    ningún OTRO perfil que enlace la oferta las necesita. Basta uno que no la tenga en
    ese mismo estado para conservar el texto (D1): la fila de `descripciones` es de todos.
    """
    return {f[0]: f[1] for f in conexion().execute(
        """
        WITH en_desuso (chat_id, url) AS (
            SELECT m.chat_id, m.url FROM marcas m
            WHERE m.accion = 'oculta' AND (m.fecha IS NULL OR m.fecha < :limite)
              AND NOT EXISTS (SELECT 1 FROM marcas x WHERE x.chat_id = m.chat_id AND x.url = m.url
                              AND x.accion IN ('favorita', 'aplicada'))
        )
        SELECT d.url, d.texto FROM en_desuso e
        JOIN ofertas_perfil op ON op.chat_id = e.chat_id AND op.url = e.url
        JOIN descripciones d ON d.url = e.url
        WHERE e.chat_id = :chat AND d.texto IS NOT NULL AND d.texto <> ''
          AND NOT EXISTS (SELECT 1 FROM ofertas_perfil otro WHERE otro.url = e.url
                          AND NOT EXISTS (SELECT 1 FROM en_desuso u
                                          WHERE u.chat_id = otro.chat_id AND u.url = otro.url))
        """, {"chat": _exigir(chat_id), "limite": limite})}


@_vigilar
def podar(chat_id: int, urls: Iterable[str]) -> int:
    """Quita el texto y deja la señal de que lo hubo: el registro y su enlace se quedan."""
    chat_id = _exigir(chat_id)
    podadas = 0
    with _transaccion() as conn:
        for url in urls:
            podadas += conn.execute("UPDATE descripciones SET texto = NULL, podada = 1 WHERE url = ?",
                                    (url,)).rowcount
        if podadas:
            _subir_firma(conn, chat_id)
    return podadas


# ----------------------------------------------------------------------
# CONSULTAS (MP3, D5): cuándo se hizo por última vez cada consulta a los portales. Es del sistema:
# la rotación mira la más antigua sin importar de quién sea.
# ----------------------------------------------------------------------


@_vigilar
def ultimas_consultas() -> dict[str, str | None]:
    """{clave: cuándo se hizo por última vez}. Sin fila o con None, no se ha hecho nunca."""
    return dict(conexion().execute("SELECT clave, ultima FROM consultas").fetchall())


@_vigilar
def anotar_consultas(plan: Iterable[tuple[str, str, str]], hechas: dict[str, int], cuando: str) -> None:
    """
    Deja la tabla como dice el plan y apunta lo hecho en esta corrida. `plan` son (clave, params,
    perfiles), con los dos últimos ya en JSON, y `hechas` {clave: resultados}.

    Una sola marca y una sola transacción por corrida. La marca única no es un detalle: la rotación
    ordena por `ultima` y, a igualdad, por el orden del plan, y eso es lo que hace que con un solo
    perfil salgan siempre las mismas 33 en el mismo orden. Lo que deja de estar en el plan se borra:
    si vuelve, no se ha hecho nunca y va la primera, que es lo que le conviene.
    """
    guardadas = {f[0]: f[1:] for f in conexion().execute("SELECT clave, params, perfiles FROM consultas")}
    vigentes = set()
    with _transaccion() as conn:
        for clave, params, quienes in plan:
            vigentes.add(clave)
            if clave not in guardadas:
                conn.execute("INSERT INTO consultas (clave, params, perfiles) VALUES (?, ?, ?)",
                             (clave, params, quienes))
            elif guardadas[clave] != (params, quienes):
                conn.execute("UPDATE consultas SET params = ?, perfiles = ? WHERE clave = ?",
                             (params, quienes, clave))
        for clave, resultados in hechas.items():
            conn.execute("UPDATE consultas SET ultima = ?, resultados = ? WHERE clave = ?",
                         (cuando, resultados, clave))
        for clave in guardadas.keys() - vigentes:
            conn.execute("DELETE FROM consultas WHERE clave = ?", (clave,))


# ----------------------------------------------------------------------
# ALTAS Y BAJAS DE PERFILES (MP3, MP5)
# ----------------------------------------------------------------------

JUEGO_DE_LA_PLANTILLA = "titular-2026-10"
# D8: quien entra con la plantilla solo recibe lo que pida 1 año o menos, como el adicional.
TOPE_DE_ANOS_DE_LA_PLANTILLA = 1
# Lo que /invitar deja elegir (MP5, decidido por el titular el 03-11): o la plantilla, que avisa
# desde la primera corrida, o la encuesta, que hasta MP8 solo dice «en preparación».
PLANTILLAS = ("soporte", "encuesta")
DIAS_DE_UNA_INVITACION = 7
# Las invitaciones viejas, usadas o no, no sirven para nada pasado un mes: se podan al crear otra.
DIAS_QUE_SE_GUARDA_UNA_INVITACION = 30


def datos_de_plantilla() -> dict:
    """
    El perfil de quien entra con la plantilla: lo que recibía el adicional (D8), sacado del juego.

    Sale de `titular-2026-10` y NO de config.json. Hasta MP5 se copiaba lo personal de config.json
    (`datos_del_adicional`), y desde MP4 eso es ciberseguridad: un alta nueva habría buscado con los
    términos del titular y puntuado con las reglas viejas. Comprobado el 03-11 contra la copia de la
    base de antes de la baja: el bloque del adicional era igual al `config` del juego en las 23
    claves salvo `anos_experiencia_max`, así que con solo el tope en el bloque sale lo mismo.

    El tope va en el bloque y fuera, como lo tenía el adicional: en la config efectiva manda el del
    bloque y, si no lo hay, `experiencia_max_exigida`. Sin ninguno de los dos se quedaría en el 3 del
    titular que trae el juego, y nada fallaría (lo vigila mutaciones_mp3.py).
    """
    return {"v": 1, "reglas": JUEGO_DE_LA_PLANTILLA,
            "config": {"anos_experiencia_max": TOPE_DE_ANOS_DE_LA_PLANTILLA},
            "experiencia_max_exigida": TOPE_DE_ANOS_DE_LA_PLANTILLA, "experiencia_estricta": True,
            "avisos": {"modo": "instantaneo"}}


def _hueco(conn: sqlite3.Connection, config_global: dict) -> bool:
    """¿Cabe uno más? El titular cuenta aunque no tenga fila, y el perfil sombra también (D4)."""
    tope = int(config_global.get("max_perfiles") or 0)
    existentes = {f[0] for f in conn.execute("SELECT chat_id FROM perfiles")} | {titular()}
    return not tope or len(existentes) < tope


def hay_hueco(config_global: dict) -> bool:
    return _hueco(conexion(), config_global)


@_vigilar
def crear_invitacion(plantilla: str, dias: int = DIAS_DE_UNA_INVITACION) -> str:
    """
    Un código de un solo uso (D4). `secrets.token_urlsafe(16)` son 22 caracteres de
    [A-Za-z0-9_-], que es lo que admite el parámetro `start` de Telegram (hasta 64): 128 bits, que
    no se adivinan.

    La plantilla va en `meta` y no en una columna nueva de `invitaciones`: con una columna habría
    que subir el esquema, y el código de MP4 se niega a abrir una base con un esquema que no conoce
    (`_aplicar_esquema`), así que volver atrás exigiría restaurar la copia y perder lo de después.
    """
    import secrets

    if plantilla not in PLANTILLAS:
        raise ValueError(f"Plantilla desconocida: {plantilla}")
    codigo = secrets.token_urlsafe(16)
    ahora = datetime.now()
    viejas = (ahora - timedelta(days=DIAS_QUE_SE_GUARDA_UNA_INVITACION)).isoformat(timespec="seconds")
    with _transaccion() as conn:
        for (vieja,) in conn.execute("SELECT codigo FROM invitaciones WHERE creada < ?", (viejas,)).fetchall():
            conn.execute("DELETE FROM meta WHERE clave = ?", (f"invitacion:{vieja}",))
        conn.execute("DELETE FROM invitaciones WHERE creada < ?", (viejas,))
        conn.execute("INSERT INTO invitaciones (codigo, creada, caduca) VALUES (?, ?, ?)",
                     (codigo, ahora.isoformat(timespec="seconds"),
                      (ahora + timedelta(days=dias)).isoformat(timespec="seconds")))
        conn.execute("INSERT INTO meta VALUES (?, ?)", (f"invitacion:{codigo}", plantilla))
    return codigo


@_vigilar
def usar_invitacion(codigo: str, chat_id: int, config_global: dict) -> str:
    """
    Da de alta a `chat_id` con esa invitación, si vale. Devuelve qué ha pasado: «alta»,
    «inexistente», «caducada», «usada», «sin_hueco» o «ya_tiene_perfil».

    Todo en UNA transacción, con la condición repetida en el propio UPDATE: dos /start a la vez con
    el mismo código (dos personas a las que se reenvió el enlace) no pueden crear dos perfiles.
    """
    chat_id = _exigir(chat_id)
    ahora = _ahora()
    with _transaccion() as conn:
        fila = conn.execute("SELECT caduca, usada_por FROM invitaciones WHERE codigo = ?", (codigo,)).fetchone()
        if fila is None:
            return "inexistente"
        if chat_id == titular() or conn.execute("SELECT 1 FROM perfiles WHERE chat_id = ?", (chat_id,)).fetchone():
            return "ya_tiene_perfil"
        if fila[1] is not None:
            return "usada"
        if fila[0] <= ahora:
            return "caducada"
        if not _hueco(conn, config_global):
            return "sin_hueco"
        if conn.execute("UPDATE invitaciones SET usada_por = ? WHERE codigo = ? AND usada_por IS NULL"
                        " AND caduca > ?", (chat_id, codigo, ahora)).rowcount != 1:
            return "usada"
        plantilla = (conn.execute("SELECT valor FROM meta WHERE clave = ?", (f"invitacion:{codigo}",))
                     .fetchone() or ("encuesta",))[0]
        datos, estado = ((datos_de_plantilla(), "activo") if plantilla == "soporte"
                         else (datos_a_cero(), "encuesta"))
        conn.execute("INSERT INTO perfiles (chat_id, estado, datos, version, creado, actualizado,"
                     " ultima_actividad) VALUES (?, ?, ?, 1, ?, ?, ?)",
                     (chat_id, estado, _json(datos), ahora, ahora, ahora))
        conn.execute("DELETE FROM meta WHERE clave = ?", (f"invitacion:{codigo}",))
    return "alta"


def invitaciones_pendientes() -> int:
    """Cuántas hay sin usar y sin caducar: solo un número, para /estado."""
    return conexion().execute("SELECT COUNT(*) FROM invitaciones WHERE usada_por IS NULL AND caduca > ?",
                              (_ahora(),)).fetchone()[0]


@_vigilar
def bloquear(chat_id: int) -> bool:
    """
    Telegram ha dicho que esta persona bloqueó el bot (403): estado `bloqueado`, fuera de
    `activos()`, sin consultas ni avisos. Se guarda el estado de antes para que un /start lo
    devuelva tal cual (un perfil pausado sigue pausado). El titular no se bloquea nunca: sin él no
    hay quien administre, y sus corridas mantienen la base. Devuelve si lo ha bloqueado.
    """
    chat_id = _exigir(chat_id)
    perfil = obtener(chat_id)
    if perfil is None or chat_id == titular() or perfil["estado"] == "bloqueado":
        return False
    guardar(chat_id, {**perfil["datos"], "antes_de_bloquear": perfil["estado"]}, "bloqueado")
    return True


@_vigilar
def desbloquear(chat_id: int) -> str | None:
    """El /start de quien estaba bloqueado: vuelve al estado de antes. Devuelve el nuevo, o None."""
    chat_id = _exigir(chat_id)
    perfil = obtener(chat_id)
    if perfil is None or perfil["estado"] != "bloqueado":
        return None
    datos = dict(perfil["datos"])
    estado = datos.pop("antes_de_bloquear", "activo")
    if estado not in _ESTADOS_DE_PERFIL or estado == "bloqueado":
        estado = "activo"
    guardar(chat_id, datos, estado)
    return estado


@_vigilar
def cambiar_estado(chat_id: int, estado: str) -> None:
    """Pausar y reanudar: solo el estado. Los datos no cambian, así que la versión tampoco."""
    chat_id = _exigir(chat_id)
    if estado not in _ESTADOS_DE_PERFIL:
        raise ValueError(f"Estado de perfil desconocido: {estado}")
    with _transaccion() as conn:
        conn.execute("UPDATE perfiles SET estado = ?, actualizado = ? WHERE chat_id = ?",
                     (estado, _ahora(), chat_id))


@_vigilar
def anotar_actividad(chat_id: int) -> None:
    """
    Última vez que esta persona hizo algo (D9: a los 60 días, pausa). Se escribe como mucho una vez
    al día: el dato es de días, y un clic no tiene por qué costar una escritura más en ese disco.
    """
    chat_id = _exigir(chat_id)
    ahora = _ahora()
    fila = conexion().execute("SELECT ultima_actividad FROM perfiles WHERE chat_id = ?", (chat_id,)).fetchone()
    if fila is None or (fila[0] or "")[:10] == ahora[:10]:
        return
    with _transaccion() as conn:
        conn.execute("UPDATE perfiles SET ultima_actividad = ? WHERE chat_id = ?", (ahora, chat_id))


def crear_desde(fichero: str | Path, config_global: dict) -> int:
    """
    Un perfil desde un JSON {"chat_id", "datos"[, "estado"]}. Devuelve su chat_id.

    Sirve para el perfil sombra, con el que se mide la carga sin molestar a nadie. Por eso un
    chat_id negativo, que no es de ninguna persona, exige `avisos.modo = "ninguno"`: el error de
    dejárselo puesto sería escribir a un grupo. El tope de perfiles se comprueba aquí y no en
    guardar(), que es lo que usa quien ya existe.
    """
    contenido = json.loads(Path(fichero).read_text(encoding="utf-8"))
    chat_id, datos = _exigir(contenido.get("chat_id")), contenido.get("datos")
    if not isinstance(datos, dict):
        raise ValueError("el fichero no trae los datos del perfil")
    if obtener(chat_id) is not None:
        raise ValueError("ese perfil ya existe")
    if chat_id < 0 and (datos.get("avisos") or {}).get("modo") != "ninguno":
        raise ValueError('un chat_id negativo exige "avisos": {"modo": "ninguno"}')
    tope = int(config_global.get("max_perfiles") or 0)
    existentes = {p["chat_id"] for p in listar()} | {titular()}
    if tope and len(existentes) >= tope:
        raise ValueError(f"ya hay {len(existentes)} perfiles y el tope es {tope}")
    # Un perfil mal escrito tiene que fallar aquí, y no en mitad de una corrida.
    _construir_efectiva(datos, config_global)
    guardar(chat_id, datos, contenido.get("estado", "activo"))
    return chat_id


def datos_a_cero(reiniciado: str | None = None) -> dict:
    """
    Los datos de un perfil que todavía no ha hecho la encuesta: lo que escribe `usar_invitacion` y, con la
    marca `reiniciado`, lo que deja un reinicio (`borrar(..., reiniciar=True)`).

    La marca es lo único que sobrevive a un reinicio, y hace dos trabajos. Uno: quien empezó una corrida
    con el perfil de antes sabe que ya no es el mismo y no le guarda ni le avisa de lo que buscó para el
    viejo (app._es_el_de_antes). Otro: /reiniciar se puede una vez cada 24 h, porque cada reinicio acaba en
    una corrida de arranque, que son hasta 8 consultas a los portales fuera de las 33 de la corrida (regla 2).
    """
    datos = {"v": 1, "reglas": "catalogo", "plan": {}}
    if reiniciado:
        datos["reiniciado"] = reiniciado
    return datos


@_vigilar
def borrar(chat_id: int, conservar_perfil: bool = False, reiniciar: bool = False) -> dict:
    """
    Quita todo lo de un perfil. Lo que solo enlazaba él desaparece; lo que enlaza otro se queda
    (D1, _quitar_enlace). Se cuenta lo quitado para poder decirlo sin nombrar a nadie.

    Con `reiniciar` (11-11) el perfil no se va: se queda su fila, con su fecha de alta y su sitio en el
    tope, pero con los datos a cero y en `encuesta`, como quien acaba de entrar con una invitación. Va en
    la MISMA transacción que el borrado: en dos, una corrida que pasara por medio vería un perfil activo
    con el plan de antes y la lista vacía, y se la volvería a llenar.

    El del titular también se reinicia desde el 12-11 (hasta entonces se negaba aquí y en el bot). Se
    pidió al preparar la copia pública: quien instala el bot arranca con el perfil de fábrica y puede
    querer empezar de cero sin tocar ficheros. Pierde sus reglas escritas a mano (`config: "global"`) y
    pasa a ser un perfil del cuestionario; config.json no se toca, así que lo de antes sigue ahí y en la
    copia. Mientras está en `encuesta` no entra en `activos()`: las corridas siguen para los demás y él
    sigue administrando. Sin fila (una instalación nueva antes de su primera corrida) se le crea la de
    siempre en esta misma transacción, para que el reinicio la ponga a cero.

    «Todo» incluye `meta`: cualquier clave que acabe en «:<su chat_id>» (la firma de su lista, su
    última búsqueda, las marcas `adicional_creado:`/`adicional_avisado:` de MP3, que el borrado del
    03-11 se dejó y hubo que quitar a mano) y la invitación con la que entró. `consultas.perfiles`
    no se toca aquí: es el plan de la corrida, y la siguiente lo reescribe sin él.

    El titular no se puede borrar (D4) salvo con `conservar_perfil`, que es su /borrar con doble
    confirmación: se va todo lo suyo y se queda su fila, con su juego, porque sigue siendo el
    administrador. Quien lo llame guarda antes una copia (regla 7): ver `exportar`.
    """
    chat_id = _exigir(chat_id)
    es_el_titular = chat_id == titular()
    if es_el_titular and not (conservar_perfil or reiniciar):
        raise ValueError("el titular no se puede borrar")
    with _transaccion() as conn:
        if reiniciar and conn.execute("SELECT 1 FROM perfiles WHERE chat_id = ?", (chat_id,)).fetchone() is None:
            if not es_el_titular:
                raise ValueError("ese perfil no existe")
            ahora = _ahora()
            conn.execute("INSERT INTO perfiles (chat_id, estado, datos, version, creado, actualizado)"
                         " VALUES (?, 'activo', ?, 1, ?, ?)", (chat_id, _json(PERFIL_GLOBAL["datos"]), ahora, ahora))
        urls = [f[0] for f in conn.execute("SELECT url FROM ofertas_perfil WHERE chat_id = ?", (chat_id,))]
        for url in urls:
            _quitar_enlace(conn, chat_id, url)
        quitado = {"enlaces": len(urls)}
        for tabla in ("marcas", "avisos", "conversacion", "pendientes"):
            quitado[tabla] = conn.execute(f"DELETE FROM {tabla} WHERE chat_id = ?", (chat_id,)).rowcount
        if reiniciar:
            # Con microsegundos, y no con `_ahora()`: la marca es también la identidad del perfil para una
            # corrida en marcha, y dos reinicios en el mismo segundo no pueden parecer el mismo perfil.
            marca = datetime.now().isoformat(timespec="microseconds")
            conn.execute("UPDATE perfiles SET estado = 'encuesta', datos = ?, version = version + 1, actualizado = ?"
                         " WHERE chat_id = ?", (_json(datos_a_cero(reiniciado=marca)), _ahora(), chat_id))
        elif not conservar_perfil:
            conn.execute("DELETE FROM perfiles WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM invitaciones WHERE usada_por = ?", (chat_id,))
        claves = [f[0] for f in conn.execute("SELECT clave FROM meta")
                  if f[0].rpartition(":")[2] == str(chat_id)]
        conn.executemany("DELETE FROM meta WHERE clave = ?", [(c,) for c in claves])
    # Si no, su config seguiría en memoria hasta el siguiente reinicio (CAMBIOS-MP2.md §7).
    with _lock_efectiva:
        _CACHE_EFECTIVA.pop(chat_id, None)
    return quitado


# ----------------------------------------------------------------------
# MIGRACIÓN DESDE LOS JSON
# ----------------------------------------------------------------------

_JSON_DE_ORIGEN = ("estado.json", "ofertas.json", "telegram_avisos.json")


def _huella(camino: Path) -> str:
    return hashlib.sha256(camino.read_bytes()).hexdigest()


def _huellas(carpeta: Path) -> dict[str, str]:
    return {n: _huella(carpeta / n) for n in _JSON_DE_ORIGEN if (carpeta / n).exists()}


def _copia_previa(origen: Path) -> None:
    """Copia a *.pre-mp1. Nunca pisa una copia distinta: esa es la que hay que conservar."""
    destino = origen.with_name(origen.name + ".pre-mp1")
    if destino.exists():
        if _huella(destino) == _huella(origen):
            return
        destino = origen.with_name(f"{origen.name}.pre-mp1-{datetime.now():%Y%m%d_%H%M%S}")
    temporal = destino.with_name(destino.name + ".tmp")
    shutil.copy2(origen, temporal)
    os.replace(temporal, destino)


def _leer_origen(camino: Path, tipo: type, por_defecto):
    # A diferencia de almacen._leer_json, aquí un fichero ilegible NO da «vacío»: migrar
    # vacío y apuntar que se ha migrado perdería los datos sin que nada avisara.
    if not camino.exists():
        return por_defecto
    datos = json.loads(camino.read_text(encoding="utf-8"))
    if not isinstance(datos, tipo):
        raise ValueError(f"{camino.name} no tiene el formato esperado")
    return datos


def _migrar_si_toca(conn: sqlite3.Connection, carpeta: Path) -> dict:
    """La migración, en UNA transacción: o entra entera o no ha pasado nada."""
    fila = conn.execute("SELECT valor FROM meta WHERE clave = 'migrado'").fetchone()
    huellas = _huellas(carpeta)
    if fila:
        guardadas = json.loads(conn.execute("SELECT valor FROM meta WHERE clave = 'huellas_json'").fetchone()[0])
        cambiados = sorted(n for n in huellas if guardadas.get(n) != huellas[n])
        if cambiados:
            logger.warning("Han cambiado desde la migración, y no se toca nada: %s", ", ".join(cambiados))
        return {"migrado_ahora": False, "ya_estaba": True, "json_cambiados": cambiados}
    if not huellas:
        return {"migrado_ahora": False, "ya_estaba": False, "json_cambiados": []}

    estado = _leer_origen(carpeta / "estado.json", dict, {})
    ofertas = _leer_origen(carpeta / "ofertas.json", list, [])
    avisos_json = _leer_origen(carpeta / "telegram_avisos.json", dict, {})
    for nombre in huellas:
        _copia_previa(carpeta / nombre)

    chat_id = _titular_del_entorno()
    if chat_id is None:
        chat_id = TITULAR_LOCAL
    ahora = _ahora()
    conservar: set[str] = set()
    por_url: dict[str, dict] = {}
    cuenta = [0, 0]  # candidatas, protegidas
    with _en_transaccion(conn):
        conn.execute(
            "INSERT OR IGNORE INTO perfiles (chat_id, estado, datos, version, creado, actualizado)"
            " VALUES (?, 'activo', ?, 1, ?, ?)",
            (chat_id, _json({"v": 1, "reglas": "titular-2026-10", "config": "global"}), ahora, ahora))
        for accion, clave in _CLAVE_DE_ACCION.items():
            marcas_json = estado.get(clave)
            for url, info in (marcas_json if isinstance(marcas_json, dict) else {}).items():
                _poner_marca(conn, chat_id, url, accion, info if isinstance(info, dict) else {})
                conservar.add(url)
        # El tramo protegido es el prefijo de la lista, no «lo que hoy tiene marca»: si el
        # titular marcó algo después de la última corrida, esa oferta sigue entre las
        # candidatas de la lista hasta la próxima fusión, igual que en el JSON.
        en_prefijo = True
        for oferta in ofertas:
            url = oferta.get("job_url") if isinstance(oferta, dict) else None
            if not url:
                continue  # fusionar_ofertas tampoco los conservaba: sin enlace no son accesibles
            por_url[url] = oferta
            en_prefijo = en_prefijo and url in conservar
            _escribir_oferta(conn, chat_id, oferta, int(en_prefijo), cuenta[en_prefijo])
            cuenta[en_prefijo] += 1
        for clave, entrada in avisos_json.items():
            if not isinstance(entrada, dict):
                continue
            completa = por_url.get(entrada.get("url")) or {}
            vacante = vacante_de({"title": entrada.get("titulo"), "company": entrada.get("empresa"),
                                  "location": completa.get("location")})
            conn.execute(
                "INSERT OR REPLACE INTO avisos (chat_id, clave, url, vacante, tipo, titulo, empresa,"
                " relevancia, fuente, avisada, respuesta, motivo, respondida)"
                " VALUES (?, ?, ?, ?, 'aviso', ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, clave, entrada.get("url"), vacante, entrada.get("titulo"), entrada.get("empresa"),
                 entrada.get("relevancia"), entrada.get("fuente"), entrada.get("avisada"),
                 entrada.get("respuesta"), entrada.get("motivo"), entrada.get("respondida")))
        pares = [("migrado", ahora), ("huellas_json", _json(huellas)), ("titular", str(chat_id)),
                 ("version_bloques", str(VERSION_BLOQUES)),
                 # La primera fusión mira todas las candidatas: el dedupe por bloques da por
                 # hecho que la base ya no tiene duplicados, y eso hay que ganárselo.
                 (f"dedupe_pendiente:{chat_id}", "1"), (f"firma_ofertas:{chat_id}", "1")]
        if estado.get("ultima_busqueda"):
            pares.append((f"ultima_busqueda:{chat_id}", estado["ultima_busqueda"]))
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", pares)
    # La migración deja el -wal del tamaño de la base entera; no hace falta que se quede así.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    logger.info("Migrados los JSON a perfiles.db: %s ofertas, %s marcas, %s avisos.",
                sum(cuenta), sum(len(estado.get(c) or {}) for c in _CLAVE_DE_ACCION.values()),
                len(avisos_json))
    return {"migrado_ahora": True, "ya_estaba": False, "json_cambiados": []}


@_vigilar
def migrar_desde_json() -> dict:
    """
    Idempotente: dos veces dejan las mismas filas. Si hubo una migración, no vuelve a leer
    los JSON aunque hayan cambiado: avisa y deja la decisión a quien lo lance.
    """
    return _migrar_si_toca(conexion(), ruta().parent)


def _diferencias(a: dict, b: dict) -> list[str]:
    return sorted(k for k in a.keys() | b.keys() if k not in a or k not in b or a[k] != b[k])


@_vigilar
def verificar(chat_id: int | None = None) -> dict:
    """
    Recuentos y campos de la base contra los *.pre-mp1. Justo tras migrar, 0 diferencias.
    Después de la primera corrida las habrá y es lo normal (ofertas nuevas, `visto_por_
    ultima_vez`, marcas), así que el resultado separa lo que falta de lo que ha cambiado.
    """
    chat_id = resolver(chat_id)
    carpeta = ruta().parent
    informe: dict = {"marcas_distintas": [], "ofertas_distintas": [], "avisos_distintos": [],
                     "ofertas_que_faltan": [], "recuentos": {}, "orden_igual": None}
    estado = _leer_origen(carpeta / "estado.json.pre-mp1", dict, {})
    ofertas = _leer_origen(carpeta / "ofertas.json.pre-mp1", list, [])
    avisos_json = _leer_origen(carpeta / "telegram_avisos.json.pre-mp1", dict, {})

    actuales = marcas(chat_id)
    for clave in _CLAVE_DE_ACCION.values():
        origen, destino = estado.get(clave) or {}, actuales[clave]
        informe["recuentos"][clave] = (len(origen), len(destino))
        for url in origen.keys() | destino.keys():
            if origen.get(url) != destino.get(url):
                informe["marcas_distintas"].append(f"{clave}: {url}")

    base = ofertas_de(chat_id)
    por_url = {o["job_url"]: o for o in base}
    informe["recuentos"]["ofertas"] = (len(ofertas), len(base))
    for oferta in ofertas:
        url = oferta.get("job_url")
        if url not in por_url:
            informe["ofertas_que_faltan"].append(url)
        elif _diferencias(oferta, por_url[url]):
            informe["ofertas_distintas"].append(f"{url}: {', '.join(_diferencias(oferta, por_url[url]))}")
    informe["orden_igual"] = [o.get("job_url") for o in ofertas] == [o["job_url"] for o in base]

    guardados = avisos(chat_id)
    informe["recuentos"]["avisos"] = (len(avisos_json), len(guardados))
    for clave in avisos_json.keys() | guardados.keys():
        if avisos_json.get(clave) != guardados.get(clave):
            informe["avisos_distintos"].append(clave)

    informe["ultima_busqueda_igual"] = estado.get("ultima_busqueda") == ultima_busqueda(chat_id)
    informe["integridad"] = conexion().execute("PRAGMA quick_check(1)").fetchone()[0]
    informe["bloques_desfasados"] = sum(
        1 for datos, empresa, titulo in conexion().execute(
            "SELECT datos, bloque_empresa, bloque_titulo FROM ofertas")
        if _bloques(json.loads(datos)) != (empresa, titulo))
    return informe


@_vigilar
def exportar_comprimido(chat_id: int) -> bytes:
    """
    Todo lo de este perfil en un JSON comprimido, para /misdatos (D9, MP5): ficha, enlaces con lo
    del portal, marcas, avisos, conversación y pendientes.

    Sin las descripciones: son texto del portal, no de la persona, y el 80 % del peso. Se escribe
    comprimido según se lee, oferta a oferta, y no se toca el disco: con las ~3.000 del titular
    son unos 3 MB de JSON que en memoria se quedan en lo que ocupe comprimido. Solo sale lo que
    este chat_id tiene enlazado (_exigir y el JOIN de iterar_ofertas): nada de otro perfil.
    """
    import gzip
    import io

    chat_id = _exigir(chat_id)
    # Sin el chat_id: es suyo y ya lo sabe, y un fichero que se reenvía no tiene por qué llevarlo.
    def sin_chat_id(fila: dict) -> dict:
        return {c: v for c, v in fila.items() if c != "chat_id"}

    cabecera = {"generado": _ahora(), "perfil": sin_chat_id(obtener(chat_id) or {}), "marcas": marcas(chat_id),
                "avisos": avisos(chat_id),
                "conversacion": [sin_chat_id(f) for f in _filas_de(chat_id, "conversacion")],
                "pendientes": [sin_chat_id(f) for f in _filas_de(chat_id, "pendientes")]}
    salida = io.BytesIO()
    with gzip.GzipFile(fileobj=salida, mode="wb") as comprimido:
        def escribir(texto: str) -> None:
            comprimido.write(texto.encode("utf-8"))

        escribir(json.dumps(cabecera, ensure_ascii=False, default=str)[:-1] + ', "ofertas": [')
        for i, oferta in enumerate(iterar_ofertas(chat_id, con_texto=False)):
            oferta.pop("description", None)
            escribir(("," if i else "") + json.dumps(oferta, ensure_ascii=False, default=str))
        escribir("]}")
    return salida.getvalue()


def _filas_de(chat_id: int, tabla: str) -> list[dict]:
    """Las filas de una tabla con chat_id, de este perfil, como diccionarios."""
    cursor = conexion().execute(f"SELECT * FROM {tabla} WHERE chat_id = ?", (_exigir(chat_id),))
    columnas = [c[0] for c in cursor.description]
    return [dict(zip(columnas, f)) for f in cursor]


@_vigilar
def exportar(chat_id: int, carpeta: str | Path) -> dict:
    """
    Escribe estado.json, ofertas.json y telegram_avisos.json de este perfil en una carpeta.

    Es la vuelta atrás: el código anterior a MP1 los lee tal cual, con lo marcado y
    descubierto después del despliegue. Y sirve para cualquier análisis a mano.

    Desde MP5 escribe también perfil.json: su fila de `perfiles`, su conversación y sus pendientes.
    Es la copia de `borrar` (regla 7), y la del adicional del 03-11 salió sin su ficha: para saber
    qué buscaba hubo que ir a la copia entera de la base.
    """
    chat_id = _exigir(chat_id)
    destino = Path(carpeta)
    destino.mkdir(parents=True, exist_ok=True)
    ofertas = ofertas_de(chat_id)
    estado = {**marcas(chat_id), "ultima_busqueda": ultima_busqueda(chat_id)}
    guardados = avisos(chat_id)
    ficha = {"perfil": obtener(chat_id), "conversacion": _filas_de(chat_id, "conversacion"),
             "pendientes": _filas_de(chat_id, "pendientes")}
    for nombre, contenido in (("estado.json", estado), ("ofertas.json", ofertas),
                              ("telegram_avisos.json", guardados), ("perfil.json", ficha)):
        temporal = destino / (nombre + ".tmp")
        temporal.write_text(json.dumps(contenido, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temporal, destino / nombre)
    return {"ofertas": len(ofertas), "avisos": len(guardados),
            "marcas": sum(len(estado[c]) for c in _CLAVE_DE_ACCION.values())}


def main(argumentos: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argumentos is None else argumentos
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    orden = argumentos[0] if argumentos else ""
    try:
        if orden == "migrar":
            resultado = migrar_desde_json()
            print("Migrado ahora." if resultado["migrado_ahora"] else
                  "Ya estaba migrado." if resultado["ya_estaba"] else "No hay JSON que migrar.")
            if resultado["json_cambiados"]:
                print("OJO, han cambiado desde la migración y no se ha tocado nada: "
                      + ", ".join(resultado["json_cambiados"]))
            return 0
        if orden == "verificar":
            informe = verificar()
            for nombre, (antes, ahora) in informe["recuentos"].items():
                print(f"  {nombre:10s} JSON {antes:6d}   base {ahora:6d}")
            distintas = ("marcas_distintas", "ofertas_distintas", "ofertas_que_faltan", "avisos_distintos")
            for clave in distintas:
                print(f"  {clave:20s} {len(informe[clave])}")
                for linea in informe[clave][:5]:
                    print("      " + linea)
            print(f"  orden de la lista igual: {informe['orden_igual']}")
            print(f"  última búsqueda igual:   {informe['ultima_busqueda_igual']}")
            print(f"  integridad: {informe['integridad']} · bloques desfasados: {informe['bloques_desfasados']}")
            limpio = (not any(informe[c] for c in distintas) and informe["orden_igual"]
                      and informe["ultima_busqueda_igual"] and informe["integridad"] == "ok"
                      and not informe["bloques_desfasados"])
            print("Sin diferencias." if limpio else
                  "Hay diferencias: es lo normal después de la primera corrida, no antes.")
            return 0 if limpio else 1
        if orden == "exportar" and len(argumentos) == 3:
            chat_id = titular() if argumentos[1] == "titular" else int(argumentos[1])
            resumen = exportar(chat_id, argumentos[2])
            print(f"Exportado en {argumentos[2]}: {resumen['ofertas']} ofertas, "
                  f"{resumen['marcas']} marcas, {resumen['avisos']} avisos.")
            return 0
        if orden == "crear" and len(argumentos) == 3 and argumentos[1] == "--desde":
            crear_desde(argumentos[2], _config_del_sistema())
            print("Perfil creado.")  # sin el chat_id: regla 3
            return 0
        if orden == "borrar" and len(argumentos) >= 2:
            return _borrar_desde_consola(argumentos[1], "--confirmar" in argumentos[2:])
        if orden == "reenfocar-titular":
            return _reenfocar_desde_consola(argumentos[1:])
    except BaseNoDisponible as e:
        print(f"perfiles.db no está disponible: {e}")
        return 2
    except (OSError, ValueError, TypeError) as e:
        print(f"No se ha podido: {e}")
        return 2
    print("Uso: python -m perfiles migrar | verificar | exportar <chat_id|titular> <carpeta>\n"
          "     python -m perfiles crear --desde <perfil.json>\n"
          "     python -m perfiles borrar <chat_id> [--confirmar]\n"
          "     python -m perfiles reenfocar-titular --confirmar | --revertir")
    return 2


def _config_del_sistema() -> dict:
    # Aquí dentro y no arriba: config.py arrastra buscador y jobspy, y `verificar` no los necesita.
    import config as config_mod

    return config_mod.cargar_config()


def copia_y_borrado(chat_id: int, conservar_perfil: bool = False, reiniciar: bool = False) -> tuple[Path, dict]:
    """
    Lo que hacen el `borrar` de consola y el /borrar de Telegram: copia y luego borrado. Y lo mismo el
    reinicio de un perfil (`reiniciar`): su copia es la única vuelta atrás, porque de un reinicio no queda
    nada en la fila (ni el historial: la persona ha pedido empezar de cero, y /misdatos lo enseñaría).

    Regla 7: nunca sin copia (decidido por el titular el 03-11 también para el /borrar de cada
    persona, que lo sabe por el mensaje de confirmación). La carpeta lleva la fecha y no el chat_id
    (regla 3), con los segundos y, si chocara, un sufijo: dos borrados en el mismo segundo no se
    pisan la copia. La borra el titular cuando quiera.
    """
    base = ruta().parent / "borrados" / datetime.now().strftime("%Y%m%d_%H%M%S")
    copia, n = base, 1
    while copia.exists():
        n += 1
        copia = base.with_name(f"{base.name}_{n}")
    exportar(chat_id, copia)
    return copia, borrar(chat_id, conservar_perfil=conservar_perfil, reiniciar=reiniciar)


def _borrar_desde_consola(valor: str, confirmado: bool) -> int:
    """
    Copia y borrado. Un chat_id negativo es un perfil sombra y va sin más; uno positivo es una
    persona y hay que pedirlo a propósito (cada persona tiene además su /borrar desde MP5).
    """
    chat_id = int(valor)
    if obtener(chat_id) is None:
        print("Ese perfil no existe.")
        return 2
    if chat_id > 0 and not confirmado:
        print("Es el perfil de una persona: repite con --confirmar si es lo que quieres.")
        return 2
    copia, quitado = copia_y_borrado(chat_id)
    print(f"Borrado, con copia en {copia}: " + ", ".join(f"{v} {k}" for k, v in quitado.items()) + ".")
    return 0


# ----------------------------------------------------------------------
# REENFOQUE DEL TITULAR (MP4, D11)
#
# Lo ejecuta el titular, dentro del contenedor y sin una corrida en marcha. Solo toca dos cosas: su
# fila de `perfiles` y, de config.json, lo que distingue a un juego de otro. Se lee y se escribe el
# fichero en crudo, y no con config.cargar_config, que le fusionaría los valores por defecto.
#
# config.json va primero: si algo se corta en medio, repetir la orden lo completa, y mientras tanto
# _construir_efectiva ya hace caso a config.json.
# ----------------------------------------------------------------------
JUEGO_DEL_REENFOQUE = "titular-ciber"
_JUEGO_PREVIO = "titular-2026-10"
_CLAVES_DEL_REENFOQUE = ("categorias", "jooble_terminos", "jooble_ubicacion", "pesos_relevancia",
                         "reglas_descarte")


def _copiar_atomico(origen: Path, destino: Path) -> None:
    temporal = destino.with_name(destino.name + ".tmp")
    shutil.copy2(origen, temporal)
    os.replace(temporal, destino)


def _escribir_config(camino: Path, datos: dict) -> None:
    temporal = camino.with_name(camino.name + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copymode(camino, temporal)
    os.replace(temporal, camino)


def _copia_de_la_base(destino: Path) -> None:
    """Con la API de backup: con el WAL, copiar el fichero suelto se dejaría lo último escrito."""
    copia = sqlite3.connect(destino)
    try:
        conexion().backup(copia)
    finally:
        copia.close()
    os.chmod(destino, 0o600)  # lleva marcas y chat_id


def escribir_claves_de_config(claves: dict) -> bool:
    """
    Cambia solo esas claves de config.json, leído y escrito en crudo como en el reenfoque: con
    config.cargar_config se le fusionarían los valores por defecto y el fichero acabaría con
    claves que nadie ha decidido. Si ya tenían esos valores, no escribe (cada escritura cuenta).
    Lo usa /frecuencia (MP5). Devuelve si ha escrito.
    """
    camino = ruta().parent / "config.json"
    config = _leer_origen(camino, dict, {})
    if all(config.get(c) == v for c, v in claves.items()):
        return False
    nueva = {**config, **claves}
    if camino.exists():
        _escribir_config(camino, nueva)
    else:
        camino.write_text(json.dumps(nueva, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _titular_para_reenfocar() -> tuple[int, dict, Path, dict]:
    chat = titular()
    perfil = obtener(chat)
    if perfil is None or perfil["datos"].get("config") != "global":
        raise ValueError("el titular no tiene su fila con config global")
    camino = ruta().parent / "config.json"
    config = _leer_origen(camino, dict, None)
    if config is None:
        raise ValueError("no hay config.json")
    return chat, perfil, camino, config


def reenfocar_titular() -> str:
    """Pasa al titular a `titular-ciber`, con copia previa. Repetirla no hace nada."""
    chat, perfil, camino, config = _titular_para_reenfocar()
    juego = reglas.obtener(JUEGO_DEL_REENFOQUE)
    destino = {**config, **copy.deepcopy({c: juego.config[c] for c in _CLAVES_DEL_REENFOQUE}),
               "puntuador": {"tipo": "reglas", "juego": juego.nombre}}
    cambia_config = destino != config
    cambia_perfil = perfil["datos"].get("reglas") != juego.nombre
    if not cambia_config and not cambia_perfil:
        return "Ya estaba en titular-ciber: no se ha tocado nada."
    fecha = f"{datetime.now():%Y%m%d_%H%M%S}"
    _copia_de_la_base(camino.with_name(f"perfiles.db.bak-pre-ciber-{fecha}"))
    if cambia_config:
        _copiar_atomico(camino, camino.with_name(f"config.json.bak-pre-ciber-{fecha}"))
        _escribir_config(camino, destino)
    if cambia_perfil:
        # Desde MP6: lo que el titular marcó antes del cambio de sector no cuenta para la capa
        # aprendida (aprendizaje.py, decidido por el titular el 04-11, con el dato).
        datos = {**perfil["datos"], "reglas": juego.nombre}
        datos["ajustes_aprendidos"] = {**(datos.get("ajustes_aprendidos") or {}), "desde": _ahora()}
        guardar(chat, datos)
    return f"Titular en titular-ciber. Copias con la fecha {fecha}."


def revertir_reenfoque() -> str:
    """La vuelta atrás: el perfil a las reglas de antes y config.json a su última copia previa."""
    chat, perfil, camino, config = _titular_para_reenfocar()
    if perfil["datos"].get("reglas") == _JUEGO_PREVIO and "puntuador" not in config:
        return "Ya estaba en titular-2026-10: no se ha tocado nada."
    copias = sorted(camino.parent.glob("config.json.bak-pre-ciber-*"))
    if not copias:
        raise ValueError("no hay ninguna copia config.json.bak-pre-ciber-*")
    fecha = f"{datetime.now():%Y%m%d_%H%M%S}"
    _copiar_atomico(camino, camino.with_name(f"config.json.bak-post-ciber-{fecha}"))  # regla 7
    _copiar_atomico(copias[-1], camino)
    guardar(chat, {**perfil["datos"], "reglas": _JUEGO_PREVIO})
    return f"Titular en titular-2026-10. Restaurada {copias[-1].name}."


def _reenfocar_desde_consola(argumentos: list[str]) -> int:
    if "--revertir" in argumentos:
        print(revertir_reenfoque())
        return 0
    if "--confirmar" in argumentos:
        print(reenfocar_titular())
        return 0
    print("Cambia los avisos del titular: repite con --confirmar si es lo que quieres, "
          "o con --revertir para volver atrás.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
