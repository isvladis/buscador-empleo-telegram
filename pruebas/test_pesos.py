# -*- coding: utf-8 -*-
"""
Pruebas de los pesos de relevancia que se decidieron el 25-10-2026: N2 resta 2,0 y
administrador de sistemas resta 2,5, ninguno de los dos descarta.

Lo que protegen estas pruebas es el equilibrio que costó medir: que las ofertas que el
usuario marcó como favoritas o a las que aplicó sigan apareciendo, y que los falsos
positivos que motivaron los pesos caigan por debajo del umbral de aviso.

    python pruebas/test_pesos.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JOBSPY_DATA_DIR", tempfile.mkdtemp(prefix="test-pesos-"))

import buscador  # noqa: E402

UMBRAL = buscador.UMBRAL_ENCAJE_ALTO

# (título, ubicación, ¿debe avisar?, por qué)
# Los títulos son reales: salen del historial del usuario o de los falsos positivos
# medidos en la muestra del 21-24/10.
CASOS = [
    # --- Lo que NO debe avisar (eran falsos positivos) ---
    ("Técnico/a Helpdesk Nivel 2", "Barcelona, Catalonia, Spain", False,
     "favorita del usuario, pero es N2: baja a la web, no despierta"),
    ("Técnico/a Soporte N2", "Barcelona, Spain", False, "ídem"),
    ("TECHNICAL SUPPORT L2", "A Coruña, GA, ES", False, "L2"),
    ("Corporate IT Systems Administrator", "Barcelona, CT, ES", False,
     "administrador de sistemas sin señal junior"),
    ("Administrador de sistemas Interim - Barcelona - 11 meses", "Barcelona, Catalonia, Spain", False,
     "ídem, y además interim"),
    ("Administrador/a de Sistemas VMware (N2/N3)", "Greater Barcelona Metropolitan Area", False,
     "N2 + N3 + administrador: se acumulan a propósito"),
    ("Técnico/a de Sistemas Windows N2/N3", "Greater Barcelona Metropolitan Area", False, "N2 + N3"),

    # --- Lo que SÍ debe seguir avisando (está en su historial) ---
    ("Administrador/a de Sistemas Junior 24x7 – Barcelona", "Barcelona, Spain", True,
     "APLICÓ a esta: la excepción por «junior» es lo que la salva"),
    ("Administrador/a de Sistemas Junior (Remote)", "Spain", True, "APLICÓ a esta"),
    ("Administrador/a Endpoint (Microsoft & Azure)", "Barcelona, Catalonia, Spain", True,
     "le interesa el puesto de endpoint: excepción explícita"),
    ("IT HelpDesk Specialist | L1 - L2 | Provider", "Barcelona, Spain", True,
     "el L2 resta, pero el L1 suma más: sale sola, sin excepción escrita"),
    ("Técnico/a de Help Desk - Soporte IT Nivel 1", "Barcelona, CT, ES", True,
     "el caso ideal: los pesos no lo tocan"),
    ("SOC Analyst", "Barcelona, Catalonia, Spain", True, "el objetivo principal"),
    ("Analista de Ciberseguridad N1 (SOC)", "Barcelona, Spain", True, "N1 no resta"),
    ("Técnico/a Helpdesk", "Barcelona, Catalonia, Spain", True, "soporte sin nivel indicado"),
]

fallos: list[str] = []
hechas = 0


def main() -> int:
    global hechas
    print("Pesos de relevancia (umbral de aviso: %.1f)\n" % UMBRAL)
    for titulo, ubicacion, debe_avisar, motivo in CASOS:
        puntos = buscador.puntuar_con_reglas(titulo, ubicacion)
        avisa = puntos >= UMBRAL
        ok = avisa == debe_avisar
        hechas += 1
        print("  %s %6.1f  %-3s  %-52s  %s"
              % ("OK   " if ok else "FALLA", puntos, "avisa" if avisa else "web",
                 titulo[:52], motivo))
        if not ok:
            fallos.append("%s: %.1f, se esperaba %s"
                          % (titulo, puntos, "avisar" if debe_avisar else "no avisar"))

    # Ninguno de los dos pesos debe DESCARTAR: la oferta tiene que seguir en la base
    # para verla en la web. Se comprueba que el filtro de ruido no la toca.
    print("\nNi N2 ni administrador descartan (solo restan):")
    import pandas as pd
    import config as config_mod
    df = pd.DataFrame([
        {"title": "Técnico/a Helpdesk Nivel 2", "location": "Barcelona, Spain",
         "description": "soporte", "job_url": "caso://n2", "is_remote": False},
        {"title": "Corporate IT Systems Administrator", "location": "Barcelona, CT, ES",
         "description": "sistemas", "job_url": "caso://admin", "is_remote": False},
    ])
    quedan = buscador.marcar_ruido(df, dict(config_mod.CONFIG_POR_DEFECTO))
    sobreviven = quedan[~quedan["fuera_de_perfil"]]
    hechas += 1
    if len(sobreviven) == 2:
        print("  OK    las dos siguen en la base (solo bajan de puntuación)")
    else:
        print("  FALLA alguna se ha descartado: %d de 2" % len(sobreviven))
        fallos.append("un peso está descartando en vez de restar")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
