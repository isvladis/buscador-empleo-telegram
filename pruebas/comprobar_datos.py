# -*- coding: utf-8 -*-
"""
Comprueba que los datos siguen enteros después de un despliegue: que perfiles.db se puede
leer y que las decisiones del usuario (favoritas, ocultas, aplicadas) están ahí.

El despliegue no toca data/ en ningún paso, así que esto es una red de seguridad: si
alguna vez un cambio lo tocara, es lo que lo delataría.

    python3 pruebas/comprobar_datos.py <directorio-data>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def abrir_sin_escribir(ruta: Path) -> sqlite3.Connection:
    """
    Abre la base en solo lectura desde el host, como `vc`, siendo sus ficheros de root.

    Probado en Linux con un usuario sin permiso de escritura: con el contenedor en marcha
    (hay -wal y -shm) `mode=ro` funciona y ve lo último. Con el contenedor parado no hay
    -shm y él no puede crearlo, así que falla; ahí nadie escribe y `immutable=1` es seguro.
    """
    uri = ruta.resolve().as_uri()
    conn = sqlite3.connect(uri + "?mode=ro", uri=True)
    try:
        conn.execute("SELECT count(*) FROM meta").fetchone()
        return conn
    except sqlite3.OperationalError:
        conn.close()
        return sqlite3.connect(uri + "?immutable=1", uri=True)


def main() -> int:
    if len(sys.argv) < 2:
        print("  MAL   falta el directorio data/")
        return 1
    data = Path(sys.argv[1])
    malo = 0

    base = data / "perfiles.db"
    if not base.exists():
        print("  MAL   falta %s" % base)
        malo += 1
    else:
        try:
            conn = abrir_sin_escribir(base)
            # Solo lo del titular: esto lo mira él, y las marcas de otro perfil no son asunto suyo.
            # Además, sumadas, «las mismas que antes de desplegar» dejaría de significar nada.
            fila = conn.execute("SELECT valor FROM meta WHERE clave = 'titular'").fetchone()
            chat = int(fila[0]) if fila else 0
            ofertas = conn.execute("SELECT count(*) FROM ofertas_perfil WHERE chat_id = ?",
                                   (chat,)).fetchone()[0]
            print("  OK    perfiles.db: %d ofertas enlazadas" % ofertas)
            if not ofertas:
                print("  MAL   la base de ofertas está vacía")
                malo += 1
            marcas = dict(conn.execute("SELECT accion, count(*) FROM marcas WHERE chat_id = ?"
                                       " GROUP BY accion", (chat,)))
            print("  OK    marcas: %d favoritas, %d ocultas, %d aplicadas"
                  % (marcas.get("favorita", 0), marcas.get("oculta", 0), marcas.get("aplicada", 0)))
            if not marcas.get("aplicada") and not marcas.get("favorita"):
                print("  MAL   no hay ninguna favorita ni aplicada: eso no cuadra")
                malo += 1
            conn.close()
        except sqlite3.Error as e:
            print("  MAL   perfiles.db no se puede leer: %s" % e)
            malo += 1

    salud_path = data / "salud_fuentes.json"
    if salud_path.exists():
        try:
            salud = json.loads(salud_path.read_text(encoding="utf-8"))
            desactivadas = [f for f, i in (salud.get("fuentes") or {}).items()
                            if i.get("desactivada")]
            pendientes = len(salud.get("incidencias_pendientes") or [])
            print("  ---   salud: %s desactivada(s)%s, %d incidencia(s) por avisar"
                  % (len(desactivadas),
                     (" (" + ", ".join(desactivadas) + ")") if desactivadas else "",
                     pendientes))
        except (OSError, json.JSONDecodeError) as e:
            print("  MAL   salud_fuentes.json no se puede leer: %s" % e)
            malo += 1
    return malo


if __name__ == "__main__":
    raise SystemExit(main())
