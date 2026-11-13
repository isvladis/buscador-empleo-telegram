# -*- coding: utf-8 -*-
"""
Pruebas de la puntuación: que explique el número, que los pesos se puedan ajustar desde la
config, y las señales añadidas el 25-10-2026 (idioma, desarrollo, coordinación, atención al
usuario y la única que lee la descripción).

Los casos son ofertas reales de pruebas/*.json. Cada señal nueva lleva además la
comprobación de lo que NO debe tocar, que es lo que la hizo entrar o descartarse: ninguna
entró sin pasar por las ofertas marcadas como interesantes en el histórico de la instalación original.

    python pruebas/test_puntuacion.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

# Un directorio de datos PROPIO con una config que sobrescribe pesos: la puntuación lee la
# config al primer uso y la cachea, así que hay que dejarla puesta antes de importar.
DATOS = Path(tempfile.mkdtemp(prefix="test-puntuacion-"))
os.environ["JOBSPY_DATA_DIR"] = str(DATOS)
(DATOS / "config.json").write_text(json.dumps({
    "_config_version": 2,
    "pesos_relevancia": {
        "nivel 2": -5.0,                  # válido: debe aplicarse
        "consultoría": "muchísimo",       # inválido: debe ignorarse sin romper nada
        "regla que no existe": 9.0,       # desconocida: no afecta a nada
    },
}), encoding="utf-8")

import buscador as b  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def cargar(nombre):
    return json.load(open(AQUI / nombre, encoding="utf-8"))["casos"]


ETIQ, HIST = cargar("etiquetadas.json"), cargar("historial.json")


def caso(lista, prefijo):
    return next((c for c in lista if (c["title"] or "").startswith(prefijo)), None)


def main() -> int:
    print("\n[1] La explicación suma exactamente el número")
    for titulo, ubicacion in (("Técnico/a de Help Desk - Soporte IT Nivel 1", "Barcelona, CT, ES"),
                              ("SOC Analyst", "Barcelona, Catalonia, Spain"),
                              ("Senior Sales Manager", "Madrid"),
                              ("Técnico/a Helpdesk Nivel 2", "Barcelona")):
        puntos, motivos = b.explicar_relevancia(titulo, ubicacion)
        comprueba(abs(puntos - round(sum(p for _, p in motivos), 1)) < 1e-9,
                  "%s: %.1f = suma de %d motivos" % (titulo[:38], puntos, len(motivos)))
        comprueba(puntos == b.puntuar_relevancia(titulo, ubicacion),
                  "   y puntuar_relevancia() da lo mismo")

    print("\n[2] Los motivos tienen nombre legible")
    _, motivos = b.explicar_relevancia("SOC Analyst Junior N1", "Barcelona, Spain")
    nombres = [n for n, _ in motivos]
    comprueba("junior o nivel 1" in nombres, "«junior o nivel 1» aparece")
    comprueba(any("SOC" in n for n in nombres), "«SOC / SIEM» aparece")
    comprueba(all(isinstance(p, float) for _, p in motivos), "los pesos son números")
    linea = b.texto_de_motivos(motivos)
    comprueba(linea.startswith("+") and "," in linea and ".0" not in linea,
              "la línea de Telegram usa signo y coma decimal: %r" % linea[:60])
    comprueba(len(b.texto_de_motivos(motivos).split(" · ")) <= 3, "como mucho 3 motivos")

    print("\n[3] La experiencia y la descripción entran en la explicación")
    _, m = b.explicar_relevancia("Técnico/a Helpdesk", "Barcelona", anos_experiencia=5)
    comprueba(any("pide 5 años" in n for n, _ in m), "«pide 5 años (tu tope: 3)» explica la resta")
    comprueba(any(p < 0 for n, p in m if "pide" in n), "y resta")
    _, m = b.explicar_relevancia("Técnico/a Helpdesk", "Barcelona", anos_experiencia=0)
    comprueba(any("sin experiencia" in n for n, _ in m), "«sin experiencia» suma")

    print("\n[4] Pesos ajustables desde la config (pesos_relevancia)")
    base_n2 = b.reglas_que_aplican("Técnico/a Helpdesk Nivel 2", "Barcelona")
    comprueba(dict(base_n2)["nivel 2"] == -2.0, "sin override el peso de «nivel 2» es −2,0")
    puntos, motivos = b.explicar_relevancia("Técnico/a Helpdesk Nivel 2", "Barcelona")
    comprueba(dict(motivos)["nivel 2"] == -5.0, "con el override de la config vale −5,0")
    comprueba(puntos == 1.5, "y la puntuación baja a 1,5 (era 4,5)")
    puntos_c, motivos_c = b.explicar_relevancia("Consultor de seguridad", "Barcelona")
    comprueba(dict(motivos_c).get("consultoría") == -2.5,
              "un peso que no es un número se ignora y se queda el de siempre (−2,5)")

    print("\n[5] Idioma que no hablas (resta 4,0, no descarta)")
    eurofins = caso(ETIQ, "Junior Help Desk Engineer (Spanish")
    comprueba(b.puntuar_relevancia(eurofins["title"], eurofins["location"]) < 5.0,
              "«…(Spanish, English and French)» ya no cruza el 5,0 (llegó a Telegram con 8,0)")
    for t in ("Soporte IT con alemán", "Técnico helpdesk Francés e inglés", "IT Support Dutch speaker"):
        comprueba(any(n == "idioma que no hablas" for n, _ in b.reglas_que_aplican(t)),
                  "se reconoce en %r" % t)
    for t, por_que in (("Técnico soporte portugués e inglés", "el portugués es tu lengua"),
                       ("Helpdesk Spanish and English", "castellano e inglés los hablas"),
                       ("Soporte IT Alemania", "un PAÍS no es un idioma"),
                       ("Support Technician Germany", "un PAÍS no es un idioma")):
        comprueba(not any(n == "idioma que no hablas" for n, _ in b.reglas_que_aplican(t)),
                  "NO se aplica en %r (%s)" % (t, por_que))

    print("\n[6] Desarrollo y coordinación")
    nippon = caso(ETIQ, "Technical Support and Development Specialist")
    comprueba(b.puntuar_relevancia(nippon["title"], nippon["location"]) < 5.0,
              "«Technical Support and Development Specialist» baja de 5,5")
    comprueba(not any(n == "desarrollo en el título" for n, _ in
                      b.reglas_que_aplican("Business Development Representative")),
              "«business development» no se cuenta dos veces (ya lo recoge la regla de ventas)")
    soc = caso(ETIQ, "SOC Coordinator")
    comprueba(b.puntuar_relevancia(soc["title"], soc["location"]) < 5.0, "«SOC Coordinator» baja de 6,5")

    print("\n[7] Atención al usuario (SAU, user support)")
    for t in ("Técnico/a SAU", "Operador/a de Soporte Telefónico SAU", "Remote Computer User Support"):
        comprueba(any("atención al usuario" in n for n, _ in b.reglas_que_aplican(t)),
                  "sube %r" % t)
    comprueba(not any("atención al usuario" in n for n, _ in b.reglas_que_aplican("Técnico de sistemas")),
              "no se activa con «sistemas»")

    print("\n[8] La única señal que lee la descripción")
    cobser = caso(ETIQ, "Cloud Security Operations Engineer")
    sin = b.puntuar_relevancia(cobser["title"], cobser["location"], None)
    con = b.puntuar_relevancia(cobser["title"], cobser["location"], None, cobser["description"])
    comprueba(con == sin - 2.5, "Cobser: la descripción (Kubernetes, Terraform, CI/CD) resta 2,5")
    comprueba(b.puntuar_relevancia(cobser["title"], cobser["location"]) == sin,
              "sin pasar la descripción, la puntuación es la de siempre (compatibilidad)")
    _, m = b.explicar_relevancia(cobser["title"], cobser["location"], None, cobser["description"])
    comprueba(any("ingeniería cloud/DevOps" in n for n, _ in m), "y lo explica con su nombre")

    print("\n    Lo que NO debe tocar (las que le interesan al usuario):")
    tocadas = []
    for h in HIST:
        if not h.get("description"):
            continue
        _, m = b.explicar_relevancia(h["title"], h.get("location"), None, h["description"])
        if any("ingeniería cloud/DevOps" in n for n, _ in m):
            tocadas.append(h["title"])
    comprueba(not tocadas, "ninguna de las %d aplicadas/favoritas con descripción se ve afectada%s"
              % (sum(1 for h in HIST if h.get("description")), (": %s" % tocadas[:3]) if tocadas else ""))
    for prefijo in ("Técnico/a de Sistemas Junior Linux", "Técnico/a de LINUX y redes"):
        h = caso(HIST, prefijo)
        if h and h.get("description"):
            _, m = b.explicar_relevancia(h["title"], h.get("location"), None, h["description"])
            comprueba(not any("cloud/DevOps" in n for n, _ in m),
                      "%r (llevan Docker y Kubernetes) NO se penaliza: título de sistemas, no de seguridad"
                      % prefijo)
    una = "Security Operations Analyst"
    solo_uno = "Conocimientos de Docker valorables. Monitorización de alertas SIEM."
    comprueba(b.reglas_de_la_descripcion(una, solo_uno) == [],
              "un solo término del stack («Docker») no convierte un puesto SOC en uno de plataforma")
    comprueba(b.reglas_de_la_descripcion("Técnico Helpdesk", "kubernetes terraform ansible docker") == [],
              "un título que no es de seguridad no se toca aunque la descripción lleve el stack entero")
    comprueba(b.reglas_de_la_descripcion("Cloud Security Engineer", None) == [], "sin descripción, nada")

    print("\n[9] Los motivos se guardan con la oferta")
    import pandas as pd
    df = pd.DataFrame([{"title": "SOC Analyst Junior", "location": "Barcelona, Spain",
                        "description": "Monitorización 24x7. Sin experiencia previa."}])
    out = b.anotar_relevancia(df)
    comprueba("motivos_relevancia" in out.columns, "anotar_relevancia añade motivos_relevancia")
    guardado = out["motivos_relevancia"].iloc[0]
    comprueba(isinstance(guardado, list) and guardado and "(" in guardado[0] and "+" in guardado[0],
              "como lista de «nombre (+4,0)»: %r" % (guardado[0] if guardado else None))
    esperado = b.puntuar_relevancia("SOC Analyst Junior", "Barcelona, Spain",
                                    b.extraer_anos_experiencia(df["description"].iloc[0]),
                                    df["description"].iloc[0])
    comprueba(abs(out["relevancia"].iloc[0] - esperado) < 1e-9,
              "la puntuación guardada es la que da puntuar_relevancia (%.1f)" % esperado)
    comprueba(abs(out["relevancia"].iloc[0] - round(sum(
        float(m.rsplit("(", 1)[1].rstrip(")").replace(",", ".")) for m in guardado), 1)) < 1e-9,
              "y los motivos guardados suman esa puntuación")
    import etiquetas
    comprueba("motivos_relevancia" in etiquetas.COLUMNAS_UTILES, "y la columna pasa a la base (COLUMNAS_UTILES)")

    print("\n[10] Ajustes del 27-10, sacados de los avisos reales de Jooble y de los botones")
    comprueba(bool(b.SENIOR_EXCLUDE.search("Líder de Infraestructura y Soporte Técnico — Remoto")),
              "«Líder de …» cae como senior (el «lead» en español)")
    comprueba(bool(b.SENIOR_EXCLUDE.search("Lider Técnico de Proyectos")), "   también sin tilde")
    comprueba(not b.SENIOR_EXCLUDE.search("Técnico/a de Soporte con liderazgo de incidencias"),
              "   y «liderazgo» no (palabra completa)")
    import config as config_mod
    import pandas as pd
    df = b.marcar_ruido(pd.DataFrame([{"title": "Diseñador/a Técnico/a - Soporte a Dirección"},
                                      {"title": "UX Designer"}, {"title": "Técnico/a Helpdesk N1"}]),
                        dict(config_mod.CONFIG_POR_DEFECTO))
    comprueba(list(df["fuera_de_perfil"]) == [True, True, False],
              "«Diseñador/a…» y «Designer» quedan fuera de perfil; el helpdesk no")
    comprueba(df["motivo_descarte"].iloc[0] == "Diseño (técnico, gráfico o de producto)", "   con su motivo")
    for texto, esperado in (("Al menos tres años de experiencia en soporte informático", 3),
                            ("Two to four years of relevant professional experience", 2),
                            ("Around one year of professional experience", 1),
                            ("un año de experiencia", 1),
                            ("dues anys d'experiència", 2),
                            # El «+» escapado, tal y como lo deja la conversión de LinkedIn.
                            ("3\\+ years of experience in Cybersecurity", 3),
                            ("**5\\+ años de experiencia** en soporte", 5),
                            ("2 \\- 3\\+ years of experience", 2),
                            ("3+ years of experience", 3),
                            ("Over 30\\+ years of experience in the sector", None),
                            ("No necesitas tener diez años de experiencia ni haber sobrevivido a todo", None),
                            ("Contrato de 1 año, renovable", None)):
        comprueba(b.extraer_anos_experiencia(texto) == esperado,
                  "años: «%s» -> %s" % (texto[:55], esperado))

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
