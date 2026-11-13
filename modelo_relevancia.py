"""
Modelo de relevancia aprendido del historial real de decisiones (aplicada / oculta).

Alternativa a los pesos escritos a mano de `buscador.puntuar_relevancia()`. Aquí vive
todo lo que necesitan tanto el entrenamiento (`entrenar_modelo.py`) como la puntuación
en caliente (`buscador.py`): tokenización, TF-IDF, regresión logística, métricas y
validación cruzada.

Solo depende de numpy y la biblioteca estándar. Con ~1.100 ejemplos y ~30 positivos no
hay ninguna razón para arrastrar scikit-learn: la regresión logística son treinta líneas
y guardar el modelo como JSON legible (en vez de un pickle) evita que una actualización
de librería deje el archivo ilegible y permite abrirlo y ver qué palabra pesa cuánto.

El modelo entrenado se guarda en `modelo_relevancia.json`.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

# Ver la misma variable en buscador.py/almacen.py: sin JOBSPY_DATA_DIR, se comporta
# igual que siempre (el JSON junto al código); con ella, apunta al volumen de Docker.
DIR_BASE = Path(os.environ.get("JOBSPY_DATA_DIR", str(Path(__file__).parent)))
MODELO_PATH = DIR_BASE / "modelo_relevancia.json"
VERSION_FORMATO = 1


# ----------------------------------------------------------------------
# TEXTO
# ----------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9+#]+")


def normalizar(texto) -> str:
    """Minúsculas y sin acentos: «Técnico» y «tecnico» tienen que ser el mismo token."""
    if texto is None:
        return ""
    if isinstance(texto, float) and math.isnan(texto):
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _palabras(texto: str) -> list[str]:
    return _TOKEN.findall(normalizar(texto))


def tokens(titulo, ubicacion=None, ngramas=(1, 2)) -> list[str]:
    """
    Tokens de una oferta. El prefijo separa los espacios de nombres: "barcelona" en el
    título (la empresa lo ha puesto ahí a propósito) no es lo mismo que "barcelona" en
    la ubicación, y mezclarlos haría que el modelo no pudiera distinguirlos.
    """
    p = _palabras(titulo)
    out = [f"t:{w}" for w in p] if 1 in ngramas else []
    if 2 in ngramas:
        out += [f"t:{a}_{b}" for a, b in zip(p, p[1:])]
    if ubicacion:
        out += [f"l:{w}" for w in _palabras(ubicacion)]
    return out


# ----------------------------------------------------------------------
# TF-IDF
# ----------------------------------------------------------------------


class TfIdf:
    """TF-IDF con suavizado y normalización L2, equivalente a TfidfVectorizer(sublinear_tf=False).

    `min_df` descarta los tokens que aparecen en menos de N ofertas: con 30 positivos,
    una palabra vista una sola vez solo puede memorizar esa oferta concreta.
    """

    def __init__(self, min_df: int = 2):
        self.min_df = min_df
        self.vocab: dict[str, int] = {}
        self.idf = np.zeros(0)

    def fit(self, docs: list[list[str]]) -> "TfIdf":
        df = Counter()
        for d in docs:
            df.update(set(d))
        terminos = sorted(t for t, c in df.items() if c >= self.min_df)
        self.vocab = {t: i for i, t in enumerate(terminos)}
        n = len(docs)
        self.idf = np.array([math.log((1 + n) / (1 + df[t])) + 1.0 for t in terminos])
        return self

    def transform(self, docs: list[list[str]]) -> np.ndarray:
        X = np.zeros((len(docs), len(self.vocab)))
        for i, d in enumerate(docs):
            for tok in d:
                j = self.vocab.get(tok)
                if j is not None:
                    X[i, j] += 1.0
        X *= self.idf
        norma = np.linalg.norm(X, axis=1, keepdims=True)
        norma[norma == 0] = 1.0
        return X / norma


# ----------------------------------------------------------------------
# REGRESIÓN LOGÍSTICA (L2, sin penalizar el sesgo)
# ----------------------------------------------------------------------


def _norma_espectral2(X: np.ndarray, iters: int = 30) -> float:
    """||X||₂² por iteración de potencia. El SVD completo daría lo mismo pero domina
    el coste cuando se entrenan cientos de modelos en validación cruzada."""
    if X.size == 0:
        return 0.0
    v = np.ones(X.shape[1]) / math.sqrt(X.shape[1])
    val = 0.0
    for _ in range(iters):
        v = X.T @ (X @ v)
        nv = float(np.linalg.norm(v))
        if nv == 0:
            return 0.0
        v /= nv
        val = nv
    return val * 1.02  # margen: la iteración converge por debajo del valor real


def entrenar_logistica(X, y, lam=2.0, peso_clase=None, iters=2000, tol=1e-9):
    """
    Ajusta w, b minimizando la log-verosimilitud con penalización L2 sobre w.

    Gradiente acelerado (FISTA) con paso fijo 1/L, siendo L la cota de Lipschitz del
    gradiente: converge sin necesidad de ajustar la tasa de aprendizaje ni depender de
    un optimizador externo. Comprobado contra sklearn.LogisticRegression: los
    coeficientes coinciden hasta ~1e-7 con el mismo objetivo (C = 1/lam).
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    if peso_clase is None:
        sw = np.ones(n)
    else:
        sw = np.where(y == 1, peso_clase[1], peso_clase[0]).astype(float)
    sw = sw / sw.sum() * n

    L = 0.25 * (_norma_espectral2(X) + n) * sw.max() + lam
    paso = 1.0 / L

    w = np.zeros(d)
    b = 0.0
    wz, bz, t = w.copy(), b, 1.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ wz + bz, -35, 35)))
        r = sw * (p - y)
        w_new = wz - paso * (X.T @ r + lam * wz)
        b_new = bz - paso * r.sum()
        if np.max(np.abs(w_new - w)) < tol and abs(b_new - b) < tol:
            return w_new, float(b_new)
        t_new = (1 + math.sqrt(1 + 4 * t * t)) / 2
        wz = w_new + ((t - 1) / t_new) * (w_new - w)
        bz = b_new + ((t - 1) / t_new) * (b_new - b)
        w, b, t = w_new, b_new, t_new
    return w, float(b)


