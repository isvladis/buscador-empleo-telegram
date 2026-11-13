# -*- coding: utf-8 -*-
"""
El caché de descripciones ya no se carga entero: se pregunta enlace a enlace.

Hasta el 11-11-2026 era `descripciones_vistas.json`: entero en memoria durante la corrida, parseado
otra vez dentro de `recordar_descripciones` y reescrito entero al acabar. Con el fichero real del
servidor de ese día (12,7 MB, 3.896 entradas) eso eran +136 MiB de pico de RSS y 12,7 MB escritos
por corrida; ahora es un SQLite aparte (`descripciones_vistas.db`), 5 MiB y lo que cambie. Ver el
comentario de `almacen.py` y `REVISION-2026-11-11.md` §3.

Lo que se comprueba, cada cosa con su control negativo:

1. **Que sale lo mismo.** El `almacen.py` de antes (el del JSON, sacado con `git show`) y el de ahora
   reciben el mismo caché y las mismas llamadas a `recordar_descripciones`, y tienen que acabar con
   EXACTAMENTE las mismas descripciones y los mismos campos del descubrimiento. Regla 4: esto no cambia
   ninguna decisión.
2. **Que el JSON de antes se importa una vez y no se toca** (regla 7).
3. **Que una corrida no recorre el caché.** Con las sentencias que ejecuta la conexión delante: todas
   son por clave. El control: `dict(vista)` sí lo recorre, y el detector lo ve.
4. **Que solo se escribe lo que cambia.** Repetir lo mismo no escribe nada; pasar de día toca la fecha y
   no el texto.
5. **Que un caché roto no tumba la corrida**: se aparta, se empieza vacío y se sigue.
6. **Que si un enlace está en la base y en el caché, manda la base**, como con el diccionario de antes.

Ninguna prueba sale a la red: no se consulta ningún portal (`buscador.buscar_para_perfiles` es un
espía) y no se manda nada a Telegram (sin token configurado no se llega a enviar).

    PYTHONUTF8=1 python pruebas/test_cache.py
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-cache-")
for variable in ("JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_ID", "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
    os.environ.pop(variable, None)

import almacen  # noqa: E402
import app  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import etiquetas  # noqa: E402
import perfiles  # noqa: E402

# El último commit con el caché en JSON y el doble parseo. No cambia: es la referencia de la equivalencia.
COMMIT_DE_REFERENCIA = "b5ecb7a"

HOY = date.today()
RECIENTE = (HOY - timedelta(days=2)).isoformat()
VIEJA = (HOY - timedelta(days=almacen.DIAS_DESCRIPCIONES_VISTAS + 3)).isoformat()

# Entradas rebuscadas a propósito: con y sin descripción, con y sin campos del
# descubrimiento, con campos a None (que no cuentan), con la descripción vacía (que no
# sirve de caché pero cuyo dato de competencia sí vale), una que ya debería haberse podado, una con
# emoji (4 bytes por letra en memoria: lo que hacía pesar el fichero entero) y una que no es un dict.
CACHE = {
    "https://x.test/1": {"d": "texto 1", "v": RECIENTE, "candidatos": 12,
                         "candidatos_tope": False, "publicada_en": "2026-10-28"},
    "https://x.test/2": {"d": "texto 2", "v": RECIENTE},
    "https://x.test/3": {"v": RECIENTE, "candidatos": 7},
    "https://x.test/4": {"d": "", "v": RECIENTE, "candidatos": 3},
    "https://x.test/5": {"d": "texto 5", "v": RECIENTE, "candidatos": None},
    "https://x.test/6": "esto no es un dict",
    "https://x.test/7": {"d": "texto 7, que nadie ha vuelto a ver", "v": VIEJA, "candidatos": 40},
    "https://x.test/8": {"d": "¡Únete! 🚀 Buscamos a alguien con ñ y acentos", "v": "fecha rota"},
}

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def carpeta_con_cache(nombre: str, contenido) -> Path:
    """Carpeta limpia con su propio caché; la base y la poda, aparte de las demás."""
    perfiles.cerrar()
    almacen.cerrar_cache()
    carpeta = Path(tempfile.mkdtemp(prefix=f"test-cache-{nombre}-"))
    os.environ["JOBSPY_DATA_DIR"] = str(carpeta)
    almacen.DIR_BASE = carpeta
    almacen.DESCRIPCIONES_PATH = carpeta / "descripciones_vistas.json"
    almacen.PODADAS_PATH = carpeta / "descripciones_podadas.json.gz"
    config_mod.CONFIG_PATH = carpeta / "config.json"
    if contenido is not None:
        (carpeta / "descripciones_vistas.json").write_text(
            contenido if isinstance(contenido, str) else json.dumps(contenido, ensure_ascii=False), encoding="utf-8")
    return carpeta


def cargar_referencia(carpeta: Path):
    """El almacen.py de antes del arreglo, tal cual estaba, con su propio caché en JSON."""
    # `REPO_DE_REFERENCIA`: las mutaciones corren esta prueba en una copia del árbol, que no lleva `.git`.
    fuente = subprocess.run(["git", "show", f"{COMMIT_DE_REFERENCIA}:almacen.py"],
                            cwd=os.environ.get("REPO_DE_REFERENCIA") or RAIZ,
                            capture_output=True, check=True).stdout.decode("utf-8")
    espec = importlib.util.spec_from_loader("almacen_doble", loader=None)
    modulo = importlib.util.module_from_spec(espec)
    modulo.__file__ = str(RAIZ / "almacen.py")
    exec(compile(fuente, "almacen_doble.py", "exec"), modulo.__dict__)
    modulo.DESCRIPCIONES_PATH = carpeta / "descripciones_vistas.json"
    modulo.PODADAS_PATH = carpeta / "descripciones_podadas.json.gz"
    return modulo


def lecturas_del_json(trabajo) -> int:
    """Cuántas veces se lee el JSON de antes mientras corre `trabajo`."""
    original = almacen._leer_json
    leidos = []

    def espia(path, por_defecto):
        if path == almacen.DESCRIPCIONES_PATH:
            leidos.append(path)
        return original(path, por_defecto)

    almacen._leer_json = espia
    try:
        trabajo()
    finally:
        almacen._leer_json = original
    return len(leidos)


def sentencias(trabajo) -> list[str]:
    """Las sentencias que ejecuta la conexión del caché mientras corre `trabajo`."""
    vistas: list[str] = []
    with almacen._lock_cache:
        conn = almacen._abrir_cache()
        conn.set_trace_callback(vistas.append)
    try:
        trabajo()
    finally:
        with almacen._lock_cache:
            if almacen._cache["conn"] is not None:
                almacen._cache["conn"].set_trace_callback(None)
    return vistas


def recorre_la_tabla(lista: list[str]) -> bool:
    """
    ¿Alguna lectura que no sea por un enlace concreto? Eso es traerse el caché entero, que es lo que ya no
    se hace. «Por un enlace» es `url = ?` o, como lo enseña la traza con el valor ya puesto, `url = '…'`;
    un `v.url = t.url` de juntar dos tablas no lo es.
    """
    return any(s.lstrip().upper().startswith("SELECT") and not re.search(r"url = (?:\?|')", s) for s in lista)


def caso_sale_lo_mismo() -> None:
    """El caché de antes y el de ahora, con el mismo contenido y las mismas llamadas."""
    print("\n[1] Equivalencia con el código de antes (%s)" % COMMIT_DE_REFERENCIA)
    carpeta_vieja = carpeta_con_cache("referencia", CACHE)
    viejo = cargar_referencia(carpeta_vieja)
    antes_desc, antes_comp = viejo.cargar_descripciones_vistas(), viejo.cargar_competencia_vista()

    carpeta_con_cache("equivalencia", CACHE)
    descripciones, competencia = almacen.cargar_cache_vistas()
    comprueba(dict(descripciones) == antes_desc, "las descripciones son exactamente las de antes")
    comprueba(dict(competencia) == antes_comp, "los campos del descubrimiento son exactamente los de antes")
    comprueba(all(descripciones.get(u) == antes_desc.get(u) and competencia.get(u) == antes_comp.get(u)
                  for u in list(CACHE) + ["https://x.test/no-esta", "", None, float("nan")]),
              "   y preguntando enlace a enlace, también (con uno que no está, uno vacío, None y un NaN de pandas)")
    comprueba(descripciones.get("https://x.test/8") == CACHE["https://x.test/8"]["d"],
              "   el emoji, la ñ y los acentos vuelven tal cual")
    comprueba("https://x.test/3" not in descripciones and "https://x.test/4" not in descripciones
              and "https://x.test/6" not in descripciones,
              "   la entrada sin «d», la vacía y la que no es dict no son descripciones")
    comprueba(competencia.get("https://x.test/3") == {"candidatos": 7}
              and competencia.get("https://x.test/4") == {"candidatos": 3}
              and competencia.get("https://x.test/5") is None,
              "   y la competencia se conserva aunque no haya descripción (como antes)")
    comprueba(dict(almacen.cargar_descripciones_vistas()) == antes_desc
              and dict(almacen.cargar_competencia_vista()) == antes_comp,
              "las dos funciones de siempre siguen dando lo mismo por separado")

    # Las mismas llamadas a recordar en los dos: una nueva con competencia, una que ya estaba y trae otra
    # competencia (manda la vieja), una que ya estaba sin competencia y ahora la trae, una sin texto (no
    # entra) y una refrescada. La 7, que lleva diez días sin verse, se poda; la 3 y la 4, sin descripción,
    # se van con ella.
    nuevas = {"https://x.test/9": "texto 9, recién bajado", "https://x.test/1": "texto 1",
              "https://x.test/2": "texto 2", "https://x.test/10": "", "https://x.test/8": CACHE["https://x.test/8"]["d"]}
    comp = {"https://x.test/9": {"candidatos": 5, "candidatos_tope": True, "publicada_en": None},
            "https://x.test/1": {"candidatos": 999}, "https://x.test/2": {"candidatos": 21}}
    quedan_antes = viejo.recordar_descripciones(dict(nuevas), dict(comp))
    quedan_ahora = almacen.recordar_descripciones(dict(nuevas), dict(comp))
    antes_desc, antes_comp = viejo.cargar_descripciones_vistas(), viejo.cargar_competencia_vista()
    descripciones, competencia = almacen.cargar_cache_vistas()
    comprueba(quedan_ahora == quedan_antes == len(antes_desc), "recordar devuelve las mismas que antes (%d)" % quedan_antes)
    comprueba(dict(descripciones) == antes_desc, "tras recordar, las descripciones son las de antes")
    comprueba(dict(competencia) == antes_comp, "   y los campos del descubrimiento, también")
    comprueba(competencia.get("https://x.test/1", {}).get("candidatos") == 12,
              "   la competencia se escribe una sola vez: manda la del día que se descargó, no la nueva")
    comprueba(competencia.get("https://x.test/2") == {"candidatos": 21},
              "   la que no la tenía, la coge")
    comprueba("https://x.test/7" not in descripciones and "https://x.test/7" not in competencia,
              "   la que lleva más de %d días sin verse se poda" % almacen.DIAS_DESCRIPCIONES_VISTAS)
    comprueba("https://x.test/5" in descripciones,
              "   y la que no ha vuelto a salir pero es de hace dos días, se queda")
    comprueba("https://x.test/10" not in descripciones, "   una descripción vacía no entra")
    with almacen._lock_cache:
        filas = almacen._abrir_cache().execute("SELECT (SELECT COUNT(*) FROM textos), (SELECT COUNT(*) FROM vistas)").fetchone()
    comprueba(filas == (quedan_ahora, quedan_ahora),
              "   y lo podado se va de verdad del fichero: ni textos sin fecha ni fechas sin texto (%s)" % (filas,))

    # Lo que no se recuerda en SIETE días se va; lo que se sigue viendo, no. Se simula envejeciendo la fecha.
    with almacen._lock_cache:
        almacen._abrir_cache().execute("UPDATE vistas SET visto = ?", (VIEJA,))
    almacen.recordar_descripciones({"https://x.test/9": "texto 9, recién bajado"}, {})
    comprueba(sorted(almacen.cargar_descripciones_vistas()) == ["https://x.test/9"],
              "la fecha se refresca al volver a ver el enlace: solo sobrevive el que ha vuelto a salir")

    # Un fichero que no es un dict (basura) tampoco rompe, igual que antes.
    carpeta = carpeta_con_cache("basura", "[1, 2, 3]")
    viejo = cargar_referencia(carpeta)
    comprueba(almacen.cargar_cache_vistas() == ({}, {})
              == (viejo.cargar_descripciones_vistas(), viejo.cargar_competencia_vista()),
              "un caché que no es un dict da dos diccionarios vacíos, como antes")
    carpeta_con_cache("ilegible", "{esto no es json")
    comprueba(almacen.cargar_cache_vistas() == ({}, {}), "   y uno que ni siquiera es JSON, también")
    carpeta_con_cache("sin-fichero", None)
    comprueba(almacen.cargar_cache_vistas() == ({}, {}) and almacen.recordar_descripciones({"https://x.test/1": "a"}) == 1,
              "   sin ningún fichero se empieza vacío y se puede recordar")


def caso_importacion() -> None:
    """El JSON de antes entra una vez y se queda como estaba."""
    print("\n[2] El JSON de antes se importa una vez y no se toca")
    carpeta = carpeta_con_cache("importar", CACHE)
    original = (carpeta / "descripciones_vistas.json").read_bytes()
    comprueba(not almacen.ruta_cache().exists(), "antes de la primera consulta no hay caché nuevo")
    veces = lecturas_del_json(almacen.cargar_cache_vistas)
    comprueba(veces == 1 and almacen.ruta_cache().exists(), "la primera vez lee el JSON y crea descripciones_vistas.db")
    comprueba(almacen.ruta_cache().parent == carpeta and almacen.ruta_cache().name == "descripciones_vistas.db",
              "   junto al JSON y con su nombre")
    almacen.recordar_descripciones({"https://x.test/9": "nueva"}, {})
    comprueba((carpeta / "descripciones_vistas.json").read_bytes() == original,
              "el JSON sigue byte a byte como estaba, también después de recordar (regla 7)")

    # Cerrar y volver a abrir (un reinicio): no se vuelve a importar, y lo que se añada al JSON ya no entra.
    almacen.cerrar_cache()
    cambiado = dict(CACHE, **{"https://x.test/99": {"d": "añadida al JSON después", "v": RECIENTE}})
    (carpeta / "descripciones_vistas.json").write_text(json.dumps(cambiado), encoding="utf-8")
    veces = lecturas_del_json(lambda: almacen.cargar_cache_vistas()[0].get("https://x.test/99"))
    comprueba(veces == 0, "tras un reinicio no se vuelve a leer el JSON")
    comprueba(almacen.cargar_descripciones_vistas().get("https://x.test/99") is None
              and almacen.cargar_descripciones_vistas().get("https://x.test/9") == "nueva",
              "   y lo recordado antes del reinicio sigue ahí")

    # CONTROL NEGATIVO: sin la marca de «ya importado», el contador ve la lectura y lo añadido entra.
    with almacen._lock_cache:
        almacen._abrir_cache().execute("DELETE FROM meta WHERE clave = 'importado_del_json'")
    almacen.cerrar_cache()
    veces = lecturas_del_json(almacen.cargar_cache_vistas)
    comprueba(veces == 1 and almacen.cargar_descripciones_vistas().get("https://x.test/99") == "añadida al JSON después",
              "control: sin la marca se importa otra vez, y el contador lo ve")


def caso_una_corrida() -> None:
    """La corrida de app.py no parsea el JSON ni recorre el caché: pregunta por lo que tiene delante."""
    print("\n[3] Una corrida no carga el caché")
    carpeta_con_cache("corrida", CACHE)
    almacen.cargar_cache_vistas()  # la importación, fuera de la cuenta

    recibido = {}
    guardados = buscador.buscar_para_perfiles, etiquetas.a_registros
    pedidas = ["https://x.test/1", "https://x.test/2", "https://x.test/no-esta"]

    def espia_buscar(activos, config, al_evaluar, excluidas, **kwargs):
        recibido.update(kwargs)
        # Lo que hace `completar_descripciones`: preguntar por los enlaces de la corrida, y avisar de lo bajado.
        recibido["textos"] = {u: kwargs["descripciones_conocidas"].get(u) for u in pedidas}
        recibido["competencia"] = {u: kwargs["competencia_conocida"].get(u) for u in pedidas}
        kwargs["al_descargar_descripciones"]({"https://x.test/1": "texto 1", "https://x.test/nueva": "recién bajada"}, {})
        al_evaluar(activos[0], [])

    buscador.buscar_para_perfiles = espia_buscar
    etiquetas.a_registros = lambda df: df
    try:
        leidos = []
        ejecutadas = sentencias(lambda: leidos.append(lecturas_del_json(lambda: app._ejecutar_busqueda(
            config_mod.cargar_config(), False, automatica=False))))
    finally:
        buscador.buscar_para_perfiles, etiquetas.a_registros = guardados

    comprueba(app.BUSQUEDA["error"] is None, "la corrida termina sin error")
    comprueba(almacen._cache["conn"] is None,
              "al acabar la corrida el caché queda cerrado: entre corridas no hay ningún fichero cogido")
    comprueba(recibido and "portal" not in recibido, "no se ha consultado ningún portal")
    comprueba(leidos == [0], "una corrida entera no parsea el JSON ni una vez (antes 1, y antes de MP1, 2)")
    comprueba(len(ejecutadas) > 0 and not recorre_la_tabla([s for s in ejecutadas if "COUNT(*)" not in s]),
              "ni recorre el caché: todas sus lecturas son por enlace (%d sentencias)" % len(ejecutadas))
    comprueba(recibido["textos"] == {"https://x.test/1": "texto 1", "https://x.test/2": "texto 2",
                                     "https://x.test/no-esta": None},
              "a la búsqueda le llegan las descripciones del caché, como antes")
    comprueba(recibido["competencia"]["https://x.test/1"] == {"candidatos": 12, "candidatos_tope": False,
                                                              "publicada_en": "2026-10-28"}
              and recibido["competencia"]["https://x.test/2"] is None,
              "   y la competencia")
    comprueba(almacen.cargar_descripciones_vistas().get("https://x.test/nueva") == "recién bajada",
              "lo que la corrida descarga queda en el caché para la siguiente")
    comprueba(isinstance(recibido["descripciones_conocidas"], almacen.Conocidas)
              and bool(recibido["descripciones_conocidas"]) and bool(recibido["competencia_conocida"]),
              "lo conocido es base + caché sin juntarlos, y es «verdadero» sin contar filas")

    # CONTROL NEGATIVO: el detector sí distingue. Volcar la vista a un diccionario, que es lo que hacía
    # `{**en_cache, **en_base}`, recorre la tabla, y se ve.
    volcado = sentencias(lambda: dict(almacen.cargar_descripciones_vistas()))
    comprueba(recorre_la_tabla(volcado), "control: volcar el caché a un diccionario sí lo recorre, y el detector lo ve")


def caso_solo_lo_que_cambia() -> None:
    """Repetir lo mismo no escribe; pasar de día toca la fecha y deja el texto."""
    print("\n[4] Solo se escribe lo que cambia")
    carpeta_con_cache("escrituras", None)
    lote = {"https://x.test/%d" % i: "descripción %d " % i * 50 for i in range(20)}
    almacen.recordar_descripciones(lote, {"https://x.test/3": {"candidatos": 4}})
    with almacen._lock_cache:
        conn = almacen._abrir_cache()
        antes = conn.total_changes
    almacen.recordar_descripciones(lote, {"https://x.test/3": {"candidatos": 4}})
    comprueba(conn.total_changes == antes, "repetir lo mismo el mismo día no escribe ni una fila")

    with almacen._lock_cache:
        conn.execute("UPDATE vistas SET visto = ?", (RECIENTE,))
        antes = conn.total_changes
    almacen.recordar_descripciones(lote, {})
    comprueba(conn.total_changes - antes == len(lote),
              "al día siguiente se toca la fecha de cada enlace (%d filas) y ningún texto" % len(lote))
    comprueba(almacen.cargar_competencia_vista().get("https://x.test/3") == {"candidatos": 4},
              "   y la competencia que ya tenía sigue ahí")

    # CONTROL NEGATIVO: el contador ve un texto que cambia de verdad.
    antes = conn.total_changes
    almacen.recordar_descripciones({"https://x.test/0": "otra descripción"}, {})
    comprueba(conn.total_changes - antes == 1 and almacen.cargar_descripciones_vistas().get("https://x.test/0") == "otra descripción",
              "control: una descripción que cambia sí se escribe, y el contador lo ve")


def caso_cache_roto() -> None:
    """Un fichero ilegible se aparta y se empieza de nuevo; la corrida no se entera."""
    print("\n[5] Un caché roto no tumba nada")
    carpeta = carpeta_con_cache("roto", CACHE)
    almacen.cargar_cache_vistas()
    almacen.cerrar_cache()
    for sufijo in ("-wal", "-shm"):
        Path(str(almacen.ruta_cache()) + sufijo).unlink(missing_ok=True)
    almacen.ruta_cache().write_bytes(b"esto no es una base de datos " * 400)

    descripciones, competencia = almacen.cargar_cache_vistas()
    comprueba(descripciones.get("https://x.test/1") == "texto 1" or descripciones.get("https://x.test/1") is None,
              "preguntar a un caché roto no lanza nada")
    apartados = sorted(p.name for p in carpeta.glob("descripciones_vistas.db.roto-*") if not p.name.endswith(("-wal", "-shm")))
    comprueba(len(apartados) == 1, "el fichero roto se aparta con fecha, no se borra (%s)" % ", ".join(apartados))
    comprueba(almacen.recordar_descripciones({"https://x.test/n": "después del susto"}) >= 1
              and almacen.cargar_descripciones_vistas().get("https://x.test/n") == "después del susto",
              "y el caché nuevo funciona: se puede recordar y volver a preguntar")
    try:
        sqlite3.connect(str(almacen.ruta_cache())).execute("PRAGMA quick_check").fetchone()
        sano = True
    except sqlite3.DatabaseError:
        sano = False
    comprueba(sano, "   y es un SQLite sano")

    # Otra avería, de las que no se arreglan apartando: no se puede abrir (aquí, porque en su sitio hay una
    # carpeta). Una corrida pregunta por cientos de enlaces: tiene que seguir, y decirlo UNA vez.
    carpeta = carpeta_con_cache("no-abre", None)
    almacen.ruta_cache().mkdir()
    lineas: list[str] = []
    escucha = type("Escucha", (logging.Handler,), {"emit": lambda self, r: lineas.append(r.getMessage())})()
    almacen.logger.addHandler(escucha)
    try:
        descripciones, competencia = almacen.cargar_cache_vistas()
        respuestas = [descripciones.get("https://x.test/%d" % i) for i in range(50)]
        respuestas += [competencia.get("https://x.test/%d" % i) for i in range(50)]
        guardadas = almacen.recordar_descripciones({"https://x.test/1": "no se va a poder guardar"})
    finally:
        almacen.logger.removeHandler(escucha)
    comprueba(respuestas == [None] * 100 and guardadas == 0,
              "un caché que no se puede abrir contesta «no lo tengo» a todo y no guarda, sin lanzar nada")
    avisos = [linea for linea in lineas if "no responde" in linea]
    comprueba(len(avisos) == 1, "   y lo dice una vez, no una por enlace (%d líneas con 101 intentos)" % len(avisos))
    almacen.cerrar_cache()
    almacen.ruta_cache().rmdir()
    comprueba(almacen.recordar_descripciones({"https://x.test/1": "ahora sí"}) == 1
              and almacen.cargar_descripciones_vistas().get("https://x.test/1") == "ahora sí",
              "   y en la corrida siguiente, arreglado el motivo, vuelve a funcionar solo")


def caso_base_y_cache() -> None:
    """`Conocidas` responde como el `{**en_cache, **en_base}` de antes."""
    print("\n[6] Si un enlace está en la base y en el caché, manda la base")
    carpeta_con_cache("conocidas", CACHE)
    en_cache, _ = almacen.cargar_cache_vistas()
    en_base = {"https://x.test/1": "la de la base", "https://x.test/solo-base": "solo en la base", "https://x.test/vacia": ""}
    conocidas = almacen.Conocidas(en_base, en_cache)
    de_antes = {**dict(en_cache), **{u: t for u, t in en_base.items()}}
    comprueba(conocidas.get("https://x.test/1") == "la de la base" == de_antes["https://x.test/1"],
              "en los dos: la de la base")
    comprueba(conocidas.get("https://x.test/2") == "texto 2" and conocidas.get("https://x.test/solo-base") == "solo en la base",
              "solo en uno: la de donde esté")
    comprueba(conocidas.get("https://x.test/nada") is None and "https://x.test/nada" not in conocidas
              and "https://x.test/2" in conocidas and conocidas["https://x.test/2"] == "texto 2",
              "`in`, `[]` y `.get` como un diccionario (así lo usan los espías de las otras pruebas)")
    comprueba(conocidas.get("https://x.test/vacia") is None,
              "una descripción vacía en la base no tapa nada (antes daba «», que el buscador trata igual que no tenerla)")
    comprueba(sorted(conocidas) == sorted(de_antes), "y recorrerlo da los mismos enlaces que el diccionario de antes")


def main() -> int:
    caso_sale_lo_mismo()
    caso_importacion()
    caso_una_corrida()
    caso_solo_lo_que_cambia()
    caso_cache_roto()
    caso_base_y_cache()
    almacen.cerrar_cache()
    if fallos:
        print("\nFALLOS (%d de %d comprobaciones):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
