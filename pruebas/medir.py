# -*- coding: utf-8 -*-
"""
Medidor de precisión del filtrado. Es la herramienta que decide si un cambio en los
filtros o en los pesos mejora o empeora, en vez de discutirlo de palabra.

Por qué existe: hasta la auditoría del 25-10-2026 no había forma de saberlo. La
puntuación guardada en ofertas.json es la del día del descubrimiento, y los pesos han
cambiado varias veces, así que ni se podía comparar con ella (en 50 de 117 ofertas la
guardada no coincidía con la que daba el código de ese mismo día).

Qué mide, sobre los casos de pruebas/etiquetadas.json:
  - cuántos avisos saldrían, con qué precisión y qué recall, a varios umbrales;
  - en qué se equivoca: el desglose de los falsos positivos por causa;
  - y lo que de verdad protege de un filtro demasiado agresivo: cuántas de las ofertas
    a las que el usuario APLICÓ o marcó como favorita (pruebas/historial.json) se
    estaría descartando.

Uso:
    python pruebas/medir.py                 # tabla completa
    python pruebas/medir.py --umbral 5.0    # detalle de un umbral
    python pruebas/medir.py --json          # salida para comparar entre commits
    python pruebas/medir.py --muestra etiquetadas.json --historial historial.json \
                            --juego fabrica         # la medida de linea_base_fabrica.json

Sin argumentos mide al titular de hoy: `titular-ciber` sobre su muestra, y debe dar exactamente
pruebas/linea_base_mp4.json. Todo lo de la persona sale del juego, reglas de descarte incluidas,
y test_ciber.py comprueba que es lo mismo que se escribe en el config.json del servidor.

`--juego fabrica` es la config por defecto del código, con las 20 reglas de fábrica, que no son
las del servidor (CAMBIOS-MP2.md §2). Con ella y las dos muestras viejas sale linea_base_fabrica.json,
que vigila `titular-2026-10`: es lo que recibe el adicional. Es linea_base_mp0.json con el arreglo
del «3\\+ years» (CAMBIOS-MP4.md §9); aquella se queda como foto de MP0.

Hace falta el entorno con las dependencias del proyecto (python-jobspy incluido,
porque buscador.py lo importa al cargarse). En el PC: D:\\Dev\\Envs\\venvs\\jobspy312.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))

# buscador.py y config.py escriben/leen JSON en JOBSPY_DATA_DIR al importarse y al
# puntuar (caché de geocodificación, modelo entrenado). Se les da un directorio
# temporal para que medir nunca toque los datos reales ni los del servidor.
if not os.environ.get("JOBSPY_DATA_DIR"):
    os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="medir-jobspy-")

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import reglas  # noqa: E402

UMBRALES = (3.0, 4.0, 5.0, 6.0, 6.5, 7.0, 7.5)

# Causas de un falso positivo. Se mira el título y la ubicación, en este orden: la
# primera que encaja es la que se apunta, así que lo más determinante va primero.
CAUSAS = [
    ("fuera de Barcelona", re.compile(
        r"madrid|granada|alicante|valencia|sevilla|bilbao|bizkaia|zaragoza|m[aá]laga"
        r"|coru[nñ]a|oviedo|getafe|rozas|vitoria|marbella|moralzarzal|portugalete"
        r"|poland|portugal|netherl|stockholm|groningen|lisbon|berlin|le[oó]n|alcorc[oó]n"
        r"|torrej[oó]n|san sebasti[aá]n|canary|canarias|\bcn, es\b|\bpv, es\b|\bcb, es\b"
        r"|\ban, es\b|\bga, es\b|\bas, es\b|\bmd, es\b|\bvc, es\b", re.I)),
    ("nivel N2/N3/L2/L3", re.compile(r"\bn2\b|\bn3\b|\bl2\b|\bl3\b|nivel\s*[23]\b|level\s*[23]\b", re.I)),
    ("administrador de sistemas", re.compile(r"administrador|administrator|sysadmin|systems? admin", re.I)),
    ("engineer/especialista/coordinador", re.compile(
        r"engineer|ingenier|especialista|specialist|coordinator|coordinador", re.I)),
    ("idioma que no habla", re.compile(r"french|franc[eé]s|german|alem[aá]n|dutch|neerland[eé]s|italian", re.I)),
]


def cargar(nombre: str) -> list[dict]:
    with open(AQUI / nombre, encoding="utf-8") as f:
        return json.load(f)["casos"]


def _a_dataframe(casos: list[dict]) -> pd.DataFrame:
    """DataFrame con las columnas que esperan los filtros de buscador.py."""
    df = pd.DataFrame(casos)
    for columna, valor in (("min_amount", None), ("max_amount", None), ("job_type", None),
                           ("company_industry", None), ("job_url", ""), ("interval", None)):
        if columna not in df.columns:
            df[columna] = valor
    if "job_url" in df.columns:
        # Los fixtures no llevan enlaces (ver etiquetadas.json): se sintetiza uno estable
        # porque algunos filtros y el dedupe lo usan como identificador.
        df["job_url"] = ["caso://" + str(i) for i in df["id"]]
    return df


def pipeline(casos: list[dict], config: dict, puntuador=None) -> pd.DataFrame:
    """
    Pasa los casos por los MISMOS filtros que la búsqueda real aplica después de tener
    la descripción, y los puntúa con el puntuador vivo.

    No se simulan los filtros de origen (el portal) ni el dedupe: los casos ya salen de
    la base, así que ya los han pasado. Lo que se mide aquí es lo que decide si una
    oferta llega a Telegram.
    """
    df = _a_dataframe(casos)
    df = buscador.filtrar_empresas_excluidas(df, config)
    if config.get("filtrar_senior", True):
        df = df[~df["title"].fillna("").str.contains(buscador.SENIOR_EXCLUDE)]
    df = buscador.marcar_ruido(df, config)
    if config.get("descartar_fuera_de_perfil", True):
        df = df[~df["fuera_de_perfil"]]
    for nombre, funcion in (("nivel", buscador.filtrar_por_nivel),
                            ("presencial lejos", buscador.filtrar_presenciales_lejos),
                            ("fuera de la UE", buscador.filtrar_fuera_de_ue),
                            ("idioma", buscador.filtrar_por_idioma)):
        antes = len(df)
        df = funcion(df, config)
        if antes != len(df):
            print("    filtro '%s' descarta %d" % (nombre, antes - len(df)), file=sys.stderr)
    if df.empty:
        return df
    return buscador.anotar_relevancia(df, puntuador)


def metricas(df: pd.DataFrame, casos: list[dict], umbral: float) -> dict:
    relevantes_total = sum(1 for c in casos if c["relevante"])
    if df.empty:
        return {"umbral": umbral, "avisos": 0, "tp": 0, "fp": 0, "fn": relevantes_total,
                "precision": 0.0, "recall": 0.0}
    avisadas = df[df["relevancia"] >= umbral]
    tp = int(avisadas["relevante"].sum())
    fp = len(avisadas) - tp
    fn = relevantes_total - tp
    return {
        "umbral": umbral, "avisos": len(avisadas), "tp": tp, "fp": fp, "fn": fn,
        "precision": 100.0 * tp / len(avisadas) if len(avisadas) else 0.0,
        "recall": 100.0 * tp / relevantes_total if relevantes_total else 0.0,
    }


def causa_del_fallo(fila) -> str:
    texto = "%s %s" % (fila.get("title") or "", fila.get("location") or "")
    for nombre, patron in CAUSAS:
        if patron.search(texto):
            return nombre
    return "otra"


def coste_en_el_historial(config: dict, nombre: str = "historial.json") -> tuple[int, list[dict]]:
    """
    Cuántas de las ofertas a las que el usuario aplicó o marcó como favorita
    descartarían los filtros actuales. Es el contraste que impide que una regla que
    'mejora la precisión' se lleve por delante justo lo que interesaba.
    """
    casos = [c for c in cargar(nombre) if c.get("en_la_base")]
    df = _a_dataframe(casos)
    supervivientes = set(pipeline(casos, config)["job_url"]) if not df.empty else set()
    victimas = [c for c in casos
                if ("caso://" + str(c["id"])) not in supervivientes]
    return len(casos), victimas


def config_de_medida(juego: str) -> tuple[dict, object]:
    """
    (config, puntuador) con los que se mide. Con «fabrica», la config por defecto del código, no
    la del servidor: así la medida no depende de cómo esté el panel en un momento dado, y las dos
    diferencias que importan para el filtrado se fijan aquí a los valores reales de producción.
    """
    config = dict(config_mod.CONFIG_POR_DEFECTO)
    if juego == "fabrica":
        config["max_distancia_km"] = 8
        config["anos_experiencia_max"] = 3
        return config, None
    config.update(copy.deepcopy(reglas.obtener(juego).config))
    config["puntuador"] = {"tipo": "reglas", "juego": juego}
    return config, buscador.crear_puntuador(config)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--umbral", type=float, help="detalle de un umbral concreto")
    p.add_argument("--json", action="store_true", help="salida JSON para comparar commits")
    p.add_argument("--muestra", default="etiquetadas_ciber.json", help="fichero de pruebas/ con los casos")
    p.add_argument("--historial", default="historial_ciber.json", help="fichero de pruebas/ que protege")
    p.add_argument("--juego", default="titular-ciber", help="juego de reglas, o «fabrica»")
    args = p.parse_args()

    casos = cargar(args.muestra)
    config, puntuador = config_de_medida(args.juego)

    print("Casos: %d (%d relevantes según las etiquetas de pruebas/%s)"
          % (len(casos), sum(1 for c in casos if c["relevante"]), args.muestra), file=sys.stderr)
    df = pipeline(casos, config, puntuador)
    print("Sobreviven a los filtros duros: %d de %d\n" % (len(df), len(casos)), file=sys.stderr)

    filas = [metricas(df, casos, u) for u in UMBRALES]
    n_hist, victimas = coste_en_el_historial(config, args.historial)

    if args.json:
        print(json.dumps({
            "casos": len(casos),
            "relevantes": sum(1 for c in casos if c["relevante"]),
            "supervivientes": len(df),
            "umbrales": filas,
            "historial": {"total": n_hist, "descartadas": len(victimas),
                          "titulos": [v["title"] for v in victimas]},
        }, ensure_ascii=False, indent=1))
        return 0

    print("| Umbral | Avisos | Precisión | Recall |")
    print("|--------|--------|-----------|--------|")
    for f in filas:
        print("| %6.1f | %6d | %8.0f %% | %5.0f %% |"
              % (f["umbral"], f["avisos"], f["precision"], f["recall"]))

    umbral = args.umbral if args.umbral is not None else buscador.UMBRAL_ENCAJE_ALTO
    print("\n--- Falsos positivos en el umbral %.1f, por causa ---" % umbral)
    if not df.empty:
        malos = df[(df["relevancia"] >= umbral) & (~df["relevante"])]
        por_causa: dict[str, list] = {}
        for _, fila in malos.iterrows():
            por_causa.setdefault(causa_del_fallo(fila), []).append(fila)
        for causa, filas_causa in sorted(por_causa.items(), key=lambda kv: -len(kv[1])):
            print("  %-34s %d" % (causa, len(filas_causa)))
            for fila in filas_causa[:3]:
                print("      %4.1f  %-44s | %s"
                      % (fila["relevancia"], str(fila["title"])[:44], str(fila["location"])[:26]))

        print("\n--- Relevantes que NO se avisan en el umbral %.1f ---" % umbral)
        perdidas = df[(df["relevancia"] < umbral) & (df["relevante"])]
        descartadas_del_todo = [c for c in casos if c["relevante"]
                                and ("caso://" + c["id"]) not in set(df["job_url"])]
        for _, fila in perdidas.iterrows():
            print("      %4.1f  %-44s | %s"
                  % (fila["relevancia"], str(fila["title"])[:44], str(fila["location"])[:26]))
        for c in descartadas_del_todo:
            print("      FUERA  %-44s | %s  (la tumba un filtro duro)"
                  % (str(c["title"])[:44], str(c["location"])[:26]))

    print("\n--- Coste en el historial real (aplicadas y favoritas) ---")
    print("  %d de %d descartadas por los filtros duros" % (len(victimas), n_hist))
    for v in victimas:
        print("      [%-8s] %-46s | %s"
              % (v["estado"], str(v["title"])[:46], str(v["location"])[:26]))
    if not victimas:
        print("      ninguna: los filtros no tocan nada de lo que te interesó")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
