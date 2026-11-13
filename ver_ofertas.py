"""
Exporta una instantánea HTML autocontenida de las ofertas actuales, para consultarlas
sin tener el servidor arrancado (o para guardarte una copia de un día concreto).

Es de solo lectura: para marcar favoritas, aplicadas u ocultas usa la web (python app.py),
que es donde vive el estado. Aquí se muestran esas marcas ya aplicadas.

Uso: python ver_ofertas.py
"""

from __future__ import annotations

import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import almacen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIR_BASE = Path(__file__).parent
HTML_SALIDA = DIR_BASE / "reporte_ofertas.html"


def preparar_datos() -> list[dict]:
    ofertas = almacen.cargar_ofertas()
    if not ofertas:
        print("No hay ofertas en la base. Ejecuta primero:  python app.py  (o python test.py)")
        sys.exit(1)

    estado = almacen.cargar_estado()
    favoritas, ocultas, aplicadas = (
        set(estado["favoritas"]), set(estado["ocultas"]), set(estado["aplicadas"])
    )
    for o in ofertas:
        url = o.get("job_url")
        o["es_favorita"] = url in favoritas
        o["es_oculta"] = url in ocultas
        o["es_aplicada"] = url in aplicadas
    return ofertas


PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Ofertas de empleo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg:#f4f5f7; --card-bg:#fff; --text:#1a1d23; --text-muted:#666b76; --border:#e2e4e9;
    --accent:#3b6ef6; --accent-soft:#eaf0ff; --warn-bg:#fff4e5; --warn-text:#9a5b00;
    --tag-bg:#eef1f6; --ok-bg:#e7f7ed; --ok-text:#1c7a44;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#14161a; --card-bg:#1d2027; --text:#eaecef; --text-muted:#9aa1ad; --border:#2b2f38;
      --accent:#6d93ff; --accent-soft:#232a42; --warn-bg:#3a2d10; --warn-text:#f2c069;
      --tag-bg:#262a33; --ok-bg:#13341f; --ok-text:#6fcf97;
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--text); }
  header { position:sticky; top:0; z-index:10; background:var(--bg); border-bottom:1px solid var(--border); padding:16px 24px; }
  h1 { margin:0 0 4px; font-size:20px; }
  .subtitulo { color:var(--text-muted); font-size:13px; margin-bottom:14px; }
  .filtros { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  input[type=text], select { padding:9px 11px; border-radius:8px; border:1px solid var(--border);
    background:var(--card-bg); color:var(--text); font-size:14px; font-family:inherit; }
  #buscar { flex:1 1 220px; }
  label.chk { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--text-muted); }
  #contador { font-size:13px; color:var(--text-muted); margin-left:auto; white-space:nowrap; }
  main { padding:20px 24px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:14px; }
  .card { background:var(--card-bg); border:1px solid var(--border); border-radius:12px; padding:16px;
    display:flex; flex-direction:column; gap:8px; }
  .card.es-favorita { border-color:#e0b400; }
  .card.es-aplicada { border-color:var(--ok-text); }
  .card h3 { margin:0; font-size:15.5px; line-height:1.3; }
  .card h3 a { color:var(--text); text-decoration:none; }
  .card h3 a:hover { color:var(--accent); }
  .empresa { color:var(--text-muted); font-size:13.5px; }
  .meta { font-size:12.5px; color:var(--text-muted); display:flex; flex-wrap:wrap; gap:8px; }
  .tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:2px; }
  .tag { font-size:11px; padding:3px 8px; border-radius:999px; background:var(--tag-bg);
    color:var(--text-muted); white-space:nowrap; }
  .tag.accent { background:var(--accent-soft); color:var(--accent); }
  .tag.warn { background:var(--warn-bg); color:var(--warn-text); }
  .tag.ok { background:var(--ok-bg); color:var(--ok-text); }
  .tag.fav { background:#fff6da; color:#8a6d00; }
  @media (prefers-color-scheme: dark) { .tag.fav { background:#3a3110; color:#f2d060; } }
  .salario { font-size:13px; font-weight:600; color:var(--accent); }
  details { margin-top:4px; }
  summary { cursor:pointer; font-size:12.5px; color:var(--accent); user-select:none; }
  .descripcion { font-size:12.5px; color:var(--text-muted); margin-top:8px; max-height:260px;
    overflow-y:auto; white-space:pre-wrap; line-height:1.5; }
  .acciones { margin-top:auto; padding-top:6px; }
  .boton { display:inline-block; padding:8px 14px; border-radius:8px; background:var(--accent);
    color:#fff; text-decoration:none; font-size:13px; font-weight:600; }
  .vacio { grid-column:1/-1; text-align:center; color:var(--text-muted); padding:60px 0; }
</style>
</head>
<body>
<header>
  <h1>Ofertas de empleo</h1>
  <div class="subtitulo" id="subtitulo"></div>
  <div class="filtros">
    <input type="text" id="buscar" placeholder="Buscar por título, empresa o ubicación...">
    <select id="vista">
      <option value="activas">Ofertas activas</option>
      <option value="favoritas">★ Solo favoritas</option>
      <option value="aplicadas">✓ Aplicadas</option>
      <option value="ocultas">Ocultadas</option>
      <option value="todas">Todas</option>
    </select>
    <select id="categoria"><option value="">Todas las categorías</option></select>
    <select id="ambito"><option value="">Todos los ámbitos</option></select>
    <select id="pais"><option value="">Todos los países</option></select>
    <select id="experiencia"><option value="">Toda experiencia</option></select>
    <select id="contrato"><option value="">Todo tipo de contrato</option></select>
    <select id="modalidad"><option value="">Remoto y presencial</option></select>
    <select id="orden">
      <option value="fecha">Más recientes primero</option>
      <option value="distancia">Menor distancia</option>
      <option value="salario">Mayor salario</option>
    </select>
    <label class="chk"><input type="checkbox" id="ocultarRestringidas"> Ocultar posibles restricciones geográficas</label>
    <span id="contador"></span>
  </div>
</header>
<main id="contenedor"></main>
<script>
const OFERTAS = __DATOS__;
const $ = id => document.getElementById(id);
const contenedor = $("contenedor");

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function llenarSelect(select, valores) {
  [...new Set(valores.filter(Boolean))].sort().forEach(v => {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = v; select.appendChild(opt);
  });
}
llenarSelect($("categoria"), OFERTAS.map(o => o.categoria_label));
llenarSelect($("ambito"), OFERTAS.map(o => o.ambito_label));
llenarSelect($("pais"), OFERTAS.map(o => o.pais));
llenarSelect($("experiencia"), OFERTAS.map(o => o.experiencia_label));
llenarSelect($("contrato"), OFERTAS.map(o => o.contrato_label));
llenarSelect($("modalidad"), OFERTAS.map(o => o.modalidad_label));

function tarjeta(o) {
  const tags = [];
  if (o.es_favorita) tags.push(`<span class="tag fav">★ Favorita</span>`);
  if (o.es_aplicada) tags.push(`<span class="tag ok">✓ Aplicada</span>`);
  if (o.es_oculta) tags.push(`<span class="tag">Ocultada</span>`);
  tags.push(`<span class="tag accent">${escapeHtml(o.categoria_label)}</span>`);
  tags.push(`<span class="tag">${escapeHtml(o.ambito_label)}</span>`);
  if (o.site) tags.push(`<span class="tag">${escapeHtml(o.site)}</span>`);
  if (o.pais && o.pais !== "No indicado") tags.push(`<span class="tag">${escapeHtml(o.pais)}</span>`);
  if (o.experiencia_label && o.experiencia_label !== "No especificado")
    tags.push(`<span class="tag">${escapeHtml(o.experiencia_label)}</span>`);
  if (o.contrato_label && o.contrato_label !== "No indicado")
    tags.push(`<span class="tag">${escapeHtml(o.contrato_label)}</span>`);
  tags.push(`<span class="tag">${escapeHtml(o.modalidad_label)}</span>`);
  if (o.distancia_km !== null && o.distancia_km !== undefined)
    tags.push(`<span class="tag">${o.distancia_km} km</span>`);
  if (o.posible_restriccion_geografica)
    tags.push(`<span class="tag warn">Posible restricción geográfica</span>`);

  const clases = ["card", o.es_favorita ? "es-favorita" : "", o.es_aplicada ? "es-aplicada" : ""]
    .filter(Boolean).join(" ");

  return `
    <div class="${clases}">
      <h3><a href="${escapeHtml(o.job_url)}" target="_blank" rel="noopener">${escapeHtml(o.title)}</a></h3>
      <div class="empresa">${escapeHtml(o.company || "Empresa no indicada")}</div>
      <div class="meta">
        <span>${escapeHtml(o.location || "Ubicación no indicada")}</span>
        ${o.date_posted ? `<span>· ${escapeHtml(o.date_posted)}</span>` : ""}
      </div>
      ${o.salario ? `<div class="salario">${escapeHtml(o.salario)}</div>` : ""}
      <div class="tags">${tags.join("")}</div>
      ${o.description ? `<details><summary>Ver descripción</summary>
        <div class="descripcion">${escapeHtml(o.description)}</div></details>` : ""}
      <div class="acciones">
        <a class="boton" href="${escapeHtml(o.job_url)}" target="_blank" rel="noopener">Abrir oferta</a>
      </div>
    </div>`;
}

function render() {
  const texto = $("buscar").value.trim().toLowerCase();
  const vista = $("vista").value;
  const cat = $("categoria").value, amb = $("ambito").value, pais = $("pais").value;
  const exp = $("experiencia").value, con = $("contrato").value, mod = $("modalidad").value;
  const sinRestriccion = $("ocultarRestringidas").checked;

  let lista = OFERTAS.filter(o => {
    if (vista === "activas" && (o.es_oculta || o.es_aplicada || o.es_favorita)) return false;
    if (vista === "favoritas" && !o.es_favorita) return false;
    if (vista === "aplicadas" && !o.es_aplicada) return false;
    if (vista === "ocultas" && !o.es_oculta) return false;
    if (cat && o.categoria_label !== cat) return false;
    if (amb && o.ambito_label !== amb) return false;
    if (pais && o.pais !== pais) return false;
    if (exp && o.experiencia_label !== exp) return false;
    if (con && o.contrato_label !== con) return false;
    if (mod && o.modalidad_label !== mod) return false;
    if (sinRestriccion && o.posible_restriccion_geografica) return false;
    if (texto && !`${o.title} ${o.company} ${o.location}`.toLowerCase().includes(texto)) return false;
    return true;
  });

  const orden = $("orden").value;
  if (orden === "fecha") lista.sort((a, b) => (b.date_posted || "").localeCompare(a.date_posted || ""));
  else if (orden === "distancia") lista.sort((a, b) => (a.distancia_km ?? 1e9) - (b.distancia_km ?? 1e9));
  else if (orden === "salario")
    lista.sort((a, b) => (b.max_amount || b.min_amount || 0) - (a.max_amount || a.min_amount || 0));

  $("contador").textContent = `${lista.length} de ${OFERTAS.length} ofertas`;
  contenedor.innerHTML = lista.length ? lista.map(tarjeta).join("")
    : `<div class="vacio">No hay ofertas que coincidan con los filtros.</div>`;
}

["buscar","vista","categoria","ambito","pais","experiencia","contrato","modalidad","orden",
 "ocultarRestringidas"].forEach(id => $(id).addEventListener("input", render));

$("subtitulo").textContent =
  `${OFERTAS.length} ofertas · instantánea del __FECHA__ · solo lectura (para gestionarlas: python app.py)`;
render();
</script>
</body>
</html>
"""


def main() -> None:
    datos = preparar_datos()
    # Los textos vienen de webs externas: si un título contuviera "</script>" cerraría
    # el bloque <script> del informe. Escapando "<" como < el JSON sigue siendo
    # válido y ya no puede romper el HTML.
    datos_json = json.dumps(datos, ensure_ascii=False).replace("<", "\\u003c")
    html = PLANTILLA.replace("__DATOS__", datos_json)
    html = html.replace("__FECHA__", datetime.now().strftime("%d/%m/%Y %H:%M"))
    HTML_SALIDA.write_text(html, encoding="utf-8")
    print(f"Instantánea generada: {HTML_SALIDA} ({len(datos)} ofertas)")
    webbrowser.open(HTML_SALIDA.resolve().as_uri())


if __name__ == "__main__":
    main()
