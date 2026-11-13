# -*- coding: utf-8 -*-
"""
Equivalencia oferta a oferta sobre la copia REAL de los datos (MP1, regla 4 de CLAUDE.md).

Coge una copia de data/ traída del Toshiba, la migra a SQLite y comprueba tres cosas:

1. Que la migración no cambia nada: recuentos y contenido, campo a campo y en orden.
2. Que ninguna decisión cambia: la puntuación de cada oferta sale igual leída de SQLite que
   del JSON, y la de toda la base coincide con la `puntuacion.config_viva` de la línea base.
3. Que el almacén nuevo se comporta igual que el viejo con los datos de verdad: corridas
   simuladas sobre las dos, con republicaciones, duplicados, marcas y poda, comparando
   lista, estado, resumen y archivo de podadas. Después, el listado de la web.

Trabaja sobre carpetas temporales: la copia no se toca. No sale a la red. No está en
todas.py porque necesita datos que no van a git; se lanza a mano:

    PYTHONUTF8=1 python pruebas/comprobar_migracion.py [carpeta-de-la-copia]
"""

from __future__ import annotations

import copy
import gzip
import json
import logging
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(AQUI))

COPIA_POR_DEFECTO = RAIZ / "backups_toshiba" / "20261030_013626_mp0"
FICHEROS = ("ofertas.json", "estado.json", "telegram_avisos.json", "config.json",
            "descripciones_podadas.json.gz", "descripciones_vistas.json")
COMMIT_DE_REFERENCIA = "e732641"

fallos: list[str] = []


def comprueba(ok: bool, que: str) -> None:
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def preparar(copia: Path) -> tuple[Path, Path]:
    dir_nuevo = Path(tempfile.mkdtemp(prefix="migracion-sqlite-"))
    dir_ref = Path(tempfile.mkdtemp(prefix="migracion-json-"))
    for nombre in FICHEROS:
        for destino in (dir_nuevo, dir_ref):
            shutil.copy2(copia / nombre, destino / nombre)
    return dir_nuevo, dir_ref


def cargar_referencia(carpeta: Path, reloj):
    import importlib.util

    fuente = subprocess.run(["git", "show", f"{COMMIT_DE_REFERENCIA}:almacen.py"], cwd=RAIZ,
                            capture_output=True, check=True).stdout.decode("utf-8")
    espec = importlib.util.spec_from_loader("almacen_json", loader=None)
    modulo = importlib.util.module_from_spec(espec)
    modulo.__file__ = str(RAIZ / "almacen.py")
    exec(compile(fuente, "almacen_json.py", "exec"), modulo.__dict__)
    modulo.OFERTAS_PATH = carpeta / "ofertas.json"
    modulo.ESTADO_PATH = carpeta / "estado.json"
    modulo.PODADAS_PATH = carpeta / "descripciones_podadas.json.gz"
    modulo.DESCRIPCIONES_PATH = carpeta / "descripciones_vistas.json"
    modulo.datetime = reloj
    return modulo


def corrida_simulada(base: list[dict], rng: random.Random, ronda: int, ocultas: set[str],
                     favoritas: set[str]) -> list[dict]:
    """Lo que devolvería buscar() en una corrida, hecho con ofertas reales de la base."""
    candidatas = [o for o in base if o["job_url"] not in ocultas and o["job_url"] not in favoritas]
    entran: list[dict] = []
    # Las que siguen publicadas: vuelven igual, salvo los campos que no se repiten.
    for o in rng.sample(candidatas, min(len(candidatas), 40)):
        vuelta = {k: v for k, v in o.items() if not k.startswith("visto_por") and k != "descripcion_podada"}
        vuelta.update(candidatos=None, candidatos_tope=None, publicada_en=None)
        entran.append(vuelta)
    # Republicaciones de lo descartado: enlace nuevo, mismo anuncio.
    for i, o in enumerate(rng.sample([x for x in base if x["job_url"] in ocultas], 25)):
        entran.append({k: v for k, v in o.items() if not k.startswith("visto_por") and k != "descripcion_podada"}
                      | {"job_url": f"https://republicada.test/{ronda}/{i}"})
    # La misma vacante en otro portal: el dedupe difuso tiene que juntarlas.
    for i, o in enumerate(rng.sample(candidatas, min(len(candidatas), 12))):
        entran.append({k: v for k, v in o.items() if not k.startswith("visto_por") and k != "descripcion_podada"}
                      | {"job_url": f"https://otro-portal.test/{ronda}/{i}",
                         "site": rng.choice(["indeed", "jooble", "tecnoempleo"])})
    # Y alguna nueva de verdad.
    for i in range(6):
        modelo = rng.choice(base)
        entran.append({k: v for k, v in modelo.items() if not k.startswith("visto_por") and k != "descripcion_podada"}
                      | {"job_url": f"https://nueva.test/{ronda}/{i}", "title": f"Vacante nueva {ronda}-{i}",
                         "company": f"Empresa nueva {i}", "description": "Texto de una oferta nueva " * 20})
    rng.shuffle(entran)
    return entran


