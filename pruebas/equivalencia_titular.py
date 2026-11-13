# -*- coding: utf-8 -*-
"""
La prueba que manda en MP2: con el perfil del titular, oferta a oferta, nada cambia.

Enfrenta el `buscador.py` de antes de MP2, extraído con `git show`, con el de ahora, sobre la
copia REAL de los datos. Por qué hace falta, si ya está pruebas/medir.py: medir arranca con un
directorio vacío y mide con CONFIG_POR_DEFECTO, así que no ve la config del servidor, que trae 25
reglas de descarte frente a 20. Sobre la base esas reglas separan 1.133 ofertas; sobre
etiquetadas.json, 0. Aquí se mira con las DOS configs.

Comprueba, por cada config y cada conjunto de ofertas:

1. Cada filtro por separado: la misma decisión para cada oferta.
2. `buscar()` entero, de portal a DataFrame, con `scrape_jobs` sustituido por un espía: las mismas
   filas, en el mismo orden, con las mismas columnas y los mismos mensajes de progreso.
3. La misma relevancia, los mismos motivos y las mismas alertas para TODAS las ofertas, también las
   que los filtros tumban.

Y además, que `titular-2026-10` es copia exacta de la config real; que un perfil con ese juego y
un bloque vacío decide igual que el titular (es lo que recibirá el adicional, antes de su tope);
que `medir.py --json` da la línea base, y el tiempo de puntuar la base.

Solo 480 de las 2.787 ofertas conservan la descripción, y sin texto ni los años, ni el idioma, ni
la señal de la descripción se ejercitan: hay una pasada con las podadas restauradas del .gz.

**Controles negativos.** Al terminar se relanza a sí misma rompiendo el juego a propósito, y cada
vez tiene que FALLAR. Una prueba de equivalencia que no puede fallar no prueba nada.

Trabaja sobre carpetas temporales: la copia no se toca y no sale a la red. No está en todas.py
porque necesita datos que no van a git; se lanza a mano:

    PYTHONUTF8=1 python pruebas/equivalencia_titular.py [carpeta-de-la-copia]
"""

from __future__ import annotations

import argparse
import ast
import copy
import gzip
import importlib.util
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(AQUI))

COPIA_POR_DEFECTO = RAIZ / "backups_toshiba" / "20261030_013626_mp0"
COMMIT_DE_ANTES = "6fedb4c"
TOPE_TIEMPO = 1.10
CONTROLES = ("reglas20", "peso", "senal", "filtro")

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que), flush=True)
    if not ok:
        fallos.append(que)


def preparar_directorio(copia: Path) -> Path:
    carpeta = Path(tempfile.mkdtemp(prefix="equivalencia-"))
    for nombre in ("geocode_cache.json",):
        shutil.copy2(copia / nombre, carpeta / nombre)
    return carpeta


def cargar_antes():
    """El buscador de antes de MP2, con otro nombre y sus propios globales."""
    carpeta = Path(tempfile.mkdtemp(prefix="buscador-antes-"))
    fuente = subprocess.run(["git", "show", f"{COMMIT_DE_ANTES}:buscador.py"], cwd=RAIZ,
                            capture_output=True, check=True).stdout
    fichero = carpeta / "buscador_antes.py"
    fichero.write_bytes(fuente)
    spec = importlib.util.spec_from_file_location("buscador_antes", fichero)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["buscador_antes"] = modulo
    spec.loader.exec_module(modulo)
    # La única diferencia con aquel código que se da por buena: el «3\+ years» que el 02-11 se
    # arregló a propósito (CAMBIOS-MP4.md §9). El resto tiene que seguir dando 0 diferencias.
    modulo._EXP_PATRONES[:] = [re.compile(rx.pattern.replace(r"\s*\+?", r"\s*\\?\+?"), rx.flags)
                               for rx in modulo._EXP_PATRONES]
    return modulo


class _SinRed:
    """Ni Nominatim ni nadie: una ubicación que no esté en el caché, no se sabe."""

    def __init__(self, *a, **k):
        pass

    def geocode(self, *a, **k):
        return None


