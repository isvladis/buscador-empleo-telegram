# -*- coding: utf-8 -*-
"""
Pruebas del canario de InfoJobs, del anuncio de fuentes desactivadas una vez por corrida
y del escape del aviso de incidencias.

El canario existe porque fuentes_extra/infojobs.py hacía `if not cards: break` sin dejar
rastro: si InfoJobs cambia su marcado, la fuente se quedaría sin ofertas para siempre y el
sistema lo vería igual que «no hay nada nuevo».

No se hace ninguna petición real: `requests.get` del módulo se sustituye por respuestas
fabricadas.

    python pruebas/test_canario.py
"""

from __future__ import annotations

import json
import re
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-canario-")

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
from fuentes_extra import infojobs, salud  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def limpia() -> None:
    if salud.SALUD_PATH.exists():
        salud.SALUD_PATH.unlink()
    salud._ULTIMO_ANUNCIO_DESACTIVADA.clear()


def estado_ij() -> dict:
    if not salud.SALUD_PATH.exists():
        return {}
    return json.loads(salud.SALUD_PATH.read_text(encoding="utf-8"))["fuentes"].get("infojobs", {})


class _Resp:
    def __init__(self, texto, codigo=200):
        self.text, self.status_code = texto, codigo

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


PAGINA_SIN_TARJETAS = "<html><body><div class='otra-clase-nueva'>Ofertas</div></body></html>"
PAGINA_CON_TARJETA = ("<html><body><div class='ij-OfferCardContent'><a>Técnico Helpdesk</a>"
                      "</div></body></html>")


def con_pagina(html, fn):
    original = infojobs._get
    infojobs._get = lambda *a, **k: _Resp(html)
    try:
        return fn()
    finally:
        infojobs._get = original


def caso_salud():
    print("\n[1] La racha de corridas vacías en salud.py")
    limpia()
    comprueba(salud.registrar_corrida_vacia("infojobs") is False, "la 1.ª corrida vacía no avisa")
    comprueba(salud.registrar_corrida_vacia("infojobs") is True, "la 2.ª sí (CORRIDAS_VACIAS_PARA_AVISAR = 2)")
    comprueba(salud.registrar_corrida_vacia("infojobs") is False, "la 3.ª ya no repite el aviso")
    pend = salud.incidencias_pendientes()
    comprueba(len(pend) == 1 and pend[0]["tipo"] == "vacia", "una sola incidencia, de tipo «vacia»")
    comprueba(salud.esta_activa("infojobs") is True, "y la fuente NO se desactiva")
    salud.registrar_corrida_con_resultados("infojobs")
    pend = salud.incidencias_pendientes()
    comprueba(len(pend) == 2 and pend[1]["tipo"] == "recuperada", "al volver a dar ofertas, se avisa de que se recupera")
    comprueba(estado_ij().get("corridas_vacias_consecutivas") == 0
              and not estado_ij().get("aviso_vacio_enviado"), "y la racha se cierra")

    print("\n    Una corrida con resultados sin racha previa no escribe nada:")
    limpia()
    salud.registrar_corrida_con_resultados("infojobs")
    comprueba(not salud.SALUD_PATH.exists(), "no se crea salud_fuentes.json por gusto")