def main() -> int:
    copia = Path(sys.argv[1]) if len(sys.argv) > 1 else COPIA_POR_DEFECTO
    if not (copia / "ofertas.json").exists():
        print(f"No hay datos en {copia}")
        return 2
    dir_nuevo, dir_ref = preparar(copia)
    # Antes de importar nada: config y buscador fijan aquí de dónde leen.
    os.environ["JOBSPY_DATA_DIR"] = str(dir_nuevo)
    for variable in ("JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_ID", "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
        os.environ.pop(variable, None)
    logging.disable(logging.WARNING)

    import almacen
    import app
    import buscador
    import perfiles

    class Reloj(datetime):
        actual = datetime(2026, 10, 30, 12, 0, 0)

        @classmethod
        def now(cls, tz=None):
            return cls.actual

    print(f"Copia: {copia}")
    print("\n[1] La migración no cambia nada")
    inicio = time.perf_counter()
    perfiles.migrar_desde_json()
    print(f"      migrada en {time.perf_counter() - inicio:.2f} s")
    informe = perfiles.verificar()
    for nombre, (antes, ahora) in informe["recuentos"].items():
        comprueba(antes == ahora, f"{nombre}: {antes} en el JSON, {ahora} en la base")
    for clave in ("marcas_distintas", "ofertas_distintas", "ofertas_que_faltan", "avisos_distintos"):
        comprueba(not informe[clave], f"{clave}: {len(informe[clave])}")
    comprueba(informe["orden_igual"], "la lista sale en el mismo orden")
    comprueba(informe["ultima_busqueda_igual"], "`ultima_busqueda` igual")
    comprueba(informe["integridad"] == "ok" and not informe["bloques_desfasados"], "integridad y bloques")
    originales = {n: (copia / n).read_bytes() for n in ("ofertas.json", "estado.json", "telegram_avisos.json")}
    comprueba(all((dir_nuevo / n).read_bytes() == b for n, b in originales.items()), "los JSON siguen intactos")
    comprueba(all((dir_nuevo / (n + ".pre-mp1")).read_bytes() == b for n, b in originales.items()),
              "y están copiados a *.pre-mp1")
    print(f"      perfiles.db: {(dir_nuevo / 'perfiles.db').stat().st_size:,} B")

    ref = cargar_referencia(dir_ref, Reloj)
    desde_json = ref.cargar_ofertas()
    desde_sqlite = almacen.cargar_ofertas()
    comprueba(desde_json == desde_sqlite, f"las {len(desde_json)} ofertas, idénticas leídas por los dos almacenes")

    print("\n[2] Ninguna decisión cambia: la puntuación, oferta a oferta")

    def puntuar(oferta: dict) -> tuple:
        anos = buscador.extraer_anos_experiencia(oferta.get("description"))
        return (anos, *buscador.explicar_relevancia(oferta.get("title"), oferta.get("location"), anos,
                                                    oferta.get("description")))

    distintas = [a["job_url"] for a, b in zip(desde_json, desde_sqlite) if puntuar(a) != puntuar(b)]
    comprueba(not distintas, f"{len(distintas)} diferencias en {len(desde_json)} ofertas puntuadas")
    notas = [puntuar(o)[1] for o in desde_sqlite]
    linea = json.loads((AQUI / "linea_base_mp0.json").read_text(encoding="utf-8"))["puntuacion"]["config_viva"]
    altas = sum(1 for n in notas if n >= buscador.UMBRAL_ENCAJE_ALTO)
    comprueba(altas == linea["ofertas_en_encaje_alto"],
              f"en Encaje alto: {altas} (línea base {linea['ofertas_en_encaje_alto']})")
    comprueba(round(statistics.median(notas), 1) == linea["mediana"], f"mediana {statistics.median(notas)}")
    comprueba(round(statistics.mean(notas), 2) == linea["media"], f"media {statistics.mean(notas):.2f}")

    print("\n[3] Corridas simuladas sobre los dos almacenes")
    almacen.datetime = perfiles.datetime = Reloj
    rng = random.Random(2026)
    tiempos = []
    for ronda in range(6):
        Reloj.actual += timedelta(days=rng.choice([0, 1, 2, 9]), hours=1)
        estado = ref.cargar_estado()
        entran = corrida_simulada(ref.cargar_ofertas(), rng, ronda, set(estado["ocultas"]) | set(estado["aplicadas"]),
                                  set(estado["favoritas"]))
        r1 = ref.fusionar_ofertas(copy.deepcopy(entran))
        inicio = time.perf_counter()
        r2 = almacen.fusionar_ofertas(copy.deepcopy(entran))
        tiempos.append(time.perf_counter() - inicio)
        comprueba(r1 == r2, "ronda %d: resumen igual (%d nuevas, %d republicadas, %d fusionadas, %d podadas, %d en total)"
                  % (ronda, r2["nuevas"], r2["republicaciones_descartadas"], r2["duplicados_fusionados"],
                     r2["descripciones_podadas"], r2["total"]))
        a, b = ref.cargar_ofertas(), almacen.cargar_ofertas()
        comprueba([o["job_url"] for o in a] == [o["job_url"] for o in b] and a == b, "   lista idéntica, en orden")
        for oferta in rng.sample(b, 15):
            accion = rng.choice(["oculta", "favorito", "aplicada"])
            ref.marcar(oferta["job_url"], accion, {"title": oferta["title"], "company": oferta.get("company")})
            almacen.marcar(oferta["job_url"], accion, {"title": oferta["title"], "company": oferta.get("company")})
        e1, e2 = ref.cargar_estado(), almacen.cargar_estado()
        comprueba(e1 == e2, "   estado idéntico tras 15 marcas")
    print("      fusionar_ofertas: " + ", ".join(f"{t * 1000:.0f} ms" for t in tiempos))
    leer = lambda p: json.loads(gzip.decompress(p.read_bytes()))  # noqa: E731
    comprueba(leer(ref.PODADAS_PATH) == leer(almacen.PODADAS_PATH), "el archivo de descripciones podadas es igual")

    # [4] comparaba el listado de la web (`app._listado_ligero`), que se fue con MP5.

    perfiles.cerrar()
    print()
    if fallos:
        print(f"FALLOS ({len(fallos)}):")
        for f in fallos:
            print("  - " + f)
        return 1
    print("TODO BIEN: 0 diferencias en la copia real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