# ----------------------------------------------------------------------
# MÉTRICAS
# ----------------------------------------------------------------------


def auc(y, s) -> float:
    """Área bajo la curva ROC = P(una aplicada puntúa más alto que una ocultada).
    Calculada por rangos (Mann-Whitney), con medio punto por cada empate."""
    y = np.asarray(y)
    s = np.asarray(s, dtype=float)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    orden = np.argsort(s, kind="mergesort")
    s_ord = s[orden]
    rangos = np.empty(len(s))
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s_ord[j + 1] == s_ord[i]:
            j += 1
        rangos[i:j + 1] = (i + j) / 2.0 + 1.0
        i = j + 1
    r = np.empty(len(s))
    r[orden] = rangos
    return float((r[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def average_precision(y, s) -> float:
    """Precisión media. Con 29 positivos entre 1.104 es más informativa que el AUC:
    penaliza tener basura arriba del todo, que es justo lo que se nota al usar la app."""
    y = np.asarray(y)
    orden = np.argsort(-np.asarray(s, dtype=float), kind="mergesort")
    yv = y[orden]
    tp = np.cumsum(yv)
    prec = tp / np.arange(1, len(yv) + 1)
    return float((prec * yv).sum() / max(yv.sum(), 1))


def recall_en_top(y, s, frac: float) -> float:
    """Qué porcentaje de las aplicadas cae en el mejor `frac` del ranking."""
    y = np.asarray(y)
    k = max(1, int(round(len(y) * frac)))
    orden = np.argsort(-np.asarray(s, dtype=float), kind="mergesort")
    return float(y[orden][:k].sum() / max(y.sum(), 1))


# ----------------------------------------------------------------------
# VALIDACIÓN CRUZADA AGRUPADA
# ----------------------------------------------------------------------


def grupos_por_titulo(titulos: list[str]) -> np.ndarray:
    """
    Agrupa los títulos que son «el mismo anuncio». La misma vacante aparece repetida
    (tres «Analista de Ciberseguridad (SOC 24/7) - Barcelona» de empresas distintas,
    dos «Junior IT Support Specialist»...). Si una copia cae en entrenamiento y otra en
    validación, el modelo la ha visto literalmente y el AUC sale inflado. Todas las
    copias van al mismo fold.
    """
    clave_a_id: dict[str, int] = {}
    return np.array([
        clave_a_id.setdefault(" ".join(sorted(set(_palabras(t)))), len(clave_a_id))
        for t in titulos
    ])


def folds_agrupados(y, grupos, k: int, rng) -> np.ndarray:
    """
    Reparte los grupos en k folds: primero los que contienen positivos (equilibrando
    positivos), después el resto equilibrando solo tamaño.

    Los dos pases son necesarios. Con un único pase que mire siempre el número de
    positivos, todos los grupos sin positivos caen en el mismo fold (añadirlos nunca
    sube su cuenta de positivos, así que sigue siendo el mínimo) y los folds salen
    absurdamente desequilibrados.
    """
    y = np.asarray(y)
    unicos = np.unique(grupos)
    rng.shuffle(unicos)
    tam = {g: int((grupos == g).sum()) for g in unicos}
    pos = {g: int(y[grupos == g].sum()) for g in unicos}

    asign: dict[int, int] = {}
    carga_pos = np.zeros(k)
    carga_n = np.zeros(k)
    for g in sorted([g for g in unicos if pos[g]], key=lambda g: -pos[g]):
        f = int(np.lexsort((carga_n, carga_pos))[0])
        asign[g] = f
        carga_pos[f] += pos[g]
        carga_n[f] += tam[g]
    for g in sorted([g for g in unicos if not pos[g]], key=lambda g: -tam[g]):
        f = int(np.argmin(carga_n))
        asign[g] = f
        carga_n[f] += tam[g]
    return np.array([asign[g] for g in grupos])


# ----------------------------------------------------------------------
# CALIBRACIÓN A LA ESCALA DE SIEMPRE
# ----------------------------------------------------------------------
#
# La interfaz tiene los cortes escritos a mano: «Encaje alto» ≥ 5, «Encaje medio» ≥ 3
# (templates/index.html). Un modelo logístico devuelve log-odds en un rango que no
# tiene nada que ver (más o menos −9…+2), así que soltarlo tal cual dejaría todas las
# ofertas sin etiqueta y sin ningún aviso.
#
# En vez de cambiar los cortes, se traslada el modelo a la escala vieja: se guardan los
# percentiles de la puntuación del modelo sobre la base de ofertas y los de la puntuación
# por reglas sobre esa misma base, y se interpola entre unos y otros. El resultado tiene
# la misma distribución que tenían las reglas — «Encaje alto» sigue siendo «el 10 % mejor»
# y «Encaje medio» «el 30 % mejor» —, solo que el ORDEN lo decide el modelo, que es lo
# único que se quería cambiar.

PERCENTILES = list(range(0, 101, 2))


def construir_calibracion(s_modelo, s_referencia) -> dict:
    return {
        "percentiles": PERCENTILES,
        "modelo": [float(v) for v in np.percentile(s_modelo, PERCENTILES)],
        "referencia": [float(v) for v in np.percentile(s_referencia, PERCENTILES)],
    }


def aplicar_calibracion(valor: float, calib: dict | None) -> float:
    if not calib:
        return float(valor)
    x = np.asarray(calib["modelo"], dtype=float)
    fp = np.asarray(calib["referencia"], dtype=float)
    # np.interp exige xp estrictamente creciente; los percentiles lo son salvo empates,
    # que se rompen con un incremento a escala del propio dato.
    paso = max(float(np.abs(x).max()), 1.0) * 1e-9
    x = np.maximum.accumulate(x) + np.arange(len(x)) * paso
    # Fuera del rango, np.interp devuelve los extremos: una oferta rarísima se queda en
    # el tope de la escala en vez de dispararse a un número que la interfaz no espera.
    return float(np.interp(float(valor), x, fp))


# ----------------------------------------------------------------------
# MODELO ENTRENADO: ENTRENAR / GUARDAR / CARGAR / PUNTUAR
# ----------------------------------------------------------------------


# La puntuación de las reglas, cuando entra como columna extra en el híbrido, se divide
# por esto para dejarla en el mismo orden de magnitud que el TF-IDF normalizado. Tiene
# que ser el MISMO número al validar y al puntuar, o el peso aprendido no significa lo
# mismo en los dos sitios.
ESCALA_REGLAS = 10.0


def con_columna_reglas(X: np.ndarray, s_reglas) -> np.ndarray:
    """Añade la puntuación de las reglas como última columna de una matriz TF-IDF."""
    return np.hstack([X, np.asarray(s_reglas, dtype=float).reshape(-1, 1) / ESCALA_REGLAS])


def matriz(vec: TfIdf, docs, s_reglas=None) -> np.ndarray:
    """TF-IDF, más la puntuación de las reglas como última columna si se pasa."""
    X = vec.transform(docs)
    return X if s_reglas is None else con_columna_reglas(X, s_reglas)


def ajustar(docs: list[list[str]], y, lam=2.0, min_df=2, s_reglas=None):
    """Ajusta vectorizador + logística. Devuelve (vec, w, b, peso_reglas)."""
    vec = TfIdf(min_df=min_df).fit(docs)
    w, b = entrenar_logistica(matriz(vec, docs, s_reglas), y, lam=lam)
    if s_reglas is None:
        return vec, w, b, None
    return vec, w[:-1], b, float(w[-1])


def a_diccionario(vec: TfIdf, w, b, *, lam, min_df, ngramas, usar_ubicacion,
                  peso_reglas, calibracion, metricas, n_ejemplos, n_positivos) -> dict:
    """Serializa el modelo. `idf` y `peso` van por token para poder leerlo a ojo."""
    terminos = sorted(vec.vocab, key=lambda t: vec.vocab[t])
    return {
        "version_formato": VERSION_FORMATO,
        "entrenado_en": datetime.now().isoformat(timespec="seconds"),
        "n_ejemplos": int(n_ejemplos),
        "n_positivos": int(n_positivos),
        "hiperparametros": {
            "lam": lam, "min_df": min_df,
            "ngramas": list(ngramas), "usar_ubicacion": bool(usar_ubicacion),
        },
        "sesgo": float(b),
        # Distinto de None => híbrido: hace falta pasarle también la puntuación por
        # reglas para poder puntuar (ver ModeloCargado.usa_reglas).
        "peso_reglas": None if peso_reglas is None else float(peso_reglas),
        "vocabulario": {
            t: [round(float(vec.idf[i]), 6), round(float(w[i]), 6)]
            for i, t in enumerate(terminos)
        },
        "calibracion": calibracion,
        "metricas": metricas,
    }


def guardar(modelo: dict, path: Path = MODELO_PATH) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(modelo, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class ModeloCargado:
    """Modelo listo para puntuar. Se construye una vez y se reutiliza."""

    def __init__(self, datos: dict):
        self.datos = datos
        hp = datos.get("hiperparametros", {})
        self.ngramas = tuple(hp.get("ngramas", [1, 2]))
        self.usar_ubicacion = bool(hp.get("usar_ubicacion", True))
        self.sesgo = float(datos.get("sesgo", 0.0))
        self.calibracion = datos.get("calibracion")
        self.peso_reglas = datos.get("peso_reglas")
        # {token: (idf, peso)} — puntuar es recorrer los tokens de un título, así que
        # un dict directo es más rápido y más simple que reconstruir matrices.
        self.vocabulario = {
            t: (float(v[0]), float(v[1]))
            for t, v in (datos.get("vocabulario") or {}).items()
        }

    @property
    def usa_reglas(self) -> bool:
        """True si es un híbrido y hay que pasarle `puntuacion_reglas` para puntuar."""
        return self.peso_reglas is not None

    def puntuar_bruto(self, titulo, ubicacion=None, puntuacion_reglas=0.0) -> float:
        """Log-odds. Repite exactamente lo que hace TfIdf.transform sobre una fila:
        cuenta de token × idf, normalizado L2 sobre el vocabulario conocido."""
        toks = tokens(titulo, ubicacion if self.usar_ubicacion else None, self.ngramas)
        cuentas: Counter = Counter(t for t in toks if t in self.vocabulario)
        s = self.sesgo
        if cuentas:
            valores = {t: c * self.vocabulario[t][0] for t, c in cuentas.items()}
            norma = math.sqrt(sum(v * v for v in valores.values())) or 1.0
            s += sum(v / norma * self.vocabulario[t][1] for t, v in valores.items())
        if self.usa_reglas:
            s += self.peso_reglas * float(puntuacion_reglas) / ESCALA_REGLAS
        return s

    def puntuar(self, titulo, ubicacion=None, puntuacion_reglas=0.0) -> float:
        """Puntuación en la escala de siempre (la de los cortes 5 / 3 de la interfaz)."""
        bruto = self.puntuar_bruto(titulo, ubicacion, puntuacion_reglas)
        return round(aplicar_calibracion(bruto, self.calibracion), 1)


def cargar(path: Path = MODELO_PATH) -> ModeloCargado | None:
    """Devuelve el modelo guardado, o None si no existe o no se puede leer.

    Nunca lanza: si el archivo está corrupto, la app tiene que seguir arrancando y
    caer a las reglas, no morir al abrir el listado.
    """
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(datos, dict) or not datos.get("vocabulario"):
        return None
    return ModeloCargado(datos)
