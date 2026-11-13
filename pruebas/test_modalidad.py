# -*- coding: utf-8 -*-
"""
Pruebas de la modalidad y del filtro geográfico, con ofertas reales.

Los casos no están inventados: son ofertas que de verdad estaban en la base y que el
sistema clasificaba mal. Cada uno lleva apuntado qué hacía antes, para que si alguien
toca los patrones se vea enseguida qué se rompe.

Se cargan de pruebas/etiquetadas.json buscándolas por título, en vez de copiar aquí la
descripción: así la prueba usa el texto real y no una versión mía recortada.

    python pruebas/test_modalidad.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
os.environ.setdefault("JOBSPY_DATA_DIR", tempfile.mkdtemp(prefix="test-modalidad-"))

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402

_TODOS = json.load(open(AQUI / "etiquetadas.json", encoding="utf-8"))["casos"]


def caso(prefijo: str) -> dict | None:
    """Busca una oferta por el principio de su título (los títulos reales son largos)."""
    for c in _TODOS:
        if (c["title"] or "").startswith(prefijo):
            return c
    return None

# (trozo del título, modalidad esperada, ¿se conserva?, por qué)
ESPERADO = [
    ("Operador de Sistemas Junior -Turnos 24X7 ( Hibrido) Madrid", "Híbrido", False,
     "híbrido en Madrid: antes contaba como remoto y llegaba a Telegram con 8,0"),
    ("Técnico/a IT Junior Soporte con inglés - hibrido Madrid", "Híbrido", False,
     "mismo caso, llegaba con 8,5"),
    ("Técnico/a Service Desk (LT1)", "Remoto", True,
     "el portal la ubica en Madrid, pero dice «100 % teletrabajo desde Barcelona»"),
    ("Técnico/a de Soporte Digital N1", "Presencial", False,
     "is_remote=True del portal, pero la descripción dice «Ubicación del trabajo: "
     "Empleo presencial»: manda el campo estructurado"),
    ("Técnico/a Helpdesk (m/f/d)", "Híbrido", False,
     "is_remote=True del portal contra «Trabajo a distancia: Híbrido» en el texto"),
    ("Técnico/a de Sistemas Junior – Granada", "Presencial", False,
     "LinkedIn la marca is_remote=True, pero el anuncio dice «Modalidad: presencial "
     "durante los primeros 6 meses»: el campo estructurado gana a la bandera del portal"),
    ("Service Desk Technician 24*7 (Remote)", "Remoto", True,
     "remota de verdad en Lisboa: dentro de la UE y se puede hacer desde Barcelona"),
    ("Técnico/a Helpdesk Nivel 2", "Presencial", True,
     "presencial en Barcelona: el filtro geográfico no se aplica dentro del área"),
    ("Cloud Security Operations Engineer | Barcelona (Híbrido)", "Híbrido", True,
     "híbrido en Barcelona SÍ vale: el área es la que es"),
]

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    if ok:
        print("  OK    %s" % que)
    else:
        print("  FALLA %s" % que)
        fallos.append(que)


def comprobar_el_area() -> None:
    """
    Comprueba `esta_en_el_area()` con los formatos reales de los portales.

    Existe porque en la primera versión del filtro geográfico el patrón del área llevaba
    `\bct, es\b` para reconocer el formato de Indeed («Barcelona, CT, ES»), y eso daba
    por área de Barcelona CUALQUIER sitio de Cataluña: por ahí entraron 14 ofertas, entre
    ellas dos de Lleida (160 km), y un «Responsable Helpdesk» de Mataró cruzó el umbral
    de aviso con 5,0.
    """
    print("\nÁrea de Barcelona, con los formatos reales de los portales:")
    casos = [
        # dentro
        ("Barcelona, CT, ES", True), ("Barcelona, Catalonia, Spain", True),
        ("L'Hospitalet de Llobregat, Catalonia, Spain", True),
        ("Greater Barcelona Metropolitan Area", True), ("Badalona, CT, ES", True),
        # fuera, y antes se colaban por el código de provincia
        ("Mataró, CT, ES", False), ("Lleida, CT, ES", False), ("Tarragona, CT, ES", False),
        ("Terrassa, CT, ES", False), ("Sant Boi de Llobregat, CT, ES", False),
        # fuera de Cataluña: los códigos de Indeed SÍ dicen la región
        ("PV, ES", False), ("MD, ES", False), ("AN, ES", False),
        ("Madrid, Community of Madrid, Spain", False), ("Poland", False),
        # no se puede saber: solo la región catalana, o solo el país
        ("CT, ES", None), ("Catalonia, ES", None), ("ES", None), ("Spain", None), ("", None),
    ]
    for ubicacion, esperado in casos:
        resultado = buscador.esta_en_el_area(ubicacion)
        comprueba(resultado is esperado,
                  "%-46s -> %-5s %s" % (repr(ubicacion)[:46], resultado,
                                        "" if resultado is esperado else "(esperaba %s)" % esperado))


def main() -> int:
    config = dict(config_mod.CONFIG_POR_DEFECTO)
    print("Modalidad y filtro geográfico, sobre ofertas reales de la base\n")
    for titulo, mod_esperada, se_conserva, motivo in ESPERADO:
        encontrado = caso(titulo)
        if encontrado is None:
            comprueba(False, "falta el caso %r en etiquetadas.json" % titulo[:40])
            continue
        fila = dict(encontrado)
        mod = buscador.modalidad(fila)
        comprueba(mod == mod_esperada,
                  "%-44s -> %-11s (esperado %-11s) %s"
                  % (titulo[:44], mod, mod_esperada, "" if mod == mod_esperada else "<<<"))
        df = pd.DataFrame([{**fila, "job_url": "caso://x"}])
        conservada = not buscador.filtrar_presenciales_lejos(df, config).empty
        comprueba(conservada == se_conserva,
                  "   %s  (%s)" % ("se conserva" if conservada else "se descarta", motivo))

    print("\nEl interruptor de configuración sigue funcionando:")
    fila = dict(caso("Operador de Sistemas Junior -Turnos 24X7"))
    df = pd.DataFrame([{**fila, "job_url": "caso://x"}])
    sin_filtro = dict(config, exigir_remoto_fuera_de_barcelona=False)
    comprueba(not buscador.filtrar_presenciales_lejos(df, sin_filtro).empty,
              "con exigir_remoto_fuera_de_barcelona=false se conserva (reversible)")

    print("\nUna oferta sin ubicación se conserva (no se puede saber dónde es):")
    df = pd.DataFrame([{"title": "Técnico de soporte", "location": "", "description": "",
                        "is_remote": None, "job_url": "caso://y"}])
    comprueba(not buscador.filtrar_presenciales_lejos(df, config).empty,
              "sin ubicación, se conserva")

    comprobar_el_area()

    print("\nLa etiqueta que ve el usuario usa el mismo criterio que el filtro:")
    import etiquetas
    fila = dict(caso("Técnico/a de Soporte Digital N1"))
    df = etiquetas.enriquecer(pd.DataFrame([fila]))
    comprueba(df["modalidad_label"].iloc[0] == "Presencial",
              "modalidad_label dice «Presencial» aunque is_remote del portal sea True")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
