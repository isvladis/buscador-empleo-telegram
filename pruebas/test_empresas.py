# -*- coding: utf-8 -*-
"""
Pruebas del filtro de empresas excluidas (Evolve, Tech Nova).

Motivo: anuncios que se presentan como puestos de ciberseguridad pero son formación
(«Programa Talento Joven - Ciberseguridad»). Ninguna regla por título puede separarlos de
un puesto real, porque el título es el de un puesto real; lo que los delata es la empresa.

    python pruebas/test_empresas.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JOBSPY_DATA_DIR", tempfile.mkdtemp(prefix="test-empresas-"))

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def sobreviven(empresas: list[str], config: dict | None = None) -> list[str]:
    df = pd.DataFrame([{"title": "Analista de Ciberseguridad Junior", "company": e,
                        "job_url": f"caso://{i}"} for i, e in enumerate(empresas)])
    cfg = dict(config or config_mod.CONFIG_POR_DEFECTO)
    return list(buscador.filtrar_empresas_excluidas(df, cfg)["company"])


def main() -> int:
    print("\n[1] Los nombres reales de la base se descartan")
    for nombre in ("Evolve", "TECH NOVA", "evolve", "Tech Nova", "TechNova", "TECHNOVA",
                   "  Evolve  ", "Evolve S.L.", "Tech Nova, S.L."):
        comprueba(sobreviven([nombre]) == [], "se descarta %r" % nombre)

    print("\n[2] Empresas que se parecen pero no son, NO se tocan")
    for nombre in ("Revolve Labs", "Evolved Security", "Involve Consulting", "Nova Security",
                   "Tech Novation", "Novatech", "Technology Nova Partners", "Capitole",
                   "BizAway Srl", "TECH DATA", "Allianz Services"):
        comprueba(sobreviven([nombre]) == [nombre], "se conserva %r" % nombre)

    print("\n[3] Acentos, mayúsculas y signos no engañan")
    comprueba(sobreviven(["ÉVOLVE"]) == [], "«ÉVOLVE» (con acento) se descarta")
    comprueba(sobreviven(["Tech-Nova"]) == [], "«Tech-Nova» se descarta")

    print("\n[4] Casos límite")
    comprueba(len(sobreviven([None, "", float("nan")])) == 3,
              "sin nombre de empresa (None, vacío, NaN) sobreviven las 3: no se puede saber")
    df = pd.DataFrame([{"title": "x", "company": "Evolve", "job_url": "caso://1"}])
    comprueba(buscador.filtrar_empresas_excluidas(df.iloc[0:0], {}).empty,
              "un DataFrame vacío no rompe")
    comprueba(sobreviven(["Evolve"], dict(config_mod.CONFIG_POR_DEFECTO, empresas_excluidas=[])) == ["Evolve"],
              "con la lista vacía no se descarta nada (se puede apagar)")
    comprueba(sobreviven(["Otra SL"], dict(config_mod.CONFIG_POR_DEFECTO,
                                           empresas_excluidas=["Otra"])) == [],
              "se puede añadir otra empresa desde la config")

    print("\n[5] Está de serie en la config por defecto")
    comprueba("Evolve" in config_mod.CONFIG_POR_DEFECTO["empresas_excluidas"], "Evolve")
    comprueba("Tech Nova" in config_mod.CONFIG_POR_DEFECTO["empresas_excluidas"], "Tech Nova")

    print("\n[6] Un título perfecto de una empresa excluida se descarta igual")
    df = pd.DataFrame([
        {"title": "Analista SOC Junior N1", "company": "TECH NOVA", "job_url": "caso://a"},
        {"title": "Analista SOC Junior N1", "company": "Capitole", "job_url": "caso://b"},
    ])
    out = buscador.filtrar_empresas_excluidas(df, dict(config_mod.CONFIG_POR_DEFECTO))
    comprueba(list(out["company"]) == ["Capitole"],
              "mismo título: cae el de TECH NOVA y se queda el de Capitole")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
