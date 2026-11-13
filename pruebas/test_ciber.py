# -*- coding: utf-8 -*-
"""
Pruebas de MP4: el juego `titular-ciber`, la orden que lo aplica y que el adicional no se entera.

Lo que aquí se puede romper sin que nada más lo vea:

- que un soporte genérico vuelva a llegar, o que deje de llegar una subárea que entró el 30-10;
- que apagar `it_saas` quitándolo de config.json resucite los términos de fábrica;
- que la web siga puntuando con las reglas viejas, porque no lee `perfiles.db`;
- que aplicar el reenfoque le toque algo al adicional (regla 5);
- y que `medir.py` deje de ser la misma medida que el servidor.

El antes y el después sobre la copia real está en pruebas/medir_mp4.py. Aquí nada sale a la red:
solo se planifican las consultas, sin hacer ninguna, y no se llama a Telegram.

    python pruebas/test_ciber.py
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))

TITULAR, ADICIONAL = 91000001, 92000002
DATOS = Path(tempfile.mkdtemp(prefix="test-ciber-"))
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import perfiles  # noqa: E402
import reglas  # noqa: E402

fallos: list[str] = []
hechas = 0

# `titular-2026-10` está congelado: lo usa el adicional, y una regla que cambie ahí le cambia los
# avisos. Saltos de línea normalizados: en el PC el fichero está en CRLF y en git en LF.
HUELLA_JUEGO_CONGELADO = "164d989302053b7b313252f30f1fcc47c044f80d3673ba0a105bd27622ed9fbd"

CIBER = reglas.TITULAR_CIBER
VIEJO = reglas.TITULAR_2026_10


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def puntuador(juego) -> buscador.PuntuadorReglas:
    return buscador.PuntuadorReglas(juego, juego.config["pesos_relevancia"], 3, None)


def nota(juego, titulo: str, descripcion: str | None = None, ubicacion: str = "Barcelona, Spain") -> float:
    return buscador.puntuar_relevancia(titulo, ubicacion, None, descripcion, puntuador(juego))


def descartada(juego, titulo: str) -> bool:
    df = pd.DataFrame({"title": [titulo]})
    return bool(buscador.marcar_ruido(df, {"reglas_descarte": juego.config["reglas_descarte"]})["fuera_de_perfil"].iloc[0])


def llega(juego, titulo: str, descripcion: str | None = None) -> bool:
    return not descartada(juego, titulo) and nota(juego, titulo, descripcion) >= 5.0


def probar_juego() -> None:
    print("\n[1] El juego: lo congelado no se mueve y las cuatro reglas se quedan")
    fichero = (RAIZ / "reglas" / "titular_2026_10.py").read_bytes().replace(b"\r\n", b"\n")
    comprueba(hashlib.sha256(fichero).hexdigest() == HUELLA_JUEGO_CONGELADO,
              "titular_2026_10.py es byte a byte el de MP3")
    comprueba(reglas.obtener("titular-ciber") is CIBER, "titular-ciber está registrado")
    comprueba(CIBER.patron_senior.pattern == VIEJO.patron_senior.pattern, "el filtro de senior no cambia")
    comprueba(CIBER.config["anos_experiencia_max"] == 3, "el tope de años sigue en 3")
    pesos = {n: p for n, p, _ in CIBER.reglas_titulo}
    comprueba(pesos.get("GRC, IAM o pentest") == -2.0 and pesos.get("auditoría") == -2.0
              and pesos.get("DevOps o cloud engineer") == -1.5, "GRC, auditoría y DevOps siguen restando")
    comprueba(any(r["nombre"] == "ingeniería cloud/DevOps en un puesto de seguridad" and r["peso"] == -2.5
                  for r in CIBER.reglas_descripcion), "la señal de ingeniería cloud sigue en −2,5")
    comprueba("atención al usuario (SAU, user support)" not in pesos and not CIBER.config["pesos_relevancia"],
              "fuera la señal de atención al usuario y su peso")
    # Ni una sola combinación de señales de título puede cruzar el umbral sin ciberseguridad: esa
    # es la promesa de D11, y el peso se eligió para cumplirla.
    tope = sum(p for n, p, _ in CIBER.reglas_titulo if p > 0 and n != "puesto de ciberseguridad")
    sin_ciber = {"ciberseguridad", "SOC / SIEM"}
    tope -= sum(p for n, p, _ in CIBER.reglas_titulo if n in sin_ciber)
    comprueba(tope + 0.5 + pesos["soporte sin ciberseguridad en el título"] < 5.0,
              "soporte sin ciberseguridad: ni con todas las señales llega a 5,0 (%.1f)"
              % (tope + 0.5 + pesos["soporte sin ciberseguridad en el título"]))


def probar_titulos() -> None:
    print("\n[2] Qué llega por el título")
    for titulo in ("Analista SOC Junior", "Junior Cybersecurity", "Técnico/a de Soporte N1 de Ciberseguridad",
                   "DFIR Analyst", "Threat Intelligence Analyst", "Malware Analyst", "Incident Responder",
                   "Incident Response Analyst", "Técnico/a de Redes y Seguridad", "Técnico de seguridad de redes",
                   "Técnico/a de Seguridad IT", "OT SOC Analyst", "Técnico Vulnerabilidades",
                   "Técnico/a de monitorización de ciberseguridad",
                   "Information Security Functional Monitoring"):
        comprueba(llega(CIBER, titulo), "llega: " + titulo)
    for titulo in ("Soporte Service Desk N1", "Técnico Help Desk N1 - Soporte IT (Windows/365)",
                   "IT Support Specialist L1", "Técnico/a de Sistemas Junior", "NOC Operator",
                   "Técnico/a de Monitorización (24x7)", "Administrador/a de Redes (24x7)",
                   "Especialista en Ciberseguridad Cloud", "Cloud Security Analyst (AWS)", "GRC Analyst",
                   "Junior Penetration Tester", "Consultor CyberArk", "Ingeniero/a de Redes y Seguridad",
                   "Cybersecurity Engineer", "SOC Coordinator", "Prácticas Soporte IT",
                   # Desde el 04-11 tampoco: las tres que llegaron se descartaron.
                   "Prácticas Ciberseguridad"):
        comprueba(not llega(CIBER, titulo), "no llega: " + titulo)
    # Controles negativos: las mismas reglas de hoy se comportan al revés, o la prueba no mide nada.
    comprueba(descartada(VIEJO, "DFIR Analyst") and not descartada(CIBER, "DFIR Analyst"),
              "control: titular-2026-10 descartaba las subáreas que entran")
    comprueba(descartada(VIEJO, "Prácticas Ciberseguridad"), "control: y las prácticas de ciberseguridad")
    comprueba(llega(VIEJO, "Soporte Service Desk N1"), "control: y avisaba del soporte genérico")
    comprueba(nota(VIEJO, "Especialista en Ciberseguridad Cloud") >= 5.0, "control: y de la seguridad cloud")


NOMBRE_REGLA = "Programas de prácticas o de recién titulado"


def motivo_de(titulo: str) -> str | None:
    """Qué regla descarta ese título, por nombre: el booleano no distingue cuál fue."""
    df = pd.DataFrame({"title": [titulo]})
    marcado = buscador.marcar_ruido(df, {"reglas_descarte": CIBER.config["reglas_descarte"]})
    return marcado["motivo_descarte"].iloc[0]


def probar_practicas() -> None:
    print("\n[2b] Prácticas y programas de recién titulado (04-11, con los botones)")
    # Tres casos reales descartados, con la nota que les dio el medidor.
    for titulo in ("Prácticas IT: Ciberseguridad 26-27",
                   "FY27 | Junior | T&T | Ciberseguridad - Seguridad Física",
                   "Prácticas en Smart Factory y Ciberseguridad STP27",
                   "Graduate Program Cybersecurity", "Programa de graduados en seguridad",
                   "Analista de ciberseguridad recién titulado"):
        comprueba(descartada(CIBER, titulo), "se descarta: " + titulo)
    # Controles negativos: lo que el titular SÍ marcó favorito sigue entrando.
    for titulo in ("Analista N1 SOC ** BARCELONA", "Técnico/A Junior De Redes Y Seguridad",
                   "Cyber Defense Analyst", "Técnico/a de Redes y Seguridad",
                   "Técnico de Ciberseguridad WAF"):
        comprueba(llega(CIBER, titulo), "sigue llegando: " + titulo)
    # Ojo: «Consultor de buenas prácticas» SÍ se descarta, pero por la regla de consultoría. Lo que
    # hay que comprobar es que no la descarte ESTA regla, así que se mira el motivo y no el booleano.
    comprueba(motivo_de("Consultor de buenas prácticas de seguridad") != NOMBRE_REGLA,
              "«buenas prácticas» no la descarta la regla nueva")
    comprueba(motivo_de("Prácticas IT: Ciberseguridad 26-27") == NOMBRE_REGLA,
              "   y las de prácticas sí, con su nombre en el motivo")
    # La decisión del 25-10 sigue en pie: «Prácticas y becas» mantiene su excepción de ciberseguridad.
    comprueba(not descartada(CIBER, "Becario de ciberseguridad"),
              "un becario de ciberseguridad sigue pasando (decisión del 25-10)")
    comprueba(descartada(CIBER, "Becario de contabilidad"), "   y uno de contabilidad, no")
    # Control: el juego congelado NO lleva la regla, así que su línea base no se mueve. (Descartar
    # esa oferta sí la descarta, por sus propias reglas; lo que importa es que la nueva no está.)
    comprueba(NOMBRE_REGLA not in [r["nombre"] for r in VIEJO.config["reglas_descarte"]],
              "control: titular-2026-10 no lleva la regla nueva")
    comprueba(len(CIBER.config["reglas_descarte"]) == len(VIEJO.config["reglas_descarte"]) + 1,
              "   y el juego vivo tiene exactamente una regla más")


def probar_descripcion() -> None:
    print("\n[3] Soporte con ciberseguridad en la descripción: dos señales y una fuerte")
    titulo = "IT Support Specialist"
    for texto, pasa, que in (("Trabajarás con SIEM y con EDR.", True, "dos fuertes"),
                             ("Gestión de EDR y firewall.", True, "una fuerte y una general"),
                             ("Firewall y antivirus.", False, "dos generales"),
                             ("Firewall, antivirus y ciberseguridad.", False, "tres generales"),
                             ("Solo SIEM.", False, "una fuerte sola"),
                             ("Soporte a usuarios.", False, "ninguna")):
        comprueba(llega(CIBER, titulo, texto) == pasa, "%s: %s" % (que, "pasa" if pasa else "no pasa"))
    regla = next(r for r in CIBER.reglas_descripcion if r["nombre"].startswith("soporte con"))
    sin_fuertes = {k: v for k, v in regla.items() if k != "fuertes"}
    laxo = [sin_fuertes if r is regla else r for r in CIBER.reglas_descripcion]
    import dataclasses
    otro = dataclasses.replace(CIBER, reglas_descripcion=laxo)
    comprueba(nota(otro, titulo, "Firewall y antivirus.") > nota(CIBER, titulo, "Firewall y antivirus."),
              "control: sin `fuertes`, dos señales generales sí pasarían")
    comprueba(all(not r.get("fuertes") for r in VIEJO.reglas_descripcion), "titular-2026-10 no usa `fuertes`")


def crudo_del_adicional() -> tuple:
    return perfiles.conexion().execute("SELECT * FROM perfiles WHERE chat_id = ?", (ADICIONAL,)).fetchone()


def preparar() -> tuple[bytes, dict]:
    """Datos como los del servidor: config.json con las claves de `titular-2026-10` y dos perfiles."""
    config = copy.deepcopy(VIEJO.config)
    config.update(hours_old=38, max_busquedas_por_corrida=33, busqueda_automatica_horas=1,
                  clave_que_no_se_toca="sigue")
    # jooble y las reglas salen del juego; config.json real no los trae hasta aplicar.
    (DATOS / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    perfiles.guardar(TITULAR, {"v": 1, "reglas": "titular-2026-10", "config": "global"}, "activo")
    perfiles.guardar(ADICIONAL, perfiles.datos_de_plantilla(), "activo")
    config_mod.cargar_config()  # lo deja migrado (`_config_version`), como el del servidor, antes de copiarlo
    return (DATOS / "config.json").read_bytes(), config


def de_la_consola(*argumentos: str) -> tuple[int, str]:
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        codigo = perfiles.main(list(argumentos))
    return codigo, salida.getvalue()


def copias(patron: str) -> list[Path]:
    return sorted(DATOS.glob(patron))


def efectiva_adicional() -> dict:
    return perfiles.config_efectiva(perfiles.obtener(ADICIONAL), config_mod.cargar_config())


def web_nota(titulo: str) -> float:
    buscador._REL.update(comprobado=0.0, firma=None, puntuador=None)
    return buscador.puntuar_relevancia(titulo, "Barcelona, Spain")


def probar_aplicacion() -> None:
    print("\n[4] La orden: copia, aplica, se puede repetir y se puede deshacer")
    original, _ = preparar()
    adicional_antes = crudo_del_adicional()
    efectiva_antes = efectiva_adicional()
    muestra = json.loads((AQUI / "etiquetadas.json").read_text(encoding="utf-8"))["casos"][:40]
    punt = perfiles.puntuador_de(perfiles.obtener(ADICIONAL), config_mod.cargar_config())
    notas_antes = [buscador.explicar_relevancia(c["title"], c["location"], None, c["description"], punt)
                   for c in muestra]
    comprueba(web_nota("Técnico/a Help Desk N1") >= 5.0, "antes, la web avisa del helpdesk")

    codigo, salida = de_la_consola("reenfocar-titular")
    comprueba(codigo == 2 and (DATOS / "config.json").read_bytes() == original and not copias("*.bak-*"),
              "sin --confirmar no hace nada")

    codigo, salida = de_la_consola("reenfocar-titular", "--confirmar")
    comprueba(codigo == 0, "aplica")
    comprueba(str(TITULAR) not in salida and str(ADICIONAL) not in salida, "no escribe ningún chat_id")
    previa = copias("config.json.bak-pre-ciber-*")
    comprueba(len(previa) == 1 and previa[0].read_bytes() == original, "copia de config.json idéntica")
    base = copias("perfiles.db.bak-pre-ciber-*")
    conn = sqlite3.connect(base[0]) if base else None
    comprueba(bool(conn) and conn.execute("SELECT COUNT(*) FROM perfiles").fetchone()[0] == 2,
              "copia de perfiles.db legible y con los dos perfiles")
    if conn:
        conn.close()
    crudo = json.loads((DATOS / "config.json").read_text(encoding="utf-8"))
    comprueba(crudo.get("clave_que_no_se_toca") == "sigue", "lo demás de config.json no se toca")
    datos_titular = dict(perfiles.obtener(TITULAR)["datos"])
    aprendido = datos_titular.pop("ajustes_aprendidos", {})
    comprueba(datos_titular == {"v": 1, "reglas": "titular-ciber", "config": "global"},
              "el perfil del titular dice titular-ciber")
    # MP6: lo marcado antes del reenfoque no cuenta para la capa aprendida (aprendizaje.py).
    comprueba(set(aprendido) == {"desde"} and bool(aprendido["desde"]),
              "y apunta desde cuándo cuentan sus etiquetas")
    comprueba(crudo["puntuador"]["juego"] == perfiles.obtener(TITULAR)["datos"]["reglas"],
              "perfil y config.json dicen lo mismo")

    print("\n    Lo que lee cada camino")
    config = config_mod.cargar_config()
    efectiva = perfiles.config_efectiva(perfiles.obtener(TITULAR), config)
    terminos = sorted({b["params"]["search_term"] for b in buscador.construir_busquedas(efectiva)})
    comprueba(terminos == sorted(CIBER.config["categorias"]["ciberseguridad"]["terminos"]),
              "busca los 11 términos de ciberseguridad y ninguno más")
    comprueba(efectiva["reglas_descarte"] == CIBER.config["reglas_descarte"],
              "config.json aplicado = reglas de descarte del juego: medir.py mide lo mismo que el servidor")
    comprueba(perfiles.puntuador_de(perfiles.obtener(TITULAR), config).juego is CIBER, "la corrida puntúa con titular-ciber")
    comprueba(web_nota("Técnico/a Help Desk N1") < 5.0, "la web también")
    sin_fila = perfiles.config_efectiva(perfiles.PERFIL_GLOBAL, config)
    comprueba(sin_fila["puntuador"]["juego"] == "titular-ciber", "y un titular sin fila, también")

    print("\n    Controles negativos")
    guardada = (DATOS / "config.json").read_bytes()
    sin_clave = {k: v for k, v in crudo.items() if k != "puntuador"}
    (DATOS / "config.json").write_text(json.dumps(sin_clave, ensure_ascii=False), encoding="utf-8")
    comprueba(web_nota("Técnico/a Help Desk N1") >= 5.0, "sin `puntuador` en config.json, la web usaría el juego viejo")
    categorias = {k: v for k, v in crudo["categorias"].items() if k != "it_saas"}
    (DATOS / "config.json").write_text(json.dumps({**crudo, "categorias": categorias}, ensure_ascii=False),
                                       encoding="utf-8")
    resucitado = config_mod.cargar_config()["categorias"]["it_saas"]
    comprueba("IT support SaaS" in resucitado["terminos"] and resucitado["activa"],
              "quitando it_saas de config.json, vuelven los términos de fábrica")
    (DATOS / "config.json").write_bytes(guardada)

    print("\n    El adicional, campo a campo")
    comprueba(crudo_del_adicional() == adicional_antes, "su fila es idéntica, versión y fecha incluidas")
    despues = efectiva_adicional()
    distintos = sorted(c for c in set(efectiva_antes) | set(despues) if efectiva_antes.get(c) != despues.get(c))
    comprueba(not distintos, "su config efectiva es idéntica, clave a clave (%s)" % distintos)
    punt = perfiles.puntuador_de(perfiles.obtener(ADICIONAL), config_mod.cargar_config())
    comprueba(punt.juego is VIEJO, "sigue con titular-2026-10")
    comprueba(notas_antes == [buscador.explicar_relevancia(c["title"], c["location"], None, c["description"], punt)
                              for c in muestra], "misma nota y mismos motivos en 40 ofertas")
    contaminado = perfiles.config_efectiva(
        {"chat_id": None, "version": 0, "datos": {"v": 1, "reglas": "titular-2026-10", "config": "global"}},
        config_mod.cargar_config())
    comprueba(contaminado["reglas_descarte"] != efectiva_antes["reglas_descarte"],
              "control: con config global, a él sí le habría llegado")

    print("\n    Repetir y deshacer")
    version = perfiles.obtener(TITULAR)["version"]
    n_base = len(copias("perfiles.db.bak-pre-ciber-*"))
    codigo, salida = de_la_consola("reenfocar-titular", "--confirmar")
    comprueba(codigo == 0 and "no se ha tocado nada" in salida, "repetirla no hace nada")
    comprueba(perfiles.obtener(TITULAR)["version"] == version and (DATOS / "config.json").read_bytes() == guardada
              and len(copias("perfiles.db.bak-pre-ciber-*")) == n_base and len(copias("config.json.bak-pre-ciber-*")) == 1,
              "   ni versión, ni fichero, ni copias nuevas")

    codigo, salida = de_la_consola("reenfocar-titular", "--revertir")
    comprueba(codigo == 0 and (DATOS / "config.json").read_bytes() == original, "--revertir deja config.json byte a byte")
    comprueba(perfiles.obtener(TITULAR)["datos"]["reglas"] == "titular-2026-10", "   y el perfil en titular-2026-10")
    comprueba(len(copias("config.json.bak-post-ciber-*")) == 1, "   sin perder el config.json que había (regla 7)")
    comprueba(crudo_del_adicional() == adicional_antes, "   y sin tocar al adicional")
    comprueba(web_nota("Técnico/a Help Desk N1") >= 5.0, "   y la web vuelve a las reglas de antes")
    codigo, salida = de_la_consola("reenfocar-titular", "--revertir")
    comprueba(codigo == 0 and "no se ha tocado nada" in salida, "--revertir dos veces no hace nada")


def probar_consultas() -> None:
    print("\n[5] Consultas: el presupuesto no sube y la rotación aguanta")
    de_la_consola("reenfocar-titular", "--confirmar")
    config = config_mod.cargar_config()
    activos = perfiles.activos()
    plan = buscador.planificar_consultas(activos, config)
    comprueba(len(plan) == 48, "48 consultas únicas: las 33 del adicional y 15 nuevas (%d)" % len(plan))
    tope = int(config["max_busquedas_por_corrida"])
    comprueba(tope == 33, "el presupuesto sigue en 33")
    comprueba(len(buscador.elegir_consultas(plan, {}, tope)) == 33, "una corrida hace 33")
    solo_adicional = [c for c in plan if c.perfiles == [ADICIONAL]]
    comprueba(len(solo_adicional) == 15 and all(
        c.busqueda["params"]["search_term"] in ("helpdesk", "service desk", "junior it support",
                                                "tecnico soporte n1", "tecnico informatico")
        for c in solo_adicional), "las 15 de soporte son solo del adicional")
    comprueba(sum(len(c.perfiles) == 2 for c in plan) == 18, "las 18 de ciberseguridad de siempre se comparten")

    ultimas: dict = {}
    vistas: dict = {}
    huecos = []
    for hora in range(120):
        for consulta in buscador.elegir_consultas(plan, ultimas, tope):
            if consulta.clave in vistas and hora > 12:
                huecos.append(hora - vistas[consulta.clave])
            vistas[consulta.clave] = hora
            ultimas[consulta.clave] = "%05d" % hora
    comprueba(max(huecos) <= 2, "ninguna tarda más de 2 h en repetirse (%d)" % max(huecos))
    comprueba(1.4 < sum(huecos) / len(huecos) < 1.5, "de media, 1,45 h (%.2f)" % (sum(huecos) / len(huecos)))
    comprueba(buscador.periodo_de_rotacion(len(plan), config) <= config["hours_old"] / 2,
              "muy por debajo de la mitad de las 38 h de `hours_old`, donde saltaría el aviso")

    todos, tanda = buscador.planificar_jooble(activos, config, {})
    comprueba(len(todos) == 7 and len(tanda) == 4, "Jooble: 7 términos únicos y 4 peticiones por tanda")
    compartidos = [t for t in todos if len(t.perfiles) == 2]
    comprueba([t.palabra for t in compartidos] == ["ciberseguridad"], "solo «ciberseguridad» se comparte")
    de_la_consola("reenfocar-titular", "--revertir")


def probar_lineas_base() -> None:
    print("\n[6] Las dos líneas base")
    entorno = {k: v for k, v in os.environ.items() if k != "JOBSPY_DATA_DIR"}

    def medir(*argumentos: str) -> dict:
        proceso = subprocess.run([sys.executable, str(AQUI / "medir.py"), "--json", *argumentos],
                                 capture_output=True, text=True, encoding="utf-8", env=entorno)
        return json.loads(proceso.stdout)

    comprueba(medir() == json.loads((AQUI / "linea_base_mp4.json").read_text(encoding="utf-8")),
              "`medir.py --json` da linea_base_mp4.json: el titular")
    fabrica = medir("--juego", "fabrica", "--muestra", "etiquetadas.json", "--historial", "historial.json")
    comprueba(fabrica == json.loads((AQUI / "linea_base_fabrica.json").read_text(encoding="utf-8")),
              "con los argumentos viejos da linea_base_fabrica.json: lo que vigila titular-2026-10")
    # MP0 se queda como foto: solo la mueve el «3\+ years». Biorce pide 5 años y baja de 5,0; Novanta,
    # 4, y baja de 6,5. Si la diferencia es otra, ha cambiado otra cosa.
    mp0 = json.loads((AQUI / "linea_base_mp0.json").read_text(encoding="utf-8"))["medir"]
    distintos = [u["umbral"] for u, v in zip(fabrica["umbrales"], mp0["umbrales"]) if u != v]
    comprueba(distintos == [5.0, 6.0, 6.5, 7.0, 7.5] and fabrica["umbrales"][2]["avisos"] == 61,
              "respecto a linea_base_mp0.json solo se mueve lo del «3\\+ years» (%s)" % distintos)
    victimas = medir("--juego", "titular-2026-10", "--muestra", "etiquetadas_ciber.json",
                     "--historial", "historial_ciber.json")["historial"]["titulos"]
    comprueba(sorted(medir()["historial"]["titulos"]) == sorted(victimas),
              "titular-ciber no descarta ninguna aplicada o favorita de ciberseguridad de más")


def main() -> int:
    probar_juego()
    probar_titulos()
    probar_practicas()
    probar_descripcion()
    probar_aplicacion()
    probar_consultas()
    probar_lineas_base()
    perfiles.cerrar()
    shutil.rmtree(DATOS, ignore_errors=True)
    print()
    if fallos:
        print("FALLAN %d de %d:" % (len(fallos), hechas))
        for fallo in fallos:
            print("  - " + fallo)
        return 1
    print("TODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
