# -*- coding: utf-8 -*-
"""
Pruebas del lector del feed de Tecnoempleo y, sobre todo, de SUS SALVAGUARDAS, que son lo
que justifica leer ese feed (ver fuentes_extra/tecnoempleo.py y CAMBIOS-FASE4.md).

El feed de estas pruebas es SINTÉTICO, escrito a mano con la forma de un RSS 2.0, porque
Claude no abre tecnoempleo.com (su robots.txt bloquea por nombre a los agentes de IA). Los
campos que dependen de cómo publique Tecnoempleo se ajustan con la copia real que guarde
el usuario. Ninguna prueba hace peticiones: `requests.get` se sustituye por un espía.

    python pruebas/test_tecnoempleo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-tecnoempleo-")

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import dedupe_difuso  # noqa: E402
from fuentes_extra import salud, tecnoempleo as te  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def _rfc(horas_atras: float) -> str:
    return format_datetime(datetime.now(timezone.utc) - timedelta(hours=horas_atras))


def feed_sintetico(items) -> bytes:
    cuerpo = "".join(
        "<item><title>%s</title><link>%s</link><dc:creator>%s</dc:creator>"
        "<description><![CDATA[<p>%s</p><b>Barcelona</b>]]></description>"
        "<pubDate>%s</pubDate></item>" % it for it in items)
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Feed</title>'
            + cuerpo + "</channel></rss>").encode("utf-8")


FEED = feed_sintetico([
    ("Técnico/a Helpdesk N1", "https://www.tecnoempleo.com/oferta-1", "Empresa Uno",
     "Soporte a usuarios. Sin experiencia.", _rfc(2)),
    ("Analista SOC Junior", "https://www.tecnoempleo.com/oferta-2", "Empresa Dos",
     "Monitorización 24x7 con SIEM.", _rfc(10)),
    ("Programador Java Senior", "https://www.tecnoempleo.com/oferta-3", "Empresa Tres",
     "Spring Boot.", _rfc(100)),
])


class Resp:
    def __init__(self, contenido, codigo=200):
        self.content, self.status_code = contenido, codigo
        self.text = contenido.decode("utf-8", "replace") if isinstance(contenido, bytes) else contenido

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class Espia:
    def __init__(self, contenido=FEED, codigo=200):
        self.llamadas, self.contenido, self.codigo = [], contenido, codigo
        self._candado = threading.Lock()

    def get(self, url, params=None, headers=None, timeout=None):
        with self._candado:
            self.llamadas.append({"url": url, "params": params, "headers": headers})
        time.sleep(0.01)  # que las llamadas en paralelo se pisen de verdad
        return Resp(self.contenido, self.codigo)


def con_espia(espia, fn):
    original = te.requests.get
    te.requests.get = espia.get
    try:
        return fn()
    finally:
        te.requests.get = original


def reinicia():
    if salud.SALUD_PATH.exists():
        salud.SALUD_PATH.unlink()
    salud._ULTIMO_ANUNCIO_DESACTIVADA.clear()
    te._ULTIMA_DESCARGA["cuando"] = None
    te.empezar_corrida()


def main() -> int:
    print("\n[1] Lectura del RSS")
    ofertas = te.parsear_feed(FEED)
    comprueba(len(ofertas) == 3, "lee las 3 <item>")
    o = ofertas[0]
    comprueba(o["title"] == "Técnico/a Helpdesk N1" and o["job_url"].endswith("oferta-1"), "título y enlace")
    comprueba(o["company"] == "Empresa Uno", "empresa desde dc:creator (con espacio de nombres)")
    comprueba("<p>" not in o["description"] and "Soporte a usuarios" in o["description"],
              "la descripción llega sin HTML")
    comprueba(o["site"] == "tecnoempleo" and o["location"] == "Barcelona, Spain", "fuente y ubicación")
    comprueba(o["date_posted"] == (datetime.now(timezone.utc) - timedelta(hours=2)).date().isoformat(),
              "fecha desde pubDate")
    comprueba(te.parsear_feed(b"<html>esto no es un feed") == [], "un XML roto devuelve [] sin romper")
    comprueba(te.parsear_feed(feed_sintetico([])) == [], "un feed sin <item> devuelve []")

    print("\n[2] Salvaguarda 1: UNA petición por corrida, aunque se llame 22 veces en paralelo")
    reinicia()
    espia = Espia()
    resultados = []

    def una():
        resultados.append(te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain", hours_old=38))

    hilos = [threading.Thread(target=una) for _ in range(22)]
    con_espia(espia, lambda: ([h.start() for h in hilos], [h.join() for h in hilos]))
    comprueba(len(espia.llamadas) == 1, "22 llamadas simultáneas -> 1 sola petición (%d)" % len(espia.llamadas))
    comprueba(sum(len(r) for r in resultados) == 2,
              "y las ofertas se entregan una vez (2 dentro de 38 h; la de hace 100 h se queda fuera)")

    print("\n[3] Salvaguarda 1: como mucho una descarga cada %d horas" % te.HORAS_ENTRE_DESCARGAS)
    te.empezar_corrida()  # corrida siguiente, una hora después
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain"))
    comprueba(len(espia.llamadas) == 1, "en la corrida siguiente NO vuelve a pedir el feed")
    te._ULTIMA_DESCARGA["cuando"] = time.monotonic() - te.HORAS_ENTRE_DESCARGAS * 3600 - 1
    te.empezar_corrida()
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain"))
    comprueba(len(espia.llamadas) == 2, "pasadas %d horas, sí" % te.HORAS_ENTRE_DESCARGAS)

    print("\n[4] Solo el feed de Barcelona, y solo en el ámbito de Barcelona")
    reinicia()
    espia = Espia()
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Spain", is_remote=True))
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "European Union", is_remote=True))
    comprueba(espia.llamadas == [], "los ámbitos remotos no lo consultan (traerían lo mismo otra vez)")
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain"))
    comprueba(espia.llamadas[0]["params"] == {"pr": ",240,"}, "pide la provincia 240 (Barcelona)")

    print("\n[5] Salvaguarda 2: User-Agent honesto, no el que se hace pasar por Chrome")
    ua = espia.llamadas[0]["headers"]["User-Agent"]
    comprueba("Chrome" not in ua and "Mozilla" not in ua, "no se hace pasar por un navegador")
    comprueba("personal" in ua and "no comercial" in ua, "dice que es de uso personal y no comercial")

    print("\n[6] Salvaguarda 3: al primer «no» del servidor, se para")
    reinicia()
    espia = Espia(b"<html>Forbidden</html>", 403)
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain"))
    comprueba(salud.esta_activa("tecnoempleo") is False, "un solo 403 la desactiva")
    te._ULTIMA_DESCARGA["cuando"] = None
    te.empezar_corrida()
    con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain"))
    comprueba(len(espia.llamadas) == 1, "y no se vuelve a consultar")
    comprueba(any(i.get("fuente") == "tecnoempleo" for i in salud.incidencias_pendientes()),
              "deja incidencia para avisar por Telegram")

    print("\n[7] Salvaguarda 4: canario si el feed responde vacío")
    reinicia()
    espia = Espia(feed_sintetico([]))
    for _ in range(2):
        te._ULTIMA_DESCARGA["cuando"] = None
        te.empezar_corrida()
        con_espia(espia, lambda: te.buscar_tecnoempleo("helpdesk", "Barcelona, Spain"))
        te.cerrar_corrida()
    comprueba(any(i.get("tipo") == "vacia" and i.get("fuente") == "tecnoempleo"
                  for i in salud.incidencias_pendientes()),
              "dos descargas seguidas sin ofertas -> aviso «responde pero sin ofertas»")
    reinicia()
    te.empezar_corrida()
    te.cerrar_corrida()
    comprueba(not salud.SALUD_PATH.exists(),
              "una corrida en la que no tocaba descargar no cuenta para el canario")

    print("\n[8] Salvaguarda 5: apagada por defecto")
    for clave, amb in config_mod.CONFIG_POR_DEFECTO["ambitos"].items():
        comprueba("tecnoempleo" not in amb.get("sitios", []), "no está en los sitios de «%s»" % clave)

    print("\n[9] Con buscar() real: encendida en el ámbito de Barcelona")
    reinicia()
    cfg = dict(config_mod.CONFIG_POR_DEFECTO)
    cfg["categorias"] = {"c": {"activa": True, "etiqueta": "C", "terminos": ["helpdesk", "service desk", "soc"]}}
    cfg["ambitos"] = {
        "barcelona_local": {"activo": True, "etiqueta": "BCN", "sitios": ["tecnoempleo"],
                            "location": "Barcelona, Spain", "usar_distancia": False},
        "remoto_es": {"activo": True, "etiqueta": "R", "sitios": ["tecnoempleo"],
                      "location": "Spain", "is_remote": True},
    }
    cfg["descripciones_dos_fases"] = False
    espia = Espia()
    df = con_espia(espia, lambda: buscador.buscar(config=cfg))
    comprueba(len(espia.llamadas) == 1, "6 búsquedas (3 términos x 2 ámbitos) -> 1 sola petición")
    comprueba(not df.empty and set(df["site"]) == {"tecnoempleo"}, "y sus ofertas llegan al resultado")
    comprueba("Programador Java Senior" not in set(df["title"]),
              "pasan por los mismos filtros que el resto (el senior de hace 100 h no está)")

    print("\n[10] En un duplicado entre portales se queda la copia de InfoJobs")
    comprueba(dedupe_difuso.ORDEN_FUENTES["infojobs"] < dedupe_difuso.ORDEN_FUENTES["tecnoempleo"]
              < dedupe_difuso.ORDEN_FUENTES["eures"], "orden: InfoJobs < Tecnoempleo < EURES")

    print("\n[11] El formato REAL de Tecnoempleo (extracto de la copia del 26-10)")
    import buscador as b
    real = te.parsear_feed((Path(__file__).parent / "fixtures" / "tecnoempleo_extracto.xml").read_bytes())
    comprueba(len(real) == 5, "lee las 5 ofertas del extracto")
    comprueba(all(o["company"] for o in real), "la empresa sale del campo «Empresa:» de la descripción")
    por_mod = {b.modalidad(o) for o in real}
    comprueba({"Remoto", "Híbrido", "Presencial"} <= por_mod,
              "«Provincia» se traduce a modalidad: remoto, híbrido y presencial")
    madrid = [o for o in real if o["location"].startswith("Madrid")]
    comprueba(len(madrid) == 1 and madrid[0]["is_remote"] is False,
              "la de «Provincia: Madrid» queda en Madrid y presencial (y el filtro geográfico la quita)")
    import pandas as pd
    df_m = pd.DataFrame([{**madrid[0], "job_url": "caso://madrid"}])
    comprueba(b.filtrar_presenciales_lejos(df_m, dict(config_mod.CONFIG_POR_DEFECTO)).empty,
              "   comprobado: filtrar_presenciales_lejos la descarta")
    remotas = [o for o in real if o["is_remote"] is True]
    comprueba(remotas and all(o["location"] == "Barcelona, Spain" for o in remotas),
              "las remotas se quedan con ubicación Barcelona y is_remote=True")
    con_sal = [o for o in real if o["min_amount"]]
    comprueba(con_sal and all(o["max_amount"] >= o["min_amount"] and o["currency"] == "EUR" for o in con_sal),
              "salario «30000 € - 36000 € Bruto/año» -> mínimo, máximo y EUR")
    intervalos = {(o["min_amount"], o["interval"]) for o in con_sal}
    comprueba((30000.0, "yearly") in intervalos and (900.0, "monthly") in intervalos,
              "«Bruto/año» queda anual y «Bruto/mes» mensual (no se mezclan)")
    comprueba(all(b.extraer_anos_experiencia(o["description"]) is not None for o in real),
              "los años de experiencia se extraen en todas (es un campo explícito)")
    comprueba(not any("Descubre m" in o["description"] for o in real),
              "se corta el «Descubre más: https://…» pegado a la formación")
    comprueba(all(o["description"].startswith("Modalidad: ") for o in real),
              "la descripción empieza por «Modalidad: …», que es lo que lee buscador.modalidad()")
    item_no_req = feed_sintetico([]).replace(b"</channel>", (
        "<item><title>Técnico/a Helpdesk</title><link>https://x.test/nr</link><description><![CDATA["
        "<b>Empresa:</b>&nbsp;Una<br /><b>Provincia:</b>&nbsp;100% En remoto<br />"
        "<b>Experiencia:</b>&nbsp;No requerida<br /><b>Descripción:</b>&nbsp;Soporte.]]></description>"
        "<pubDate>" + _rfc(1) + "</pubDate></item></channel>").encode("utf-8"))
    nr = te.parsear_feed(item_no_req)[0]
    comprueba(b.extraer_anos_experiencia(nr["description"]) == 0,
              "«Experiencia: No requerida» -> 0 años (y suma como «sin experiencia»)")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
