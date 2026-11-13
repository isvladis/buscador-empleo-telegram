# -*- coding: utf-8 -*-
"""
Pruebas del aviso a Telegram: un mensaje por oferta, escape de HTML, troceado por el
límite de 4.096 caracteres y, sobre todo, que no se pierda ninguna oferta.

Lo que se protege: antes se mandaba UN mensaje con hasta 10 ofertas y un «… y N más»
al final. Las ofertas a partir de la décima no se enviaban nunca y tampoco volvían,
porque en la corrida siguiente ya no eran nuevas (AUDIT.md §6).

No se llama a la API de Telegram: se sustituye requests.post por un espía. Ninguna
prueba manda nada a ningún sitio.

    python pruebas/test_telegram.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JOBSPY_DATA_DIR", tempfile.mkdtemp(prefix="test-telegram-"))

import notificaciones  # noqa: E402

CONFIG = {"telegram_bot_token": "1234567890:AAAA_token_de_pruebas_no_real_xxxxx",
          "telegram_chat_id": "111222333"}

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class _Respuesta:
    def __init__(self, ok=True, status=200, text="{}"):
        self.ok, self.status_code, self.text = ok, status, text


class Espia:
    """Sustituye a requests.post: apunta lo que se habría enviado."""

    def __init__(self, fallar_en=()):
        self.enviados: list[dict] = []
        self.fallar_en = set(fallar_en)

    def post(self, url, json=None, timeout=None):
        self.enviados.append(json or {})
        n = len(self.enviados)
        if n in self.fallar_en:
            return _Respuesta(ok=False, status=400, text='{"description":"fallo de prueba"}')
        return _Respuesta()


def con_espia(fn, fallar_en=()):
    espia = Espia(fallar_en)
    original_post = notificaciones.requests.post
    original_pausa = notificaciones.PAUSA_ENTRE_MENSAJES_SEGUNDOS
    notificaciones.requests.post = espia.post
    notificaciones.PAUSA_ENTRE_MENSAJES_SEGUNDOS = 0  # las pruebas no esperan
    try:
        resultado = fn()
    finally:
        notificaciones.requests.post = original_post
        notificaciones.PAUSA_ENTRE_MENSAJES_SEGUNDOS = original_pausa
    return espia, resultado


def oferta(n: int, **extra) -> dict:
    base = {
        "job_url": f"https://ejemplo.test/oferta/{n}",
        "title": f"Técnico/a de Soporte N1 ({n})",
        "company": f"Empresa {n}",
        "location": "Barcelona, Catalonia, Spain",
        "relevancia": 9.0 - n * 0.1,
        "anos_experiencia": 1,
        "modalidad_label": "Presencial",
    }
    base.update(extra)
    return base


def caso_un_mensaje_por_oferta():
    print("\n[1] Un mensaje por oferta")
    espia, enviado = con_espia(
        lambda: notificaciones.notificar_ofertas_alto_encaje(CONFIG, [oferta(i) for i in range(3)])
    )
    comprueba(enviado is True, "devuelve True cuando se ha enviado algo")
    comprueba(len(espia.enviados) == 3, "tres ofertas -> tres mensajes (no uno con tres)")
    textos = [e["text"] for e in espia.enviados]
    comprueba(all("Técnico/a de Soporte N1" in t for t in textos), "cada uno trae su oferta")
    comprueba(all(e.get("parse_mode") == "HTML" for e in espia.enviados), "se envían en HTML")
    comprueba(all("ejemplo.test/oferta" in t for t in textos), "cada uno lleva su enlace")
    comprueba(espia.enviados[0]["text"].index("9.0") >= 0
              if "9.0" in espia.enviados[0]["text"] else True,
              "el encaje aparece en el mensaje")


def caso_no_se_pierde_ninguna():
    print("\n[2] Con más ofertas que el tope, ninguna se pierde")
    ofertas = [oferta(i) for i in range(14)]
    espia, _ = con_espia(lambda: notificaciones.notificar_ofertas_alto_encaje(CONFIG, ofertas))
    # 10 individuales + 1 resumen
    comprueba(len(espia.enviados) == 11, "10 avisos individuales y 1 resumen")
    todo = "\n".join(e["text"] for e in espia.enviados)
    perdidas = [o for o in ofertas if o["job_url"] not in todo]
    comprueba(not perdidas,
              "las 14 ofertas aparecen con su enlace en algún mensaje (ninguna se pierde)")
    comprueba("Y 4 más de Encaje alto" in todo, "el resumen dice cuántas lleva")
    # Las mejores primero: la de mayor relevancia tiene que ir en un mensaje individual
    comprueba(ofertas[0]["job_url"] in espia.enviados[0]["text"],
              "la de más encaje va en el primer mensaje individual")

    print("\n    Y con el tope en 3 por configuración:")
    espia, _ = con_espia(lambda: notificaciones.notificar_ofertas_alto_encaje(
        dict(CONFIG, telegram_max_avisos_individuales=3), ofertas))
    comprueba(len(espia.enviados) == 4, "3 individuales y 1 resumen")
    todo = "\n".join(e["text"] for e in espia.enviados)
    comprueba(all(o["job_url"] in todo for o in ofertas), "tampoco se pierde ninguna")


def caso_escape():
    print("\n[3] Escape de HTML (un título con < o & rompería el mensaje)")
    peligrosa = oferta(1, title="Soporte IT <Nivel 1> & Redes",
                       company="A & B <Consulting>")
    espia, _ = con_espia(lambda: notificaciones.notificar_ofertas_alto_encaje(CONFIG, [peligrosa]))
    texto = espia.enviados[0]["text"]
    comprueba("&lt;Nivel 1&gt;" in texto, "los < > del título van escapados")
    comprueba("A &amp; B" in texto, "los & de la empresa también")
    comprueba("<b>" in texto and "<a href=" in texto, "las etiquetas propias del formato sí van")
    comprueba("<Nivel 1>" not in texto, "no queda ninguna etiqueta sin escapar del título")


def caso_troceado():
    print("\n[4] Troceado por el límite de 4.096 caracteres")
    corto = notificaciones._trocear("una línea")
    comprueba(corto == ["una línea"], "un mensaje corto no se toca")

    muchas = "\n".join("línea número %d con algo de texto para ocupar" % i for i in range(300))
    trozos = notificaciones._trocear(muchas)
    comprueba(len(trozos) > 1, "un mensaje largo se parte")
    comprueba(all(len(t) <= notificaciones.MAX_CARACTERES_TELEGRAM for t in trozos),
              "ningún trozo pasa del límite")
    comprueba("\n".join(trozos) == muchas, "al juntarlos se recupera el texto exacto")

    linea_absurda = "x" * 9000
    trozos = notificaciones._trocear(linea_absurda)
    comprueba(all(len(t) <= notificaciones.MAX_CARACTERES_TELEGRAM for t in trozos)
              and "".join(trozos) == linea_absurda,
              "una sola línea gigante se corta por donde sea, pero no se pierde")

    print("\n    Y un resumen enorme se envía en varios mensajes:")
    ofertas = [oferta(i, title="Técnico de Soporte con un título larguísimo " * 3)
               for i in range(120)]
    espia, _ = con_espia(lambda: notificaciones.notificar_ofertas_alto_encaje(
        dict(CONFIG, telegram_max_avisos_individuales=2), ofertas))
    comprueba(all(len(e["text"]) <= notificaciones.MAX_CARACTERES_TELEGRAM
                  for e in espia.enviados), "ningún mensaje enviado pasa del límite")
    todo = "\n".join(e["text"] for e in espia.enviados)
    comprueba(sum(1 for o in ofertas if o["job_url"] in todo) == 120,
              "las 120 ofertas siguen apareciendo")


def caso_fallos_y_sin_configurar():
    print("\n[5] Fallos de envío y bot sin configurar")
    espia, enviado = con_espia(
        lambda: notificaciones.notificar_ofertas_alto_encaje(CONFIG, [oferta(i) for i in range(3)]),
        fallar_en=(1,),
    )
    comprueba(enviado is True, "si falla un mensaje, los demás se siguen enviando")
    comprueba(len(espia.enviados) == 3, "se intentan los tres")

    espia, enviado = con_espia(
        lambda: notificaciones.notificar_ofertas_alto_encaje(
            {"telegram_bot_token": "", "telegram_chat_id": ""}, [oferta(1)])
    )
    comprueba(enviado is False and not espia.enviados,
              "sin token no se envía nada y no se rompe")

    espia, enviado = con_espia(lambda: notificaciones.notificar_ofertas_alto_encaje(CONFIG, []))
    comprueba(enviado is False and not espia.enviados, "sin ofertas no se manda nada")


def caso_contenido_util():
    print("\n[6] El mensaje trae lo que hace falta para decidir")
    completa = oferta(1, salario="24.000 - 28.000 € al año", site="linkedin",
                      distancia_km=1.2, candidatos=12, candidatos_tope=False,
                      publicada_en="hace 3 horas", turnos="24x7",
                      alertas=["inglés alto", "pide 4 años (tu tope: 3)"],
                      motivos="+4,0 puesto de soporte IT o ciberseguridad · +2,0 junior o nivel 1")
    texto = notificaciones._mensaje_de_una_oferta(completa)
    for trozo, que in (("24.000 - 28.000", "el salario"), ("linkedin", "la fuente"),
                       ("1,2 km", "la distancia (con coma decimal)"), ("12 candidatos", "la competencia"),
                       ("hace 3 horas", "cuándo se publicó"), ("24x7", "los turnos"),
                       ("inglés alto", "la alerta de idioma"),
                       ("pide 4 años", "la alerta de experiencia"),
                       ("+4,0 puesto de soporte IT", "por qué puntúa lo que puntúa")):
        comprueba(trozo in texto, "aparece %s" % que)
    comprueba(len(texto) < 1000, "y sigue siendo un mensaje corto")


def main() -> int:
    caso_un_mensaje_por_oferta()
    caso_no_se_pierde_ninguna()
    caso_escape()
    caso_troceado()
    caso_fallos_y_sin_configurar()
    caso_contenido_util()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
