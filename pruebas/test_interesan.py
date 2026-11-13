# -*- coding: utf-8 -*-
"""
Pruebas de /interesan (12-11): la lista de lo guardado con ⭐ que aún no se ha aplicado.

Desde que se quitó la web (MP5) «Me interesa» no se podía ver en ningún sitio. Lo que se comprueba:

- salen las ⭐ de quien lo pide, lo último guardado primero, y no las que ya aplicó o descartó;
- de cinco en cinco, con «Siguientes» y «Anteriores», y una página imposible no rompe nada;
- cada oferta lleva ✅ y 🙈 con los botones de siempre, que cambian solo su fila y la sacan de la lista;
- dice cuándo se guardó y si sigue saliendo en las búsquedas, sin prometer que esté abierta;
- **nada de otro perfil** (regla 5): ni sus ofertas, ni sus botones, ni su número;
- enseñar la lista no cambia las cifras de avisos ni el tope del día, ni toca un aviso que ya existía;
- todo cabe en Telegram (64 bytes por botón, 4.096 caracteres) y el título va escapado.

Ninguna sale a la red: `requests.post` es un espía, y los chat_id y el token, inventados.

    python pruebas/test_interesan.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATOS = Path(tempfile.mkdtemp(prefix="test-interesan-"))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A = 1111111, 2222222
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
CONFIG_EN_DISCO = {"_config_version": 2, "max_perfiles": 3, "busqueda_automatica_activa": False, "busqueda_automatica_horas": 1,
                   "hours_old": 38, "telegram_bot_token": "", "telegram_chat_id": ""}
(DATOS / "config.json").write_text(json.dumps(CONFIG_EN_DISCO, indent=2), encoding="utf-8")

import requests  # noqa: E402

import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import telegram_bot as bot  # noqa: E402
import telegram_botones as tb  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, datos):
        self._datos, self.status_code, self.ok, self.text = datos, 200, True, json.dumps(datos)

    def json(self):
        return self._datos


class Espia:
    def __init__(self):
        self.llamadas: list[dict] = []
        self.mensaje_id = 100

    def post(self, url, json=None, timeout=None, data=None, files=None):  # noqa: A002
        metodo = url.rsplit("/", 1)[-1]
        self.llamadas.append({"metodo": metodo, "cuerpo": json if json is not None else dict(data or {})})
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot_de_prueba"}})
        if metodo == "sendMessage":
            self.mensaje_id += 1
            return Resp({"ok": True, "result": {"message_id": self.mensaje_id}})
        return Resp({"ok": True, "result": True})

    def ultimo(self, chat, metodos=("sendMessage", "editMessageText")) -> dict:
        con = [c["cuerpo"] for c in self.llamadas if c["metodo"] in metodos and str(c["cuerpo"].get("chat_id")) == str(chat)]
        return con[-1] if con else {}

    def limpiar(self):
        self.llamadas.clear()


espia = Espia()
requests.post = espia.post
notificaciones.time.sleep = lambda *_a, **_k: None


def config() -> dict:
    return config_mod.cargar_config()


def escribe(texto, chat=TITULAR, n=[0]):
    n[0] += 1
    return bot.procesar({"update_id": n[0], "message": {"message_id": n[0], "text": texto,
                                                        "chat": {"id": chat, "type": "private"}, "from": {"id": chat}}}, config())


def pulsa(datos, chat=TITULAR, teclado=None, n=[1000]):
    n[0] += 1
    mensaje = {"message_id": 77, "chat": {"id": chat, "type": "private"}}
    if teclado is not None:
        mensaje["reply_markup"] = teclado
    return bot.procesar({"update_id": n[0], "callback_query": {"id": "cb%d" % n[0], "data": datos, "from": {"id": chat},
                                                               "message": mensaje}}, config())


def url_de(n) -> str:
    return f"https://www.linkedin.com/jobs/view/{n}"


def oferta(n, titulo, empresa="Empresa %s", dias_sin_verla=0):
    cuando = (datetime.now() - timedelta(days=dias_sin_verla, hours=1)).isoformat(timespec="seconds")
    return {"job_url": url_de(n), "title": titulo, "company": empresa % n if "%s" in empresa else empresa,
            "location": "Barcelona", "site": "linkedin", "relevancia": 6.0,
            "visto_por_primera_vez": cuando, "visto_por_ultima_vez": cuando}


def guardar(chat, o, hace_dias, enlazar=True) -> None:
    """Una ⭐ de ese perfil, guardada hace tantos días (la fecha se pone a mano: `fijar_marca` escribe la de ahora)."""
    if enlazar:
        perfiles.aplicar_cambios(chat, escribir=[(o, 0, 0)], ultima_busqueda=datetime.now().isoformat(timespec="seconds"))
    perfiles.fijar_marca(chat, o["job_url"], "favorito", {"title": o["title"], "company": o["company"]}, {"origen": "telegram"})
    fecha = (datetime.now() - timedelta(days=hace_dias, minutes=5)).isoformat(timespec="seconds")
    with perfiles._transaccion() as conn:
        conn.execute("UPDATE marcas SET fecha = ? WHERE chat_id = ? AND url = ?", (fecha, chat, o["job_url"]))


def botones(teclado) -> list[str]:
    return [b["callback_data"] for fila in (teclado or {}).get("inline_keyboard", []) for b in fila]


def numeros(texto) -> list[int]:
    return [int(n) for n in re.findall(r"^(\d+)\. <a href", texto, flags=re.M)]


def titulos(texto) -> list[str]:
    return re.findall(r"<b>(Oferta [^<]+)</b>", texto)


def alta_de_a() -> None:
    escribe("/invitar")
    pulsa("adm1:inv:soporte")
    codigo = re.search(r"\?start=([A-Za-z0-9_-]+)", espia.ultimo(TITULAR)["text"]).group(1)
    escribe("/start " + codigo, A)


def caso_vacia() -> None:
    print("\n[1] Sin nada guardado, lo dice")
    alta_de_a()
    espia.limpiar()
    comprueba(escribe("/interesan") == "comando:interesan", "(control: el comando existe)")
    m = espia.ultimo(TITULAR)
    comprueba("No tienes ninguna" in m.get("text", "") and "reply_markup" not in m, "«no tienes ninguna», y sin botones")
    comprueba("interesan" in dict(bot.COMANDOS_DE_TODOS) and re.fullmatch(r"[a-z0-9_]{1,32}", "interesan") is not None,
              "/interesan está en el menú de todos")


def caso_lista() -> None:
    print("\n[2] Las suyas, lo último guardado primero, de cinco en cinco")
    # 12 del titular, guardadas hace 0..11 días; la 3 y la 4 llevan una semana sin salir en las búsquedas.
    for n in range(12):
        guardar(TITULAR, oferta(n, "Oferta T%02d" % n, dias_sin_verla=9 if n in (3, 4) else 0), hace_dias=n)
    # Una que ya aplicó y otra que descartó después de guardarla: no son pendientes.
    for n, accion in ((50, "aplicada"), (51, "oculta")):
        guardar(TITULAR, oferta(n, "Oferta T%02d" % n), hace_dias=1)
        perfiles.fijar_marca(TITULAR, url_de(n), accion, {"title": "Oferta T%02d" % n}, {"origen": "telegram"})
    # Y una con las dos marcas a la vez, que la web permitía (aplicar y luego ⭐: `perfiles.alternar_marca`
    # solo quita la oculta): `fijar_marca` nunca las deja juntas, así que se escribe a mano.
    guardar(TITULAR, oferta(52, "Oferta T52"), hace_dias=1)
    with perfiles._transaccion() as conn:
        conn.execute("INSERT INTO marcas (chat_id, url, accion, fecha) VALUES (?, ?, 'aplicada', ?)",
                     (TITULAR, url_de(52), datetime.now().isoformat(timespec="seconds")))
    # Una guardada en la web hace mucho, que ya no está entre sus ofertas.
    guardar(TITULAR, oferta(60, "Oferta T60 huérfana"), hace_dias=40, enlazar=False)
    # Y las de A, que no son suyas.
    for n in (70, 71):
        guardar(A, oferta(n, "Oferta A%02d" % n, empresa="Empresa Secreta De A"), hace_dias=n - 70)
    espia.limpiar()
    escribe("/interesan")
    m = espia.ultimo(TITULAR)
    texto, teclado = m.get("text", ""), m.get("reply_markup") or {}
    comprueba("1–5 de 13" in texto, "dice cuántas son: «1–5 de 13» (12 y la huérfana; ni la aplicada ni la descartada)")
    comprueba(titulos(texto) == ["Oferta T%02d" % n for n in range(5)], "las cinco guardadas más recientemente, en orden")
    comprueba(numeros(texto) == [1, 2, 3, 4, 5], "   numeradas")
    comprueba("T50" not in texto and "T51" not in texto, "   sin la aplicada ni la descartada")
    comprueba("T52" not in texto and set(perfiles.marcas(TITULAR)["favoritas"]) & set(perfiles.marcas(TITULAR)["aplicadas"]) == {url_de(52)},
              "   ni la que sigue con su ⭐ pero ya está aplicada (las dos marcas a la vez, de la web)")
    comprueba("Oferta A" not in texto and "Secreta" not in texto and str(A) not in texto, "   y nada de A (regla 5)")
    filas = teclado.get("inline_keyboard", [])
    comprueba(len(filas) == 6 and all(len(f) == 2 and f[0]["callback_data"].startswith("a:")
                                      and f[1]["callback_data"].startswith("o:") for f in filas[:5]),
              "cada una con su ✅ y su 🙈, que son los botones de siempre")
    comprueba([b["callback_data"] for b in filas[5]] == ["aj1:in:5"], "   y «Siguientes», sin «Anteriores» en la primera")
    comprueba(filas[0][0]["text"].startswith("1 ") and filas[4][0]["text"].startswith("5 "), "   con su número delante")
    comprueba("guardada hoy" in texto and "guardada hace 1 día ·" in texto and "guardada hace 4 días" in texto,
              "dice cuándo se guardó cada una")
    comprueba(texto.count("sigue saliendo en las búsquedas") == 3 and texto.count("⚠️ no sale en las búsquedas desde hace 9 días") == 2,
              "   y si sigue saliendo en las búsquedas o desde cuándo no")
    comprueba(all(len(d.encode("utf-8")) <= 64 for d in botones(teclado)) and len(texto) < 4096, "cabe en Telegram")


def caso_paginas() -> None:
    print("\n[3] Pasar página, y una página que no existe")
    espia.limpiar()
    comprueba(pulsa("aj1:in:5") == "boton:aj1:interesan", "«Siguientes» responde")
    m = espia.ultimo(TITULAR, ("editMessageText",))
    comprueba("6–10 de 13" in m.get("text", "") and titulos(m["text"]) == ["Oferta T%02d" % n for n in range(5, 10)],
              "   edita el mismo mensaje con las cinco siguientes")
    comprueba([d for d in botones(m["reply_markup"]) if d.startswith("aj1:")] == ["aj1:in:0", "aj1:in:10"],
              "   con «Anteriores» y «Siguientes»")
    pulsa("aj1:in:10")
    m = espia.ultimo(TITULAR, ("editMessageText",))
    comprueba("11–13 de 13" in m["text"] and [d for d in botones(m["reply_markup"]) if d.startswith("aj1:")] == ["aj1:in:5"],
              "la última: tres, y solo «Anteriores»")
    comprueba("Oferta T60 huérfana" in m["text"] and "ya no está en tu lista" in m["text"] and "guardada hace 40 días" in m["text"],
              "   la que ya no está entre sus ofertas sale con lo que se guardó de ella, y lo dice")
    for raro in ("aj1:in:999", "aj1:in:-20", "aj1:in:abc", "aj1:in"):
        pulsa(raro)
    m = espia.ultimo(TITULAR, ("editMessageText",))
    comprueba("1–5 de 13" in m["text"], "un número imposible o roto no revienta: se queda en una página que existe")
    pulsa("aj1:in:999")
    comprueba("11–13 de 13" in espia.ultimo(TITULAR, ("editMessageText",))["text"], "   y uno demasiado alto, en la última")


def caso_botones() -> None:
    print("\n[4] ✅ y 🙈 marcan con los botones de siempre, cambian solo su fila y la sacan de la lista")
    espia.limpiar()
    escribe("/interesan")
    teclado = espia.ultimo(TITULAR)["reply_markup"]
    clave1, clave2 = tb.clave_de(url_de(1)), tb.clave_de(url_de(2))
    comprueba(pulsa("a:" + clave1, teclado=teclado) == "aplicada", "✅ en la segunda de la lista: aplicada")
    m = perfiles.marcas(TITULAR)
    comprueba(url_de(1) in m["aplicadas"] and url_de(1) not in m["favoritas"], "   pasa de «Me interesa» a «Aplicada»")
    editado = espia.ultimo(TITULAR, ("editMessageReplyMarkup",)).get("reply_markup", {})
    filas = editado.get("inline_keyboard", [])
    comprueba(len(filas) == 6 and filas[1][0]["text"].startswith("2 ") and "Aplicada" in filas[1][0]["text"],
              "   su fila cambia y conserva el número")
    comprueba(filas[0] == teclado["inline_keyboard"][0] and filas[2:] == teclado["inline_keyboard"][2:],
              "   las otras cuatro y la de pasar página quedan como estaban")
    comprueba(pulsa("o:" + clave2, teclado=editado) == "oculta" and url_de(2) in perfiles.marcas(TITULAR)["ocultas"],
              "🙈 en la tercera: descartada")
    espia.limpiar()
    escribe("/interesan")
    texto = espia.ultimo(TITULAR)["text"]
    comprueba("1–5 de 11" in texto and "Oferta T01" not in texto and "Oferta T02" not in texto,
              "al volver a pedirla ya no están: quedan 11")
    comprueba(titulos(texto) == ["Oferta T00", "Oferta T03", "Oferta T04", "Oferta T05", "Oferta T06"], "   y suben las siguientes")


def caso_no_toca_las_cifras() -> None:
    print("\n[5] Enseñar la lista no es avisar: ni cifras, ni tope, ni tocar un aviso que ya existía")
    # Una ⭐ que sí viene de un aviso de verdad, de hace tres días.
    o = oferta(80, "Oferta T80 avisada")
    perfiles.aplicar_cambios(TITULAR, escribir=[(o, 0, 0)], ultima_busqueda=datetime.now().isoformat(timespec="seconds"))
    hace3 = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    clave = tb.registrar_aviso(o, TITULAR, avisada=hace3)
    guardar(TITULAR, o, hace_dias=0, enlazar=False)
    perfiles.anotar_respuesta(TITULAR, clave, "favorito")
    antes = perfiles.conexion().execute("SELECT tipo, avisada, respuesta FROM avisos WHERE chat_id = ? AND clave = ?",
                                        (TITULAR, clave)).fetchone()
    cifras, avisos_hoy = perfiles.cifras_de_avisos(TITULAR), perfiles.contar_avisos(TITULAR, "aviso", "2000")
    espia.limpiar()
    escribe("/interesan")
    comprueba("Oferta T80 avisada" in espia.ultimo(TITULAR)["text"], "(control: la avisada sale en la lista)")
    for desde in (5, 10):
        pulsa("aj1:in:%d" % desde)
    despues = perfiles.conexion().execute("SELECT tipo, avisada, respuesta FROM avisos WHERE chat_id = ? AND clave = ?",
                                          (TITULAR, clave)).fetchone()
    comprueba(tuple(antes) == tuple(despues) and antes[0] == "aviso", "el aviso que ya existía queda igual: tipo, fecha y respuesta")
    comprueba(perfiles.cifras_de_avisos(TITULAR) == cifras and perfiles.contar_avisos(TITULAR, "aviso", "2000") == avisos_hoy,
              "las cifras de avisos y lo que cuenta para el tope no se mueven")
    tipos = dict(perfiles.conexion().execute("SELECT tipo, COUNT(*) FROM avisos WHERE chat_id = ? GROUP BY tipo", (TITULAR,)).fetchall())
    comprueba(tipos.get("interesa", 0) >= 10 and tipos.get("aviso") == 1, "las demás se apuntan como «interesa», que no es un aviso")
    n = perfiles.conexion().execute("SELECT COUNT(*) FROM avisos WHERE chat_id = ?", (TITULAR,)).fetchone()[0]
    escribe("/interesan")
    comprueba(perfiles.conexion().execute("SELECT COUNT(*) FROM avisos WHERE chat_id = ?", (TITULAR,)).fetchone()[0] == n,
              "pedirla otra vez no apunta nada más")


def caso_aislamiento() -> None:
    print("\n[6] Cada uno, lo suyo")
    espia.limpiar()
    escribe("/interesan", A)
    m = espia.ultimo(A)
    comprueba("1–2 de 2" in m["text"] and titulos(m["text"]) == ["Oferta A70", "Oferta A71"], "A ve las dos suyas")
    comprueba("Oferta T" not in m["text"] and str(TITULAR) not in m["text"], "   y ninguna del titular, ni su número")
    comprueba(not [d for d in botones(m["reply_markup"]) if d.startswith("aj1:")], "   sin botones de página: caben en una")
    del_titular = tb.clave_de(url_de(5))
    comprueba(perfiles.aviso(TITULAR, del_titular) is not None and perfiles.aviso(A, del_titular) is None,
              "la fila de botones de una oferta del titular es solo suya")
    comprueba(pulsa("a:" + del_titular, A) == "desconocida" and url_de(5) not in perfiles.marcas(A)["aplicadas"]
              and url_de(5) in perfiles.marcas(TITULAR)["favoritas"],
              "A no puede marcar con el botón de una oferta del titular, ni se la mueve")
    comprueba(str(A) not in json.dumps([c["cuerpo"].get("text", "") for c in espia.llamadas])
              and all(str(A) not in d and str(TITULAR) not in d for c in espia.llamadas
                      for d in botones(c["cuerpo"].get("reply_markup"))), "ni un texto ni un botón llevan un chat_id")


def caso_textos_raros() -> None:
    print("\n[7] Un título absurdo ni rompe el HTML ni desborda el mensaje")
    for n in range(90, 95):
        guardar(A, oferta(n, "Oferta <b>rara</b> & larga " + "x" * 400, empresa="Empresa <i>%s</i> " + "y" * 200), hace_dias=0)
    espia.limpiar()
    escribe("/interesan", A)
    m = espia.ultimo(A)
    comprueba("<b>rara</b>" not in m["text"] and "&lt;b&gt;rara" in m["text"] and "<i>" not in m["text"], "el título y la empresa van escapados")
    comprueba(len(m["text"]) < 4096 and len(espia.llamadas) == 1, "y cinco así caben en un solo mensaje (%d caracteres)" % len(m["text"]))


def main() -> int:
    for caso in (caso_vacia, caso_lista, caso_paginas, caso_botones, caso_no_toca_las_cifras, caso_aislamiento, caso_textos_raros):
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