def aislar(*modulos) -> None:
    time.sleep = lambda *_: None  # el segundo de Nominatim, en las dos versiones
    for m in modulos:
        m.Nominatim = _SinRed
        m.FUENTES_PROPIAS = {n: (lambda **k: None) for n in m.FUENTES_PROPIAS}
        m.FUENTES_CON_CORRIDA = {}


def reiniciar_puntuadores(antes, ahora) -> None:
    antes._REL.update(comprobado=0.0, firma=None, puntuar=None)
    ahora._REL.update(comprobado=0.0, firma=None, puntuador=None)


def ofertas_de_la_base(copia: Path, con_podadas: bool, limite: int | None) -> list[dict]:
    ofertas = json.loads((copia / "ofertas.json").read_text(encoding="utf-8"))
    if con_podadas:
        with gzip.open(copia / "descripciones_podadas.json.gz", "rt", encoding="utf-8") as f:
            podadas = json.load(f)
        for o in ofertas:
            if not o.get("description") and o.get("job_url") in podadas:
                o["description"] = podadas[o["job_url"]]
    return ofertas[:limite] if limite else ofertas


def casos_de(nombre: str) -> list[dict]:
    casos = json.loads((AQUI / nombre).read_text(encoding="utf-8"))["casos"]
    return [{"description": None, "job_level": None, "is_remote": None, "company": None,
             "ambito": "barcelona_local", "job_url": "caso://%s" % c.get("id"), **c,
             } for c in casos]


def a_dataframe(ofertas: list[dict]):
    import pandas as pd

    df = pd.DataFrame(ofertas)
    for columna in ("min_amount", "max_amount", "job_type", "company_industry", "interval",
                    "site", "currency"):
        if columna not in df.columns:
            df[columna] = None
    df["job_url"] = df["job_url"].fillna("").astype(str)
    return df.reset_index(drop=True)


def comparar_filtros(antes, ahora, df, config, nombre: str, con_idioma: bool) -> None:
    def indices(salida):
        return list(salida.index)

    senior_a = df[~df["title"].fillna("").str.contains(antes.SENIOR_EXCLUDE)]
    senior_b = df[~df["title"].fillna("").str.contains(ahora.SENIOR_EXCLUDE)]
    comprueba(indices(senior_a) == indices(senior_b), f"{nombre}: senior")

    ruido_a, ruido_b = antes.marcar_ruido(df, config), ahora.marcar_ruido(df, config)
    comprueba(ruido_a["fuera_de_perfil"].equals(ruido_b["fuera_de_perfil"])
              and ruido_a["motivo_descarte"].equals(ruido_b["motivo_descarte"]),
              f"{nombre}: ruido, con el mismo motivo ({int(ruido_b['fuera_de_perfil'].sum())} fuera)")

    efectiva = _efectiva_titular(config)
    filtros = ["filtrar_empresas_excluidas", "filtrar_por_nivel", "filtrar_presenciales_lejos",
               "filtrar_fuera_de_ue"] + (["filtrar_por_idioma"] if con_idioma else [])
    for filtro in filtros:
        a = getattr(antes, filtro)(df, config)
        b = getattr(ahora, filtro)(df, efectiva)
        comprueba(indices(a) == indices(b), f"{nombre}: {filtro} ({len(df) - len(b)} fuera)")

    a = antes.calcular_distancias(df, config)["distancia_km"]
    b = ahora.calcular_distancias(df, efectiva)["distancia_km"]
    comprueba(a.equals(b), f"{nombre}: calcular_distancias")


def _efectiva_titular(config: dict) -> dict:
    import perfiles

    return perfiles.config_efectiva(perfiles.PERFIL_GLOBAL, config)


