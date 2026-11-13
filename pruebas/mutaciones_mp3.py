# -*- coding: utf-8 -*-
"""
Los controles negativos de MP3: rompe el código a propósito y exige que test_multiperfil.py falle.

Una prueba de aislamiento que no puede fallar no prueba nada, y aquí el fallo que importa es
silencioso: sin el filtro por chat_id todo sigue funcionando, solo que una persona ve lo de otra.
Cada mutación quita UN arreglo, en una copia del árbol: el repositorio no se toca.

Una mutación cuenta como cazada si la prueba acaba con «FALLOS», es decir, si la caza una
comprobación y no una excepción cualquiera. Antes de nada se pasa la prueba sobre la copia sin
tocar: si eso falla, el resultado de las demás no diría nada.

No está en todas.py porque tarda: es una pasada de la prueba entera por mutación.

    PYTHONUTF8=1 python pruebas/mutaciones_mp3.py [--solo <trozo del nombre>]
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUERA = {".git", "data", "backups_toshiba", "__pycache__", "API keys", ".claude", ".vscode", "prompts"}
OBREROS = 4

# (nombre, fichero, texto de ahora, texto roto)
MUTACIONES = [
    # --- Aislamiento (regla 5): lo que pide el «hecho cuando»
    ("botón: el aviso se busca en el índice del titular", "telegram_botones.py",
     "aviso = perfiles.aviso(chat_id, clave)", "aviso = perfiles.aviso(perfiles.titular(), clave)"),
    ("botón: la marca va a la base del titular", "telegram_botones.py",
     "almacen.fijar_marca(url, accion, datos_oferta, extra, chat_id=chat_id)",
     "almacen.fijar_marca(url, accion, datos_oferta, extra)"),
    ("botón: la respuesta va al aviso del titular", "telegram_botones.py",
     "perfiles.anotar_respuesta(chat_id, clave, accion)",
     "perfiles.anotar_respuesta(perfiles.titular(), clave, accion)"),
    ("botón: deshacer quita las marcas del titular", "telegram_botones.py",
     "almacen.quitar_marcas(url, chat_id=chat_id)", "almacen.quitar_marcas(url)"),
    ("botón: vale cualquier chat", "telegram_botones.py",
     "return chat_id if perfiles.obtener(chat_id) is not None else None", "return chat_id"),
    ("aviso: se registra en el índice del titular", "notificaciones.py",
     "telegram_botones.registrar_aviso(oferta, chat_id, avisada=avisada))", "telegram_botones.registrar_aviso(oferta, avisada=avisada))"),
    ("aviso: el de otro perfil sale al titular", "app.py",
     "chat_id=None if posicion is None else chat_id,", "chat_id=None,"),
    ("aviso: vuelve la copia a los adicionales", "notificaciones.py",
     "chat_ids = [str(c) for c in chat_ids] if chat_ids else _principal(config)",
     "chat_ids = [str(c) for c in chat_ids] if chat_ids else _chat_ids(config)"),
    ("corrida: todas las filas para todos", "buscador.py",
     "mascara = origen.isin([i for i, c in enumerate(consultas) if chat in c.etiquetas])",
     "mascara = origen == origen"),
    ("corrida: lo de Jooble es de quien pidió la consulta", "buscador.py",
     "duenos[url] = jooble.get(url) or set(consultas[-1 - origen].perfiles)",
     "duenos[url] = set(consultas[-1 - origen].perfiles)"),
    ("corrida: Tecnoempleo es de quien pidió la consulta", "buscador.py",
     "            duenos[url] = tecnoempleo\n", "            duenos[url] = set(consultas[-1 - origen].perfiles)\n"),
    ("corrida: las etiquetas de uno se cuelan en otro", "buscador.py",
     "    if cambios:\n        filas = filas.copy()", "    if False:\n        filas = filas.copy()"),
    ("corrida: lo que excluye el titular vale para todos", "app.py",
     "activos, config, guardar, almacen.urls_excluidas,",
     "activos, config, guardar, lambda chat: almacen.urls_excluidas(),"),
    ("corrida: nadie excluye nada", "buscador.py",
     "    if excluidas:\n        todo = todo[~todo", "    if False:\n        todo = todo[~todo"),
    ("web: «reemplazar» vacía a todos", "app.py",
     "reemplazar=reemplazar and es_titular", "reemplazar=reemplazar"),
    ("D1: el dedupe borra la oferta aunque otro la enlace", "perfiles.py",
     'if borrados and conn.execute("SELECT 1 FROM ofertas_perfil WHERE url = ? LIMIT 1", (url,)).fetchone() is None:',
     "if borrados:"),
    ("D1: la descarga fallida de uno borra el texto de otro", "perfiles.py",
     "if fila_texto is not None and not _la_enlaza_otro(conn, chat_id, url):", "if fila_texto is not None:"),
    ("regla 5: el log dice qué marca otro perfil", "telegram_botones.py",
     "    if chat_id == perfiles.titular():\n        logger.info(\"Botón de Telegram: %s en",
     "    if True:\n        logger.info(\"Botón de Telegram: %s en"),
    ("regla 5: el log dice qué se le avisa a otro perfil", "app.py",
     "            if posicion is None:\n                logger.info(\n                    \"No se avisa de",
     "            if True:\n                logger.info(\n                    \"No se avisa de"),
    ("categorías siempre visibles del titular para todos", "almacen.py",
     "patrones_siempre_visibles = _patrones_siempre_visibles(config)",
     "patrones_siempre_visibles = _patrones_siempre_visibles()"),
    # --- El perfil del adicional
    # Desde MP5 el adicional entra con la plantilla (perfiles.datos_de_plantilla), que sale del juego
    # y no copia config.json: «se copia config.json entero» y «resucita tras borrarlo» ya no tienen
    # código que romper. Lo que queda es el tope y que sea estricto.
    ("adicional: la plantilla se queda en el 3 del juego", "perfiles.py",
     '"config": {"anos_experiencia_max": TOPE_DE_ANOS_DE_LA_PLANTILLA},\n'
     '            "experiencia_max_exigida": TOPE_DE_ANOS_DE_LA_PLANTILLA,',
     '"config": {},\n            "experiencia_max_exigida": None,'),
    ("adicional: el tope de la plantilla solo resta", "perfiles.py",
     '"experiencia_max_exigida": TOPE_DE_ANOS_DE_LA_PLANTILLA, "experiencia_estricta": True,',
     '"experiencia_max_exigida": TOPE_DE_ANOS_DE_LA_PLANTILLA, "experiencia_estricta": False,'),
    ("adicional: el titular también recibe la primera corrida con tope", "app.py",
     "primera = not es_titular and (perfiles.contar_ofertas(chat_id) == 0",
     "primera = (perfiles.contar_ofertas(chat_id) == 0 and False"),
    ("primera corrida: sin tope", "app.py",
     "tope=AVISOS_EN_LA_PRIMERA_CORRIDA if primera else None, con_resumen=not primera,",
     "tope=None, con_resumen=True,"),
    ("primera corrida: con resumen", "app.py", "con_resumen=not primera,", "con_resumen=True,"),
    # «el mensaje sale sin --confirmar» y «el mensaje se repite» eran de `avisar-adicionales`, que se
    # fue con MP5: la bienvenida la da ahora el /start de cada invitación (mutaciones_mp5.py).
    # --- Carga y presupuesto
    ("consultas: no se deduplican", "buscador.py",
     'clave = _clave_de_consulta(busqueda["params"])\n            consulta =',
     'clave = _clave_de_consulta(busqueda["params"]) + str(chat)\n            consulta ='),
    ("consultas: el término no se normaliza", "buscador.py",
     'termino = " ".join(str(params.get("search_term") or "").casefold().split())',
     'termino = str(params.get("search_term") or "")'),
    ("consultas: sin rotación", "buscador.py",
     'orden = sorted(range(len(plan)), key=lambda i: (ultimas.get(plan[i].clave) or "", i))',
     "orden = list(range(len(plan)))"),
    ("consultas: sin presupuesto", "buscador.py",
     "return [plan[i] for i in orden[:max(0, tope)]]", "return [plan[i] for i in orden]"),
    ("rotación: un aviso en cada corrida", "buscador.py",
     "    if perfiles.meta_get(clave) != hoy:\n", "    if True:\n"),
    ("perfiles: los pausados siguen en la corrida", "perfiles.py", 'todos = listar("activo")', "todos = listar()"),
    ("avisos: el modo «ninguno» no se mira", "perfiles.py",
     'return (perfil["datos"].get("avisos") or {}).get("modo") != "ninguno"', "return True"),
    ("sombra: un chat_id negativo con avisos", "perfiles.py",
     '    if chat_id < 0 and (datos.get("avisos") or {}).get("modo") != "ninguno":\n', "    if False:\n"),
    ("sombra: sin tope de perfiles", "perfiles.py", "    if tope and len(existentes) >= tope:\n", "    if False:\n"),
    ("borrar: la config se queda en memoria", "perfiles.py",
     "        _CACHE_EFECTIVA.pop(chat_id, None)\n    return quitado", "        pass\n    return quitado"),
    # --- Jooble
    ("Jooble: se gasta un término de más por perfil", "buscador.py",
     "return todos, [todos[i] for i in orden[:jooble_mod.PETICIONES_POR_DESCARGA]]",
     "return todos, [todos[i] for i in orden]"),
    ("Jooble: siempre Barcelona", "fuentes_extra/jooble.py",
     'cuerpo = {"keywords": palabra, "location": termino.ubicacion,',
     'cuerpo = {"keywords": palabra, "location": UBICACION,'),
    ("Jooble: no apunta lo pedido y no rota", "fuentes_extra/jooble.py",
     '_REPARTO["pedidos"].append(termino.clave)', "pass"),
    # --- Lo compartido
    ("descarga: lo compartido no llega a los demás perfiles", "buscador.py",
     '    if df.empty:\n        return df\n    df = df.copy()\n    enlaces = df["job_url"]',
     "    return df\n    df = df.copy()\n    enlaces = df[\"job_url\"]"),
    ("idioma: se detecta otra vez el mismo texto", "buscador.py",
     "@functools.lru_cache(maxsize=512)\ndef _idiomas_de", "def _idiomas_de"),
    ("una marca por perfil en vez de una por corrida", "app.py",
     'marca = marca or datetime.now().isoformat(timespec="seconds")', "marca = None"),
    # --- Averías
    ("un perfil roto tumba la corrida", "buscador.py",
     "            if not i:\n                raise\n            _perfil_fallido(posicion, \"su configuración\")",
     "            raise"),
    ("un fallo con un perfil tumba a los demás", "buscador.py",
     "        if p is primero:\n            raise\n", "        raise\n"),
    ("un fallo con el titular se esconde", "buscador.py",
     "        if p is primero:\n            raise\n", ""),
    ("las corridas manuales no avisan a nadie", "app.py",
     "        nuevas = resumen.get(\"ofertas_nuevas\", [])\n",
     "        nuevas = resumen.get(\"ofertas_nuevas\", []) if automatica else []\n"),
    ("no poder apuntar las consultas tira la corrida", "buscador.py",
     'logger.exception("No se han podido apuntar las consultas de esta corrida")', "raise"),
    # «no poder crear un perfil impide arrancar» se fue con MP5: el arranque ya no crea perfiles.
    ("no repetir: la tabla `avisos` no se mira", "app.py",
     "    if dias <= 0:\n        return {}\n    nuevas = {o.get(\"job_url\") for o in ofertas_nuevas}\n    desde",
     "    return {}\n    nuevas = {o.get(\"job_url\") for o in ofertas_nuevas}\n    desde"),
]


def copiar_arbol(destino: Path) -> None:
    def ignorar(_carpeta, nombres):
        return {n for n in nombres if n in FUERA or n.endswith(".pyc")}

    shutil.copytree(RAIZ, destino, ignore=ignorar)


def pasar_prueba(arbol: Path) -> tuple[int, str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
    entorno.update(MP3_SIN_EQUIVALENCIA="1", PYTHONUTF8="1")
    proceso = subprocess.run([sys.executable, str(arbol / "pruebas" / "test_multiperfil.py")],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno)
    return proceso.returncode, proceso.stdout + proceso.stderr


def una(mutacion: tuple, base: Path) -> tuple[str, str]:
    nombre, fichero, viejo, nuevo = mutacion
    arbol = Path(tempfile.mkdtemp(prefix="mutacion-")) / "arbol"
    try:
        copiar_arbol(arbol)
        ruta = arbol / fichero
        texto = ruta.read_bytes().decode("utf-8")
        if texto.count(viejo) != 1:
            return nombre, "NO APLICA: el texto aparece %d veces" % texto.count(viejo)
        ruta.write_bytes(texto.replace(viejo, nuevo).encode("utf-8"))
        codigo, salida = pasar_prueba(arbol)
        if codigo == 0:
            return nombre, "VIVA: la prueba pasa con el código roto"
        if "\nFALLOS (" in salida:
            primera = re.search(r"\n  - (.+)", salida[salida.index("\nFALLOS ("):])
            return nombre, "cazada: " + (primera.group(1) if primera else "?")
        return nombre, "ROTA: la prueba reventó sin llegar a una comprobación\n" + salida[-600:]
    finally:
        shutil.rmtree(arbol.parent, ignore_errors=True)


def main() -> int:
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else ""
    base = Path(tempfile.mkdtemp(prefix="mutacion-base-")) / "arbol"
    copiar_arbol(base)
    codigo, salida = pasar_prueba(base)
    shutil.rmtree(base.parent, ignore_errors=True)
    if codigo:
        print("La prueba falla sin mutar nada: no se puede medir.\n" + salida[-1500:])
        return 2
    print("Sin mutar, en verde.\n")
    elegidas = [m for m in MUTACIONES if solo in m[0]]
    with ThreadPoolExecutor(max_workers=OBREROS) as ex:
        resultados = list(ex.map(lambda m: una(m, base), elegidas))
    malas = 0
    for nombre, resultado in resultados:
        cazada = resultado.startswith("cazada")
        malas += not cazada
        print("  %s %s\n         %s" % ("OK   " if cazada else "FALLA", nombre, resultado))
    print("\n%d de %d cazadas." % (len(resultados) - malas, len(resultados)))
    return 1 if malas else 0


if __name__ == "__main__":
    raise SystemExit(main())
