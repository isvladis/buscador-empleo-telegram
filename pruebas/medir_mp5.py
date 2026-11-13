# -*- coding: utf-8 -*-
"""
La memoria del servicio con la web (antes de MP5) y sin ella (MP5), en el PC y con la base real.

Cada árbol corre en su propio proceso, sobre una copia nueva de los datos y sin red, y se mide en
tres puntos, siempre con lo mismo encima:

1. **arrancado**: importado el servicio, abierta la base (lo que hace `main` antes de los hilos);
2. **atendido**: lo que cada versión hace cuando el titular mira sus ofertas. Antes, un refresco de
   la web (`GET /api/datos`, que monta y guarda el listado entero puntuado); ahora, /top, /estado y
   /perfil por el bot. Es lo que se va con la web, y es lo único que MP5 puede bajar;
3. **pico**: el máximo residente del proceso hasta ahí.

Lo que NO mide, a propósito: una corrida. Pone el 51,5 % del RSS en régimen (CAMBIOS-MP0.md §7) y
MP5 no la toca; meterla aquí escondería la diferencia de verdad entre otras diez veces más grandes.
La memoria es la del proceso: en Linux VmRSS + VmSwap y VmHWM; en Windows WorkingSet, más lo que
esté en el fichero de paginación, y PeakWorkingSet. En el Toshiba se mide aparte (DESPLIEGUE.md).

    PYTHONUTF8=1 python pruebas/medir_mp5.py <carpeta-con-perfiles.db> [--antes <commit>]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
COMMIT_DE_ANTES = "HEAD"
REPETICIONES = 3

MEDIR = r'''
import ctypes, json, os, sys
from pathlib import Path


def memoria():
    """(residente + paginado, pico residente), en MiB."""
    estado = Path("/proc/self/status")
    if estado.exists():
        v = {l.split(":")[0]: int(l.split()[1]) for l in estado.read_text().splitlines() if l.split()[1:2] and l.split()[1].isdigit()}
        return (v.get("VmRSS", 0) + v.get("VmSwap", 0)) / 1024, v.get("VmHWM", 0) / 1024
    from ctypes import wintypes

    class Contadores(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = Contadores()
    c.cb = ctypes.sizeof(c)
    # Sin los tipos, en 64 bits el HANDLE (-1) se trunca y la llamada devuelve ceros sin fallar.
    proceso = ctypes.windll.kernel32.GetCurrentProcess
    proceso.restype = wintypes.HANDLE
    info = ctypes.windll.psapi.GetProcessMemoryInfo
    info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Contadores), wintypes.DWORD]
    if not info(proceso(), ctypes.byref(c), c.cb):
        raise OSError("GetProcessMemoryInfo ha fallado")
    return c.WorkingSetSize / 1048576, c.PeakWorkingSetSize / 1048576


sys.path.insert(0, sys.argv[1])
import logging
logging.disable(logging.CRITICAL)
import requests


class Resp:
    status_code, ok, text = 200, True, "{}"

    def json(self):
        return {"ok": True, "result": {"message_id": 1}}


requests.post = lambda *a, **k: Resp()
import app
import perfiles
perfiles.conexion()
medidas = {"arrancado": memoria()[0]}
if hasattr(app, "app"):  # antes de MP5: la web
    respuesta = app.app.test_client().get("/api/datos")
    medidas["trabajo"] = "GET /api/datos: %d ofertas" % len(respuesta.get_json()["ofertas"])
else:
    import config as config_mod
    import telegram_bot
    titular = perfiles.titular()
    cfg = config_mod.cargar_config()
    for texto in ("/top", "/estado", "/perfil"):
        telegram_bot.procesar({"update_id": 1, "message": {"message_id": 1, "text": texto,
                               "chat": {"id": titular, "type": "private"}}}, {**cfg, "telegram_bot_token": "x", "telegram_chat_id": str(titular)})
    medidas["trabajo"] = "/top, /estado y /perfil"
medidas["atendido"], medidas["pico"] = memoria()
print(json.dumps(medidas))
'''


def arbol_de_antes(commit: str) -> Path:
    destino = Path(tempfile.mkdtemp(prefix="medir-mp5-antes-"))
    archivo = subprocess.run(["git", "archive", commit], cwd=RAIZ, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(destino)], input=archivo, check=True)
    return destino


def una_medida(arbol: Path, copia: Path) -> dict:
    datos = Path(tempfile.mkdtemp(prefix="medir-mp5-datos-"))
    try:
        for fichero in copia.iterdir():
            if fichero.is_file():
                shutil.copy2(fichero, datos / fichero.name)
        entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
        entorno.update(JOBSPY_DATA_DIR=str(datos), PYTHONUTF8="1")
        proceso = subprocess.run([sys.executable, "-c", MEDIR, str(arbol)], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=entorno, cwd=datos)
        if proceso.returncode:
            raise RuntimeError(proceso.stderr[-1500:])
        return json.loads(proceso.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(datos, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("copia")
    p.add_argument("--antes", default=COMMIT_DE_ANTES)
    args = p.parse_args()
    copia = Path(args.copia)
    antes = arbol_de_antes(args.antes)
    try:
        filas = {}
        for nombre, arbol in (("antes (web)", antes), ("MP5 (bot)", RAIZ)):
            medidas = [una_medida(arbol, copia) for _ in range(REPETICIONES)]
            filas[nombre] = medidas
        print(f"Copia: {copia} · {REPETICIONES} procesos por versión, mediana\n")
        print("| Versión | Arrancado | Tras atender | Pico | Qué atiende |")
        print("|---|---|---|---|---|")
        for nombre, medidas in filas.items():
            mediana = {c: sorted(m[c] for m in medidas)[len(medidas) // 2] for c in ("arrancado", "atendido", "pico")}
            print(f"| {nombre} | {mediana['arrancado']:.1f} | {mediana['atendido']:.1f} | {mediana['pico']:.1f} | "
                  f"{medidas[0]['trabajo']} |")
    finally:
        shutil.rmtree(antes, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