def comparar_puntuacion(antes, ahora, ofertas: list[dict], nombre: str, anos_max) -> None:
    distintas, altas = [], 0
    for o in ofertas:
        anos_a = antes.extraer_anos_experiencia(o.get("description"))
        anos_b = ahora.extraer_anos_experiencia(o.get("description"))
        a = antes.explicar_relevancia(o.get("title"), o.get("location"), anos_a, o.get("description"))
        b = ahora.explicar_relevancia(o.get("title"), o.get("location"), anos_b, o.get("description"))
        alertas_a = antes.alertas({**o, "anos_experiencia": anos_a}, anos_max)
        alertas_b = ahora.alertas({**o, "anos_experiencia": anos_b}, anos_max)
        altas += b[0] >= ahora.UMBRAL_ENCAJE_ALTO
        if (anos_a, a, alertas_a) != (anos_b, b, alertas_b):
            distintas.append(o.get("title"))
    comprueba(not distintas, f"{nombre}: relevancia, motivos, alertas, {len(ofertas)} ofertas "
                             f"({altas} en Encaje alto), {len(distintas)} diferencias")
    for titulo in distintas[:5]:
        print("        distinta:", titulo)


def correr_buscar(modulo, df, config):
    """`buscar()` de verdad, con las ofertas de cada ámbito saliendo de la primera consulta suya."""
    pendientes = {
        "Barcelona, Spain": df[df["ambito"] == "barcelona_local"],
        "Spain": df[df["ambito"] == "remoto_es_ue"],
        "European Union": df[~df["ambito"].isin(["barcelona_local", "remoto_es_ue"])],
    }

    def espia(**k):
        return pendientes.pop(k.get("location"), None)

    modulo.scrape_jobs = espia
    mensajes = []
    salida = modulo.buscar(config=config, progreso=lambda m, h, t: mensajes.append(m))
    return salida.reset_index(drop=True), mensajes


def comparar_buscar(antes, ahora, df, config, nombre: str) -> None:
    import pandas as pd

    # Sin descargar nada, y en serie para que el orden no dependa de los hilos.
    config = {**copy.deepcopy(config), "linkedin_fetch_description": False,
              "busquedas_en_paralelo": 1}
    a, msg_a = correr_buscar(antes, df, config)
    b, msg_b = correr_buscar(ahora, df, config)
    try:
        pd.testing.assert_frame_equal(a, b, check_like=False)
        igual = True
    except AssertionError as e:
        print("        ", str(e)[:400])
        igual = False
    comprueba(igual, f"{nombre}: buscar() de punta a punta, {len(b)} ofertas en el mismo orden")
    comprueba(msg_a == msg_b, f"{nombre}: los mismos {len(msg_b)} mensajes de progreso")


def comparar_bloque_vacio(ahora, df, config, nombre: str) -> None:
    """Lo que recibirá el adicional antes de su tope: el juego solo, sin mirar config.json."""
    import pandas as pd
    import perfiles

    perfil = {"chat_id": -1, "version": 1,
              "datos": {"v": 1, "reglas": "titular-2026-10", "config": {}}}
    del_juego = perfiles.config_efectiva(perfil, config)
    puntuador = perfiles.puntuador_de(perfil, config)
    del_titular = _efectiva_titular(config)
    geo = ahora.Geocodificador()

    def evaluar(cfg, punt):
        return ahora.evaluar_caro(ahora.evaluar_barato(df, cfg, geo=geo), cfg, punt, geo=geo)

    a, b = evaluar(del_titular, None), evaluar(del_juego, puntuador)
    try:
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
        igual = True
    except AssertionError as e:
        print("        ", str(e)[:400])
        igual = False
    comprueba(igual, f"{nombre}: el juego solo decide igual que el titular ({len(b)} ofertas)")


def jooble_de_antes() -> dict:
    """
    Los términos de Jooble no están en config.json: hasta MP3 eran dos constantes del módulo. Su
    referencia es por tanto ese módulo tal y como estaba, no el fichero ni el juego de ahora.
    """
    fuente = subprocess.run(["git", "show", f"{COMMIT_DE_ANTES}:fuentes_extra/jooble.py"], cwd=RAIZ,
                            capture_output=True, check=True, text=True, encoding="utf-8").stdout
    return {"jooble_terminos": ast.literal_eval(re.search(r"^TERMINOS = (\[.*\])$", fuente, re.M).group(1)),
            "jooble_ubicacion": ast.literal_eval(re.search(r"^UBICACION = (.+)$", fuente, re.M).group(1))}


