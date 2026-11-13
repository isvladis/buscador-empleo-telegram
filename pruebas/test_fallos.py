# -*- coding: utf-8 -*-
"""
Pruebas del aviso de corridas fallidas (12-11): que el titular se entere cuando el bot no puede trabajar.

El 11-11 el disco del servidor se quedó en solo lectura y el bot siguió arrancado sin guardar nada: una
corrida que falla solo quedaba en el log y en /estado. Lo que se comprueba:

- un fallo suelto no avisa; el segundo seguido, sí, **solo al titular** y una sola vez;
- el aviso no lleva el token (regla 3), va escapado y cabe en un mensaje;
- cuando una corrida vuelve a salir bien se dice, una vez, y la cuenta empieza de cero;
- la base corrupta (`BaseNoDisponible`) sigue con su aviso de siempre y no suma aquí;
- si Telegram tampoco responde, no se da por avisado y se reintenta en el fallo siguiente;
- nada de esto escribe en disco: es lo que más probablemente está roto.

Ninguna sale a la red: `requests.post` es un espía y la corrida de verdad (`app._ejecutar_busqueda`) corre
con `buscador.buscar_para_perfiles` sustituido, que es donde se hace fallar.

    python pruebas/test_fallos.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR = 91000001
DATOS = Path(tempfile.mkdtemp(prefix="test-fallos-"))
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)

import requests  # noqa: E402

import app  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    status_code, ok, text, url = 200, True, "{}", ""

    def json(self):
        return {"ok": True, "result": {"message_id": 1}}

    def raise_for_status(self):
        pass


class Red:
    def __init__(self):
        self.telegram: list[dict] = []
        self.caida = False

    def post(self, url, json=None, timeout=None, **k):  # noqa: A002
        if self.caida:
            raise requests.ConnectionError("sin red: https://api.telegram.org/bot%s/sendMessage" % TOKEN)
        self.telegram.append({"metodo": url.rsplit("/", 1)[-1], **(json or {})})
        return Resp()

    def mensajes(self) -> list[dict]:
        return [m for m in self.telegram if m["metodo"] == "sendMessage"]


red = Red()
requests.post = red.post
# Sin esperas entre reintentos: con Telegram caído la prueba tardaría lo que tarda el emisor de verdad.
notificaciones.time.sleep = lambda *_a, **_k: None

SOLO_LECTURA = sqlite3.OperationalError("attempt to write a readonly database")
lo_que_pasa: list = []  # lo que hace la «corrida» cada vez: una excepción que lanzar, o None si va bien


def _buscar(*_a, **_k) -> None:
    error = lo_que_pasa.pop(0)
    if error is not None:
        raise error


buscador.buscar_para_perfiles = _buscar


def corrida(error=None) -> None:
    lo_que_pasa.append(error)
    app._ejecutar_busqueda(config_mod.cargar_config(), False, True)


def de_cero() -> None:
    app._FALLOS.update(seguidos=0, avisado=False)
    red.telegram.clear()
    red.caida = False


def ficheros() -> dict[str, tuple[int, int]]:
    """Lo que hay en el directorio de datos, con tamaño y fecha: para ver que avisar no escribe nada."""
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in DATOS.iterdir()
            if not p.name.endswith(("-wal", "-shm"))}


def caso_avisa_al_segundo_y_una_vez() -> None:
    print("\n[1] Un fallo suelto no avisa; dos seguidos, sí, al titular y una sola vez")
    de_cero()
    corrida()
    comprueba(not red.mensajes(), "(control: una corrida que sale bien no manda nada)")
    corrida(SOLO_LECTURA)
    comprueba(not red.mensajes() and app.BUSQUEDA["error"], "el primer fallo no avisa; /estado ya lo enseña")
    antes = ficheros()
    corrida(SOLO_LECTURA)
    m = red.mensajes()
    comprueba(len(m) == 1 and str(m[0]["chat_id"]) == str(TITULAR), "el segundo seguido avisa, y solo al titular")
    texto = m[0]["text"] if m else ""
    comprueba("2 veces seguidas" in texto and "readonly" in texto, "dice cuántas van y por qué (el error, tal cual)")
    comprueba("OperationalError" in texto and "disco" in texto, "   y de qué tipo es, con la pista del disco")
    comprueba(len(texto) < 1000, "   en un mensaje corto (%d caracteres)" % len(texto))
    comprueba(ficheros() == antes, "avisar no escribe nada en el directorio de datos")
    for _ in range(3):
        corrida(SOLO_LECTURA)
    comprueba(len(red.mensajes()) == 1, "tres fallos más no repiten el aviso: uno, no uno por hora")
    comprueba(app._FALLOS["seguidos"] == 5, "   y la cuenta sigue (5)")


def caso_vuelve_a_funcionar() -> None:
    print("\n[2] Cuando vuelve a salir bien se dice, una vez, y se empieza de cero")
    corrida()
    m = red.mensajes()
    comprueba(len(m) == 2 and "vuelve a funcionar" in m[-1]["text"] and "5 corridas" in m[-1]["text"],
              "la primera que sale bien lo dice, con cuántas fallaron")
    comprueba(str(m[-1]["chat_id"]) == str(TITULAR), "   solo al titular")
    comprueba(app._FALLOS == {"seguidos": 0, "avisado": False}, "   y la cuenta queda a cero")
    corrida()
    comprueba(len(red.mensajes()) == 2, "la siguiente ya no dice nada")
    corrida(SOLO_LECTURA)
    corrida()
    corrida(SOLO_LECTURA)
    comprueba(len(red.mensajes()) == 2, "fallo, bien, fallo: no son dos seguidos y no avisa")
    corrida(SOLO_LECTURA)
    comprueba(len(red.mensajes()) == 3, "   el siguiente sí: es otra avería y se avisa otra vez")


def caso_sin_secretos() -> None:
    print("\n[3] El aviso no lleva el token ni un chat_id, aunque el error sí")
    de_cero()
    sucio = RuntimeError("HTTPSConnectionPool: /bot%s/getUpdates <b>roto</b>" % TOKEN)
    corrida(sucio)
    corrida(sucio)
    texto = "\n".join(m["text"] for m in red.mensajes())
    comprueba(len(red.mensajes()) == 1, "(control: ha avisado)")
    comprueba(TOKEN not in texto and "&lt;token&gt;" in texto, "el token sale tachado")
    comprueba("<b>roto</b>" not in texto and "&lt;b&gt;roto" in texto, "el error va escapado: no rompe el HTML del mensaje")
    comprueba(all(str(m["chat_id"]) == str(TITULAR) for m in red.mensajes()), "y no le llega a nadie más")
    largo = RuntimeError("x" * 5000)
    de_cero()
    corrida(largo)
    corrida(largo)
    comprueba(len(red.mensajes()) == 1 and len(red.mensajes()[0]["text"]) < 1000, "un error enorme se recorta")


def caso_base_corrupta_va_por_su_lado() -> None:
    print("\n[4] La base corrupta sigue con su aviso de siempre y no suma aquí")
    de_cero()
    rota = perfiles.BaseNoDisponible("perfiles.db está corrupta y se ha apartado")
    corrida(rota)
    corrida(rota)
    corrida(rota)
    comprueba(app._FALLOS["seguidos"] == 0, "tres corridas con BaseNoDisponible no cuentan")
    comprueba(not any("veces seguidas" in m["text"] for m in red.mensajes()), "   ni mandan este aviso")


def caso_telegram_tambien_caido() -> None:
    print("\n[5] Si Telegram tampoco responde, no se da por avisado y se reintenta")
    de_cero()
    red.caida = True
    corrida(SOLO_LECTURA)
    corrida(SOLO_LECTURA)
    comprueba(app._FALLOS == {"seguidos": 2, "avisado": False}, "sin red no se marca como avisado")
    comprueba(app.BUSQUEDA["activa"] is False, "   y la corrida acaba igual: avisar de un fallo no es otro fallo")
    red.caida = False
    corrida(SOLO_LECTURA)
    m = red.mensajes()
    comprueba(len(m) == 1 and "3 veces seguidas" in m[0]["text"], "al fallo siguiente, ya con red, sale (y dice 3)")
    red.caida = True
    corrida()
    comprueba(app._FALLOS == {"seguidos": 0, "avisado": False}, "si el «vuelve a funcionar» no sale, no se queda atascado")


def main() -> int:
    for caso in (caso_avisa_al_segundo_y_una_vez, caso_vuelve_a_funcionar, caso_sin_secretos,
                 caso_base_corrupta_va_por_su_lado, caso_telegram_tambien_caido):
        try:
            caso()
        except Exception as e:  # una excepción es un fallo, no una prueba que no corre
            import traceback

            traceback.print_exc()
            comprueba(False, "%s ha reventado: %r" % (caso.__name__, e))
    perfiles.cerrar()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  -", f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
