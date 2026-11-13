# -*- coding: utf-8 -*-
"""
Copia de seguridad diaria de `data/perfiles.db` y de lo pequeño que la acompaña (MP10).

    python3 operacion/copia_perfiles.py                 # en el host del Toshiba, como vc
    python3 operacion/copia_perfiles.py --datos DIR --destino DIR --diarias 7 --semanales 4

**Por qué en el host y no dentro del servicio.** Medido el 05-11-2026 desde el host, como `vc`
y sin `sudo`: el `sqlite3` del sistema (Python 3.11, SQLite 3.40.1) abre `perfiles.db` con
`mode=ro` aunque la base sea de root y esté en uso, y la API de backup la copia entera en 0,37 s
con `quick_check` ok. Así la copia no necesita reconstruir ni recrear el contenedor (que corta la
serie de memoria y borra su log), no añade nada al proceso de 512 MiB y la instala el titular con
una línea de crontab. Dentro del servicio habría hecho falta desplegar en mitad del piloto.

**Por qué la API de backup y no `cp`.** La base va en WAL: lo último escrito puede estar en el
`-wal`, y un `cp` con el servicio en marcha se lo deja o lo copia a medias. La API de backup copia
una foto coherente de la base *con* lo que hay en el WAL, bajo una transacción de lectura que, en
WAL, no bloquea a quien escribe.

**Qué lleva cada copia** (un `copia-AAAAMMDD_HHMMSS.tar.gz`, hora UTC como todo lo del contenedor):

- `perfiles.db`: perfiles, ofertas, enlaces, marcas, avisos, cola… todo lo de cada persona.
- `config.json`: las reglas del titular viven ahí (`datos.config = "global"`), no en la base.
- `jooble_uso.json`, `salud_fuentes.json`, `geocode_cache.json`, `modelo_relevancia.json` y
  `descripciones_podadas.json.gz`: pequeños, y sin ellos la vuelta atrás no es la misma.
- `manifiesto.json`: la huella de cada fichero y los recuentos por tabla, **sin un solo chat_id**,
  para comprobar la copia sin abrir lo de nadie (`pruebas/comprobar_copia.py`).

**Qué NO lleva:** `descripciones_vistas.json` (caché que se regenera; `DESPLIEGUE.md` ya lo
dejaba fuera de las copias) ni los JSON de antes de MP1, que no cambian desde el 30-10.

**Rotación:** las 7 últimas y, de las anteriores, la más nueva de cada una de las 4 semanas
siguientes. Unos 3,5 MB por copia y ~40 MB en total; ~3,5 MB escritos al día frente a los ~2,2 GB
que recibe el disco (`CAMBIOS-MP1.md` §9). Una copia que falla no rota nada: nunca se borra una
buena para dejar sitio a una mala. Solo se borran ficheros con el nombre exacto de una copia.

Cada persona que se borra sigue en las copias hasta que roten (unas 5 semanas como mucho): está
escrito en `DESPLIEGUE.md` y en la ayuda de los datos.

Solo librería estándar y nada de `perfiles` ni del resto del proyecto: corre con el Python del host,
que no tiene las dependencias del contenedor. Sale con 0 si la copia está hecha y comprobada, y con
1 si no. Cada ejecución deja una línea en `<destino>/registro.log`, sin datos de nadie.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent

BASE = "perfiles.db"
# Lo pequeño que acompaña a la base. Si falta alguno, la copia sigue: en una instalación nueva
# puede no existir aún (p. ej. jooble_uso.json antes de la primera descarga de Jooble).
ACOMPANANTES = ("config.json", "jooble_uso.json", "salud_fuentes.json", "geocode_cache.json",
                "modelo_relevancia.json", "descripciones_podadas.json.gz")
MANIFIESTO = "manifiesto.json"
# Lo único que se puede borrar al rotar. Un patrón estricto: nada que se le parezca por casualidad.
NOMBRE = re.compile(r"^copia-(\d{8}_\d{6})\.tar\.gz$")
REGISTRO = "registro.log"
LINEAS_DE_REGISTRO = 200
# Sin este margen no se intenta: llenar el disco del servidor rompería también la base viva.
LIBRE_MINIMO = 200 * 1024 * 1024
FORMATO_DEL_MANIFIESTO = 1


class CopiaFallida(Exception):
    pass


def recuentos(conn: sqlite3.Connection) -> dict:
    """Filas por tabla y lo que dice si la base está entera. Solo números: ni un chat_id."""
    tablas = [f[0] for f in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    filas = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tablas}
    version = conn.execute("SELECT valor FROM meta WHERE clave = 'version_esquema'").fetchone() \
        if "meta" in tablas else None
    huerfanas = None
    if "ofertas" in tablas and "ofertas_perfil" in tablas:
        huerfanas = conn.execute(
            "SELECT COUNT(*) FROM ofertas o WHERE NOT EXISTS "
            "(SELECT 1 FROM ofertas_perfil p WHERE p.url = o.url)").fetchone()[0]
    por_estado = {}
    if "perfiles" in tablas:
        por_estado = dict(conn.execute("SELECT estado, COUNT(*) FROM perfiles GROUP BY estado").fetchall())
    return {"filas": filas, "version_esquema": version[0] if version else None,
            "ofertas_sin_enlace": huerfanas, "perfiles_por_estado": por_estado}


def copiar_base(origen: Path, destino: Path) -> dict:
    """
    Foto coherente de `origen` en `destino` (que no debe existir), comprobada antes de devolverla.

    `mode=ro`: este proceso no puede escribir la base viva ni por error, ni crear un `-wal` suyo.
    """
    if not origen.exists() or origen.stat().st_size == 0:
        raise CopiaFallida(f"no hay {origen.name} en {origen.parent}")
    fuente = sqlite3.connect(f"file:{origen.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        copia = sqlite3.connect(destino)
        try:
            fuente.backup(copia)
            resultado = copia.execute("PRAGMA quick_check").fetchone()[0]
            if resultado != "ok":
                raise CopiaFallida(f"la copia no pasa quick_check: {resultado[:200]}")
            datos = recuentos(copia)
        finally:
            copia.close()
    finally:
        fuente.close()
    # Al cerrar la última conexión, SQLite quita el -wal y el -shm de la copia. Si quedara alguno,
    # la copia no estaría entera en un solo fichero: mejor fallar que empaquetar media base.
    for sufijo in ("-wal", "-shm", "-journal"):
        if destino.with_name(destino.name + sufijo).exists():
            raise CopiaFallida(f"la copia ha dejado un {sufijo} al cerrarse")
    return datos


def hacer_copia(datos_dir: Path, destino_dir: Path, ahora: datetime | None = None) -> Path:
    ahora = ahora or datetime.now(timezone.utc)
    sello = ahora.strftime("%Y%m%d_%H%M%S")
    final = destino_dir / f"copia-{sello}.tar.gz"
    if final.exists():
        raise CopiaFallida(f"ya existe {final.name}")
    destino_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(destino_dir).free < LIBRE_MINIMO:
        raise CopiaFallida("menos de 200 MB libres en el disco: no se copia")

    # El temporal, en el mismo disco que el destino: así el os.replace del final es atómico.
    with tempfile.TemporaryDirectory(prefix=".copia-", dir=destino_dir) as temporal:
        temporal = Path(temporal)
        base = temporal / BASE
        manifiesto = {"formato": FORMATO_DEL_MANIFIESTO, "creada_utc": ahora.isoformat(timespec="seconds"),
                      "base": copiar_base(datos_dir / BASE, base), "ficheros": {}, "faltan": []}
        # Cada fichero se lee UNA vez y de esos mismos bytes salen la huella y lo que entra en el
        # tar: si el servicio reescribiera config.json justo ahora (lo hace con os.replace, así que
        # nunca se ve uno a medias), la copia llevaría una versión entera y su huella, no una mezcla.
        # Son ~11 MB en memoria del host, no del contenedor.
        contenidos: dict[str, bytes] = {BASE: base.read_bytes()}
        for nombre in ACOMPANANTES:
            camino = datos_dir / nombre
            if camino.is_file():
                contenidos[nombre] = camino.read_bytes()
            else:
                manifiesto["faltan"].append(nombre)
        for nombre, contenido in contenidos.items():
            manifiesto["ficheros"][nombre] = {"bytes": len(contenido),
                                              "sha256": hashlib.sha256(contenido).hexdigest()}

        a_medias = temporal / (final.name + ".parcial")
        sello_tar = int(ahora.timestamp())
        with tarfile.open(a_medias, "w:gz", compresslevel=6) as tar:
            # El manifiesto, el primero: comprobar una copia no obliga a descomprimirla entera.
            texto = json.dumps(manifiesto, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            for nombre, contenido in [(MANIFIESTO, texto)] + list(contenidos.items()):
                info = tarfile.TarInfo(nombre)
                info.size, info.mtime, info.mode = len(contenido), sello_tar, 0o600
                tar.addfile(info, io.BytesIO(contenido))
        os.chmod(a_medias, 0o600)
        _releer(a_medias, manifiesto)
        os.replace(a_medias, final)
    return final


def _releer(camino: Path, manifiesto: dict) -> None:
    """Lo escrito se vuelve a leer entero antes de darlo por bueno: el disco ya falló una vez."""
    try:
        with tarfile.open(camino, "r:gz") as tar:
            nombres = tar.getnames()
            for nombre, esperado in manifiesto["ficheros"].items():
                h = hashlib.sha256()
                f = tar.extractfile(nombre)
                if f is None:
                    raise CopiaFallida(f"{nombre} no está en la copia")
                for trozo in iter(lambda: f.read(1 << 20), b""):
                    h.update(trozo)
                if h.hexdigest() != esperado["sha256"]:
                    raise CopiaFallida(f"{nombre} no coincide con su huella al releer la copia")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise CopiaFallida(f"la copia no se puede releer: {e}") from e
    if nombres[:1] != [MANIFIESTO]:
        raise CopiaFallida("el manifiesto no es lo primero de la copia")


def copias_en(destino_dir: Path) -> list[tuple[datetime, Path]]:
    """Las copias del directorio, de la más nueva a la más vieja. Solo las de nombre exacto."""
    salida = []
    if not destino_dir.is_dir():
        return salida
    for camino in destino_dir.iterdir():
        m = NOMBRE.match(camino.name)
        if m and camino.is_file():
            salida.append((datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc), camino))
    return sorted(salida, reverse=True)


def a_borrar(copias: list[tuple[datetime, Path]], diarias: int, semanales: int) -> list[Path]:
    """
    Las 'diarias' más nuevas se quedan; de las demás, la más nueva de cada semana ISO, hasta
    'semanales' semanas que no estén ya cubiertas por una diaria. El resto se borra.
    """
    if diarias < 1:
        raise ValueError("hay que conservar al menos una copia")
    cubiertas = {tuple(fecha.isocalendar()[:2]) for fecha, _ in copias[:diarias]}
    tomadas = 0
    sobran = []
    for fecha, camino in copias[diarias:]:
        semana = tuple(fecha.isocalendar()[:2])
        if semana not in cubiertas and tomadas < semanales:
            cubiertas.add(semana)
            tomadas += 1
            continue
        sobran.append(camino)
    return sobran


def rotar(destino_dir: Path, diarias: int, semanales: int) -> list[Path]:
    borradas = []
    for camino in a_borrar(copias_en(destino_dir), diarias, semanales):
        if NOMBRE.match(camino.name):  # otra vez, justo antes de borrar
            camino.unlink()
            borradas.append(camino)
    return borradas


def _registrar(destino_dir: Path, linea: str) -> None:
    """Una línea por ejecución; el fichero se queda en las últimas 200 (unos 20 KB)."""
    try:
        destino_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        camino = destino_dir / REGISTRO
        anteriores = camino.read_text(encoding="utf-8").splitlines() if camino.exists() else []
        sello = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lineas = (anteriores + [f"{sello} {linea}"])[-LINEAS_DE_REGISTRO:]
        temporal = camino.with_name(camino.name + ".tmp")
        temporal.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        os.replace(temporal, camino)
    except OSError:
        pass  # el registro es una ayuda: su fallo no puede tapar el resultado, que va por la salida


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Copia diaria de perfiles.db (MP10).")
    p.add_argument("--datos", type=Path, default=AQUI.parent / "data", help="el data/ del servicio")
    p.add_argument("--destino", type=Path, default=None, help="por defecto, <datos>/copias")
    p.add_argument("--diarias", type=int, default=7)
    p.add_argument("--semanales", type=int, default=4)
    args = p.parse_args(argv)
    destino = args.destino or (args.datos / "copias")
    inicio = time.monotonic()
    try:
        copia = hacer_copia(args.datos, destino)
    except (CopiaFallida, sqlite3.Error, OSError) as e:
        mensaje = f"FALLA {type(e).__name__}: {e}"
        _registrar(destino, mensaje)
        print(mensaje, file=sys.stderr)
        return 1
    borradas = rotar(destino, args.diarias, args.semanales)
    mensaje = (f"OK {copia.name} {copia.stat().st_size / 1048576:.1f} MiB en "
               f"{time.monotonic() - inicio:.1f} s; rotadas {len(borradas)}; quedan {len(copias_en(destino))}")
    _registrar(destino, mensaje)
    print(mensaje)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
