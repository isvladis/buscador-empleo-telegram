# -*- coding: utf-8 -*-
"""
Pruebas de los botones de los avisos de Telegram (Fase 5, ver telegram_botones.py).

Ninguna prueba llama a Telegram: requests.post se sustituye por un espía que apunta lo
que se habría enviado y contesta como la API. El token y los chat_id son inventados.

    python pruebas/test_botones.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-botones-")

import requests  # noqa: E402

import almacen  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import telegram_botones as tb  # noqa: E402

TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
PRINCIPAL, OTRA_PERSONA = "1111111", "2222222"
CONFIG = {"telegram_bot_token": TOKEN, "telegram_chat_id": PRINCIPAL,
          "telegram_chat_ids_adicionales": OTRA_PERSONA, "telegram_max_avisos_individuales": 10}

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, datos, codigo=200):
        self._datos, self.status_code = datos, codigo
        self.ok = codigo < 400
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


class Espia:
    def __init__(self, updates=None, error=None):
        self.llamadas, self.updates, self.error = [], list(updates or []), error

    def post(self, url, json=None, timeout=None):
        metodo = url.rsplit("/", 1)[-1]
        self.llamadas.append({"metodo": metodo, "json": json, "url": url})
        if self.error:
            raise self.error
        if metodo == "getUpdates":
            lote, self.updates = self.updates[:1], self.updates[1:]
            return Resp({"ok": True, "result": lote})
        return Resp({"ok": True, "result": True})

    def de(self, metodo):
        return [c["json"] for c in self.llamadas if c["metodo"] == metodo]


def con_espia(espia, fn, dormir=lambda s: None):
    # notificaciones.time y telegram_botones.time son el mismo módulo: un solo parche.
    original, pausa = requests.post, notificaciones.time.sleep
    requests.post = espia.post
    notificaciones.time.sleep = dormir
    try:
        return fn()
    finally:
        requests.post, notificaciones.time.sleep = original, pausa


def oferta(n, relevancia=7.0):
    return {"job_url": f"https://es.linkedin.com/jobs/view/{n}", "title": f"Técnico/a Helpdesk N1 #{n}",
            "company": "Empresa %d" % n, "location": "Barcelona, Spain", "relevancia": relevancia,
            "site": "linkedin"}


def pulsacion(datos, chat=PRINCIPAL, update_id=1):
    return {"update_id": update_id, "callback_query": {
        "id": "cb%d" % update_id, "data": datos,
        "message": {"message_id": 77, "chat": {"id": int(chat)}}}}


def estado_de(url):
    e = almacen.cargar_estado()
    return {k for k in ("favoritas", "ocultas", "aplicadas") if url in e[k]}, e


def indice():
    """El índice de avisos del titular: lo que antes era telegram_avisos.json."""
    return perfiles.avisos(perfiles.titular())


def todos_los_datos(teclado):
    return [b["callback_data"] for fila in teclado["inline_keyboard"] for b in fila]


def main() -> int:
    print("\n[1] El aviso lleva botones, y sin decir a quién va solo al titular")
    espia = Espia()
    ofertas = [oferta(i) for i in range(1, 4)]
    con_espia(espia, lambda: notificaciones.notificar_ofertas_alto_encaje(
        dict(CONFIG, telegram_max_avisos_individuales=2), ofertas))
    envios = espia.de("sendMessage")
    al_principal = [e for e in envios if e["chat_id"] == PRINCIPAL]
    a_la_otra = [e for e in envios if e["chat_id"] == OTRA_PERSONA]
    comprueba(len(al_principal) == 3, "2 avisos + 1 resumen al titular")
    comprueba(not a_la_otra, "y NINGUNO a la otra persona: desde MP3 no hay copia, cada chat recibe lo suyo")
    comprueba(all("reply_markup" in e for e in al_principal[:2]), "los avisos individuales al principal llevan botones")
    comprueba("reply_markup" not in al_principal[2], "el resumen (varias ofertas) no lleva botones")
    textos = [b["text"] for b in al_principal[0]["reply_markup"]["inline_keyboard"][0]]
    comprueba(textos == ["⭐ Me interesa", "✅ Aplicada", "🙈 No me interesa"], "los tres botones: %s" % textos)
    avisos = indice()
    comprueba(len(avisos) == 2 and all(v["url"].startswith("https://es.linkedin.com") for v in avisos.values()),
              "las dos ofertas avisadas quedan en el índice con su enlace")
    espia = Espia()
    con_espia(espia, lambda: notificaciones.notificar_ofertas_alto_encaje(
        dict(CONFIG, telegram_botones=False), [oferta(9)]))
    comprueba(not any("reply_markup" in e for e in espia.de("sendMessage")), "con telegram_botones=false, sin botones")

    print("\n[2] Lo que cabe en un botón")
    clave = tb.clave_de(oferta(1)["job_url"])
    teclados = [tb.teclado_inicial(clave), tb.teclado_tras("favorito", clave), tb.teclado_tras("aplicada", clave),
                tb.teclado_tras("oculta", clave)] + [tb.teclado_tras("oculta", clave, m) for m in tb.MOTIVOS]
    datos = [d for t in teclados for d in todos_los_datos(t)]
    comprueba(max(len(d.encode("utf-8")) for d in datos) <= 64,
              "callback_data nunca pasa de 64 bytes (máximo %d)" % max(len(d.encode()) for d in datos))
    comprueba(len(clave) == 12 and clave == tb.clave_de(oferta(1)["job_url"]), "la clave corta es estable")

    print("\n[3] Cada botón hace lo que dice")
    url = oferta(1)["job_url"]
    espia = Espia()
    hecho = con_espia(espia, lambda: tb.procesar(pulsacion("f:" + clave), CONFIG))
    marcas, e = estado_de(url)
    comprueba(hecho == "favorito" and marcas == {"favoritas"}, "⭐ -> favoritas")
    comprueba(e["favoritas"][url].get("origen") == "telegram", "   con origen «telegram»")
    comprueba(e["favoritas"][url].get("titulo") == "Técnico/a Helpdesk N1 #1", "   y su título, sacado del índice")
    comprueba(len(espia.de("answerCallbackQuery")) == 1, "   contesta la pulsación (sin eso, el botón se queda «cargando»)")
    edit = espia.de("editMessageReplyMarkup")
    comprueba(len(edit) == 1 and edit[0]["message_id"] == 77 and edit[0]["chat_id"] == int(PRINCIPAL),
              "   y cambia los botones del mismo mensaje")
    con_espia(Espia(), lambda: tb.procesar(pulsacion("f:" + clave, update_id=2), CONFIG))
    comprueba(estado_de(url)[0] == {"favoritas"}, "pulsar ⭐ dos veces NO la desmarca (no es un interruptor)")
    con_espia(Espia(), lambda: tb.procesar(pulsacion("a:" + clave, update_id=3), CONFIG))
    comprueba(estado_de(url)[0] == {"aplicadas"}, "✅ después de ⭐ -> aplicada, y sale de favoritas")
    con_espia(Espia(), lambda: tb.procesar(pulsacion("o:" + clave, update_id=4), CONFIG))
    comprueba(estado_de(url)[0] == {"ocultas"}, "🙈 -> oculta, y sale de aplicadas")
    con_espia(Espia(), lambda: tb.procesar(pulsacion("mx:" + clave, update_id=5), CONFIG))
    marcas, e = estado_de(url)
    comprueba(marcas == {"ocultas"} and e["ocultas"][url].get("motivo") == "pide experiencia o es senior",
              "el motivo se guarda junto a la oculta")
    comprueba(indice()[clave].get("motivo") == "pide experiencia o es senior"
              and indice()[clave].get("respuesta") == "oculta", "y en el índice de avisos, para medir")
    con_espia(Espia(), lambda: tb.procesar(pulsacion("d:" + clave, update_id=6), CONFIG))
    comprueba(estado_de(url)[0] == set(), "↩️ deshacer -> sin ninguna marca")
    comprueba("respuesta" not in indice()[clave], "   y el índice olvida la respuesta")
    espia = Espia()
    con_espia(espia, lambda: tb.procesar(pulsacion("d:" + clave, update_id=7), CONFIG))
    comprueba(todos_los_datos(espia.de("editMessageReplyMarkup")[0]["reply_markup"])
              == todos_los_datos(tb.teclado_inicial(clave)), "   y vuelven los tres botones de inicio")

    print("\n[4] Repetir una actualización deja lo mismo (Telegram las repite tras un reinicio)")
    for _ in range(2):
        con_espia(Espia(), lambda: tb.procesar(pulsacion("a:" + clave, update_id=8), CONFIG))
    comprueba(estado_de(url)[0] == {"aplicadas"}, "la misma pulsación dos veces -> aplicada, una vez")

    print("\n[5] Un chat sin perfil no puede marcar")
    almacen.quitar_marcas(url)
    espia = Espia()
    hecho = con_espia(espia, lambda: tb.procesar(pulsacion("f:" + clave, chat=OTRA_PERSONA), CONFIG))
    comprueba(hecho == "rechazada" and estado_de(url)[0] == set(), "una pulsación desde otro chat no marca nada")
    comprueba(len(espia.de("answerCallbackQuery")) == 1 and not espia.de("editMessageReplyMarkup"),
              "   se le contesta, pero no se tocan los botones")
    hecho = con_espia(Espia(), lambda: tb.procesar(pulsacion("f:" + clave), dict(CONFIG, telegram_chat_id="")))
    comprueba(hecho == "rechazada", "sin chat principal configurado, tampoco")

    print("\n[6] Lo que no es nuestro, no se toca")
    comprueba(tb.procesar({"update_id": 9, "message": {"text": "/start"}}, CONFIG) is None, "un mensaje normal se ignora")
    hecho = con_espia(Espia(), lambda: tb.procesar(pulsacion("f:000000000000"), CONFIG))
    comprueba(hecho == "desconocida", "una clave que no está en el índice -> «ya no está en el índice»")
    hecho = con_espia(Espia(), lambda: tb.procesar(pulsacion("zz:" + clave), CONFIG))
    comprueba(hecho == "desconocida" and estado_de(url)[0] == set(), "un botón desconocido no marca nada")

    print("\n[7] Lo marcado desde Telegram se puede alternar desde el almacén")
    con_espia(Espia(), lambda: tb.procesar(pulsacion("f:" + clave), CONFIG))
    almacen.marcar(url, "favorito")
    comprueba(estado_de(url)[0] == set(), "almacen.marcar (el ⭐ de la web hasta MP5) desmarca una favorita de Telegram")

    # [8] El hilo que escucha se fue a telegram_bot.py con MP5, y sus pruebas a test_bot.py [10].

    print("\n[9] El token no sale en el log")
    capturado = []

    class Recoge(logging.Handler):
        def emit(self, record):
            capturado.append(record.getMessage() + (record.exc_text or ""))

    manejador = Recoge(level=logging.DEBUG)
    logging.getLogger().addHandler(manejador)
    try:
        error = requests.ConnectionError("Max retries exceeded with url: /bot%s/sendMessage" % TOKEN)
        comprueba(TOKEN in str(error), "   control: el error de requests SÍ lleva el token")
        con_espia(Espia(error=error), lambda: notificaciones._enviar_a_uno(TOKEN, PRINCIPAL, "hola", "prueba"))
        con_espia(Espia(error=error), lambda: tb._llamar(TOKEN, "answerCallbackQuery", {}))
    finally:
        logging.getLogger().removeHandler(manejador)
    comprueba(any("<token>" in m for m in capturado), "   control: los errores sí se escribieron, tachados")
    comprueba(not any(TOKEN in m for m in capturado),
              "ni el envío fallido ni la respuesta a un botón llevan el token al log")

    print("\n[10] El índice no crece sin fin")
    perfiles.registrar_aviso(perfiles.titular(), "viejo", "x",
                             avisada=(datetime.now() - timedelta(days=tb.DIAS_EN_EL_INDICE + 1)).isoformat())
    comprueba("viejo" in indice(), "   (control: el aviso viejo está en el índice antes de podar)")
    tb.registrar_aviso(oferta(5))
    comprueba("viejo" not in indice(), "los avisos de hace más de %d días se borran" % tb.DIAS_EN_EL_INDICE)
    antes = indice()[clave]
    comprueba(antes.get("respuesta") == "favorito", "   (la oferta 1 tiene respondido ⭐ desde [7])")
    tb.registrar_aviso(oferta(1))
    despues = indice()[clave]
    comprueba(despues.get("respuesta") == "favorito" and despues["avisada"] == antes["avisada"],
              "volver a avisar de la misma oferta no borra la respuesta ni la fecha del primer aviso")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  -", f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
