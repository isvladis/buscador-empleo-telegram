# -*- coding: utf-8 -*-
"""
Pruebas del lector de la API de Jooble y, sobre todo, de lo que protege su cupo de 500
peticiones y su clave (ver fuentes_extra/jooble.py).

El formato sale de una respuesta REAL de la API (pruebas/fixtures/jooble_extracto.json,
5 ofertas con el resumen recortado). Ninguna prueba hace peticiones: `requests.post` se
sustituye por un espía, y la clave es inventada.

    python pruebas/test_jooble.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-jooble-")
os.environ.pop("JOBSPY_JOOBLE_LIMITE", None)

import requests  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import dedupe_difuso  # noqa: E402
import notificaciones  # noqa: E402
from fuentes_extra import salud, jooble as jo  # noqa: E402

CLAVE = "11111111-2222-3333-4444-555555555555"
EXTRACTO = json.loads((Path(__file__).parent / "fixtures" / "jooble_extracto.json").read_text(encoding="utf-8"))

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, datos, codigo=200, url=""):
        self.status_code, self.url = codigo, url
        self.text = datos if isinstance(datos, str) else json.dumps(datos)
        self._datos = datos

    def json(self):
        if isinstance(self._datos, str):
            raise ValueError("no es JSON")
        return self._datos

    def raise_for_status(self):
        # Como requests: el mensaje lleva la URL de la respuesta.
        if self.status_code >= 400:
            raise requests.HTTPError("%d Client Error for url: %s" % (self.status_code, self.url))


class Espia:
    def __init__(self, datos=EXTRACTO, codigo=200, error=None):
        self.llamadas, self.datos, self.codigo, self.error = [], datos, codigo, error
        self._candado = threading.Lock()

    def post(self, url, json=None, timeout=None, headers=None):
        with self._candado:
            self.llamadas.append({"url": url, "json": json})
        if self.error:
            raise self.error
        time.sleep(0.01)
        return Resp(self.datos, self.codigo, url)


def con_espia(espia, fn):
    original, pausa = jo.requests.post, jo.time.sleep
    jo.requests.post = espia.post
    jo.time.sleep = lambda s: None  # las pausas entre términos, fuera de las pruebas
    try:
        return fn()
    finally:
        jo.requests.post, jo.time.sleep = original, pausa


def reinicia(clave=CLAVE):
    for ruta in (salud.SALUD_PATH, jo.USO_PATH):
        if ruta.exists():
            ruta.unlink()
    salud._ULTIMO_ANUNCIO_DESACTIVADA.clear()
    os.environ.pop("JOBSPY_JOOBLE_LIMITE", None)
    if clave:
        os.environ["JOBSPY_JOOBLE_KEY"] = clave
    else:
        os.environ.pop("JOBSPY_JOOBLE_KEY", None)
    jo.empezar_corrida()


def bcn(**kw):
    return jo.buscar_jooble("helpdesk", "Barcelona, Spain", hours_old=kw.get("hours_old", 38))


def main() -> int:
    print("\n[1] El formato real de la respuesta")
    o = jo.parsear_respuesta(EXTRACTO)
    comprueba(len(o) == 5, "lee las 5 ofertas del extracto")
    comprueba(all(x["site"] == "jooble" for x in o), "fuente «jooble»")
    comprueba(all(x["location"] == "Barcelona, Spain" for x in o), "«Barcelona» -> «Barcelona, Spain»")
    sin_empresa = [x for x in o if x["title"] == "Técnico/a Helpdesk Nivel 2"][0]
    comprueba(sin_empresa["company"] is None, "una oferta SIN el campo «company» no rompe nada")
    comprueba(all("<b>" not in x["description"] and "&nbsp;" not in x["description"] for x in o),
              "el resumen llega sin HTML ni entidades")
    comprueba(all("Resumen de Jooble" in x["description"] for x in o),
              "la descripción avisa de que es un resumen, no el anuncio entero")
    comprueba(o[0]["date_posted"] == "2026-10-21", "fecha desde «updated»")
    otra = jo.parsear_respuesta({"jobs": [{"title": "IT Support", "link": "https://es.jooble.org/jdp/1",
                                           "location": "Sant Joan Despí, Barcelona"}]})[0]
    comprueba(otra["location"] == "Sant Joan Despí, Barcelona, Spain", "ubicación con municipio")
    comprueba(jo.parsear_respuesta({"jobs": [{"title": "", "link": "x"}, {"title": "y"}]}) == [],
              "sin título o sin enlace, se salta")
    comprueba(jo.parsear_respuesta({}) == [] and jo.parsear_respuesta(None) == [], "respuesta vacía -> []")

    print("\n[2] Salario como texto")
    comprueba(jo._salario("de 30000 a 35000 €/año") == (30000.0, 35000.0, "yearly"), "«de 30000 a 35000 €/año»")
    comprueba(jo._salario("18000 €/año") == (18000.0, 18000.0, "yearly"), "una sola cifra")
    comprueba(jo._salario("1300 €/mensuales") == (1300.0, 1300.0, "monthly"), "«€/mensuales» -> mensual")
    comprueba(jo._salario("12 €/hora") == (12.0, 12.0, "hourly"), "«€/hora» -> por hora")
    comprueba(jo._salario("de 30.000 a 35.000 €/año")[:2] == (30000.0, 35000.0), "con puntos de miles")
    comprueba(jo._salario("") == (None, None, None), "vacío -> nada")
    reales = {x["title"][:20]: (x["min_amount"], x["interval"], x["currency"]) for x in o}
    comprueba(reales["Técnico/a Helpdesk ("] == (30000.0, "yearly", "EUR"), "en la oferta real, con EUR")

    print("\n[3] Sin clave, no hace nada")
    reinicia(clave=None)
    espia = Espia()
    con_espia(espia, bcn)
    comprueba(espia.llamadas == [], "sin JOBSPY_JOOBLE_KEY no hay ni una petición")

    print("\n[4] Solo en el ámbito de Barcelona")
    reinicia()
    espia = Espia()
    con_espia(espia, lambda: jo.buscar_jooble("helpdesk", "Spain", is_remote=True))
    con_espia(espia, lambda: jo.buscar_jooble("helpdesk", "European Union", is_remote=True))
    comprueba(espia.llamadas == [], "los ámbitos remotos no gastan peticiones")

    print("\n[5] Una tanda por corrida: sus %d términos, aunque se llame 22 veces a la vez" % len(jo.TERMINOS))
    reinicia()
    espia = Espia()
    resultados = []
    hilos = [threading.Thread(target=lambda: resultados.append(bcn())) for _ in range(22)]
    con_espia(espia, lambda: ([h.start() for h in hilos], [h.join() for h in hilos]))
    comprueba(len(espia.llamadas) == len(jo.TERMINOS),
              "22 llamadas -> %d peticiones, una por término (%d)" % (len(jo.TERMINOS), len(espia.llamadas)))
    comprueba(sum(len(r) for r in resultados) == 5, "las ofertas se entregan una vez (repetidas entre términos, fuera)")
    cuerpo = espia.llamadas[0]["json"]
    comprueba(espia.llamadas[0]["url"] == "https://es.jooble.org/api/" + CLAVE, "a es.jooble.org (la clave es de España)")
    comprueba(cuerpo["location"] == "Barcelona" and cuerpo["ResultOnPage"] == 50,
              "Barcelona y 50 por página (sin ResultOnPage serían 30)")
    esperado = (datetime.now(timezone.utc) - timedelta(hours=38)).date().isoformat()
    comprueba(cuerpo["datecreatedfrom"] == esperado, "datecreatedfrom = hace hours_old (%s)" % esperado)
    comprueba([c["json"]["keywords"] for c in espia.llamadas] == jo.TERMINOS, "los términos propios, no los del ámbito")

    print("\n[6] Una tanda cada %d h, y la hora vive en DISCO" % jo.HORAS_ENTRE_DESCARGAS)
    jo.empezar_corrida()
    con_espia(espia, bcn)
    comprueba(len(espia.llamadas) == len(jo.TERMINOS), "la corrida siguiente no gasta nada")
    jo._CORRIDA.update(atendida=False)  # lo que deja un reinicio del contenedor
    con_espia(espia, bcn)
    comprueba(len(espia.llamadas) == len(jo.TERMINOS), "tras un reinicio tampoco: la hora está en jooble_uso.json")
    uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
    uso["ultima_descarga"] = (datetime.now(timezone.utc) - timedelta(hours=jo.HORAS_ENTRE_DESCARGAS, minutes=1)).isoformat()
    jo.USO_PATH.write_text(json.dumps(uso), encoding="utf-8")
    jo.empezar_corrida()
    con_espia(espia, bcn)
    comprueba(len(espia.llamadas) == 2 * len(jo.TERMINOS), "pasadas %d h, otra tanda" % jo.HORAS_ENTRE_DESCARGAS)
    uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
    comprueba(uso["peticiones"] == 2 * len(jo.TERMINOS), "el contador lleva las %d gastadas" % (2 * len(jo.TERMINOS)))
    comprueba(sum(uso["por_dia"].values()) == uso["peticiones"], "y el desglose por día cuadra")

    print("\n[7] El cupo: se para sola antes del límite, y avisa")
    tope = jo.LIMITE_POR_DEFECTO - jo.RESERVA
    reinicia()
    jo.USO_PATH.write_text(json.dumps({"peticiones": tope - 2}), encoding="utf-8")
    espia = Espia()
    con_espia(espia, bcn)
    comprueba(len(espia.llamadas) == 2, "con 2 de margen hace 2 y para (%d)" % len(espia.llamadas))
    comprueba(json.loads(jo.USO_PATH.read_text(encoding="utf-8"))["peticiones"] == tope,
              "el contador se queda en el tope (%d), no lo pasa" % tope)
    avisos = [i for i in salud.incidencias_pendientes() if i.get("tipo") == "presupuesto"]
    comprueba(len(avisos) == 2, "dos avisos: el del 80 %% y el de «se para» (%d)" % len(avisos))
    comprueba(any("SE PARA" in i["motivo"] for i in avisos), "el de parada lo dice claro")
    uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
    uso["ultima_descarga"] = "2000-01-01T00:00:00+00:00"
    jo.USO_PATH.write_text(json.dumps(uso), encoding="utf-8")
    jo.empezar_corrida()
    con_espia(espia, bcn)
    comprueba(len(espia.llamadas) == 2, "agotado, la corrida siguiente no pide nada")
    comprueba(len([i for i in salud.incidencias_pendientes() if i.get("tipo") == "presupuesto"]) == 2,
              "y no repite los avisos")
    os.environ["JOBSPY_JOOBLE_LIMITE"] = "1000"
    jo.empezar_corrida()
    con_espia(espia, bcn)
    comprueba(len(espia.llamadas) == 2 + len(jo.TERMINOS), "con JOBSPY_JOOBLE_LIMITE=1000 en el .env, vuelve")
    os.environ["JOBSPY_JOOBLE_LIMITE"] = "no es un número"
    comprueba(jo.limite() == jo.LIMITE_POR_DEFECTO, "un límite mal escrito vuelve al de por defecto")

    print("\n[8] Cuenta los reintentos: un fallo de red también gasta")
    reinicia()
    espia = Espia(error=requests.ConnectionError("Max retries exceeded with url: /api/" + CLAVE))
    original_espera = jo.base.time.sleep
    jo.base.time.sleep = lambda s: None
    try:
        con_espia(espia, bcn)
    finally:
        jo.base.time.sleep = original_espera
    uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
    comprueba(uso["peticiones"] == len(espia.llamadas) == jo.base.REINTENTOS,
              "3 intentos de red = 3 gastadas, y no sigue con los demás términos")

    print("\n[9] La clave NO sale en el log ni en salud_fuentes.json")
    capturado = []

    class Recoge(logging.Handler):
        def emit(self, record):
            capturado.append(record.getMessage())

    manejador = Recoge(level=logging.DEBUG)
    logging.getLogger().addHandler(manejador)
    nivel = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        reinicia()
        jo.base.time.sleep = lambda s: None
        con_espia(Espia(error=requests.ConnectionError("HTTPSConnectionPool: /api/" + CLAVE)), bcn)
        reinicia()
        con_espia(Espia({"error": "no"}, codigo=404), bcn)
        reinicia()
        con_espia(Espia("<html>Access is available only for registered users</html>", codigo=403), bcn)
    finally:
        jo.base.time.sleep = original_espera
        logging.getLogger().removeHandler(manejador)
        logging.getLogger().setLevel(nivel)
    todo_el_log = "\n".join(capturado)
    comprueba(any("<clave>" in m for m in capturado), "   control: los mensajes de error sí se escribieron (tachados)")
    comprueba(CLAVE not in todo_el_log, "ni el error de red ni el 404 llevan la clave al log")
    comprueba(CLAVE not in salud.SALUD_PATH.read_text(encoding="utf-8"), "ni a salud_fuentes.json")

    print("\n[10] Un 403 (clave que no vale) la desactiva al primero")
    comprueba(salud.esta_activa("jooble") is False, "desactivada tras un 403")
    espia = Espia()
    jo.USO_PATH.unlink()
    jo.empezar_corrida()
    con_espia(espia, bcn)
    comprueba(espia.llamadas == [], "y no gasta más peticiones")

    print("\n[11] Canario: responde bien pero sin ofertas")
    reinicia()
    vacia = Espia({"totalCount": 0, "jobs": []})
    for _ in range(2):
        if jo.USO_PATH.exists():
            jo.USO_PATH.unlink()
        jo.empezar_corrida()
        con_espia(vacia, bcn)
        jo.cerrar_corrida()
    comprueba(any(i.get("tipo") == "vacia" and i.get("fuente") == "jooble" for i in salud.incidencias_pendientes()),
              "dos tandas seguidas sin nada -> aviso")
    reinicia()
    jo.empezar_corrida()
    resumen = jo.cerrar_corrida()
    comprueba(not salud.SALUD_PATH.exists() and "peticiones_gastadas" in resumen,
              "una corrida en la que no tocaba no cuenta, y el resumen dice lo gastado")

    print("\n[12] Apagada por defecto y en el lugar que le toca")
    for clave, amb in config_mod.CONFIG_POR_DEFECTO["ambitos"].items():
        comprueba("jooble" not in amb.get("sitios", []), "no está en los sitios de «%s»" % clave)
    orden = dedupe_difuso.ORDEN_FUENTES
    comprueba(orden["eures"] < orden["jooble"] < orden["google"],
              "en un duplicado pierde frente a las demás (solo trae un resumen)")

    print("\n[13] Con buscar() real")
    reinicia()
    cfg = dict(config_mod.CONFIG_POR_DEFECTO)
    cfg["categorias"] = {"c": {"activa": True, "etiqueta": "C", "terminos": ["helpdesk", "service desk", "soc"]}}
    cfg["ambitos"] = {
        "barcelona_local": {"activo": True, "etiqueta": "BCN", "sitios": ["jooble"],
                            "location": "Barcelona, Spain", "usar_distancia": False},
        "remoto_es": {"activo": True, "etiqueta": "R", "sitios": ["jooble"],
                      "location": "Spain", "is_remote": True},
    }
    cfg["descripciones_dos_fases"] = False
    espia = Espia()
    df = con_espia(espia, lambda: buscador.buscar(config=cfg))
    comprueba(len(espia.llamadas) == len(jo.TERMINOS),
              "6 búsquedas (3 términos x 2 ámbitos) -> %d peticiones" % len(jo.TERMINOS))
    comprueba(not df.empty and set(df["site"]) == {"jooble"}, "y sus ofertas llegan al resultado")
    comprueba("German-Speaking Customer Service Advisor" not in set(df["title"]) and len(df) == 4,
              "pasan por los mismos filtros que el resto: la de atención al cliente cae por fuera de perfil")

    print("\n[14] El aviso de cupo por Telegram")
    msj = notificaciones._mensaje_incidencias([{"fuente": "jooble", "tipo": "presupuesto",
                                                "motivo": "Lleva 400 de 500 <peticiones>"}])
    comprueba("Cupo de peticiones" in msj and "&lt;peticiones&gt;" in msj, "bloque propio, con el texto escapado")
    comprueba("desactivada" not in msj.lower(), "no se presenta como un bloqueo")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  -", f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