def comparar_juego(antes, ahora, viva: dict) -> None:
    import reglas

    juego = reglas.TITULAR_2026_10
    print("\n[1] Lo congelado es exactamente lo que aplica el servidor")
    referencia = {**viva, **jooble_de_antes()}
    for clave in reglas.CLAVES_DE_LA_PERSONA:
        comprueba(juego.config.get(clave) == referencia.get(clave), f"juego == copia real: {clave}")
    comprueba(len(juego.config["reglas_descarte"]) == 25, "25 reglas de descarte")
    comprueba(juego.config["pesos_relevancia"] == {"atención al usuario (SAU, user support)": 4.0},
              "el peso sobrescrito, a 4,0")

    print("\n[2] Las constantes de buscador.py no han cambiado")
    for nombre in ("SENALES_RELEVANCIA", "PENALIZACIONES_RELEVANCIA", "REGLAS_POR_DEFECTO"):
        comprueba(getattr(antes, nombre) == getattr(ahora, nombre), nombre)
    comprueba(antes.SENIOR_EXCLUDE.pattern == ahora.SENIOR_EXCLUDE.pattern, "SENIOR_EXCLUDE")
    comprueba(len(ahora.REGLAS_POR_DEFECTO) == 20, "las de fábrica siguen siendo 20")
    comprueba(antes.UMBRAL_ENCAJE_ALTO == ahora.UMBRAL_ENCAJE_ALTO, "UMBRAL_ENCAJE_ALTO")


def medir_tiempo(antes, ahora, ofertas: list[dict]) -> None:
    def pasada(modulo) -> float:
        t0 = time.perf_counter()
        for o in ofertas:
            modulo.explicar_relevancia(o.get("title"), o.get("location"), o.get("_anos"),
                                       o.get("description"))
        return time.perf_counter() - t0

    for o in ofertas:
        o["_anos"] = ahora.extraer_anos_experiencia(o.get("description"))
    tiempos_a, tiempos_b = [], []
    for _ in range(5):  # alternando, para que nada del calentamiento caiga de un solo lado
        tiempos_a.append(pasada(antes))
        tiempos_b.append(pasada(ahora))
    a, b = statistics.median(tiempos_a), statistics.median(tiempos_b)
    comprueba(b <= a * TOPE_TIEMPO,
              f"puntuar {len(ofertas)} ofertas: {a:.3f} s antes, {b:.3f} s ahora "
              f"({100 * (b / a - 1):+.1f} %, tope {100 * (TOPE_TIEMPO - 1):+.0f} %)")


def comparar_medir() -> None:
    print("\n[6] medir.py --json, contra la línea base")
    entorno = {k: v for k, v in os.environ.items() if k != "JOBSPY_DATA_DIR"}
    proceso = subprocess.run([sys.executable, str(AQUI / "medir.py"), "--json", "--juego", "fabrica",
                              "--muestra", "etiquetadas.json", "--historial", "historial.json"],
                             capture_output=True, text=True, encoding="utf-8", env=entorno)
    base = json.loads((AQUI / "linea_base_fabrica.json").read_text(encoding="utf-8"))
    comprueba(json.loads(proceso.stdout) == base, "idéntico a linea_base_fabrica.json")


def romper(control: str, ahora) -> None:
    """Los daños de los controles negativos. Cada uno tiene que hacer fallar la prueba."""
    import reglas

    juego = reglas.TITULAR_2026_10
    if control == "reglas20":  # congelar las de fábrica: el error que esta prueba existe para cazar
        juego.config["reglas_descarte"] = ahora.REGLAS_POR_DEFECTO
    elif control == "peso":
        juego.config["pesos_relevancia"] = {}
    elif control == "senal":
        n, p, rx = juego.reglas_titulo[1]
        juego.reglas_titulo[1] = (n, p + 0.5, rx)
    elif control == "filtro":
        ahora.filtrar_por_nivel = lambda df, config: df.iloc[1:]


