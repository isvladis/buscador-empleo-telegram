# -*- coding: utf-8 -*-
"""
Pruebas del almacén por perfil en SQLite (MP1, ver perfiles.py).

Lo central es el DIFERENCIAL: el almacen.py de antes de MP1, extraído con `git show`, y el
de ahora reciben las mismas operaciones (corridas, marcas, deshacer, poda, reemplazar,
limpiar) con el reloj fijo, y después de cada paso tienen que dar EXACTAMENTE lo mismo,
orden de la lista incluido. Eso es la regla 4 de CLAUDE.md puesta a prueba con secuencias
que nadie ha pensado a mano.

Ninguna prueba sale a la red: `requests.post` y `buscador.buscar_para_perfiles` son espías.

    PYTHONUTF8=1 python pruebas/test_perfiles.py
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-perfiles-")
for variable in ("JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_ID", "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
    os.environ.pop(variable, None)

import requests  # noqa: E402

import almacen  # noqa: E402
import app  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import etiquetas  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import telegram_botones as tb  # noqa: E402
from fuentes_extra import salud  # noqa: E402

# El último commit con el almacén en JSON. No cambia: es la referencia de la regla 4.
COMMIT_DE_REFERENCIA = "e732641"
SEMILLAS = 14
PASOS = 45
A, B = 1111, 2222

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def carpeta_nueva(nombre: str) -> Path:
    """Todo lo de una prueba a una carpeta limpia: base, poda, salud y config."""
    perfiles.cerrar()
    carpeta = Path(tempfile.mkdtemp(prefix=f"test-perfiles-{nombre}-"))
    os.environ["JOBSPY_DATA_DIR"] = str(carpeta)
    almacen.DIR_BASE = carpeta
    almacen.PODADAS_PATH = carpeta / "descripciones_podadas.json.gz"
    almacen.DESCRIPCIONES_PATH = carpeta / "descripciones_vistas.json"
    salud.SALUD_PATH = carpeta / "salud_fuentes.json"
    config_mod.CONFIG_PATH = carpeta / "config.json"
    return carpeta


class Reloj(datetime):
    """datetime con un `now()` que se mueve a mano: los dos almacenes ven la misma hora."""

    actual = datetime(2026, 10, 30, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.actual


def cargar_referencia(carpeta: Path):
    """El almacen.py del JSON, tal cual estaba, apuntando a su propia carpeta."""
    fuente = subprocess.run(["git", "show", f"{COMMIT_DE_REFERENCIA}:almacen.py"], cwd=RAIZ,
                            capture_output=True, check=True).stdout.decode("utf-8")
    espec = importlib.util.spec_from_loader("almacen_json", loader=None)
    modulo = importlib.util.module_from_spec(espec)
    modulo.__file__ = str(RAIZ / "almacen.py")
    exec(compile(fuente, "almacen_json.py", "exec"), modulo.__dict__)
    modulo.OFERTAS_PATH = carpeta / "ofertas.json"
    modulo.ESTADO_PATH = carpeta / "estado.json"
    modulo.PODADAS_PATH = carpeta / "descripciones_podadas.json.gz"
    modulo.DESCRIPCIONES_PATH = carpeta / "descripciones_vistas.json"
    modulo.datetime = Reloj
    return modulo


TEXTO = ("Buscamos una persona para dar soporte a usuarios en Barcelona, resolver incidencias de "
         "primer nivel, gestionar tickets y mantener el inventario de equipos. Se valora inglés, "
         "conocimientos de redes y ganas de aprender en un equipo con buen ambiente de trabajo. ")
TITULOS = ["Técnico/a Helpdesk N1", "Técnico/a Helpdesk N2", "Técnico de Soporte IT", "Service Desk Agent",
           "Analista SOC Junior N1", "Operador/a NOC", "Técnico de Sistemas Junior"]
EMPRESAS = ["Seidor", "Aubay", "Experis Spain", "NTT Data", "", None]
SITIOS = ["linkedin", "indeed", "jooble", "tecnoempleo", "infojobs"]


def pool_de_anuncios(rng: random.Random) -> list[dict]:
    """Anuncios con muchos parecidos entre sí: la misma vacante en varios portales y fechas."""
    pool = []
    for i in range(36):
        titulo, empresa = rng.choice(TITULOS), rng.choice(EMPRESAS)
        fecha = (datetime(2026, 10, 1) + timedelta(days=rng.randrange(12))).date().isoformat()
        for j in range(rng.randint(1, 3)):
            pool.append({
                "id": f"id-{i}-{j}", "site": rng.choice(SITIOS), "job_url": f"https://x.test/{i}-{j}",
                "job_url_direct": None, "title": titulo if rng.random() < .9 else titulo.upper(),
                # A veces la misma vacante con otro nombre de empresa: solo la junta el texto.
                "company": empresa if rng.random() < .8 else rng.choice(EMPRESAS),
                "location": rng.choice(["Barcelona, Catalonia, Spain", "Madrid, Spain", "Remoto"]),
                "date_posted": rng.choice([fecha, fecha, None]), "job_type": None, "is_remote": False,
                "company_industry": rng.choice([None, "IT Services"]),
                "_texto": (TEXTO * 3 + str(i)) if rng.random() < .7 else (TEXTO + str(i) + str(j)),
                "pais": "España", "experiencia_label": "No especificado", "contrato_label": "No indicado",
                "modalidad_label": "Presencial/Híbrido", "posible_restriccion_geografica": False,
            })
    return pool


def visto_en_corrida(anuncio: dict, rng: random.Random) -> dict:
    """Lo que devuelve buscar() para un anuncio: cambia un poco de una corrida a otra."""
    registro = {k: v for k, v in anuncio.items() if k != "_texto"}
    registro["description"] = anuncio["_texto"] if rng.random() < .85 else None
    registro.update(categoria=rng.choice(["soporte", "ciberseguridad"]), ambito=rng.choice(["local", "remoto"]),
                    busqueda="una búsqueda", categoria_label="Soporte", ambito_label="Barcelona",
                    salario=rng.choice([None, None, "25.000 €"]), distancia_km=rng.choice([None, 1.5]))
    if rng.random() < .6:
        registro["relevancia"] = rng.choice([1.5, 5.0, 7.25])
    if rng.random() < .3:
        registro["motivos_relevancia"] = ["junior o nivel 1 (+2,0)"]
    if rng.random() < .5:
        registro.update(candidatos=rng.choice([None, 12, 40]), candidatos_tope=rng.choice([None, False]),
                        publicada_en=rng.choice([None, "2026-10-01T10:00:00"]))
    return registro


def igualar_estado(estado: dict) -> dict:
    # La única diferencia admitida: el JSON sin fichero no trae la clave `ultima_busqueda`.
    return {**estado, "ultima_busqueda": estado.get("ultima_busqueda")}


def un_paso(rng, pool, ref, nuevo, urls_vistas, registro: list) -> None:
    """Una operación al azar, igual en los dos almacenes. Comparte el resultado o da error."""
    tirada = rng.random()
    if tirada < .4:
        nuevas = [visto_en_corrida(a, rng) for a in rng.sample(pool, rng.randint(1, 14))]
        reemplazar = rng.random() < .06
        registro.append(f"fusionar {len(nuevas)} reemplazar={reemplazar}")
        r1 = ref.fusionar_ofertas(copy.deepcopy(nuevas), reemplazar=reemplazar)
        r2 = nuevo.fusionar_ofertas(copy.deepcopy(nuevas), reemplazar=reemplazar)
        assert r1 == r2, ("resumen", r1, r2)
        urls_vistas.update(o["job_url"] for o in nuevas)
    elif tirada < .75 and urls_vistas:
        url = rng.choice(sorted(urls_vistas))
        accion = rng.choice(["favorito", "oculta", "oculta", "aplicada"])
        datos = {"title": "Un título", "company": rng.choice(["Una empresa", None])}
        if rng.random() < .5:
            registro.append(f"marcar {url} {accion}")
            e1, e2 = ref.marcar(url, accion, datos), nuevo.marcar(url, accion, datos)
        else:
            extra = rng.choice([None, {"origen": "telegram"}, {"origen": "telegram", "motivo": "la empresa"}])
            registro.append(f"fijar {url} {accion}")
            e1, e2 = ref.fijar_marca(url, accion, datos, extra), nuevo.fijar_marca(url, accion, datos, extra)
        assert igualar_estado(e1) == igualar_estado(e2), "marca"
    elif tirada < .85 and urls_vistas:
        url = rng.choice(sorted(urls_vistas))
        registro.append(f"quitar {url}")
        assert igualar_estado(ref.quitar_marcas(url)) == igualar_estado(nuevo.quitar_marcas(url)), "quitar"
    elif tirada < .92:
        Reloj.actual += timedelta(days=rng.choice([1, 3, 12, 33]))
        registro.append(f"reloj {Reloj.actual}")
    else:
        # Lo que hace la web al limpiar, y a veces algo peor: editar un registro de modo que
        # pase a ser duplicado de otro sin pasar por una fusión. El punto fijo del dedupe por
        # bloques se rompe ahí, y es justo lo que `dedupe_pendiente` tiene que cubrir.
        lista = copy.deepcopy(ref.cargar_ofertas())
        estado = ref.cargar_estado()
        intocables = set(estado["favoritas"]) | set(estado["ocultas"]) | set(estado["aplicadas"])
        lista = [o for o in lista if o["job_url"] in intocables or rng.random() > .1]
        if len(lista) > 1 and rng.random() < .5:
            uno, otro = rng.sample(lista, 2)
            uno.update(title=otro["title"], company=otro["company"], date_posted=otro.get("date_posted"))
            registro.append(f"editar {uno['job_url']} como {otro['job_url']}")
        registro.append(f"guardar {len(lista)}")
        ref.guardar_ofertas(copy.deepcopy(lista))
        nuevo.guardar_ofertas(copy.deepcopy(lista))


def comparar(ref, nuevo) -> None:
    o1, o2 = ref.cargar_ofertas(), nuevo.cargar_ofertas()
    assert [o.get("job_url") for o in o1] == [o.get("job_url") for o in o2], "orden de la lista"
    assert o1 == o2, "contenido de las ofertas"
    assert igualar_estado(ref.cargar_estado()) == igualar_estado(nuevo.cargar_estado()), "estado"
    assert ref.urls_excluidas() == nuevo.urls_excluidas(), "urls_excluidas"


def archivo_podadas(camino: Path) -> dict:
    return json.loads(gzip.decompress(camino.read_bytes())) if camino.exists() else {}


def una_secuencia(semilla: int, pasos: int = PASOS) -> str | None:
    """Devuelve None si los dos almacenes han ido a la par; si no, qué paso los separó."""
    rng = random.Random(semilla)
    Reloj.actual = datetime(2026, 10, 30, 12, 0, 0)
    carpeta_ref = Path(tempfile.mkdtemp(prefix="test-perfiles-ref-"))
    ref = cargar_referencia(carpeta_ref)
    nuevo = almacen
    carpeta_nueva("dif")
    pool, urls_vistas, registro = pool_de_anuncios(rng), set(), []
    for _ in range(pasos):
        try:
            un_paso(rng, pool, ref, nuevo, urls_vistas, registro)
            comparar(ref, nuevo)
        except AssertionError as e:
            return f"semilla {semilla}, tras «{registro[-1] if registro else '?'}»: {str(e)[:200]}"
    if archivo_podadas(ref.PODADAS_PATH) != archivo_podadas(nuevo.PODADAS_PATH):
        return f"semilla {semilla}: el archivo de descripciones podadas no coincide"
    return None


def con_reloj_fijo(funcion):
    previos = almacen.datetime, perfiles.datetime
    almacen.datetime = perfiles.datetime = Reloj
    try:
        return funcion()
    finally:
        almacen.datetime, perfiles.datetime = previos


def prueba_diferencial() -> None:
    print("\n[1] Diferencial: el almacén JSON de antes y el de SQLite, operación a operación")
    comprueba((RAIZ / ".git").exists(), f"hay git para extraer la referencia ({COMMIT_DE_REFERENCIA})")
    error = next(filter(None, (con_reloj_fijo(lambda s=s: una_secuencia(s)) for s in range(SEMILLAS))), None)
    comprueba(error is None, f"{SEMILLAS} secuencias de {PASOS} pasos sin ninguna diferencia"
              + (f": {error}" if error else ""))

    # Controles negativos: cada uno quita una pieza y el diferencial tiene que enterarse. Si
    # alguno pasara, esa pieza no estaría demostrada, por muy verde que saliera lo de arriba.
    def rompe(modulo, nombre, sustituto):
        original = getattr(modulo, nombre)
        setattr(modulo, nombre, sustituto)
        try:
            return next(filter(None, (con_reloj_fijo(lambda s=s: una_secuencia(s)) for s in range(SEMILLAS))), None)
        finally:
            setattr(modulo, nombre, original)

    original_pos = perfiles.posiciones
    comprueba(rompe(perfiles, "dedupe_pendiente", lambda chat: False),
              "control: sin la pasada completa tras editar un registro, se nota")
    comprueba(rompe(perfiles, "posiciones", lambda chat: {
        u: (0, int(hashlib.sha1(u.encode()).hexdigest()[:6], 16)) for u in original_pos(chat)}),
        "control: sin el orden de la lista, se nota")
    comprueba(rompe(almacen, "_competencia_original", lambda previa, nueva: {}),
              "control: sin conservar los campos del descubrimiento, se nota")
    comprueba(rompe(almacen, "_es_republicacion", lambda oferta, indice: False),
              "control: sin reconocer republicaciones, se nota")
    comprueba(rompe(almacen, "podar_descripciones_ocultas", lambda chat: 0),
              "control: sin la poda, se nota")


def oferta(n, **campos) -> dict:
    return {"job_url": f"https://x.test/o{n}", "title": f"Técnico {n}", "company": f"Empresa {n}",
            "location": "Barcelona, Spain", "site": "linkedin", "description": TEXTO + str(n),
            "categoria": "soporte", "ambito": "local", "busqueda": "b", **campos}


def prueba_migracion() -> None:
    print("\n[2] Migración desde los JSON")
    carpeta = carpeta_nueva("migra")
    ofertas = [
        {**oferta(1), "visto_por_primera_vez": "2026-10-01T10:00:00", "relevancia": 7.25,
         "motivos_relevancia": ["uno", "dos"], "candidatos": 3, "candidatos_tope": False,
         "publicada_en": "2026-10-01T09:00:00", "categoria_label": "Soporte", "ambito_label": "BCN",
         "distancia_km": 1.5, "fuentes_adicionales": ["indeed"]},
        {**oferta(2), "visto_por_primera_vez": "2026-10-02T10:00:00", "description": None,
         "descripcion_podada": True, "candidatos": None, "distancia_km": None},
        {**oferta(3), "visto_por_primera_vez": "2026-10-03T10:00:00", "description": None,
         "visto_por_ultima_vez": "2026-10-04T10:00:00", "relevancia": None},
    ]
    estado = {
        "favoritas": {ofertas[0]["job_url"]: {"fecha": "2026-10-05T10:00:00", "titulo": "T", "empresa": None}},
        "ocultas": {ofertas[1]["job_url"]: {"fecha": "2026-10-06T10:00:00", "titulo": "T", "empresa": "E",
                                            "origen": "telegram", "motivo": "la empresa", "raro": [1, 2]},
                    "https://x.test/sin-oferta": {"fecha": None, "titulo": None, "empresa": None}},
        "aplicadas": {},
        "ultima_busqueda": "2026-10-29T23:00:00",
    }
    avisos = {tb.clave_de(ofertas[0]["job_url"]): {
        "url": ofertas[0]["job_url"], "titulo": "T", "empresa": None, "relevancia": 7.25, "fuente": "linkedin",
        "avisada": "2026-10-20T10:00:00", "respuesta": "oculta", "motivo": "la empresa",
        "respondida": "2026-10-21T10:00:00"}}
    for nombre, contenido in (("ofertas.json", ofertas), ("estado.json", estado), ("telegram_avisos.json", avisos)):
        (carpeta / nombre).write_text(json.dumps(contenido, ensure_ascii=False), encoding="utf-8")
    huellas = {n: (carpeta / n).read_bytes() for n in ("ofertas.json", "estado.json", "telegram_avisos.json")}

    resultado = perfiles.migrar_desde_json()
    comprueba(perfiles.meta_get("migrado") is not None, "la primera conexión migra sola")
    comprueba(almacen.cargar_ofertas() == ofertas, "cargar_ofertas() da lo mismo, campo a campo")
    comprueba(igualar_estado(almacen.cargar_estado()) == estado, "cargar_estado() da el mismo diccionario")
    comprueba(perfiles.avisos(perfiles.titular()) == avisos, "el índice de avisos, igual")
    comprueba(all((carpeta / n).read_bytes() == b for n, b in huellas.items()), "los originales no se tocan")
    comprueba(all((carpeta / (n + ".pre-mp1")).read_bytes() == b for n, b in huellas.items()),
              "y quedan copiados a *.pre-mp1")
    perfil = perfiles.obtener(perfiles.titular())
    comprueba(perfil["estado"] == "activo" and perfil["datos"] == {"v": 1, "reglas": "titular-2026-10", "config": "global"},
              "el perfil del titular, activo y con su config global")
    fila = perfiles.conexion().execute("SELECT vacante, tipo FROM avisos").fetchone()
    comprueba(fila == ("t|@barcelona spain", "aviso"),
              "el aviso lleva su `vacante`: sin empresa, con la ubicación de la oferta")

    def filas():
        c = perfiles.conexion()
        return {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in
                ("ofertas", "descripciones", "ofertas_perfil", "marcas", "avisos", "perfiles")}

    antes = filas()
    resultado = perfiles.migrar_desde_json()
    comprueba(resultado["ya_estaba"] and not resultado["migrado_ahora"] and filas() == antes,
              "dos veces: mismas filas")
    informe = perfiles.verificar()
    comprueba(not any(informe[k] for k in ("marcas_distintas", "ofertas_distintas", "ofertas_que_faltan",
                                           "avisos_distintos"))
              and informe["orden_igual"] and informe["ultima_busqueda_igual"]
              and informe["bloques_desfasados"] == 0, "`verificar` no encuentra diferencias")
    comprueba(perfiles.main(["verificar"]) == 0, "   y el comando sale con 0")
    almacen.marcar(ofertas[2]["job_url"], "favorito")
    comprueba(perfiles.main(["verificar"]) == 1, "   control: con una marca nueva, `verificar` lo ve")
    almacen.marcar(ofertas[2]["job_url"], "favorito")

    print("\n    Exportar es la vuelta atrás")
    salida = Path(tempfile.mkdtemp(prefix="test-perfiles-exp-"))
    perfiles.exportar(perfiles.titular(), salida)
    leido = {n: json.loads((salida / n).read_text(encoding="utf-8"))
             for n in ("ofertas.json", "estado.json", "telegram_avisos.json")}
    comprueba(leido["ofertas.json"] == ofertas and leido["estado.json"] == estado
              and leido["telegram_avisos.json"] == avisos, "exportar devuelve JSON equivalentes a los migrados")
    comprueba(perfiles.main(["exportar", "titular", str(salida)]) == 0, "   y `exportar titular <carpeta>` funciona")
    ref = cargar_referencia(salida)
    comprueba(ref.cargar_ofertas() == ofertas and ref.cargar_estado() == estado,
              "el código anterior a MP1 los lee tal cual")

    print("\n    Lo que puede salir mal al migrar")
    carpeta = carpeta_nueva("migra-mal")
    (carpeta / "ofertas.json").write_text("[{esto no es json", encoding="utf-8")
    try:
        perfiles.conexion()
        lanzada = None
    except perfiles.BaseNoDisponible as e:
        lanzada = str(e)
    comprueba(lanzada is not None, "un JSON ilegible para: no se migra vacío ni se apunta como migrado")
    comprueba((carpeta / "ofertas.json").read_text(encoding="utf-8").startswith("[{esto"), "   y el fichero sigue ahí")
    comprueba(any(i.get("tipo") == "sistema" for i in salud.incidencias_pendientes()), "   y sale incidencia")
    carpeta = carpeta_nueva("migra-prefijo")
    desordenadas = [oferta(1), oferta(2), oferta(3)]
    (carpeta / "ofertas.json").write_text(json.dumps(desordenadas), encoding="utf-8")
    (carpeta / "estado.json").write_text(json.dumps({"favoritas": {}, "aplicadas": {}, "ocultas": {
        desordenadas[2]["job_url"]: {"fecha": "2026-10-06T10:00:00", "titulo": "T", "empresa": "E"}}}), encoding="utf-8")
    comprueba([o["job_url"] for o in almacen.cargar_ofertas()] == [o["job_url"] for o in desordenadas],
              "una oferta marcada después de la última corrida conserva su sitio en la lista")


def prueba_marcas() -> None:
    print("\n[3] Marcas")
    carpeta_nueva("marcas")
    url = "https://x.test/m"
    almacen.marcar(url, "favorito", {"title": "T", "company": "E"})
    comprueba(url in almacen.cargar_estado()["favoritas"], "marcar pone la marca")
    almacen.marcar(url, "favorito")
    comprueba(url not in almacen.cargar_estado()["favoritas"], "marcar otra vez la quita: es un interruptor")
    almacen.fijar_marca(url, "oculta", {"title": "T"}, {"origen": "telegram"})
    almacen.fijar_marca(url, "oculta", {}, {"motivo": "la empresa"})
    info = almacen.cargar_estado()["ocultas"][url]
    comprueba(info["origen"] == "telegram" and info["motivo"] == "la empresa" and info["titulo"] == "T",
              "fijar dos veces no desmarca y conserva lo anterior")
    almacen.fijar_marca(url, "aplicada")
    estado = almacen.cargar_estado()
    comprueba(url in estado["aplicadas"] and url not in estado["ocultas"], "fijar quita las otras: exclusión mutua")
    almacen.marcar(url, "oculta")
    estado = almacen.cargar_estado()
    comprueba(url in estado["aplicadas"] and url in estado["ocultas"],
              "marcar sí deja aplicada + oculta, como hoy la web: por eso la clave lleva la acción")
    comprueba(url in almacen.urls_excluidas(), "las ocultas y aplicadas quedan excluidas")
    almacen.quitar_marcas(url)
    estado = almacen.cargar_estado()
    comprueba(not any(url in estado[c] for c in ("favoritas", "ocultas", "aplicadas")), "deshacer las quita todas")
    for malo in (lambda: almacen.marcar(url, "nada"), lambda: almacen.fijar_marca(url, "favorita")):
        try:
            malo()
            comprueba(False, "una acción desconocida da error")
        except ValueError:
            comprueba(True, "una acción desconocida da error")


def prueba_avisos() -> None:
    print("\n[4] Avisos")
    carpeta_nueva("avisos")
    clave = perfiles.titular()
    hace_100_dias = (datetime.now() - timedelta(days=100)).isoformat(timespec="seconds")
    perfiles.registrar_aviso(clave, "k", "https://x.test/a", titulo="T", avisada=hace_100_dias)
    perfiles.anotar_respuesta(clave, "k", "oculta", "la empresa")
    perfiles.registrar_aviso(clave, "k", "https://x.test/a", titulo="T2")
    aviso = perfiles.aviso(clave, "k")
    comprueba(aviso["avisada"] == hace_100_dias and aviso["respuesta"] == "oculta" and aviso["titulo"] == "T2",
              "volver a avisar actualiza, pero conserva la fecha y la respuesta")
    perfiles.anotar_respuesta(clave, "k", "oculta")
    comprueba(perfiles.aviso(clave, "k").get("motivo") == "la empresa", "🙈 sin motivo conserva el que había")
    perfiles.anotar_respuesta(clave, "k", "favorito")
    comprueba("motivo" not in perfiles.aviso(clave, "k"), "⭐ quita el motivo")
    perfiles.anotar_respuesta(clave, "k", None)
    aviso = perfiles.aviso(clave, "k")
    comprueba(not any(c in aviso for c in ("respuesta", "motivo", "respondida")), "deshacer borra la respuesta")
    perfiles.anotar_respuesta(clave, "no-existe", "oculta")
    comprueba(perfiles.aviso(clave, "no-existe") is None, "una clave que no existe no crea nada")
    comprueba(perfiles.podar_avisos(90) == 1 and perfiles.aviso(clave, "k") is None, "se poda a los 90 días")


def prueba_aislamiento() -> None:
    print("\n[5] Aislamiento: lo de A no aparece para B, ni se deduce (regla 5)")
    carpeta_nueva("aisla")
    solo_a, compartida = oferta(1), oferta(2)
    almacen.fusionar_ofertas([solo_a, compartida], chat_id=A)
    conn = perfiles.conexion()
    comprueba(conn.execute("SELECT COUNT(*) FROM ofertas WHERE url = ?", (solo_a["job_url"],)).fetchone()[0] == 1,
              "control: la oferta de A SÍ está en la tabla compartida")
    comprueba(almacen.cargar_ofertas(chat_id=B) == [], "B no ve ninguna oferta de A")
    comprueba(almacen.cargar_ofertas() == [] and almacen.cargar_estado()["favoritas"] == {},
              "y None es el titular, no «todos»: tampoco las ve")
    comprueba(almacen.descripcion_de(solo_a["job_url"], chat_id=B) is None
              and almacen.descripcion_de(solo_a["job_url"], chat_id=A), "ni su descripción (con A sí sale)")
    comprueba(almacen.ofertas_por_url([solo_a["job_url"]], chat_id=B) == {}, "ni pidiéndola por su enlace")
    comprueba(perfiles.ofertas_en_bloques(B, {"e:empresa 1", "t:tecnico 1"}) == [], "ni por su bloque del dedupe")
    comprueba(perfiles.ofertas_recientes(B, "2000-01-01") == [], "ni entre las recientes")
    comprueba(almacen.descripciones_en_base(chat_id=B) == {}, "ni entre las descripciones conocidas")

    resumen = almacen.fusionar_ofertas([compartida], chat_id=B)
    comprueba(resumen["nuevas"] == 1 and [o["job_url"] for o in resumen["ofertas_nuevas"]] == [compartida["job_url"]],
              "para B una oferta que A ya tiene es nueva: no se puede deducir que otro la tenga")
    comprueba(conn.execute("SELECT COUNT(*) FROM ofertas WHERE url = ?", (compartida["job_url"],)).fetchone()[0] == 1,
              "y se guarda UNA sola vez")
    comprueba(len(almacen.cargar_ofertas(chat_id=A)) == 2 and len(almacen.cargar_ofertas(chat_id=B)) == 1,
              "cada uno ve lo suyo")

    almacen.fijar_marca(compartida["job_url"], "oculta", chat_id=A)
    comprueba(almacen.urls_excluidas(chat_id=B) == set() and almacen.cargar_estado(chat_id=B)["ocultas"] == {},
              "lo que A oculta no se oculta a B")
    almacen.fijar_marca(solo_a["job_url"], "favorito", chat_id=B)
    comprueba(almacen.cargar_estado(chat_id=A)["favoritas"] == {}, "lo que B marca no aparece en A")
    perfiles.registrar_aviso(A, "k", solo_a["job_url"], titulo="De A")
    comprueba(perfiles.aviso(B, "k") is None and perfiles.avisos(B) == {}, "los avisos de A no aparecen para B")
    perfiles.anotar_respuesta(B, "k", "oculta")
    comprueba("respuesta" not in perfiles.aviso(A, "k"), "y B no puede responder a un aviso de A")
    perfiles.registrar_aviso(B, "k", compartida["job_url"], titulo="De B")
    comprueba(perfiles.aviso(A, "k")["titulo"] == "De A", "la misma clave en dos chats son dos avisos")
    perfiles.meta_set(f"ultima_busqueda:{B}", "2000-01-01T00:00:00")
    almacen.fusionar_ofertas([oferta(9)], chat_id=A)
    comprueba(almacen.cargar_estado(chat_id=B)["ultima_busqueda"] == "2000-01-01T00:00:00"
              and almacen.cargar_estado(chat_id=A)["ultima_busqueda"] > "2026",
              "`ultima_busqueda` es de cada perfil")


def prueba_reglas_d1() -> None:
    print("\n[6] Las reglas de la oferta compartida (D1)")
    carpeta_nueva("d1")
    conn = perfiles.conexion()

    def filas(url):
        return conn.execute("SELECT COUNT(*) FROM ofertas WHERE url = ?", (url,)).fetchone()[0]

    print("    Dedupe: quita el enlace, no la oferta")
    hoy = "2026-10-29"
    la_mala = oferta(1, site="jooble", company="Mismo", title="Técnico Soporte", date_posted=hoy, description=None)
    la_buena = oferta(2, site="linkedin", company="Mismo", title="Técnico Soporte", date_posted=hoy)
    almacen.fusionar_ofertas([la_mala], chat_id=B)
    resumen = almacen.fusionar_ofertas([la_mala, la_buena], chat_id=A)
    comprueba(resumen["duplicados_fusionados"] == 1, "A fusiona los dos anuncios")
    comprueba([o["job_url"] for o in almacen.cargar_ofertas(chat_id=A)] == [la_buena["job_url"]],
              "A se queda con la mejor")
    comprueba(filas(la_mala["job_url"]) == 1 and len(almacen.cargar_ofertas(chat_id=B)) == 1,
              "la otra sigue existiendo para B: se quitó el enlace, no la oferta")
    comprueba(almacen.cargar_ofertas(chat_id=A)[0].get("fuentes_adicionales") == ["jooble"]
              and "fuentes_adicionales" not in almacen.cargar_ofertas(chat_id=B)[0],
              "`fuentes_adicionales` queda en A: es el resultado de SU dedupe")

    print("    Una oferta se borra solo cuando nadie la enlaza")
    comprueba(perfiles.quitar_enlace(B, la_mala["job_url"]) and filas(la_mala["job_url"]) == 0,
              "al quitar el último enlace, la oferta se va con su descripción")
    almacen.fusionar_ofertas([oferta(3)], chat_id=A)
    almacen.fusionar_ofertas([oferta(3)], chat_id=B)
    perfiles.quitar_enlace(A, oferta(3)["job_url"])
    comprueba(filas(oferta(3)["job_url"]) == 1 and len(perfiles.descripciones_de(B)) == 1,
              "control: si otro la enlaza, se queda")

    print("    La poda: solo si TODOS los que la enlazan la tienen oculta hace tiempo")
    url = oferta(4)["job_url"]
    viejo = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    reciente = datetime.now().isoformat(timespec="seconds")
    limite = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    almacen.fusionar_ofertas([oferta(4)], chat_id=A)
    almacen.fusionar_ofertas([oferta(4)], chat_id=B)

    def ocultar(chat, fecha):
        perfiles.fijar_marca(chat, url, "oculta")
        conn.execute("UPDATE marcas SET fecha = ? WHERE chat_id = ? AND url = ?", (fecha, chat, url))

    ocultar(A, viejo)
    comprueba(url not in perfiles.podables(A, limite), "A la ocultó hace 40 días y B no: no se poda")
    ocultar(B, reciente)
    comprueba(url not in perfiles.podables(A, limite), "B la ocultó ayer: tampoco")
    ocultar(B, viejo)
    comprueba(url in perfiles.podables(A, limite), "los dos hace más de 30 días: ahora sí")
    perfiles.fijar_marca(B, url, "favorito")
    comprueba(url not in perfiles.podables(A, limite), "si B la pasa a favorita, se conserva")
    perfiles.quitar_marcas(B, url)
    perfiles.quitar_enlace(B, url)
    comprueba(url in perfiles.podables(A, limite), "control: con A solo en la oferta, se poda")
    perfiles.podar(A, [url])
    registro = almacen.ofertas_por_url([url], chat_id=A)[url]
    comprueba(registro["description"] is None and registro["descripcion_podada"] is True
              and filas(url) == 1, "podar quita el texto y deja el registro")

    print("    Actualizar la oferta la actualiza para todos; el juicio, no")
    url = oferta(5)["job_url"]
    almacen.fusionar_ofertas([oferta(5, salario="20.000 €", candidatos=30, relevancia=7.0)], chat_id=A)
    almacen.fusionar_ofertas([oferta(5, salario="20.000 €", relevancia=2.0)], chat_id=B)
    almacen.fusionar_ofertas([oferta(5, salario="30.000 €", candidatos=None, relevancia=7.5)], chat_id=A)
    de_a, de_b = (almacen.ofertas_por_url([url], chat_id=c)[url] for c in (A, B))
    comprueba(de_b["salario"] == "30.000 €", "el salario nuevo le llega a B")
    comprueba(de_a["relevancia"] == 7.5 and de_b["relevancia"] == 2.0, "la puntuación de cada uno no se toca")
    comprueba(de_a["candidatos"] == 30 and de_b["candidatos"] == 30,
              "el dato del descubrimiento se conserva, aunque B no lo trajera")
    primera_a, primera_b = de_a["visto_por_primera_vez"], de_b["visto_por_primera_vez"]
    comprueba(primera_a <= primera_b, "cada perfil guarda cuándo la vio él")

    print("    Toda lectura exige chat_id")
    leen = ("marcas", "urls_excluidas", "ofertas_de", "iterar_ofertas", "ofertas_por_url", "ofertas_en_bloques",
            "ofertas_recientes", "posiciones", "descripciones_de", "descripcion_de", "contar_ofertas", "avisos",
            "aviso", "podables", "ultima_busqueda", "firma_ofertas", "exportar")
    sin_chat = []
    for nombre in leen:
        parametros = list(inspect.signature(getattr(perfiles, nombre)).parameters.values())
        relleno = ["x"] * (sum(p.default is p.empty for p in parametros) - 1)
        try:
            getattr(perfiles, nombre)(None, *relleno)
            sin_chat.append(nombre)
        except TypeError:
            pass
    comprueba(not sin_chat, f"con None, las {len(leen)} funciones de lectura dan error ({sin_chat})")

    def infractoras(modulo_funciones):
        """Públicas que tocan ofertas sin un `chat_id` obligatorio."""
        fuera = []
        for nombre, funcion in modulo_funciones:
            if nombre.startswith("_") or nombre in ("main", "migrar_desde_json", "verificar"):
                continue
            if not re.search(r"\bofertas\b|ofertas_perfil|descripciones|_LEER_OFERTA", inspect.getsource(funcion)):
                continue
            parametro = inspect.signature(funcion).parameters.get("chat_id")
            if parametro is None or parametro.default is not parametro.empty:
                fuera.append(nombre)
        return fuera

    propias = [(n, f) for n, f in inspect.getmembers(perfiles, inspect.isfunction) if f.__module__ == "perfiles"]
    comprueba(infractoras(propias) == [], "ninguna función pública lee ofertas sin chat_id obligatorio")

    def todas_las_ofertas():
        return perfiles.conexion().execute("SELECT * FROM ofertas").fetchall()

    comprueba(infractoras(propias + [("todas_las_ofertas", todas_las_ofertas)]) == ["todas_las_ofertas"],
              "control: una función así la pillaría esta prueba")

    print("    Lo que no es de la oferta no va a la fila compartida")
    datos = json.loads(conn.execute("SELECT datos FROM ofertas WHERE url = ?", (url,)).fetchone()[0])
    comprueba(not ({"relevancia", "categoria", "ambito", "busqueda", "distancia_km", "fuentes_adicionales",
                    "motivos_relevancia", "visto_por_primera_vez"} & set(datos)),
              "ningún campo del perfil queda en `ofertas.datos`")


def prueba_concurrencia() -> None:
    print("\n[7] Concurrencia: 4 hilos marcando a la vez")
    carpeta_nueva("hilos")
    perfiles.conexion()
    errores: list[str] = []
    por_hilo = 40

    def trabaja(hilo: int) -> None:
        try:
            for i in range(por_hilo):
                almacen.marcar(f"https://x.test/{hilo}-{i}", "oculta", {"title": "T"})
                if i % 5 == 0:
                    almacen.cargar_estado()
        except Exception as e:  # noqa: BLE001 - se quiere contar cualquier fallo, sea cual sea
            errores.append(repr(e))
        finally:
            perfiles.cerrar()

    hilos = [threading.Thread(target=trabaja, args=(h,)) for h in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    comprueba(not errores, f"ningún `database is locked` ni otro error {errores[:1]}")
    comprueba(len(almacen.cargar_estado()["ocultas"]) == 4 * por_hilo, f"no se pierde ninguna de las {4 * por_hilo}")


def lanza(funcion) -> str | None:
    try:
        funcion()
    except perfiles.BaseNoDisponible as e:
        return str(e)
    return None


def prueba_corrupcion() -> None:
    print("\n[8] Base corrupta o ausente: se aparta, se avisa y se para")
    carpeta = carpeta_nueva("corrupta")
    almacen.fusionar_ofertas([oferta(1)])
    perfiles.cerrar()
    perfiles._preparadas.clear()
    comprueba(lanza(perfiles.conexion) is None and not list(carpeta.glob("*.corrupta-*")),
              "control: una base sana no se aparta")
    perfiles.cerrar()
    perfiles._preparadas.clear()
    base = carpeta / "perfiles.db"
    for sufijo in ("-wal", "-shm"):
        (carpeta / ("perfiles.db" + sufijo)).unlink(missing_ok=True)
    basura = b"esto no es una base de datos " * 400
    base.write_bytes(basura)
    motivo = lanza(perfiles.conexion)
    apartadas = [p for p in carpeta.glob("perfiles.db.corrupta-*") if not p.name.endswith(("-wal", "-shm"))]
    comprueba(motivo is not None, "abrirla da BaseNoDisponible")
    comprueba(len(apartadas) == 1 and apartadas[0].read_bytes() == basura, "se aparta entera, sin perder un byte")
    comprueba(not base.exists(), "y no se crea una vacía en su lugar")
    comprueba(lanza(perfiles.conexion) is not None and not base.exists(), "ni al segundo intento")
    incidencias = [i for i in salud.incidencias_pendientes() if i.get("tipo") == "sistema"]
    comprueba(len(incidencias) == 1, "queda UNA incidencia en la cola, aunque se reintente")
    comprueba("PARADO" in notificaciones._mensaje_incidencias(incidencias), "y el aviso dice que está parado")

    print("\n    Una página dañada en una base que parecía buena")
    carpeta = carpeta_nueva("danada")
    almacen.fusionar_ofertas([oferta(n) for n in range(1, 400)])
    perfiles.conexion().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    perfiles.cerrar()
    perfiles._preparadas.clear()
    base = carpeta / "perfiles.db"
    contenido = bytearray(base.read_bytes())
    contenido[8192:len(contenido) // 2] = b"\xAB" * (len(contenido) // 2 - 8192)
    base.write_bytes(contenido)
    comprueba(lanza(perfiles.conexion) is not None and not base.exists()
              and bool(list(carpeta.glob("perfiles.db.corrupta-*"))), "quick_check la detecta al abrir y se aparta")

    print("\n    Falta la base pero consta que existió")
    carpeta = carpeta_nueva("ausente")
    (carpeta / "ofertas.json").write_text(json.dumps([oferta(1)]), encoding="utf-8")
    comprueba(len(almacen.cargar_ofertas()) == 1, "(primera vez: migra)")
    perfiles.cerrar()
    perfiles._preparadas.clear()
    for nombre in ("perfiles.db", "perfiles.db-wal", "perfiles.db-shm"):
        (carpeta / nombre).unlink(missing_ok=True)
    comprueba(lanza(perfiles.conexion) is not None and not (carpeta / "perfiles.db").exists(),
              "con *.pre-mp1 y sin base, no se vuelve a migrar en silencio")

    # MP10: sin *.pre-mp1 (la limpieza los propone) pero con copias diarias, tampoco se crea una vacía.
    carpeta = carpeta_nueva("ausente-con-copias")
    almacen.fusionar_ofertas([oferta(1)])
    perfiles.cerrar()
    perfiles._preparadas.clear()
    for nombre in ("perfiles.db", "perfiles.db-wal", "perfiles.db-shm"):
        (carpeta / nombre).unlink(missing_ok=True)
    comprueba(not list(carpeta.glob("*.pre-mp1")), "(sin *.pre-mp1 en esta carpeta)")
    (carpeta / "copias").mkdir()
    (carpeta / "copias" / "copia-20261106_031000.tar.gz").write_bytes(b"x")
    comprueba(lanza(perfiles.conexion) is not None and not (carpeta / "perfiles.db").exists(),
              "con copias en copias/ y sin base, no se crea una vacía")
    perfiles.cerrar()
    perfiles._preparadas.clear()
    (carpeta / "copias" / "copia-20261106_031000.tar.gz").unlink()
    comprueba(lanza(perfiles.conexion) is None and (carpeta / "perfiles.db").exists(),
              "control: sin copias ni *.pre-mp1, una carpeta nueva sí crea su base")
    perfiles.cerrar()
    perfiles._preparadas.clear()

    print("\n    Un fallo que no es corrupción no aparta nada")
    error = sqlite3.OperationalError("database is locked")
    comprueba(not perfiles._es_corrupcion(error), "`database is locked` no es corrupción")

    print("\n    Con la base caída no se consulta ningún portal")
    carpeta = carpeta_nueva("parada")
    (carpeta / "perfiles.db.corrupta-20261030_120000").write_bytes(b"x")
    llamadas: list[str] = []
    buscar_original, enviar_original = buscador.buscar_para_perfiles, notificaciones.notificar_incidencias_fuentes
    buscador.buscar_para_perfiles = lambda *a, **k: llamadas.append("portal")
    notificaciones.notificar_incidencias_fuentes = lambda c, i: llamadas.append("aviso") or True
    try:
        app._ejecutar_busqueda(config_mod.cargar_config(), False, automatica=True)
    finally:
        buscador.buscar_para_perfiles, notificaciones.notificar_incidencias_fuentes = buscar_original, enviar_original
    comprueba("portal" not in llamadas, "la corrida falla antes de buscar")
    comprueba("aviso" in llamadas, "y el aviso sale igualmente, aunque no llegue al final de la corrida")
    comprueba(app.BUSQUEDA["error"] and "perfiles.db" in app.BUSQUEDA["error"], "y la web ve el error")
    app.BUSQUEDA.update(activa=False, error=None)


def prueba_perfiles_y_esquema() -> None:
    print("\n[9] Perfiles, identidad del titular y esquema")
    carpeta_nueva("api")
    comprueba(perfiles.obtener(A) is None, "un perfil que no existe es None")
    comprueba(perfiles.guardar(A, {"v": 1}, "pendiente") == 1, "guardar lo crea en la versión 1")
    comprueba(perfiles.guardar(A, {"v": 2}) == 2 and perfiles.obtener(A)["datos"] == {"v": 2},
              "guardar otra vez sube la versión")
    comprueba(perfiles.obtener(A)["estado"] == "pendiente", "   y sin decir estado, conserva el que tenía")
    perfiles.guardar(B, {"v": 1}, "activo")
    comprueba([p["chat_id"] for p in perfiles.listar(estado="activo")] == [B]
              and len(perfiles.listar()) == 2, "listar filtra por estado")
    try:
        perfiles.guardar(A, {}, "inventado")
        comprueba(False, "un estado que no existe da error")
    except ValueError:
        comprueba(True, "un estado que no existe da error")
    perfiles.meta_set("una", "1")
    perfiles.meta_set("otra", "2")
    perfiles.meta_set("otra", None)
    comprueba(perfiles.meta_get("una") == "1" and perfiles.meta_get("otra", "x") == "x", "meta_get y meta_set")
    tablas = {f[0] for f in perfiles.conexion().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    comprueba({"conversacion", "consultas", "invitaciones", "pendientes"} <= tablas,
              "las tablas de las fases siguientes ya existen")
    comprueba(perfiles.meta_get("version_esquema") == str(perfiles.MIGRACIONES[-1][0]), "meta.version_esquema")
    pragmas = perfiles.conexion()
    comprueba(pragmas.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
              and pragmas.execute("PRAGMA cache_size").fetchone()[0] == -512
              and pragmas.execute("PRAGMA synchronous").fetchone()[0] == 1
              and pragmas.execute("PRAGMA temp_store").fetchone()[0] == 1, "los PRAGMA del D2")
    planes = " ".join(f[3] for f in pragmas.execute(
        "EXPLAIN QUERY PLAN " + perfiles._LEER_OFERTA.format(descripcion=perfiles._CON_TEXTO, desde=perfiles._DESDE_EL_ENLACE)
        + " WHERE op.chat_id = 1 ORDER BY op.protegida DESC, op.orden"))
    comprueba("TEMP B-TREE" not in planes, "leer la lista no necesita sorter (escribiría un temporal en disco)")

    print("\n    Quién es el titular")
    comprueba(perfiles.titular() == perfiles.TITULAR_LOCAL, "sin entorno, el titular local")
    os.environ["JOBSPY_TELEGRAM_CHAT_ID"] = "-100987"
    comprueba(perfiles.titular() == -100987, "con entorno, ese, aunque sea negativo como un grupo")
    os.environ["JOBSPY_TELEGRAM_CHAT_ID"] = "no-es-un-numero"
    try:
        perfiles.titular()
        comprueba(False, "un valor que no es un número no se usa")
    except ValueError as e:
        comprueba("no-es-un-numero" not in str(e), "un valor que no es un número da error, y el error no lo repite")
    del os.environ["JOBSPY_TELEGRAM_CHAT_ID"]
    perfiles.meta_set("titular", "4321")
    comprueba(perfiles.titular() == 4321, "sin entorno, el guardado al migrar: una copia funciona en el PC")

    print("\n    Si cambia la regla de los bloques del dedupe")
    carpeta_nueva("bloques")
    almacen.fusionar_ofertas([oferta(1)], chat_id=A)
    conn = perfiles.conexion()
    conn.execute("UPDATE ofertas SET bloque_empresa = 'e:vieja'")
    conn.execute("DELETE FROM meta WHERE clave = ?", (f"dedupe_pendiente:{A}",))
    comprueba(perfiles.verificar(A)["bloques_desfasados"] == 1, "control: `verificar` ve el bloque desfasado")
    perfiles.meta_set("version_bloques", "0")
    perfiles.cerrar()
    perfiles._preparadas.clear()
    fila = perfiles.conexion().execute("SELECT bloque_empresa FROM ofertas").fetchone()
    comprueba(fila == ("e:empresa 1",), "al arrancar se recalculan las columnas")
    comprueba(perfiles.dedupe_pendiente(A), "y se fuerza una pasada completa del dedupe")

    print("\n    Una base de un esquema más nuevo que el código")
    carpeta = carpeta_nueva("futura")
    perfiles.conexion().execute("UPDATE meta SET valor = '99' WHERE clave = 'version_esquema'")
    perfiles.cerrar()
    perfiles._preparadas.clear()
    comprueba(lanza(perfiles.conexion) is not None and (carpeta / "perfiles.db").exists()
              and not list(carpeta.glob("*.corrupta-*")), "se para, pero NO se aparta: los datos están bien")


class Resp:
    status_code, ok, text = 200, True, "{}"

    def json(self):
        return {"ok": True, "result": True}


def prueba_de_extremo_a_extremo() -> None:
    print("\n[10] De punta a punta: una corrida, el bot y los botones, sin red")
    carpeta_nueva("punta")
    os.environ.update(JOBSPY_TELEGRAM_TOKEN="123:ABC", JOBSPY_TELEGRAM_CHAT_ID="555")
    enviados: list[dict] = []

    def espia_post(url, json=None, timeout=None):
        enviados.append({"metodo": url.rsplit("/", 1)[-1], **(json or {})})
        return Resp()

    alta = oferta(1, title="Técnico/a Helpdesk N1 Junior", location="Barcelona, Catalonia, Spain",
                  date_posted="2026-10-29", is_remote=False)
    baja = oferta(2, title="Director Comercial", location="Madrid, Spain")
    guardados = requests.post, notificaciones.time.sleep, buscador.buscar_para_perfiles, etiquetas.a_registros
    requests.post = espia_post
    notificaciones.time.sleep = lambda s: None
    buscador.buscar_para_perfiles = lambda activos, config, al_evaluar, excluidas, **k: al_evaluar(
        activos[0], [dict(alta), dict(baja)])
    etiquetas.a_registros = lambda df: df
    try:
        app._ejecutar_busqueda(config_mod.cargar_config(), False, automatica=True)
        comprueba(app.BUSQUEDA["error"] is None and app.BUSQUEDA["resumen"]["nuevas"] == 2,
                  "la corrida guarda las dos ofertas nuevas")
        avisos = [e for e in enviados if e["metodo"] == "sendMessage"]
        comprueba(len(avisos) == 1 and "reply_markup" in avisos[0], "avisa solo de la de Encaje alto, con botones")
        clave = tb.clave_de(alta["job_url"])
        comprueba(perfiles.aviso(555, clave)["titulo"] == alta["title"], "el aviso queda en la tabla, a nombre del titular")
        fila = perfiles.conexion().execute("SELECT vacante FROM avisos WHERE clave = ?", (clave,)).fetchone()
        comprueba(fila[0] == "|".join(app._clave_de_vacante(alta)), "con su `vacante`, que es lo que usará MP3")

        # Desde MP5 no hay web: lo que enseñaba el listado lo enseña /top, y la descripción que pedía
        # /api/descripcion está en la base, que es de donde sale el aviso.
        import telegram_bot

        def comando(texto: str):
            return telegram_bot.procesar({"update_id": 99, "message": {"message_id": 1, "text": texto, "chat": {
                "id": 555, "type": "private"}}}, config_mod.cargar_config())

        def lo_enviado() -> str:
            return " ".join(e.get("text", "") for e in enviados if e["metodo"] == "sendMessage")

        comprueba(perfiles.contar_ofertas(555) == 2 and perfiles.ultima_busqueda(555), "la base tiene las dos, con su última búsqueda")
        enviados.clear()
        comando("/top")
        comprueba(alta["title"] in lo_enviado(), "/top enseña la de Encaje alto (lo que antes era el listado de la web)")
        comprueba(perfiles.descripcion_de(555, alta["job_url"]) == alta["description"], "su descripción está en la base")
        comprueba(perfiles.descripcion_de(555, "https://x.test/no") is None, "   y None si no es una oferta suya")

        pulsacion = {"update_id": 1, "callback_query": {"id": "c", "data": "o:" + clave,
                                                        "message": {"message_id": 7, "chat": {"id": 555}}}}
        config = config_mod.cargar_config()
        comprueba(telegram_bot.procesar(pulsacion, config) == "oculta", "el botón 🙈 funciona, por el router nuevo")
        comprueba(alta["job_url"] in perfiles.marcas(555)["ocultas"], "y queda marcada")
        comprueba(perfiles.aviso(555, clave)["respuesta"] == "oculta", "y el índice guarda la respuesta")
        enviados.clear()
        comando("/top")
        comprueba(alta["title"] not in lo_enviado(), "   y /top ya no la enseña")

        firma = almacen.firma_ofertas()
        almacen.fusionar_ofertas([oferta(3)])
        comprueba(almacen.firma_ofertas() != firma, "la firma cambia cuando cambian las ofertas")
        firma = almacen.firma_ofertas()
        almacen.fusionar_ofertas([])
        comprueba(almacen.firma_ofertas() == firma, "y una corrida que no cambia nada no la cambia")
    finally:
        requests.post, notificaciones.time.sleep, buscador.buscar_para_perfiles, etiquetas.a_registros = guardados
        os.environ.pop("JOBSPY_TELEGRAM_TOKEN", None)
        os.environ.pop("JOBSPY_TELEGRAM_CHAT_ID", None)


def main() -> int:
    logging.disable(logging.CRITICAL)
    prueba_diferencial()
    prueba_migracion()
    prueba_marcas()
    prueba_avisos()
    prueba_aislamiento()
    prueba_reglas_d1()
    prueba_concurrencia()
    prueba_corrupcion()
    prueba_perfiles_y_esquema()
    prueba_de_extremo_a_extremo()
    perfiles.cerrar()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