def caso_contador():
    print("\n[2] El recuento por corrida del módulo de InfoJobs")
    limpia()
    infojobs.empezar_corrida()
    r = infojobs.cerrar_corrida()
    comprueba(r == {"respuestas": 0, "tarjetas": 0} and not salud.SALUD_PATH.exists(),
              "sin ninguna respuesta buena no se anota nada (bloqueo o sin consultar)")

    infojobs.empezar_corrida()
    hilos = [threading.Thread(target=infojobs._anotar_respuesta, args=(1,)) for _ in range(200)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    comprueba(infojobs.cerrar_corrida() == {"respuestas": 200, "tarjetas": 200},
              "200 anotaciones desde 200 hilos a la vez: no se pierde ninguna (lleva candado)")


def caso_extremo_a_extremo():
    print("\n[3] De punta a punta, con buscar() real y respuestas fabricadas")
    limpia()
    cfg = dict(config_mod.CONFIG_POR_DEFECTO)
    cfg["categorias"] = {"c": {"activa": True, "etiqueta": "C", "terminos": ["helpdesk", "service desk"]}}
    cfg["ambitos"] = {"a": {"activo": True, "etiqueta": "A", "sitios": ["infojobs"],
                            "location": "Barcelona, Spain"}}
    cfg["descripciones_dos_fases"] = False

    for n in (1, 2):
        con_pagina(PAGINA_SIN_TARJETAS, lambda: buscador.buscar(config=cfg))
    pend = salud.incidencias_pendientes()
    comprueba(any(i.get("tipo") == "vacia" and i.get("fuente") == "infojobs" for i in pend),
              "dos corridas con el marcado cambiado -> aviso de InfoJobs «responde pero sin ofertas»")

    con_pagina(PAGINA_CON_TARJETA, lambda: buscador.buscar(config=cfg))
    pend = salud.incidencias_pendientes()
    comprueba(any(i.get("tipo") == "recuperada" for i in pend),
              "en cuanto vuelve a haber tarjetas -> aviso de que se recupera")

    print("\n    Y un bloqueo NO cuenta como corrida vacía (ya lo registra base.peticion):")
    limpia()
    original = infojobs._get
    infojobs._get = lambda *a, **k: _Resp("<html>bloqueado</html>", 403)
    try:
        for _ in range(2):
            buscador.buscar(config=cfg)
    finally:
        infojobs._get = original
    comprueba(not estado_ij().get("corridas_vacias_consecutivas"),
              "con 403 no hay racha de corridas vacías")
    comprueba(estado_ij().get("bloqueos_consecutivos", 0) >= 1, "pero sí se anota el bloqueo")


def caso_anuncio_unico():
    print("\n[4] «fuente desactivada» se anuncia una vez por corrida, no una por búsqueda")
    limpia()
    comprueba(salud.anunciar_desactivada("eures") is True, "la primera vez se anuncia")
    comprueba(not any(salud.anunciar_desactivada("eures") for _ in range(30)),
              "las 30 siguientes de la misma corrida, no")
    comprueba(salud.anunciar_desactivada("infojobs") is True, "otra fuente tiene su propio turno")


def caso_mensaje():
    print("\n[5] El aviso de incidencias escapa el HTML y separa los tipos")
    html_crudo = ('3 bloqueo(s) (último: HTTP 403; <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">'
                  '<HTML><HEAD><TITLE>ERROR & co</TITLE>')
    texto = notificaciones._mensaje_incidencias([
        {"fuente": "eures", "motivo": html_crudo, "desactivada": True},
        {"fuente": "linkedin", "motivo": "3 bloqueos (429).", "desactivada": False},
        {"fuente": "infojobs", "tipo": "vacia", "desactivada": False, "motivo": "2 corridas sin ofertas"},
        {"fuente": "infojobs", "tipo": "recuperada", "desactivada": False, "motivo": "vuelve a traer ofertas"},
        {"fuente": "eures", "motivo": "incidencia antigua, sin claves nuevas"},
    ])
    comprueba("<!DOCTYPE" not in texto and "<HTML>" not in texto and "<TITLE>" not in texto,
              "ninguna etiqueta del HTML de la página bloqueada pasa sin escapar")
    comprueba("&lt;!DOCTYPE" in texto and "&amp; co" in texto, "va escapado (&lt; y &amp;)")
    comprueba("<b>eures</b>" in texto, "las etiquetas propias del formato sí van")
    comprueba("desactivada(s) automáticamente" in texto, "sección de desactivadas")
    comprueba("bloqueando pero siguen activas" in texto, "sección de bloqueos que solo avisan")
    comprueba("no traen NINGUNA oferta" in texto, "sección nueva: responde pero sin ofertas")
    comprueba("vuelven a funcionar" in texto, "sección nueva: recuperadas")
    comprueba(texto.count("un 429 suele levantarse") == 1,
              "el consejo del 429 sale solo en su sección, no en la del canario")

    print("\n    Y Telegram lo aceptaría (se manda por el espía, sin red):")
    enviados = []
    original = notificaciones.requests.post

    class _Ok:
        ok, status_code, text = True, 200, "{}"

    notificaciones.requests.post = lambda url, json=None, timeout=None: (enviados.append(json) or _Ok())
    try:
        notificaciones.notificar_incidencias_fuentes(
            {"telegram_bot_token": "1234567890:AAAA_token_de_pruebas_no_real_xxxxx",
             "telegram_chat_id": "1"},
            [{"fuente": "eures", "motivo": html_crudo, "desactivada": True}])
    finally:
        notificaciones.requests.post = original
    comprueba(enviados and enviados[0].get("parse_mode") == "HTML"
              and "<!DOCTYPE" not in enviados[0]["text"],
              "lo que sale hacia la API va en HTML y sin el HTML crudo de la página")


# La frase es la de la página real del 26-10 (lo demás de la página no hace falta).
PAGINA_ANTIBOT = ("<html><head><title>InfoJobs</title></head><body><h1>No podemos identificar tu "
                  "navegador</h1><p>Comprueba que JavaScript esté habilitado</p></body></html>")


def caso_antibot():
    print("\n[6] La página anti-bot de InfoJobs (desde el 26-10) cuenta como BLOQUEO, no como vacía")
    limpia()
    cfg = dict(config_mod.CONFIG_POR_DEFECTO)
    cfg["categorias"] = {"c": {"activa": True, "etiqueta": "C", "terminos": ["helpdesk"]}}
    cfg["ambitos"] = {"a": {"activo": True, "etiqueta": "A", "sitios": ["infojobs"],
                            "location": "Barcelona, Spain"}}
    cfg["descripciones_dos_fases"] = False
    con_pagina(PAGINA_ANTIBOT, lambda: buscador.buscar(config=cfg))
    comprueba(estado_ij().get("bloqueos_consecutivos") == 1, "una página anti-bot = un bloqueo, aunque llegue con 200")
    comprueba(not estado_ij().get("corridas_vacias_consecutivas"), "y NO una corrida vacía (que es lo que pasaba)")
    comprueba("anti-bot" in (estado_ij().get("ultimo_detalle") or ""), "el motivo dice que es la página anti-bot")
    for _ in range(2):
        con_pagina(PAGINA_ANTIBOT, lambda: buscador.buscar(config=cfg))
    comprueba(salud.esta_activa("infojobs") is False, "a la tercera seguida, InfoJobs se desactiva (no insiste)")
    comprueba(any(i.get("fuente") == "infojobs" and i.get("desactivada", True) for i in salud.incidencias_pendientes()),
              "y queda el aviso para Telegram")
    limpia()
    con_pagina(PAGINA_ANTIBOT, lambda: buscador.buscar(config=cfg))
    con_pagina(PAGINA_CON_TARJETA, lambda: buscador.buscar(config=cfg))
    comprueba(not estado_ij().get("bloqueos_consecutivos"), "una página buena entre medias reinicia la racha")
    comprueba(infojobs.es_pagina_antibot(_Resp(PAGINA_CON_TARJETA)) is None, "una página de resultados no es anti-bot")

    print("\n[7] El cliente con huella de Chrome")
    version_ua = re.search(r"Chrome/(\d+)", infojobs.HEADERS["User-Agent"]).group(1)
    comprueba(infojobs.CLIENTE_TLS == "chrome_" + version_ua,
              "el User-Agent dice la misma versión que la huella TLS (%s)" % infojobs.CLIENTE_TLS)
    from tls_client.exceptions import TLSClientExeption

    class SesionQueFalla:
        def get(self, *a, **k):
            raise TLSClientExeption("failed to do request: timeout")

    original = infojobs._sesion
    infojobs._sesion = lambda: SesionQueFalla()
    try:
        error = None
        try:
            infojobs._get({"keyword": "x"})
        except Exception as e:  # noqa: BLE001
            error = e
    finally:
        infojobs._sesion = original
    import requests
    comprueba(isinstance(error, requests.ConnectionError),
              "un fallo de tls_client llega como fallo de red (base.peticion lo reintenta, no es un bloqueo)")
    print("\n[8] El ritmo: una sola sesión y de una en una (lo que delató la corrida del 27-10)")
    otra = []
    hilo = threading.Thread(target=lambda: otra.append(infojobs._sesion()))
    hilo.start(); hilo.join()
    comprueba(otra[0] is infojobs._sesion(),
              "todas las búsquedas comparten la MISMA sesión (sus cookies se conservan, como en un navegador)")

    import time as _time
    tramos, candado_tramos = [], threading.Lock()

    class SesionLenta:
        def get(self, *a, **k):
            inicio = _time.monotonic()
            _time.sleep(0.02)
            with candado_tramos:
                tramos.append((inicio, _time.monotonic()))

            class R:
                status_code, text, url = 200, PAGINA_CON_TARJETA, infojobs.BASE_URL
            return R()

    original_sesion, original_pausa = infojobs._sesion, infojobs.PAUSA_ENTRE_PETICIONES
    infojobs._sesion = lambda: SesionLenta()
    infojobs.PAUSA_ENTRE_PETICIONES = (0.05, 0.08)  # la real es 4-9 s; aquí, a escala
    infojobs._ESTADO_SESION["ultima"] = None
    try:
        hilos = [threading.Thread(target=infojobs._get, args=({"keyword": str(n)},)) for n in range(6)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
    finally:
        infojobs._sesion, infojobs.PAUSA_ENTRE_PETICIONES = original_sesion, original_pausa
    tramos.sort()
    solapes = sum(1 for a, b in zip(tramos, tramos[1:]) if b[0] < a[1])
    huecos = [b[0] - a[1] for a, b in zip(tramos, tramos[1:])]
    comprueba(len(tramos) == 6 and solapes == 0,
              "6 peticiones desde 6 hilos a la vez: ninguna se solapa con otra (%d solapes)" % solapes)
    comprueba(min(huecos) >= 0.05 - 0.02,  # margen por la resolución del reloj de Windows
              "y entre una y la siguiente pasa al menos la pausa mínima (hueco mínimo %.3f s)" % min(huecos))
    comprueba(infojobs.PAUSA_ENTRE_PETICIONES[0] >= 4, "la pausa real es de 4 s o más (%s)" % (infojobs.PAUSA_ENTRE_PETICIONES,))


def main() -> int:
    caso_salud()
    caso_contador()
    caso_extremo_a_extremo()
    caso_anuncio_unico()
    caso_mensaje()
    caso_antibot()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
