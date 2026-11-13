# -*- coding: utf-8 -*-
r"""
Escribe `perfilado/catalogo.json` a partir de `perfilado/fuente.py`.

    python -m perfilado.construir            # lo escribe solo si ha cambiado
    python -m perfilado.construir --comprobar  # sale con 1 si el JSON se ha quedado atrás

Antes de escribir se comprueba que ninguna cadena lleve un carácter de retroceso (0x08): es lo que deja
un "\b" en una cadena normal de Python en vez de r"\b", y no se nota al leerlo (le pasó a un
\bfy\d{2}\b el 04-11). **Se mira en las cadenas y no en el fichero:** `json.dumps` escribe el 0x08 como
`\b`, dos caracteres normales, así que `read_bytes().count(bytes([8]))` sobre un JSON da 0 aunque esté
roto. Lo descubrió la primera versión de su prueba, que escribió un JSON corrupto sin que nada lo viera.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from perfilado import fuente

RUTA = Path(__file__).resolve().parent / "catalogo.json"
RETROCESO = chr(8)


def retrocesos(dato, donde: str = "") -> list[str]:
    """Dónde hay un carácter de retroceso dentro de `dato` (dicts, listas y cadenas)."""
    if isinstance(dato, str):
        return [donde or "(raíz)"] if RETROCESO in dato else []
    if isinstance(dato, dict):
        return [r for k, v in dato.items() for r in retrocesos(v, "%s/%s" % (donde, k))]
    if isinstance(dato, list):
        return [r for i, v in enumerate(dato) for r in retrocesos(v, "%s/%d" % (donde, i))]
    return []


def contenido() -> str:
    # indent=1: legible en un diff y la mitad de grande que con 2. ensure_ascii=False: los acentos de
    # las regex se leen tal cual.
    return json.dumps(fuente.catalogo(), ensure_ascii=False, indent=1) + "\n"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    malos = retrocesos(fuente.catalogo())
    if malos:
        print("catalogo.json llevaría un carácter de retroceso (un \\b sin r''): %s" % ", ".join(malos[:3]),
              file=sys.stderr)
        return 1
    nuevo = contenido()
    actual = RUTA.read_text(encoding="utf-8") if RUTA.exists() else None
    if "--comprobar" in argv:
        if actual != nuevo:
            print("catalogo.json no está al día: python -m perfilado.construir", file=sys.stderr)
            return 1
        print("catalogo.json al día")
        return 0
    if actual == nuevo:
        print("catalogo.json ya estaba al día; no se escribe")
        return 0
    # newline="\n": el mismo fichero en el PC y en el servidor, sin CRLF.
    RUTA.write_text(nuevo, encoding="utf-8", newline="\n")
    print("catalogo.json escrito: %d bytes" % len(nuevo.encode("utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
