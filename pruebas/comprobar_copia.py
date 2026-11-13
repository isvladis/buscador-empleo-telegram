# -*- coding: utf-8 -*-
"""
¿Esta copia de `perfiles.db` sirve para restaurar? (MP10)

    PYTHONUTF8=1 python pruebas/comprobar_copia.py <copia-AAAAMMDD_HHMMSS.tar.gz>
    PYTHONUTF8=1 python pruebas/comprobar_copia.py <copia> --con-el-codigo   # además, la abre el servicio
    PYTHONUTF8=1 python pruebas/comprobar_copia.py <copia> --extraer <carpeta>   # y la deja ahí, comprobada

Una copia que no se ha intentado restaurar no es una copia: es una esperanza. Esto hace, sin
red y sin tocar la copia, lo que haría falta para fiarse de ella:

1. **El tar**: que se lee entero, que el manifiesto va el primero, que no trae nada que no
   diga el manifiesto ni rutas que se salgan de la carpeta (`..`, absolutas), y que cada
   fichero casa con su huella.
2. **La base**: `PRAGMA integrity_check` completo (no el `quick_check` de la copia: aquí sí
   sobra tiempo), y los recuentos por tabla iguales a los del manifiesto, que salieron de la
   base viva en el momento de copiar.
3. **Con `--con-el-codigo`**: la abre `perfiles.py` como lo haría el servicio al arrancar (su
   `quick_check`, el esquema que conoce y quién es el titular), calcula la config efectiva de
   cada perfil activo con el `config.json` de la propia copia, y monta `/estado` y la ficha de
   cada uno con Telegram fuera. Es lo que de verdad tiene que funcionar tras restaurar.

**Lo que enseña son números, nunca lo de una persona** (regla 5 y D9): filas por tabla y, por
perfil, su posición («perfil 1/2», el titular primero), su estado y cuántos enlaces, marcas y
avisos tiene. Ni chat_id, ni términos, ni ofertas, ni marcas. La ficha se construye para ver que
no falla, y no se imprime.

Sale con 0 si la copia sirve y con 1 si no, diciendo por qué.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "operacion"))

import copia_perfiles  # noqa: E402  (los recuentos, calculados igual que al hacer la copia)


class CopiaMala(Exception):
    pass


def leer_tar(copia: Path, carpeta: Path) -> dict:
    """Extrae la copia en 'carpeta' (vacía) y devuelve el manifiesto. Falla si algo no cuadra."""
    try:
        with tarfile.open(copia, "r:gz") as tar:
            miembros = tar.getmembers()
            if not miembros or miembros[0].name != copia_perfiles.MANIFIESTO:
                raise CopiaMala("el manifiesto no es lo primero de la copia")
            manifiesto = json.loads(tar.extractfile(miembros[0]).read().decode("utf-8"))
            esperados = set(manifiesto.get("ficheros", {})) | {copia_perfiles.MANIFIESTO}
            for m in miembros:
                if not m.isfile() or m.name not in esperados or "/" in m.name or "\\" in m.name \
                        or m.name.startswith(".."):
                    raise CopiaMala(f"la copia trae algo que no dice el manifiesto: {m.name!r}")
            faltan = esperados - {m.name for m in miembros}
            if faltan:
                raise CopiaMala(f"faltan en la copia: {', '.join(sorted(faltan))}")
            for m in miembros[1:]:
                datos = tar.extractfile(m).read()
                if hashlib.sha256(datos).hexdigest() != manifiesto["ficheros"][m.name]["sha256"]:
                    raise CopiaMala(f"{m.name} no casa con su huella")
                (carpeta / m.name).write_bytes(datos)
    except (tarfile.TarError, OSError, EOFError, KeyError, ValueError) as e:
        raise CopiaMala(f"la copia no se puede leer entera: {type(e).__name__}: {e}") from e
    if copia_perfiles.BASE not in manifiesto["ficheros"]:
        raise CopiaMala("la copia no lleva perfiles.db")
    return manifiesto


def comprobar_base(base: Path, manifiesto: dict) -> dict:
    conn = sqlite3.connect(f"file:{base.as_posix()}?mode=ro", uri=True)
    try:
        resultado = conn.execute("PRAGMA integrity_check").fetchall()
        if [r[0] for r in resultado] != ["ok"]:
            raise CopiaMala(f"integrity_check: {'; '.join(r[0] for r in resultado[:5])}")
        recuentos = copia_perfiles.recuentos(conn)
        por_perfil = conn.execute(
            "SELECT p.estado,"
            " (SELECT COUNT(*) FROM ofertas_perfil o WHERE o.chat_id = p.chat_id),"
            " (SELECT COUNT(*) FROM marcas m WHERE m.chat_id = p.chat_id),"
            " (SELECT COUNT(*) FROM avisos a WHERE a.chat_id = p.chat_id),"
            " p.chat_id = (SELECT CAST(valor AS INTEGER) FROM meta WHERE clave = 'titular')"
            " FROM perfiles p").fetchall()
    except sqlite3.DatabaseError as e:
        raise CopiaMala(f"la base no se abre: {e}") from e
    finally:
        conn.close()
    if recuentos != manifiesto["base"]:
        raise CopiaMala("los recuentos de la base no son los del manifiesto: "
                        + json.dumps({"copia": recuentos, "manifiesto": manifiesto["base"]}, sort_keys=True))
    if recuentos["ofertas_sin_enlace"]:
        raise CopiaMala(f"{recuentos['ofertas_sin_enlace']} ofertas sin ningún enlace")
    # El titular primero, y el resto en un orden que no dice nada de nadie.
    por_perfil.sort(key=lambda f: (not f[4], f[0], -f[1]))
    return {"recuentos": recuentos, "por_perfil": [f[:4] for f in por_perfil]}


def comprobar_con_el_codigo(carpeta: Path) -> list[str]:
    """Lo que hace el servicio al arrancar sobre la base restaurada, sin red y sin Telegram."""
    os.environ["JOBSPY_DATA_DIR"] = str(carpeta)
    for variable in ("JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_ID", "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
        os.environ.pop(variable, None)
    sys.path.insert(0, str(RAIZ))
    import config as config_mod
    import perfiles
    import telegram_bot

    hechas = []
    version = int(perfiles.meta_get("version_esquema"))
    if version != perfiles.MIGRACIONES[-1][0]:
        raise CopiaMala(f"la copia va por el esquema {version} y el código por el {perfiles.MIGRACIONES[-1][0]}")
    hechas.append(f"perfiles.py la abre: esquema {version}, quick_check ok")
    config = config_mod.cargar_config()
    # Los de la tabla, no perfiles.activos(): sin el chat_id del titular en el entorno (en el PC no
    # está, regla 3) y sin «titular» en meta, activos() añadiría un titular de mentira, el 0.
    activos = perfiles.listar("activo")
    guardado = perfiles.meta_get("titular")
    titular = int(guardado) if guardado is not None else None
    if titular is None:
        hechas.append("la copia no guarda quién es el titular (meta.titular): lo pondrá el .env del servicio")
    elif not any(p["chat_id"] == titular for p in activos):
        raise CopiaMala("el titular guardado no está entre los perfiles activos de la copia")
    for n, perfil in enumerate(sorted(activos, key=lambda p: p["chat_id"] != titular), 1):
        efectiva = perfiles.config_efectiva(perfil, config)
        if not efectiva.get("puntuador"):
            raise CopiaMala(f"perfil {n}: config efectiva sin puntuador")
        if not telegram_bot.ficha(perfil, config, n, es_titular=perfil["chat_id"] == titular).strip():
            raise CopiaMala(f"perfil {n}: ficha vacía")
        hechas.append(f"perfil {n}/{len(activos)}: config efectiva ({efectiva['puntuador'].get('tipo')}) y ficha, sin error")
    estado = telegram_bot.texto_de_estado(config)
    if "Perfiles:" not in estado:
        raise CopiaMala("/estado no sale entero")
    hechas.append("/estado sale entero")
    perfiles.cerrar()
    return hechas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Comprueba una copia de perfiles.db.")
    p.add_argument("copia", type=Path)
    p.add_argument("--con-el-codigo", action="store_true", help="además, la abre perfiles.py como el servicio")
    p.add_argument("--extraer", type=Path, help="deja la copia comprobada en esta carpeta (vacía o nueva)")
    args = p.parse_args(argv)

    temporal = Path(tempfile.mkdtemp(prefix="comprobar-copia-"))
    try:
        manifiesto = leer_tar(args.copia, temporal)
        base = comprobar_base(temporal / copia_perfiles.BASE, manifiesto)
        print(f"Copia {args.copia.name}, hecha {manifiesto['creada_utc']} (UTC): tar y huellas ok, integrity_check ok.")
        filas = base["recuentos"]["filas"]
        print("  Filas: " + ", ".join(f"{t} {filas[t]}" for t in sorted(filas)))
        print(f"  Ofertas sin enlace: {base['recuentos']['ofertas_sin_enlace']} · faltan: "
              f"{', '.join(manifiesto['faltan']) or 'nada'}")
        for n, (estado, enlaces, marcas, avisos) in enumerate(base["por_perfil"], 1):
            print(f"  Perfil {n}/{len(base['por_perfil'])}: {estado}, {enlaces} enlaces, {marcas} marcas, {avisos} avisos")
        if args.extraer:
            # Antes de abrirla con el código: lo que se deja para restaurar es la copia tal cual,
            # no una base que perfiles.py ya ha abierto (y podría haber puesto al día).
            args.extraer.mkdir(parents=True, exist_ok=True)
            if any(args.extraer.iterdir()):
                raise CopiaMala(f"{args.extraer} no está vacía: no se mezcla una copia con otros datos")
            for nombre in manifiesto["ficheros"]:
                shutil.copy2(temporal / nombre, args.extraer / nombre)
            print(f"  Extraída en {args.extraer}")
        if args.con_el_codigo:
            for linea in comprobar_con_el_codigo(temporal):
                print("  " + linea)
    except CopiaMala as e:
        print(f"LA COPIA NO SIRVE: {e}")
        return 1
    finally:
        if "perfiles" in sys.modules:
            sys.modules["perfiles"].cerrar()
        shutil.rmtree(temporal, ignore_errors=True)
    print("La copia sirve para restaurar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
