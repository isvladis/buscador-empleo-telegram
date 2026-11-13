"""
Entrena el modelo de relevancia con tu historial real de decisiones y lo compara,
con la misma validación cruzada, contra los pesos escritos a mano de `buscador.py`.

    D:\\Dev\\Envs\\venvs\\jobspy312\\Scripts\\python.exe entrenar_modelo.py

Sin argumentos: construye el dataset, valida, imprime el informe y guarda
`modelo_relevancia.json`. Vuelve a ejecutarlo cada vez que hayas marcado unas cuantas
ofertas más como aplicada u oculta; no hay que tocar código.

Opciones:
    --solo-comparar   valida e informa, pero no escribe el modelo
    --lam N           fuerza la regularización (por defecto la elige la validación)
    --repes N         repeticiones de la validación cruzada (por defecto 10)
    --k N             número de folds (por defecto 5)

El dataset sale de cruzar las marcas con las ofertas del titular por `job_url`, con tres
clases: aplicada = 1.0, favorita = PESO_FAVORITA (interés real pero sin decisión
tomada), oculta = 0.0. Las favoritas entrenan como señal intermedia (más débil que
aplicada) pero NO cuentan para medir el modelo: la comparación con las reglas sigue
siendo aplicada-vs-oculta, que son las únicas dos decisiones tomadas de verdad.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

import almacen
import modelo_relevancia as mr

# Mínimo por debajo del cual no tiene sentido ni intentarlo: con menos positivos que
# esto la validación cruzada no distingue nada y el modelo memoriza. Se mide solo sobre
# aplicadas: las favoritas son una señal más débil y no deberían poder desbloquear el
# entrenamiento por sí solas.
MIN_POSITIVOS = 15

# Target de entrenamiento para una favorita, entre oculta (0.0) y aplicada (1.0). No es
# 0.5 (punto medio matemático) porque una favorita es, por definición, una oferta que ya
# pasó el primer filtro de interés real — más cerca de "sí" que de "neutral". Tampoco
# tan cerca de 1.0 como aplicada: no hay decisión tomada, y con el tiempo demostrará ser
# tanto un "la apliqué luego" como un "al final no, por lo que sea". 0.65 es una elección
# razonada, no medida: no hay (todavía) suficientes favoritas resueltas para calibrarla
# con datos.
PESO_FAVORITA = 0.65

LAMS = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0]

# Qué texto entra en el modelo. Medido con esta misma validación sobre el historial
# de la instalación original, AUC(fold) del modelo puro:
#
#   título, palabras sueltas                     0,733
#   título, palabras + pares                     0,729
#   título + ubicación, palabras sueltas         0,777  <- lo que se usa
#   título + ubicación, palabras + pares         0,766
#
# La ubicación aporta de verdad (dónde está la oferta decide si es viable). Los pares de
# palabras no: multiplican los parámetros por seis y con 29 positivos eso solo sirve para
# memorizar títulos concretos. La descripción se probó aparte y también empeora
# (0,761 frente a 0,773 sin ella, sobre las mismas particiones).
NGRAMAS = (1,)
USAR_UBICACION = True
MIN_DF = 2
ITERS_CV = 400


# ----------------------------------------------------------------------
# DATASET
# ----------------------------------------------------------------------


def construir_dataset() -> list[dict]:
    """
    Cruza las marcas con las ofertas. El título se toma de la oferta guardada y, si
    esa oferta ya no está en la base, del que `almacen.marcar()` copió en el estado:
    así no se pierden ejemplos por haber limpiado la base después de decidir.

    Tres clases, con el target de entrenamiento en `y` y la clase de origen en `clase`
    (esta última es la que decide si la fila cuenta para EVALUAR el modelo: solo
    aplicada/oculta son decisiones tomadas, favorita es señal de entrenamiento nada más
    — ver `validar()` y su parámetro `eval_mask`).
    """
    estado = almacen.cargar_estado()
    por_url = {o["job_url"]: o for o in almacen.cargar_ofertas()}

    filas = []
    for clave, etiqueta in (("aplicadas", 1.0), ("favoritas", PESO_FAVORITA), ("ocultas", 0.0)):
        for url, info in (estado.get(clave) or {}).items():
            oferta = por_url.get(url) or {}
            titulo = oferta.get("title") or (info or {}).get("titulo") or ""
            if not str(titulo).strip():
                continue
            filas.append({
                "url": url,
                "clase": clave,
                "y": etiqueta,
                "title": str(titulo),
                "location": str(oferta.get("location") or ""),
            })
    return filas


# ----------------------------------------------------------------------
# VALIDACIÓN
# ----------------------------------------------------------------------


def puntuaciones_reglas(filas) -> np.ndarray:
    import buscador
    return np.array(
        [buscador.puntuar_con_reglas(f["title"], f["location"] or None) for f in filas],
        dtype=float,
    )


def _metricas(y, s) -> dict:
    return {
        "auc": mr.auc(y, s),
        "ap": mr.average_precision(y, s),
        "r10": mr.recall_en_top(y, s, 0.10),
        "r20": mr.recall_en_top(y, s, 0.20),
    }


def validar(filas, y, grupos, s_reglas, lams=LAMS, k=5, repes=10,
            usar_ubicacion=USAR_UBICACION, ngramas=NGRAMAS, min_df=MIN_DF,
            eval_mask=None):
    """
    Validación cruzada agrupada por título, repetida con distintas particiones.

    Tres cosas importantes para que la comparación sea honesta:
      * el TF-IDF se ajusta DENTRO de cada fold, solo con el trozo de entrenamiento;
      * los títulos repetidos van juntos al mismo fold (mr.grupos_por_titulo), si no
        el modelo valida sobre anuncios que ha visto literalmente;
      * las reglas se miden sobre exactamente las mismas particiones, aunque no se
        entrenen, para poder restar resultado a resultado en vez de comparar contra
        un número calculado sobre otro conjunto.

    `eval_mask` (si se pasa) marca qué filas cuentan para las MÉTRICAS: entrenar puede
    usar todas las filas de `y` (incluidas las favoritas, con su target intermedio),
    pero medir AUC/AP/recall solo tiene sentido sobre decisiones tomadas de verdad
    (aplicada/oculta). Sin `eval_mask`, se evalúa sobre todo `y` (comportamiento de
    siempre, para cuando el dataset ya viene sin favoritas).
    """
    if eval_mask is None:
        eval_mask = np.ones(len(y), dtype=bool)
    docs = [mr.tokens(f["title"], f["location"] if usar_ubicacion else None, ngramas)
            for f in filas]

    claves = [("reglas", None)]
    claves += [(familia, lam) for lam in lams for familia in ("modelo", "hibrido")]
    resultados: dict[tuple, list] = {c: [] for c in claves}

    for rep in range(repes):
        print(f"  partición {rep + 1}/{repes}...", end="\r", flush=True)
        fold = mr.folds_agrupados(y, grupos, k, np.random.default_rng(1000 + rep))
        oof = {clave: np.full(len(y), np.nan) for clave in resultados}
        for f in range(k):
            tr, te = fold != f, fold == f
            if y[tr].sum() == 0 or y[te].sum() == 0:
                continue
            docs_tr = [d for d, m in zip(docs, tr) if m]
            docs_te = [d for d, m in zip(docs, te) if m]
            vec = mr.TfIdf(min_df=min_df).fit(docs_tr)
            Xtr, Xte = vec.transform(docs_tr), vec.transform(docs_te)
            # El híbrido añade la puntuación de las reglas como una columna más: si esa
            # señal aporta algo que el TF-IDF no tiene, la logística le dará peso. Se
            # reutiliza el TF-IDF ya calculado en vez de volver a vectorizar.
            Htr = mr.con_columna_reglas(Xtr, s_reglas[tr])
            Hte = mr.con_columna_reglas(Xte, s_reglas[te])
            for lam in lams:
                # ITERS_CV, no el tope por defecto: aquí se ajustan cientos de modelos y
                # de ellos solo se mira el ORDEN que producen, que se estabiliza mucho
                # antes de que los coeficientes converjan del todo (comprobado: a partir
                # de ~300 iteraciones el AUC no se mueve ni en la cuarta cifra). El
                # modelo que se guarda sí se ajusta hasta el final.
                w, b = mr.entrenar_logistica(Xtr, y[tr], lam=lam, iters=ITERS_CV)
                oof[("modelo", lam)][te] = Xte @ w + b
                w, b = mr.entrenar_logistica(Htr, y[tr], lam=lam, iters=ITERS_CV)
                oof[("hibrido", lam)][te] = Hte @ w + b
        oof[("reglas", None)] = s_reglas.copy()

        for clave, s in oof.items():
            m = ~np.isnan(s) & eval_mask
            por_fold = [mr.auc(y[(fold == f) & m], s[(fold == f) & m])
                        for f in range(k) if y[(fold == f) & m].sum() > 0]
            fila = _metricas(y[m], s[m])
            fila["auc_fold"] = float(np.mean(por_fold))
            resultados[clave].append(fila)

    print(" " * 40, end="\r")
    return {clave: {
        met: np.array([r[met] for r in lista])
        for met in ("auc", "ap", "r10", "r20", "auc_fold")
    } for clave, lista in resultados.items()}


def nombre(clave) -> str:
    familia, lam = clave
    return familia if lam is None else f"{familia} lam={lam}"


def informe(res):
    ref = res[("reglas", None)]["auc_fold"]
    print(f"{'sistema':22s} {'AUC(fold)':>16s} {'AUC':>7s} {'AP':>7s} "
          f"{'R@10%':>7s} {'R@20%':>7s} {'vs reglas':>11s}")
    print("-" * 82)
    for clave, m in res.items():
        d = m["auc_fold"] - ref
        extra = "" if clave[0] == "reglas" else (
            f"{d.mean():+.4f} ({int((d > 0).sum())}/{len(d)})")
        print(f"{nombre(clave):22s} {m['auc_fold'].mean():.4f}+-{m['auc_fold'].std():.4f} "
              f"{m['auc'].mean():7.4f} {m['ap'].mean():7.4f} "
              f"{m['r10'].mean():7.3f} {m['r20'].mean():7.3f} {extra:>11s}")
    print()
    print("AUC(fold): AUC medio calculado dentro de cada fold (métrica principal; no")
    print("  mezcla las escalas de modelos distintos). AUC/AP/R@: sobre las predicciones")
    print("  out-of-fold puestas en común. «vs reglas»: diferencia media de AUC(fold)")
    print("  sobre las MISMAS particiones, y en cuántas de ellas gana el modelo.")


# ----------------------------------------------------------------------
# ENTRENAMIENTO FINAL
# ----------------------------------------------------------------------


def corpus_calibracion() -> list[dict]:
    """
    Ofertas sobre las que se calibra la escala: la base actual, que es la población que
    la interfaz pinta de verdad.

    No sirve calibrar con el dataset de entrenamiento: ahí el 97 % son ofertas ocultadas
    (lo malo, por definición), así que sus percentiles no se parecen a los de la lista
    que se ve en pantalla y los cortes «Encaje alto / medio» caerían donde no toca.
    """
    return [{"title": str(o.get("title") or ""),
             "location": str(o.get("location") or "")}
            for o in almacen.cargar_ofertas() if str(o.get("title") or "").strip()]


def entrenar_final(filas, y, s_reglas, lam, hibrido=False,
                   usar_ubicacion=USAR_UBICACION, ngramas=NGRAMAS, min_df=MIN_DF,
                   metricas=None) -> dict:
    """Reajusta con TODOS los ejemplos y calibra a la escala de las reglas."""
    def tokenizar(lista):
        return [mr.tokens(f["title"], f["location"] if usar_ubicacion else None, ngramas)
                for f in lista]

    docs = tokenizar(filas)
    vec, w, b, peso_reglas = mr.ajustar(
        docs, y, lam=lam, min_df=min_df, s_reglas=s_reglas if hibrido else None)

    # La calibración se mide sobre la base de ofertas, no sobre el entrenamiento.
    ref = corpus_calibracion() or filas
    s_reglas_ref = puntuaciones_reglas(ref)
    brutos = mr.matriz(vec, tokenizar(ref), s_reglas_ref if hibrido else None) @ (
        np.append(w, peso_reglas) if hibrido else w) + b
    calib = mr.construir_calibracion(brutos, s_reglas_ref)

    # n_positivos cuenta aplicadas de verdad, no la suma de targets blandos (que
    # mezclaría aplicadas y favoritas en un número sin significado directo).
    n_aplicadas = sum(1 for f in filas if f["clase"] == "aplicadas")
    return mr.a_diccionario(
        vec, w, b, lam=lam, min_df=min_df, ngramas=ngramas,
        usar_ubicacion=usar_ubicacion, peso_reglas=peso_reglas,
        calibracion=calib, metricas=metricas,
        n_ejemplos=len(y), n_positivos=n_aplicadas,
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--solo-comparar", action="store_true",
                   help="valida e informa, pero no guarda el modelo")
    p.add_argument("--lam", type=float, default=None,
                   help="regularización fija (si no, la elige la validación)")
    p.add_argument("--repes", type=int, default=10)
    p.add_argument("--k", type=int, default=5)
    args = p.parse_args()

    filas = construir_dataset()
    n_aplicadas = sum(1 for f in filas if f["clase"] == "aplicadas")
    n_favoritas = sum(1 for f in filas if f["clase"] == "favoritas")
    n_ocultas = sum(1 for f in filas if f["clase"] == "ocultas")
    print(f"Dataset: {len(filas)} ejemplos  "
          f"({n_aplicadas} aplicadas, {n_favoritas} favoritas, {n_ocultas} ocultas)")
    if n_aplicadas < MIN_POSITIVOS:
        print(f"Solo hay {n_aplicadas} ofertas aplicadas: hacen falta al menos {MIN_POSITIVOS} "
              f"para que la validación diga algo. No se entrena nada.")
        return 1

    def _preparar(filas):
        y = np.array([f["y"] for f in filas])
        eval_mask = np.array([f["clase"] != "favoritas" for f in filas])
        grupos = mr.grupos_por_titulo([f["title"] for f in filas])
        s_reglas = puntuaciones_reglas(filas)
        return y, eval_mask, grupos, s_reglas

    y, eval_mask, grupos, s_reglas = _preparar(filas)
    print(f"Títulos distintos: {len(np.unique(grupos))} "
          f"(las copias del mismo anuncio van al mismo fold)")
    print()

    lams = [args.lam] if args.lam else LAMS
    print(f"Validación cruzada agrupada, k={args.k}, {args.repes} particiones distintas...")

    # Comparación honesta de si las favoritas ayudan: se entrena y valida DOS veces, con
    # y sin ellas en el dataset de entrenamiento, midiendo siempre sobre las mismas
    # decisiones tomadas (aplicada/oculta vía eval_mask). Si hoy no hay ninguna favorita,
    # las dos pasadas son literalmente el mismo dataset, así que no tiene sentido
    # duplicar el cálculo: se hace una sola vez y se dice explícitamente por qué.
    if n_favoritas == 0:
        print("Sin favoritas todavía: este resultado es idéntico a entrenar solo con "
              "aplicadas/ocultas. No hay nada que comparar hasta que marques alguna.")
        res = validar(filas, y, grupos, s_reglas, lams=lams, k=args.k, repes=args.repes,
                       eval_mask=eval_mask)
        res_sin_favoritas = res
    else:
        filas_sin_favoritas = [f for f in filas if f["clase"] != "favoritas"]
        y0, m0, g0, s0 = _preparar(filas_sin_favoritas)
        print(f"\n--- Sin favoritas ({n_aplicadas} aplicadas / {n_ocultas} ocultas) ---")
        res_sin_favoritas = validar(filas_sin_favoritas, y0, g0, s0, lams=lams, k=args.k,
                                    repes=args.repes, eval_mask=m0)
        informe(res_sin_favoritas)

        print(f"\n--- Con {n_favoritas} favorita(s) como señal intermedia "
              f"(target {PESO_FAVORITA}) ---")
        res = validar(filas, y, grupos, s_reglas, lams=lams, k=args.k, repes=args.repes,
                       eval_mask=eval_mask)
    print()
    informe(res)
    print()

    if n_favoritas > 0:
        cand_con = {c: m for c, m in res.items() if c[0] != "reglas"}
        mejor_con = max(cand_con, key=lambda c: cand_con[c]["auc_fold"].mean())
        cand_sin = {c: m for c, m in res_sin_favoritas.items() if c[0] != "reglas"}
        mejor_sin = max(cand_sin, key=lambda c: cand_sin[c]["auc_fold"].mean())
        auc_con = cand_con[mejor_con]["auc_fold"].mean()
        auc_sin = cand_sin[mejor_sin]["auc_fold"].mean()
        print(f"Favoritas como señal de entrenamiento: AUC(fold) {auc_sin:.4f} sin ellas "
              f"-> {auc_con:.4f} con ellas ({auc_con - auc_sin:+.4f}).")
        if abs(auc_con - auc_sin) < 0.005:
            print(f"=> Diferencia dentro del ruido: con solo {n_favoritas} favorita(s) "
                  "todavía no hay volumen suficiente para que se note. El mecanismo queda "
                  "listo para cuando se acumulen más.")
        elif auc_con > auc_sin:
            print("=> Ayudan: el modelo generaliza algo mejor incorporándolas.")
        else:
            print("=> De momento no ayudan con este volumen; puede cambiar con más.")
        print()

    # Se elige por AUC(fold): es la métrica que no mezcla escalas entre folds.
    candidatos = {c: m for c, m in res.items() if c[0] != "reglas"}
    mejor = max(candidatos, key=lambda c: candidatos[c]["auc_fold"].mean())
    d = candidatos[mejor]["auc_fold"] - res[("reglas", None)]["auc_fold"]
    gana = int((d > 0).sum())
    print(f"Mejor candidato: {nombre(mejor)}  "
          f"(AUC(fold) {candidatos[mejor]['auc_fold'].mean():.4f} frente a "
          f"{res[('reglas', None)]['auc_fold'].mean():.4f} de las reglas)")
    print(f"Diferencia media {d.mean():+.4f}; gana en {gana} de {len(d)} particiones.")
    if d.mean() <= 0:
        print("=> Las reglas a mano siguen siendo mejores. Deja "
              "'usar_modelo_entrenado' en false en config.json.")
    elif gana < len(d):
        print("=> Gana, pero no en todas las particiones: la ventaja está dentro del "
              "ruido que da tener tan pocos positivos.")
    else:
        print("=> El modelo gana en todas las particiones.")
    print()

    if args.solo_comparar:
        print("--solo-comparar: no se guarda nada.")
        return 0

    metricas = {
        "validacion": "cv agrupada por titulo",
        "k": args.k, "repeticiones": args.repes,
        "modelo": {m: float(v.mean()) for m, v in candidatos[mejor].items()},
        "reglas": {m: float(v.mean()) for m, v in res[("reglas", None)].items()},
        "candidato": nombre(mejor),
        "n_favoritas": n_favoritas,
        "peso_favorita": PESO_FAVORITA,
    }
    familia, lam_final = mejor
    hibrido = familia == "hibrido"
    modelo = entrenar_final(filas, y, s_reglas, lam=lam_final, hibrido=hibrido,
                            metricas=metricas)
    mr.guardar(modelo)
    print(f"Modelo guardado en {mr.MODELO_PATH.name}: {familia}, "
          f"{len(modelo['vocabulario'])} tokens, lam={lam_final}.")
    if hibrido:
        print(f"Es un híbrido: usa además la puntuación por reglas, con peso "
              f"{modelo['peso_reglas']:+.3f}. buscador.py se la pasa solo.")

    import buscador

    cargado = mr.cargar()
    ejemplos = ["Analista SOC Junior", "Senior Java Developer",
                "Técnico/a Helpdesk (Junior)", "SAP Consultant"]
    print("\nComprobación rápida (puntuación en la escala de siempre):")
    for t in ejemplos:
        reglas = buscador.puntuar_con_reglas(t, "Barcelona, Spain")
        print(f"  {t:34s} modelo {cargado.puntuar(t, 'Barcelona, Spain', reglas):5.1f}   "
              f"reglas {reglas:5.1f}")

    pesos = sorted(((v[1], t) for t, v in modelo["vocabulario"].items()), reverse=True)
    print("\nTokens que más suman:", ", ".join(t for _, t in pesos[:12]))
    print("Tokens que más restan:", ", ".join(t for _, t in pesos[-8:]))
    if d.mean() > 0:
        print('\nPara usarlo: pon "usar_modelo_entrenado": true en config.json.')
    else:
        print('\nSe ha guardado de todas formas, por si quieres probarlo '
              '("usar_modelo_entrenado": true), pero los números de arriba dicen que '
              'de momento no compensa.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