def controles(copia: Path) -> None:
    print("\n[7] Controles negativos: rompiendo el juego, esta prueba tiene que fallar")
    for control in CONTROLES:
        proceso = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(copia), "--romper", control],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        lineas = [l.strip() for l in (proceso.stdout or "").splitlines() if "FALLA" in l]
        comprueba(proceso.returncode == 1 and bool(lineas),
                  f"con «{control}» falla ({len(lineas)} comprobaciones): "
                  + (lineas[0][:80] if lineas else "NO HA FALLADO"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("copia", nargs="?", default=str(COPIA_POR_DEFECTO))
    p.add_argument("--romper", choices=CONTROLES, help="control negativo: uso interno")
    args = p.parse_args()
    copia = Path(args.copia)
    if not (copia / "ofertas.json").exists():
        print(f"No hay copia de los datos en {copia}.")
        return 2

    os.environ["JOBSPY_DATA_DIR"] = str(preparar_directorio(copia))
    logging.disable(logging.WARNING)
    import buscador as ahora
    import config as config_mod

    antes = cargar_antes()
    aislar(antes, ahora)
    if args.romper:
        romper(args.romper, ahora)
    viva = json.loads((copia / "config.json").read_text(encoding="utf-8"))
    print(f"Antes: {COMMIT_DE_ANTES}. Copia: {copia.name}.")
    comparar_juego(antes, ahora, viva)
    if args.romper in ("reglas20", "peso"):
        # El daño está en el juego y ya ha saltado; lo de abajo son minutos que no añaden nada.
        return 1 if fallos else 0

    limite = 300 if args.romper else None
    ruta_viva = Path(os.environ["JOBSPY_DATA_DIR"]) / "config.json"
    ruta_viva.write_text(json.dumps(viva, ensure_ascii=False), encoding="utf-8")
    por_defecto = Path(os.environ["JOBSPY_DATA_DIR"]) / "no-existe.json"
    seccion = 3
    for nombre_cfg, ruta, config in (
        ("viva", ruta_viva, config_mod._aplicar_secretos_del_entorno(
            config_mod._fusionar(config_mod.CONFIG_POR_DEFECTO, viva))),
        ("por defecto", por_defecto, dict(config_mod.CONFIG_POR_DEFECTO)),
    ):
        config_mod.CONFIG_PATH = ruta
        reiniciar_puntuadores(antes, ahora)
        print(f"\n[{seccion}] Config {nombre_cfg}")
        seccion += 1
        conjuntos = [
            ("base", ofertas_de_la_base(copia, False, limite), False),
            ("base + podadas", ofertas_de_la_base(copia, True, limite), True),
            ("etiquetadas", casos_de("etiquetadas.json"), True),
            ("historial", casos_de("historial.json"), True),
        ]
        anos_max = config.get("anos_experiencia_max")
        for nombre, ofertas, con_idioma in conjuntos:
            df = a_dataframe(ofertas)
            comparar_filtros(antes, ahora, df, config, f"{nombre_cfg}/{nombre}", con_idioma)
            comparar_puntuacion(antes, ahora, ofertas, f"{nombre_cfg}/{nombre}", anos_max)
            comparar_buscar(antes, ahora, df, config, f"{nombre_cfg}/{nombre}")
        if nombre_cfg == "viva":
            df = a_dataframe(conjuntos[1][1])
            comparar_bloque_vacio(ahora, df, config, "viva/base + podadas")

    config_mod.CONFIG_PATH = ruta_viva
    reiniciar_puntuadores(antes, ahora)
    print("\n[5] Tiempo de puntuar la base (mediana de 5, alternando)")
    medir_tiempo(antes, ahora, ofertas_de_la_base(copia, False, limite))

    if not args.romper:
        comparar_medir()
        controles(copia)

    print("\n" + ("TODO BIEN: %d comprobaciones, 0 diferencias" % hechas if not fallos
                  else "%d FALLOS de %d comprobaciones" % (len(fallos), hechas)))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
