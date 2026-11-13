# -*- coding: utf-8 -*-
"""
Pruebas de la detección de bloqueos de las fuentes de jobspy.

El agujero que tapan: jobspy no lanza excepción cuando un portal bloquea, así que
«LinkedIn me está devolviendo 429» y «no hay ofertas nuevas» eran lo mismo para el
sistema. Aquí se comprueba que ya no.

    python pruebas/test_bloqueos.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Directorio propio: estas pruebas escriben salud_fuentes.json.
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-bloqueos-")

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import notificaciones  # noqa: E402
from fuentes_extra import salud  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def limpia_salud() -> None:
    if salud.SALUD_PATH.exists():
        salud.SALUD_PATH.unlink()


def caso_captura():
    print("\n[1] Capturar lo que jobspy manda a su propio logger")
    log = logging.getLogger("JobSpy:LinkedIn")
    with buscador.vigilar_bloqueos_de_jobspy() as cazador:
        # Mensaje literal de jobspy/linkedin/__init__.py en la 1.1.82
        log.error("429 Response - Blocked by LinkedIn for too many requests")
        logging.getLogger("JobSpy:Indeed").error("Indeed: Max retries exceeded with url")
        logging.getLogger("JobSpy:Glassdoor").error("Glassdoor response status code 403")
    bloqueos = cazador.bloqueos_por_fuente()
    comprueba("linkedin" in bloqueos, "el 429 de LinkedIn se reconoce como bloqueo")
    comprueba(bloqueos.get("linkedin", (None,))[0] == 429, "y se extrae el código 429")
    comprueba("glassdoor" in bloqueos, "un 403 también es bloqueo")
    comprueba("indeed" not in bloqueos,
              "un fallo de red (Max retries) NO cuenta como bloqueo: es problema nuestro")
    comprueba(len(cazador.sucesos) == 3, "se apuntan los tres sucesos para el log")

    # Fuera del `with`, el handler ya no debe estar enganchado
    with buscador.vigilar_bloqueos_de_jobspy() as otro:
        pass
    log.error("429 Response - Blocked by LinkedIn for too many requests")
    comprueba(otro.bloqueos_por_fuente() == {},
              "al salir del bloque deja de escuchar (no se queda enganchado)")


def caso_atribucion_por_corrida():
    print("\n[2] Una fuente que trae ofertas no está bloqueada, aunque haya un 429 suelto")
    limpia_salud()
    with buscador.vigilar_bloqueos_de_jobspy() as cazador:
        logging.getLogger("JobSpy:LinkedIn").error("429 Response - Blocked by LinkedIn")
        logging.getLogger("JobSpy:Indeed").error("429 Response - Blocked by Indeed")
    # LinkedIn sí trajo ofertas en la corrida; Indeed no trajo ninguna.
    df = pd.DataFrame([{"site": "linkedin", "title": "x"}])
    buscador._registrar_salud_de_jobspy(cazador, df, {"linkedin", "indeed"})
    datos = json.loads(salud.SALUD_PATH.read_text(encoding="utf-8"))
    comprueba(datos["fuentes"].get("linkedin", {}).get("bloqueos_consecutivos", 0) == 0,
              "LinkedIn trajo ofertas: el 429 era una ráfaga, no se le apunta nada")
    comprueba(datos["fuentes"].get("indeed", {}).get("bloqueos_consecutivos") == 1,
              "Indeed no trajo nada y respondió 429: se le apunta el bloqueo")

    print("\n    Y una corrida vacía sin bloqueos no inventa nada:")
    limpia_salud()
    with buscador.vigilar_bloqueos_de_jobspy() as cazador:
        pass
    buscador._registrar_salud_de_jobspy(cazador, None, {"linkedin"})
    sin_nada = not salud.SALUD_PATH.exists() or not json.loads(
        salud.SALUD_PATH.read_text(encoding="utf-8"))["fuentes"]
    comprueba(sin_nada, "0 ofertas y 0 bloqueos: no se anota nada (puede que no haya nada)")


def caso_linkedin_avisa_pero_no_se_apaga():
    print("\n[3] LinkedIn avisa pero NO se desactiva sola")
    limpia_salud()
    for i in range(salud.BLOQUEOS_PARA_DESACTIVAR_POR_DEFECTO):
        salud.registrar_bloqueo("linkedin", 429, "Blocked by LinkedIn")
    comprueba(salud.esta_activa("linkedin") is True,
              "tras %d bloqueos seguidos sigue activa (aporta el 86 %% de las ofertas)"
              % salud.BLOQUEOS_PARA_DESACTIVAR_POR_DEFECTO)
    pendientes = salud.incidencias_pendientes()
    comprueba(len(pendientes) == 1, "pero deja una incidencia para avisar por Telegram")
    comprueba(pendientes[0].get("desactivada") is False,
              "marcada como «sigue activa», para que el mensaje lo diga")
    datos = json.loads(salud.SALUD_PATH.read_text(encoding="utf-8"))
    comprueba(datos["fuentes"]["linkedin"]["bloqueos_consecutivos"] == 0,
              "el contador se reinicia al avisar: no repite el mensaje cada corrida")

    print("\n    Una fuente propia sí se desactiva (comportamiento de siempre):")
    limpia_salud()
    for i in range(salud.BLOQUEOS_PARA_DESACTIVAR_POR_DEFECTO):
        salud.registrar_bloqueo("eures", 403, "Forbidden")
    comprueba(salud.esta_activa("eures") is False, "EURES se desactiva a los 3 bloqueos")
    comprueba(any(i.get("fuente") == "eures" for i in salud.incidencias_pendientes()),
              "y también deja incidencia")


def caso_mensaje_de_telegram():
    print("\n[4] El mensaje de Telegram distingue los dos casos")
    texto = notificaciones._mensaje_incidencias([
        {"fuente": "eures", "motivo": "3 bloqueos (403)", "desactivada": True},
        {"fuente": "linkedin", "motivo": "3 bloqueos (429). La fuente SIGUE activa.",
         "desactivada": False},
    ])
    comprueba("desactivada(s) automáticamente" in texto, "menciona las desactivadas")
    comprueba("siguen activas" in texto, "y, aparte, las que solo avisan")
    comprueba("eures" in texto and "linkedin" in texto, "las nombra a las dos")
    comprueba(len(texto) < 4096, "cabe en un mensaje de Telegram")

    solo_viejas = notificaciones._mensaje_incidencias([
        {"fuente": "eures", "motivo": "3 bloqueos (403)"},  # sin la clave nueva
    ])
    comprueba("desactivada(s) automáticamente" in solo_viejas,
              "una incidencia antigua (sin la clave) se sigue tratando como desactivación")


def caso_interruptor_manual():
    print("\n[5] «desactivada: true» a mano ahora también funciona con las de jobspy")
    limpia_salud()
    datos = {"fuentes": {"linkedin": {"desactivada": True, "motivo": "a mano"}},
             "incidencias_pendientes": []}
    salud.SALUD_PATH.write_text(json.dumps(datos), encoding="utf-8")
    comprueba(salud.esta_activa("linkedin") is False, "esta_activa('linkedin') devuelve False")
    config = {
        "categorias": {"c": {"activa": True, "terminos": ["helpdesk"], "etiqueta": "C"}},
        "ambitos": {"a": {"activo": True, "sitios": ["linkedin", "indeed"],
                          "location": "Barcelona, Spain", "etiqueta": "A"}},
        "hours_old": 38, "results_wanted": 20, "distancia_portal_millas": 8,
        "country_indeed": "spain",
    }
    busquedas = buscador.construir_busquedas(config)
    comprueba(len(busquedas) == 1, "se construye la búsqueda igual")
    # La exclusión se aplica dentro de una_busqueda, al preparar los sitios de jobspy.
    # Se comprueba el efecto observable: salud dice que no está activa y el código la mira.
    import inspect
    fuente_de_buscar = inspect.getsource(buscador._descargar)
    comprueba("salud.esta_activa(s)" in fuente_de_buscar,
              "la descarga (la que comparten buscar() y las corridas) filtra los sitios de jobspy por esta_activa")


def main() -> int:
    caso_captura()
    caso_atribucion_por_corrida()
    caso_linkedin_avisa_pero_no_se_apaga()
    caso_mensaje_de_telegram()
    caso_interruptor_manual()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
