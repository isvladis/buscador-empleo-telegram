# -*- coding: utf-8 -*-
"""
La precisión del catálogo por familia (MP7), con lo que hay para medirla.

    python pruebas/medir_mp7.py                       # las muestras, por familia
    python pruebas/medir_mp7.py --base <perfiles.db>  # y cuántos títulos reales casa cada núcleo

**Las muestras** (`catalogo_muestras.json`): para cada puesto, una persona de 3–5 años en Barcelona que
elige solo ese puesto; un título «llega» si pasa descartes y senior y puntúa ≥ 5,0 en Barcelona. Los
«si» deberían llegar; los «no» y las «rebajadas», no. Precisión = de los que llegan, cuántos son «si».

**Esto está inflado y hay que leerlo así.** Las muestras las escribió quien escribió las regex, y las
regex se corrigieron hasta acertarlas: es el mismo conjunto que se usó para ajustar, lo mismo que la
muestra del titular, que da un 81 % al umbral 5,0 y bastante menos con sus botones de verdad. Sirve
para comparar familias entre sí y para ver cuáles tienen pocos títulos reales, no para prometer aciertos.
La medida independiente es la de los núcleos contra la base ANTES de retocarlos (CAMBIOS-MP7.md §4).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
os.environ.setdefault("JOBSPY_DATA_DIR", tempfile.mkdtemp(prefix="medir-mp7-"))

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import perfiles  # noqa: E402
from perfilado import generador  # noqa: E402

CONFIG = dict(config_mod.CONFIG_POR_DEFECTO)
PERSONA = {"experiencia": "3_5", "experiencia_estricta": True, "modalidad": ["presencial"],
           "ubicacion": {"nombre": "Barcelona", "lat": 41.3874, "lon": 2.1686, "radio_km": 10},
           "idiomas": ["es", "ca", "en"], "ingles": "intermedio"}


def llegan(plan: dict, chat_id: int, titulos: list[str]) -> list[bool]:
    perfil = {"chat_id": chat_id, "version": 1, "datos": {"v": 1, "reglas": "catalogo", "plan": plan}}
    efectiva = perfiles.config_efectiva(perfil, CONFIG)
    punt = perfiles.puntuador_de(perfil, CONFIG)
    df = pd.DataFrame([{"title": t, "location": "Barcelona, Spain", "company": "ACME", "description": None,
                        "job_level": None, "is_remote": None, "ambito": "local", "job_url": "caso://%d" % i}
                       for i, t in enumerate(titulos)])
    fuera = buscador.marcar_ruido(df, efectiva)["fuera_de_perfil"].tolist() if titulos else []
    senior = buscador._compilar(efectiva["patron_senior"]) if efectiva["filtrar_senior"] else None
    salida = []
    for t, f in zip(titulos, fuera):
        nota = buscador.explicar_relevancia(t, "Barcelona, Spain", puntuador=punt)[0]
        salida.append(not f and not (senior and senior.search(t)) and nota >= efectiva["umbral"])
    return salida


def medir_muestras(catalogo: dict, muestras: dict) -> list[dict]:
    filas = []
    chat = 5000
    for familia in catalogo["familias"]:
        cuenta = {"tp": 0, "fp": 0, "fn": 0, "tp_r": 0, "fp_r": 0, "si": 0, "reales": 0, "total": 0}
        for p in familia["puestos"]:
            chat += 1
            plan = generador.generar_plan(dict(PERSONA, puestos=[p["id"]]), catalogo=catalogo)
            m = muestras[p["id"]]
            si = m["si"]
            no = m["no"] + m.get("rebajadas", [])
            ls = llegan(plan, chat, [t["titulo"] for t in si])
            ln = llegan(plan, chat, [t["titulo"] for t in no])
            cuenta["tp"] += sum(ls)
            cuenta["fn"] += len(ls) - sum(ls)
            cuenta["fp"] += sum(ln)
            cuenta["tp_r"] += sum(1 for t, x in zip(si, ls) if x and not t.get("sintetico"))
            cuenta["fp_r"] += sum(1 for t, x in zip(no, ln) if x and not t.get("sintetico"))
            cuenta["si"] += len(si)
            todos = si + no
            cuenta["reales"] += sum(1 for t in todos if not t.get("sintetico"))
            cuenta["total"] += len(todos)
        filas.append(dict(cuenta, familia=familia["id"], puestos=len(familia["puestos"])))
    return filas


def medir_base(catalogo: dict, ruta: str) -> dict:
    con = sqlite3.connect("file:%s?mode=ro" % Path(ruta).as_posix(), uri=True)
    titulos = sorted({json.loads(d).get("title", "").strip() for (d,) in con.execute("SELECT datos FROM ofertas")} - {""})
    con.close()
    salida = {"titulos": len(titulos)}
    for familia in catalogo["familias"]:
        for p in familia["puestos"]:
            rx = re.compile(p["nucleo"], re.IGNORECASE)
            salida[p["id"]] = sum(1 for t in titulos if rx.search(t))
    return salida


def pct(a: int, b: int) -> str:
    return "—" if not b else "%d %%" % round(100 * a / b)


def main(argv=None) -> int:
    args = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    args.add_argument("--base", help="copia de perfiles.db, para contar los títulos reales que casa cada núcleo")
    args.add_argument("--json", action="store_true")
    opciones = args.parse_args(argv)
    catalogo = generador.cargar_catalogo()
    muestras = json.loads((AQUI / "catalogo_muestras.json").read_text(encoding="utf-8"))
    filas = medir_muestras(catalogo, muestras)
    base = medir_base(catalogo, opciones.base) if opciones.base else None
    if opciones.json:
        print(json.dumps({"muestras": filas, "base": base}, ensure_ascii=False, indent=1))
        return 0
    print("| familia | puestos | precisión | recall | precisión solo reales | títulos reales |")
    print("|---|---|---|---|---|---|")
    for f in filas:
        print("| %s | %d | %s | %s | %s | %d de %d |" % (
            f["familia"], f["puestos"], pct(f["tp"], f["tp"] + f["fp"]), pct(f["tp"], f["si"]),
            pct(f["tp_r"], f["tp_r"] + f["fp_r"]), f["reales"], f["total"]))
    if base:
        print("\nTítulos de la base (%d) que casa cada núcleo:" % base["titulos"])
        for familia in catalogo["familias"]:
            print("  %-10s %s" % (familia["id"], ", ".join(
                "%s %d" % (p["id"].split(".", 1)[1], base[p["id"]]) for p in familia["puestos"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
