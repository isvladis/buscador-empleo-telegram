# -*- coding: utf-8 -*-
"""
Los controles negativos de la revisión del 11-11 (`REVISION-2026-11-11.md`): rompe a propósito cada uno de
los tres arreglos y exige que su prueba falle.

Mismo método que mutaciones_mp5.py, con su copia del árbol: el repositorio no se toca, y una mutación solo
cuenta como cazada si la prueba acaba con «FALLOS», es decir, si la caza una comprobación y no una
excepción cualquiera. La diferencia es que aquí cada mutación dice qué prueba tiene que cazarla: son cuatro
ficheros distintos y pasar los cuatro por cada una sería tirar el tiempo.

Lo que se rompe:

- **El caché de descripciones** (test_cache.py): que la corrida vuelva a volcarlo entero, que el JSON de
  antes se importe en cada arranque, que se reescriba lo que no ha cambiado, que la fecha no se refresque,
  que la poda no pode, que la competencia nueva pise la del día de la descarga, que un caché roto se borre
  en vez de apartarse, que el caché mande sobre la base, que una avería se diga una vez por enlace y que
  se quede abierto entre corridas.
- **Lo escrito en «Otro»** (test_generador.py y test_encuesta.py): que vuelva a ser solo palabra clave, que
  cuente dos veces en el título, que el núcleo pierda el plural o los acentos, que baste una palabra de
  varias, y que el sector escrito pase a ser un puesto.
- **El orden de la cola** (test_avisos_perfil.py): que vuelva a desempatar por el enlace, que a igualdad
  salga la más antigua, que la fecha de publicación no cuente y que la nota deje de mandar.
- **`perfilado.regenerar`** (test_encuesta.py): que el ensayo escriba, que toque a un pausado, que no guarde
  el plan de antes y que enseñe un chat_id.
- **El reinicio de un perfil** (test_reinicio.py, añadido el mismo día): que valga una confirmación vieja
  (la de la persona y la del titular), que no deje copia, que se deje las marcas, las respuestas o lo
  aprendido, que el reiniciado pierda su fila, que se salte las 24 horas, que se reinicie alguien que ya
  está en el cuestionario, que otro perfil pueda reiniciar a alguien, que el titular reinicie a quien no
  es, que a esa persona no se le diga, y que una corrida en marcha (la de siempre o la de arranque) le
  guarde o le avise a un perfil reiniciado o borrado. Del 12-11, el titular que se reinicia a sí mismo:
  que vuelva a negarse (en el bot o en `perfiles.borrar`), también sin fila; que reciba el aviso de los
  demás o se avise a sí mismo; que le falten el silencio y el tope que le promete la encuesta; y que su
  primera tanda vaya sin tope, o que la lleve siempre.
- **El aviso de corridas fallidas** (test_fallos.py, del 12-11): que un fallo no se apunte, que se avise al
  primero o en cada uno, que una corrida buena no ponga la cuenta a cero ni lo diga, que el aviso lleve el
  token o el error sin escapar, que se dé por avisado sin haberlo mandado, y que la base corrupta sume.
- **/interesan** (test_interesan.py, del 12-11): que salgan las ya aplicadas o descartadas, que el orden sea
  el contrario, que se enseñe la lista de otro, que se apunten como avisos, que el título vaya sin escapar,
  que una página imposible se acepte, que todo «siga saliendo» y que el botón de página no haga nada.

No está en todas.py porque tarda: es una pasada de una prueba entera por mutación.

    PYTHONUTF8=1 python pruebas/mutaciones_revision.py [--solo <trozo del nombre>]
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutaciones_mp3 import OBREROS, RAIZ, copiar_arbol  # noqa: E402

CACHE, GENERADOR, AVISOS, ENCUESTA = "test_cache.py", "test_generador.py", "test_avisos_perfil.py", "test_encuesta.py"
REINICIO = "test_reinicio.py"
FALLOS = "test_fallos.py"
INTERESAN = "test_interesan.py"
AL, AV, GE, RE = "almacen.py", "avisos_perfil.py", "perfilado/generador.py", "perfilado/regenerar.py"
PE, BOT = "perfiles.py", "telegram_bot.py"

# (nombre, fichero, texto de ahora, texto roto, prueba que tiene que cazarla)
MUTACIONES = [
    # --- El caché de descripciones
    ("la corrida vuelve a volcar el caché entero", "app.py",
     "        conocidas = almacen.Conocidas(en_base, en_cache)  # si está en los dos, manda la base",
     "        conocidas = {**en_cache, **en_base}", CACHE),
    ("el JSON de antes se importa en cada arranque", AL,
     """            if conn.execute("SELECT 1 FROM meta WHERE clave = 'importado_del_json'").fetchone() is None:""",
     "            if True:", CACHE),
    ("recordar reescribe los textos que no han cambiado", AL,
     '                        " WHERE textos.texto IS NOT excluded.texto", (url, str(texto)))',
     '                        "", (url, str(texto)))', CACHE),
    ("la fecha de visto no se refresca", AL,
     "                    elif previa[0] != hoy_iso or previa[1] != extra:",
     "                    elif previa[1] != extra:", CACHE),
    ("la poda no quita los textos viejos", AL,
     '                conn.execute("DELETE FROM textos WHERE url IN (SELECT url FROM vistas WHERE visto < ?)", (limite,))',
     "                pass", CACHE),
    ("la competencia nueva pisa la del día de la descarga", AL,
     "                        if campos.get(campo) is None:", "                        if True:", CACHE),
    ("un caché roto se borra en vez de apartarse", AL,
     "        os.replace(destino, apartado)", "        os.remove(destino)", CACHE),
    ("en los dos sitios manda el caché y no la base", AL,
     "        valor = self._en_base.get(url)\n        if valor:\n            return valor\n        return self._en_cache[url]",
     "        valor = self._en_cache.get(url)\n        if valor:\n            return valor\n        return self._en_base[url]",
     CACHE),
    ("una avería del caché se dice una vez por enlace", AL,
     '    if _cache["sin_servicio"]:\n        return  # ya está dicho, y es la misma avería\n', "", CACHE),
    ("el caché se queda abierto entre corridas", "app.py",
     "        # consolida, y entre corridas no queda ningún fichero cogido.\n        almacen.cerrar_cache()",
     "        # consolida, y entre corridas no queda ningún fichero cogido.\n        pass", CACHE),
    # --- Lo escrito en «Otro»
    ("lo escrito vuelve a ser solo palabra clave", GE,
     "        if nucleos_de_otros[otro]:\n            plan_puestos.append(",
     "        if False:\n            plan_puestos.append(", GENERADOR),
    ("lo escrito cuenta dos veces en el título", "buscador.py",
     "if _sin_acentos(n).strip() not in escritos]", "if True]", GENERADOR),
    ("el núcleo no quita el plural", GE,
     '    if raiz.endswith("s") and not raiz.endswith("ss"):\n        raiz = raiz[:-1]',
     '    if False:\n        raiz = raiz[:-1]', GENERADOR),
    ("el núcleo no lleva los acentos", GE,
     '"".join(_CON_ACENTOS.get(c, c) for c in raiz)', "raiz", GENERADOR),
    ("con una palabra de las escritas basta", GE,
     '    return "^" + "".join("(?=.*%s)" % t for t in trozos)', '    return "|".join(trozos)', GENERADOR),
    ("el sector escrito pasa a ser un puesto", "telegram_encuesta.py",
     '    otros = list(r.get("otros") or [])\n    if not puestos:',
     '    otros = ([r["sector_otro"]] if r.get("sector_otro") else []) + list(r.get("otros") or [])\n    if not puestos:',
     ENCUESTA),
    # --- El orden de la cola
    ("la cola vuelve a desempatar por el enlace", AV,
     '        ofertas = orden_de_salida(ofertas, {p["url"]: p["creada"] for p in vigentes})',
     '        ofertas = sorted(ofertas, key=lambda o: (-(o.get("relevancia") or 0), o["job_url"]))', AVISOS),
    ("a igualdad sale la que más lleva esperando", AV,
     '    salida.sort(key=lambda o: str(creada_de.get(o["job_url"]) or ""), reverse=True)',
     '    salida.sort(key=lambda o: str(creada_de.get(o["job_url"]) or ""))', AVISOS),
    ("la fecha de publicación no cuenta", AV, "    salida.sort(key=publicada, reverse=True)", "    pass", AVISOS),
    ("la nota deja de mandar", AV, '    salida.sort(key=lambda o: -(o.get("relevancia") or 0))', "    pass", AVISOS),
    # --- perfilado.regenerar
    ("el ensayo escribe", RE, "        if not confirmar:\n            continue\n", "", ENCUESTA),
    ("regenerar toca a un pausado", RE, '        if perfil.get("estado") != "activo":', "        if False:", ENCUESTA),
    ("regenerar no guarda el plan de antes", RE, '"plan_anterior": d.get("plan")}', '"plan_anterior": None}', ENCUESTA),
    ("regenerar enseña el chat_id", RE, '        nombre = "Perfil %d de %d" % (numero, len(todos))',
     '        nombre = "Perfil %d" % perfil["chat_id"]', ENCUESTA),
    # --- El reinicio de un perfil
    ("un «Sí» viejo reinicia", BOT, "    if sello is None or not _vigente(sello):", "    if sello is None:", REINICIO),
    ("el reinicio no deja copia", PE, "    exportar(chat_id, copia)\n", "", REINICIO),
    ("el reinicio se deja las marcas", PE, '        for tabla in ("marcas", "avisos", "conversacion", "pendientes"):',
     '        for tabla in ("avisos", "conversacion", "pendientes"):', REINICIO),
    ("el reinicio se deja las respuestas y lo aprendido", PE,
     "(_json(datos_a_cero(reiniciado=marca)), _ahora(), chat_id))",
     '(conn.execute("SELECT datos FROM perfiles WHERE chat_id = ?", (chat_id,)).fetchone()[0], _ahora(), chat_id))', REINICIO),
    ("el reiniciado pierde su fila y su sitio", PE, "        elif not conservar_perfil:", "        if not conservar_perfil:", REINICIO),
    ("el reinicio no deja su marca", PE, '        datos["reiniciado"] = reiniciado', "        pass", REINICIO),
    ("se puede reiniciar sin esperar las 24 horas", BOT, "    horas = _horas_para_reiniciar(perfil)\n    if horas:",
     "    horas = _horas_para_reiniciar(perfil)\n    if False:", REINICIO),
    ("se reinicia a quien ya está en el cuestionario", BOT,
     '    if perfil.get("estado") == "encuesta":\n        return "Tu perfil ya está a cero', '    if False:\n        return "Tu perfil ya está a cero',
     REINICIO),
    # El titular se reinicia a sí mismo (12-11). Hasta ese día las dos de aquí eran al revés: que SÍ pudiera.
    ("el titular vuelve a no poder reiniciarse", BOT,
     "    perfil = perfiles.obtener(chat.chat_id)\n    if perfil is None and not chat.es_titular:",
     '    if chat.es_titular:\n        return "Tu perfil no se reinicia."\n    perfil = perfiles.obtener(chat.chat_id)\n'
     "    if perfil is None and not chat.es_titular:", REINICIO),
    ("perfiles.borrar vuelve a negarse con el titular", PE, "    if es_el_titular and not (conservar_perfil or reiniciar):",
     "    if es_el_titular and not conservar_perfil:", REINICIO),
    ("al titular sin fila no se le ofrece", BOT, "    if perfil is None and not chat.es_titular:", "    if perfil is None:", REINICIO),
    ("perfiles.borrar no le crea la fila al titular sin fila", PE,
     '            if not es_el_titular:\n                raise ValueError("ese perfil no existe")',
     '            if True:\n                raise ValueError("ese perfil no existe")', REINICIO),
    ("el titular recibe el aviso de los demás", BOT, "AVISO_DE_REINICIO_DEL_TITULAR if chat.es_titular else AVISO_DE_REINICIO,",
     "AVISO_DE_REINICIO,", REINICIO),
    ("el titular se avisa a sí mismo de su reinicio", BOT, "    if not chat.es_titular:\n        _al_titular(chat.config,",
     "    if True:\n        _al_titular(chat.config,", REINICIO),
    ("al titular reiniciado no se le pone silencio ni tope", "telegram_encuesta.py",
     '(chat.chat_id != perfiles.titular() or (perfil and perfil["estado"] == "encuesta"))', "(chat.chat_id != perfiles.titular())", REINICIO),
    ("la primera tanda del titular reiniciado va sin tope", "app.py",
     "            primera = ((not es_titular and perfiles.contar_ofertas(chat_id) == 0)",
     "            primera = not es_titular and ((perfiles.contar_ofertas(chat_id) == 0)", REINICIO),
    ("la corrida del titular va siempre con tope de primera tanda", "app.py",
     "(not es_titular and perfiles.contar_ofertas(chat_id) == 0)", "(perfiles.contar_ofertas(chat_id) == 0)", REINICIO),
    # El primer /start del titular en una instalación nueva (12-11): el cuestionario, no el perfil de fábrica.
    ("el titular nuevo se queda con el perfil de fábrica", BOT, "    if _por_estrenar(chat):", "    if False:", REINICIO),
    ("al titular que ya usó el de fábrica se le cambia el perfil", BOT,
     "    return perfiles.contar_ofertas(chat.chat_id) == 0 and not any(marcas.values())", "    return True", REINICIO),
    ("el titular nuevo empieza activo con el plan vacío", BOT,
     '        perfiles.guardar(chat.chat_id, perfiles.datos_a_cero(), "encuesta")',
     '        perfiles.guardar(chat.chat_id, perfiles.datos_a_cero(), "activo")', REINICIO),
    ("al titular nuevo no se le dice que administra el bot", BOT, "        chat.decir(BIENVENIDA_DEL_TITULAR)\n", "", REINICIO),
    ("otro perfil puede reiniciar a alguien", BOT,
     '    if (prefijo == "adm1" and not chat.es_titular) or (prefijo in ("aj1", "ob1") and chat.perfil is None):',
     '    if prefijo in ("aj1", "ob1") and chat.perfil is None:', REINICIO),
    ("un «Sí» viejo del titular reinicia", BOT,
     '            if len(args) < 2 or not _vigente(args[1]):\n                _editar(token, chat_id, mensaje_id, "Esa confirmación ha caducado.", _teclado_de_ficha(perfil))\n'
     '                return "caducado", "Caducado"\n            copia, quitado = perfiles.copia_y_borrado(perfil["chat_id"], reiniciar=True)',
     '            if len(args) < 2:\n                _editar(token, chat_id, mensaje_id, "Esa confirmación ha caducado.", _teclado_de_ficha(perfil))\n'
     '                return "caducado", "Caducado"\n            copia, quitado = perfiles.copia_y_borrado(perfil["chat_id"], reiniciar=True)',
     REINICIO),
    ("el titular reinicia a otro que el de la ficha", BOT,
     '            copia, quitado = perfiles.copia_y_borrado(perfil["chat_id"], reiniciar=True)',
     '            copia, quitado = perfiles.copia_y_borrado(next(p["chat_id"] for p in perfiles.listar() if p["chat_id"]'
     ' not in (perfil["chat_id"], perfiles.titular())), reiniciar=True)', REINICIO),
    ("al reiniciado por el titular no se le dice", BOT, "            avisado = suyo.decir(", "            avisado = False and suyo.decir(",
     REINICIO),
    ("al reiniciado por el titular no le llega el cuestionario", BOT, "            encuesta.iniciar(suyo)\n", "", REINICIO),
    ("la ficha ofrece reiniciar a quien está en el cuestionario", BOT,
     '        if perfil.get("estado") in ESTADOS_QUE_SE_REINICIAN:\n            filas.append(',
     "        if True:\n            filas.append(", REINICIO),
    ("al recién reiniciado se le vigila el silencio desde su alta", AV,
     '    nacido = max(filter(None, (_leer(p.get("creado")), _leer((p.get("datos") or {}).get("reiniciado")))), default=None)',
     '    nacido = _leer(p.get("creado"))', REINICIO),
    ("la corrida le guarda a un reiniciado", "app.py",
     "            if not _es_el_de_antes(perfil):\n                logger.info(\"Un perfil se ha borrado o reiniciado durante la corrida: lo que",
     "            if False:\n                logger.info(\"Un perfil se ha borrado o reiniciado durante la corrida: lo que", REINICIO),
    ("la corrida le avisa a un reiniciado", "app.py",
     "            if not _es_el_de_antes(perfil):\n                # Entre guardar y avisar",
     "            if False:\n                # Entre guardar y avisar", REINICIO),
    ("el arranque le guarda a un reiniciado", "app.py",
     "            if not _es_el_de_antes(perfil_):", "            if False:", REINICIO),
    ("un reiniciado parece el perfil de antes", "app.py",
     '    return actual["datos"].get("reiniciado") == perfil["datos"].get("reiniciado")', "    return True", REINICIO),
    ("la corrida le guarda a un borrado, que no es el reiniciado", "app.py",
     "        return chat_id == perfiles.titular()", "        return True", REINICIO),
    ("el titular sin fila deja de ser el de antes, como un reiniciado", "app.py",
     "        return chat_id == perfiles.titular()", "        return False", REINICIO),
    # --- El aviso de corridas fallidas (12-11)
    ("una corrida que falla no se apunta", "app.py", "            _corrida_fallida(e)", "            pass", FALLOS),
    ("se avisa al primer fallo", "app.py", "FALLOS_PARA_AVISAR = 2", "FALLOS_PARA_AVISAR = 1", FALLOS),
    ("se avisa en cada fallo, uno por hora", "app.py",
     'toca = _FALLOS["seguidos"], _FALLOS["seguidos"] >= FALLOS_PARA_AVISAR and not _FALLOS["avisado"]',
     'toca = _FALLOS["seguidos"], _FALLOS["seguidos"] >= FALLOS_PARA_AVISAR', FALLOS),
    ("una corrida buena no pone la cuenta a cero", "app.py", "        _FALLOS.update(seguidos=0, avisado=False)\n    if not avisado:",
     "        pass\n    if not avisado:", FALLOS),
    ("no se dice que vuelve a funcionar", "app.py",
     "    if not avisado:\n        return\n    try:\n        notificaciones._enviar(config_mod.cargar_config(),",
     "    if True:\n        return\n    try:\n        notificaciones._enviar(config_mod.cargar_config(),", FALLOS),
    ("el aviso de fallos lleva el token", "app.py",
     '        detalle = notificaciones.tachar_token(f"{type(error).__name__}: {error}",\n'
     '                                              (config.get("telegram_bot_token") or "").strip())[:300]',
     '        detalle = f"{type(error).__name__}: {error}"[:300]', FALLOS),
    ("el error va sin escapar y sin recortar", "app.py", "<code>{notificaciones._esc(detalle)}</code>",
     "<code>{type(error).__name__}: {error}</code>", FALLOS),
    ("se da por avisado aunque Telegram no responda", "app.py",
     '        if notificaciones._enviar(config, texto, "corridas fallidas seguidas"):',
     '        if notificaciones._enviar(config, texto, "corridas fallidas seguidas") or True:', FALLOS),
    ("la base corrupta también suma y avisa dos veces", "app.py",
     "            _notificar_incidencias_de_fuentes()\n        else:\n", "            _notificar_incidencias_de_fuentes()\n        if True:\n", FALLOS),
    # --- /interesan (12-11)
    ("/interesan enseña las ya aplicadas y las descartadas", BOT,
     'for url, info in marcas["favoritas"].items() if url not in fuera]', 'for url, info in marcas["favoritas"].items()]', INTERESAN),
    ("/interesan sale con lo más viejo primero", BOT,
     '    pendientes.sort(key=lambda par: (par[1].get("fecha") or "", par[0]), reverse=True)',
     '    pendientes.sort(key=lambda par: (par[1].get("fecha") or "", par[0]))', INTERESAN),
    ("/interesan enseña la lista del titular a cualquiera", BOT,
     "    pendientes = _interesan_pendientes(chat.chat_id)", "    pendientes = _interesan_pendientes(perfiles.titular())", INTERESAN),
    ("lo enseñado en /interesan cuenta como aviso", BOT,
     '"relevancia": oferta.get("relevancia"), "site": oferta.get("site")}, chat.chat_id, tipo="interesa")',
     '"relevancia": oferta.get("relevancia"), "site": oferta.get("site")}, chat.chat_id, tipo="aviso")', INTERESAN),
    ("el título de /interesan va sin escapar", BOT,
     '        titulo = _esc(str(oferta.get("title") or info.get("titulo") or "Sin título")[:120])',
     '        titulo = str(oferta.get("title") or info.get("titulo") or "Sin título")[:120]', INTERESAN),
    ("el título de /interesan va sin recortar", BOT,
     '        titulo = _esc(str(oferta.get("title") or info.get("titulo") or "Sin título")[:120])',
     '        titulo = _esc(str(oferta.get("title") or info.get("titulo") or "Sin título") * 3)', INTERESAN),
    ("/interesan acepta una página que no existe", BOT,
     "    desde = max(0, min(int(desde), ((total - 1) // INTERESAN_POR_PAGINA) * INTERESAN_POR_PAGINA))",
     "    desde = int(desde)", INTERESAN),
    ("en /interesan todas «siguen saliendo»", BOT, "        elif vista <= DIAS_PARA_DARLA_POR_VISTA:", "        elif True:", INTERESAN),
    ("la que ya no está en la lista parece que sigue", BOT, "        if vista is None:\n            datos.append(\"ya no está en tu lista\")",
     "        if vista is None:\n            datos.append(\"sigue saliendo en las búsquedas\")", INTERESAN),
    ("el botón de página de /interesan no hace nada", BOT, '    if accion == "in":', '    if accion == "in_":', INTERESAN),
    ("la página se queda sin sus botones de paso", BOT, "    if paso:\n        filas.append(paso)", "    if False:\n        filas.append(paso)", INTERESAN),
]


def pasar_prueba(arbol: Path, prueba: str) -> tuple[int, str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
    # test_cache compara con el almacen.py de un commit, y la copia del árbol no lleva `.git`.
    entorno.update(PYTHONUTF8="1", REPO_DE_REFERENCIA=str(RAIZ))
    proceso = subprocess.run([sys.executable, str(arbol / "pruebas" / prueba)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno)
    return proceso.returncode, proceso.stdout + proceso.stderr


def una(mutacion: tuple) -> tuple[str, str]:
    nombre, fichero, viejo, nuevo, prueba = mutacion
    arbol = Path(tempfile.mkdtemp(prefix="mutacion-")) / "arbol"
    try:
        copiar_arbol(arbol)
        ruta = arbol / fichero
        texto = ruta.read_bytes().decode("utf-8")
        if texto.count(viejo) != 1:
            return nombre, "NO APLICA: el texto aparece %d veces" % texto.count(viejo)
        ruta.write_bytes(texto.replace(viejo, nuevo).encode("utf-8"))
        codigo, salida = pasar_prueba(arbol, prueba)
        if codigo == 0:
            return nombre, "VIVA: %s pasa con el código roto" % prueba
        if "\nFALLOS (" in salida:
            primera = re.search(r"\n  - (.+)", salida[salida.index("\nFALLOS ("):])
            return nombre, "cazada por %s: %s" % (prueba, primera.group(1) if primera else "?")
        return nombre, "ROTA: %s reventó sin llegar a una comprobación\n%s" % (prueba, salida[-600:])
    finally:
        shutil.rmtree(arbol.parent, ignore_errors=True)


def main() -> int:
    solo = sys.argv[sys.argv.index("--solo") + 1] if "--solo" in sys.argv else ""
    elegidas = [m for m in MUTACIONES if solo in m[0]]
    base = Path(tempfile.mkdtemp(prefix="mutacion-base-")) / "arbol"
    copiar_arbol(base)
    for prueba in sorted({m[4] for m in elegidas}):
        codigo, salida = pasar_prueba(base, prueba)
        if codigo:
            print("%s falla sin mutar nada: no se puede medir.\n%s" % (prueba, salida[-1500:]))
            shutil.rmtree(base.parent, ignore_errors=True)
            return 2
    shutil.rmtree(base.parent, ignore_errors=True)
    print("Sin mutar, en verde.\n")
    with ThreadPoolExecutor(max_workers=OBREROS) as ex:
        resultados = list(ex.map(una, elegidas))
    malas = 0
    for nombre, resultado in resultados:
        cazada = resultado.startswith("cazada")
        malas += not cazada
        print("  %s %s\n         %s" % ("OK   " if cazada else "FALLA", nombre, resultado))
    print("\n%d de %d cazadas." % (len(resultados) - malas, len(resultados)))
    return 1 if malas else 0


if __name__ == "__main__":
    raise SystemExit(main())
