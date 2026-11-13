# -*- coding: utf-8 -*-
"""
Los controles negativos de MP8: rompe la encuesta, /ajustes, la calibración y el arranque a propósito y
exige que sus pruebas fallen (test_encuesta, test_arranque y, para el descarte de prácticas,
test_generador).

Mismo método que mutaciones_mp5.py y mutaciones_mp6.py, con su copia del árbol: el repositorio no se
toca, y una mutación solo cuenta como cazada si alguna de las pruebas acaba con «FALLOS (», es decir, si
la caza una comprobación y no una excepción cualquiera.

Lo que se rompe es lo que MP8 promete y que, roto, no se notaría usando la encuesta: que un chat solo vea
su conversación (regla 5), que un botón de otro mensaje o de otro paso no haga nada, los límites del texto
libre y su escape, las coordenadas a 2 decimales, el umbral de la calibración, que el arranque solo toque
lo suyo (sus consultas, ni Jooble, ni las filas de los demás) y que su primera tanda vaya limitada, que
/ajustes de reglas a mano cambie solo sus claves y guarde la anterior, el recordatorio y el borrado, y el
descarte de prácticas.

No está en todas.py porque tarda: es una pasada de las pruebas enteras por mutación.

    PYTHONUTF8=1 python pruebas/mutaciones_mp8.py [--solo <trozo del nombre>]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutaciones_mp5 as base  # noqa: E402

PRUEBAS = ("test_encuesta.py", "test_arranque.py", "test_generador.py")

# (nombre, fichero, texto de ahora, texto roto)
MUTACIONES = [
    # --- Aislamiento (regla 5): cada chat, su conversación y solo la suya
    ("la conversación de todos los chats", "perfiles.py",
     '                              " WHERE chat_id = ?", (_exigir(chat_id),)).fetchone()\n    if fila is None:\n        return None\n    try:\n        borrador = json.loads',
     '                              " WHERE ? IS NOT NULL ORDER BY actualizado DESC", (_exigir(chat_id),)).fetchone()\n    if fila is None:\n        return None\n    try:\n        borrador = json.loads'),
    ("un botón de un mensaje viejo vale", "telegram_encuesta.py", "    if mensaje_id != guardado:", "    if False:"),
    ("un botón de otro paso vale", "telegram_encuesta.py", '    if paso is None or paso != b.get("paso"):', "    if paso is None:"),
    ("el borrador no se guarda en cada paso", "telegram_encuesta.py",
     "def _guardar(chat, b: dict, mensaje_id) -> None:\n    perfiles.guardar_conversacion(chat.chat_id, _clave_de_paso(b), b, mensaje_id)",
     "def _guardar(chat, b: dict, mensaje_id) -> None:\n    pass"),
    ("la calibración sale de la lista del titular", "telegram_encuesta.py",
     # Con la línea de después: desde el 07-11 `techo_de` lee lo mismo, el texto salía dos veces y esta
     # mutación llevaba desde entonces sin aplicarse («NO APLICA»), que es no vigilar nada.
     "    filas = perfiles.relevancias(chat_id)\n\n    def marcar_hecho(extra=None):",
     "    filas = perfiles.relevancias(perfiles.titular())\n\n    def marcar_hecho(extra=None):"),
    # --- Texto libre y ubicación
    ("un «Otro» sin tope de letras", "telegram_encuesta.py", "    if len(limpia) > MAX_FRASE:", "    if False:"),
    ("un texto sin tope de caracteres", "telegram_encuesta.py", '    if len(texto or "") > MAX_TEXTO:', "    if False:"),
    ("lo escrito se enseña sin escapar", "telegram_encuesta.py",
     '    return (_cabecera(b) + f"\\n{pregunta} Separadas por comas." + (f"\\nAhora: {_esc(ahora)}" if ahora else ""),',
     '    return (_cabecera(b) + f"\\n{pregunta} Separadas por comas." + (f"\\nAhora: {ahora}" if ahora else ""),'),
    ("la ubicación compartida sin redondear", "telegram_encuesta.py",
     "        coordenadas = (round(float(coordenadas[0]), 2), round(float(coordenadas[1]), 2))",
     "        coordenadas = (float(coordenadas[0]), float(coordenadas[1]))"),
    ("la ciudad confirmada sin redondear", "telegram_encuesta.py",
     '    return {**candidata, "lat": round(float(candidata["lat"]), 2), "lon": round(float(candidata["lon"]), 2)}',
     '    return {**candidata, "lat": float(candidata["lat"]), "lon": float(candidata["lon"])}'),
    ("el teclado de ubicación no se retira", "telegram_encuesta.py",
     '        retirada = _enviar(chat, "👍", {"remove_keyboard": True})', "        retirada = None"),
    # --- La calibración
    ("el umbral sale del rango", "telegram_encuesta.py",
     "    return round(min(UMBRAL_MAX, max(UMBRAL_MIN, umbral)), 1)", "    return round(umbral, 1)"),
    ("rechazar las mejores no sube el umbral", "telegram_encuesta.py",
     '    if sum(1 for v in votos_mejores if v == "no_encaja") >= 2:', '    if sum(1 for v in votos_mejores if v == "no_encaja") >= 4:'),
    ("el voto de calibración sin origen", "telegram_encuesta.py",
     '{"origen": "calibracion"}', "{}"),
    ("el borde de la calibración lejos del umbral", "telegram_encuesta.py",
     "    borde = sorted(resto, key=lambda f: (abs(f[1] - umbral), -f[1], f[0]))[:BORDE_DE_LA_CALIBRACION]",
     "    borde = resto[:BORDE_DE_LA_CALIBRACION]"),
    ("los avisos del arranque repiten la calibración", "telegram_encuesta.py",
     "    extra = [u for u in mejores_que_llegan if u not in ya][:max(0, faltan)]",
     "    extra = [u for u in mejores_que_llegan][:AVISOS_DEL_ARRANQUE]"),
    # --- La corrida de arranque
    ("el arranque parsea el catálogo", "telegram_encuesta.py",
     '    umbral = float(efectiva.get("umbral") or 5.0)\n    filas = perfiles.relevancias(chat_id)',
     '    umbral = float(efectiva.get("umbral") or 5.0)\n    menu()\n    filas = perfiles.relevancias(chat_id)'),
    ("el arranque sin tope de consultas", "buscador.py",
     "    elegidas = elegir_consultas(propio, ultimas, tope)", "    elegidas = propio"),
    ("el arranque pide Jooble", "buscador.py",
     "    _atender(en_corrida, elegidas, propio, [], config, al_evaluar, excluidas, progreso,",
     "    _atender(en_corrida, elegidas, propio, jooble_de_todos, config, al_evaluar, excluidas, progreso,"),
    ("el arranque borra las consultas de los demás", "buscador.py",
     "    plan_de_todos = planificar_consultas(activos, config)", "    plan_de_todos = planificar_consultas([perfil], config)"),
    ("el arranque hace consultas de todos", "buscador.py",
     "    propio = planificar_consultas([perfil], config)", "    propio = planificar_consultas(perfiles.activos() + [perfil], config)"),
    ("el arranque de un perfil que no está activo", "app.py",
     'perfil["estado"] != "activo" or (perfil["datos"].get("arranque")',
     '(perfil["datos"].get("arranque")'),
    ("el arranque pendiente no se recupera", "app.py",
     '        if (perfil["datos"].get("arranque") or {}).get("estado") == "pendiente" and perfil.get("chat_id") is not None:',
     "        if False:"),
    ("la primera tanda no se limita", "app.py",
     '            primera = not es_titular and (perfiles.contar_ofertas(chat_id) == 0\n                                          or bool(perfil["datos"].get("primera_pendiente")))',
     '            primera = not es_titular and (perfiles.contar_ofertas(chat_id) == 0\n                                          or False)'),
    ("la marca de primera tanda no se quita", "app.py",
     '                perfiles.actualizar_datos(perfil["chat_id"], lambda d: d.pop("primera_pendiente", None))',
     "                pass"),
    # --- /ajustes
    ("cambiar de sector no corta lo aprendido", "telegram_encuesta.py",
     '    cambio_de_sector = _familias_de(r) != _familias_de(respuestas_de(perfil["datos"])) if perfil else False',
     "    cambio_de_sector = False"),
    ("regenerar deshace lo aceptado con una sugerencia", "aprendizaje.py",
     '                    datos["experiencia_max_exigida"] = valor', "                    pass"),
    ("reglas a mano: se escribe más que sus claves", "telegram_encuesta.py",
     "        perfiles.escribir_claves_de_config(nuevos)", '        perfiles.escribir_claves_de_config({**nuevos, "reglas_descarte": []})'),
    ("reglas a mano: sin historial", "telegram_encuesta.py",
     "    _apuntar_historial(chat, codigo, antes)\n    if (chat.perfil", "    if (chat.perfil"),
    ("rehacer pierde las reglas a mano sin copia", "telegram_encuesta.py",
     '            datos["historial"] = ((datos.get("historial") or []) + [\n                {"fecha": perfiles._ahora(), "seccion": "rehacer con la encuesta", "datos_anteriores": anterior}])[-HISTORIAL_MAXIMO:]',
     '            datos["historial"] = datos.get("historial") or []'),
    ("el último ámbito se puede apagar", "telegram_encuesta.py",
     "        if not nuevo and sum(1 for a in ambitos.values() if a.get(\"activo\", True)) <= 1:",
     "        if False:"),
    # --- Recordatorio y borrado
    ("el recordatorio se repite", "telegram_encuesta.py",
     '        elif ahora - tocada >= timedelta(hours=HORAS_PARA_RECORDAR) and not b.get("recordada"):',
     "        elif ahora - tocada >= timedelta(hours=HORAS_PARA_RECORDAR):"),
    ("el borrador no se borra a los 7 días", "telegram_encuesta.py",
     "        if ahora - tocada >= timedelta(days=DIAS_PARA_BORRAR):", "        if False:"),
    # --- Prácticas
    ("el plan no descarta las prácticas", "perfilado/generador.py",
     '    if not acepta_practicas and DESCARTE_DE_PRACTICAS["nombre"] not in vistos:', "    if False:"),
]


def pasar_prueba(arbol: Path) -> tuple[int, str]:
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
    entorno.update(PYTHONUTF8="1")
    codigo, salida = 0, ""
    for prueba in PRUEBAS:
        proceso = subprocess.run([sys.executable, str(arbol / "pruebas" / prueba)], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=entorno)
        codigo = codigo or proceso.returncode
        salida += proceso.stdout + proceso.stderr
        if proceso.returncode:
            break  # con una que falle, ya está cazada: no hace falta pasar las demás
    return codigo, salida


base.MUTACIONES = MUTACIONES
base.pasar_prueba = pasar_prueba

if __name__ == "__main__":
    raise SystemExit(base.main())
