"""
Application Flask - Gestion interactive des circuits Vincennes (polyline map)
"""
from flask import Flask, jsonify, request, render_template_string, Response
import json, os, re, csv, io
import requests as http_requests
from collections import defaultdict

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = '/data' if os.path.isdir('/data') else BASE_DIR

SEGMENTS_FILE     = os.path.join(DATA_DIR, 'segments.json')
CIRCUITS_FILE     = os.path.join(DATA_DIR, 'circuits_config.json')
STREETS_CACHE     = os.path.join(DATA_DIR, 'streets_cache.json')

DEFAULT_COLORS = {
    '541': '#e74c3c',
    '542': '#3498db',
    '544': '#27ae60',
    '545': '#827f7d',
    '546': '#9b59b6',
    '547': '#313534',
    '548': '#0f178a',
}
DEFAULT_CIRCUITS = ['541', '542', '544', '545', '546', '547', '548']

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
OVERPASS_QUERY = """
[out:json][timeout:30];
area["name"="Vincennes"]["boundary"="administrative"]["admin_level"="8"]->.v;
way["highway"~"residential|primary|secondary|tertiary|unclassified|living_street|pedestrian|service"]["name"](area.v);
out geom;
"""

# ─── Init data ────────────────────────────────────────────────────────────────

def _init_data():
    """Copy initial files into DATA_DIR on first start (Render), create blanks if needed."""
    import shutil
    for fname in ('circuits_config.json',):
        src = os.path.join(BASE_DIR, fname)
        dst = os.path.join(DATA_DIR, fname)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy2(src, dst)

    # segments.json — start empty if missing
    if not os.path.exists(SEGMENTS_FILE):
        with open(SEGMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)

_init_data()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_segments():
    if os.path.exists(SEGMENTS_FILE):
        with open(SEGMENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_segments(data):
    with open(SEGMENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_circuits_config():
    if os.path.exists(CIRCUITS_FILE):
        with open(CIRCUITS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'circuits': DEFAULT_CIRCUITS, 'colors': DEFAULT_COLORS}

def save_circuits_config(cfg):
    with open(CIRCUITS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_streets_cache():
    if os.path.exists(STREETS_CACHE):
        with open(STREETS_CACHE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_streets_cache(data):
    with open(STREETS_CACHE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_streets_from_overpass():
    """Query Overpass API and return { street_name: [[lat,lon],...], ... }"""
    try:
        resp = http_requests.post(
            OVERPASS_URL,
            data={'data': OVERPASS_QUERY},
            timeout=45
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return None, str(e)

    streets = defaultdict(list)
    for element in data.get('elements', []):
        if element.get('type') != 'way':
            continue
        name = element.get('tags', {}).get('name', '').strip()
        if not name:
            continue
        geometry = element.get('geometry', [])
        coords = [[pt['lat'], pt['lon']] for pt in geometry if 'lat' in pt and 'lon' in pt]
        if coords:
            streets[name.lower()].extend(coords)

    return dict(streets), None

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/api/data')
def api_data():
    segments = load_segments()
    cfg = load_circuits_config()
    return jsonify({
        'segments': segments,
        'circuits': cfg.get('circuits', DEFAULT_CIRCUITS),
        'colors': cfg.get('colors', DEFAULT_COLORS),
    })

@app.route('/api/streets')
def api_streets():
    force = request.args.get('force', '0') == '1'
    cache = load_streets_cache()
    if cache and not force:
        return jsonify({'streets': cache, 'source': 'cache'})

    streets, error = fetch_streets_from_overpass()
    if error:
        # Return cache if available, even on error
        if cache:
            return jsonify({'streets': cache, 'source': 'cache', 'warning': error})
        return jsonify({'error': error, 'streets': {}}), 500

    save_streets_cache(streets)
    return jsonify({'streets': streets, 'source': 'overpass'})

@app.route('/api/assign', methods=['POST'])
def api_assign():
    body = request.get_json(force=True)
    rue = body.get('rue', '').strip().lower()
    circuit = str(body.get('circuit', '')).strip()
    nb_colis = body.get('nb_colis', None)
    if not rue or not circuit:
        return jsonify({'error': 'rue and circuit required'}), 400
    segments = load_segments()
    entry = {'circuit': circuit}
    if nb_colis is not None and nb_colis != '':
        try:
            entry['nb_colis'] = int(nb_colis)
        except (ValueError, TypeError):
            pass
    segments[rue] = entry
    save_segments(segments)
    return jsonify({'segments': segments})

@app.route('/api/unassign', methods=['POST'])
def api_unassign():
    body = request.get_json(force=True)
    rue = body.get('rue', '').strip().lower()
    if not rue:
        return jsonify({'error': 'rue required'}), 400
    segments = load_segments()
    segments.pop(rue, None)
    save_segments(segments)
    return jsonify({'segments': segments})

@app.route('/api/export_circuit/<circuit>')
def api_export_circuit(circuit):
    segments = load_segments()
    lines = [f"Circuit {circuit} - Export\n{'='*40}"]
    total = 0
    for rue, info in sorted(segments.items()):
        if info.get('circuit') == circuit:
            nb = info.get('nb_colis', '')
            nb_str = f"  ({nb} colis)" if nb else ''
            lines.append(f"{rue}{nb_str}")
            if nb:
                total += int(nb)
    lines.append(f"\n{'='*40}")
    lines.append(f"Total rues : {sum(1 for r,i in segments.items() if i.get('circuit')==circuit)}")
    if total:
        lines.append(f"Total colis : {total}")
    content = '\n'.join(lines)
    return Response(
        content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="circuit_{circuit}.txt"'}
    )

@app.route('/api/import_csv', methods=['POST'])
def api_import_csv():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    content = f.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(content), delimiter=';')
    segments = load_segments()
    imported = 0
    errors = []
    for row in reader:
        try:
            # Try common column names
            circuit = ''
            rue = ''
            nb_colis = None
            for k, v in row.items():
                kl = k.strip().upper()
                if kl in ('C', 'CIRCUIT'):
                    circuit = str(v).strip()
                elif kl in ('RUE', 'STREET', 'VOIE', 'NOM_RUE'):
                    rue = str(v).strip().lower()
                elif kl in ('NB_COLIS', 'COLIS', 'NOMBRE_COLIS'):
                    try:
                        nb_colis = int(v)
                    except (ValueError, TypeError):
                        pass
            if rue and circuit:
                entry = {'circuit': circuit}
                if nb_colis is not None:
                    entry['nb_colis'] = nb_colis
                segments[rue] = entry
                imported += 1
        except Exception as e:
            errors.append(str(e))
    save_segments(segments)
    return jsonify({'imported': imported, 'errors': errors, 'segments': segments})

@app.route('/api/add_circuit', methods=['POST'])
def api_add_circuit():
    body = request.get_json(force=True)
    name = str(body.get('name', '')).strip()
    color = body.get('color', '#888888')
    if not name:
        return jsonify({'error': 'name required'}), 400
    cfg = load_circuits_config()
    if name not in cfg['circuits']:
        cfg['circuits'].append(name)
    cfg.setdefault('colors', {})[name] = color
    save_circuits_config(cfg)
    return jsonify(cfg)

@app.route('/api/delete_circuit', methods=['POST'])
def api_delete_circuit():
    body = request.get_json(force=True)
    name = str(body.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    cfg = load_circuits_config()
    cfg['circuits'] = [c for c in cfg.get('circuits', []) if c != name]
    cfg.get('colors', {}).pop(name, None)
    save_circuits_config(cfg)
    # Also remove segments assigned to this circuit
    segments = load_segments()
    segments = {r: i for r, i in segments.items() if i.get('circuit') != name}
    save_segments(segments)
    return jsonify(cfg)

@app.route('/api/update_color', methods=['POST'])
def api_update_color():
    body = request.get_json(force=True)
    circuit = str(body.get('circuit', '')).strip()
    color = body.get('color', '#888888')
    if not circuit:
        return jsonify({'error': 'circuit required'}), 400
    cfg = load_circuits_config()
    cfg.setdefault('colors', {})[circuit] = color
    save_circuits_config(cfg)
    return jsonify(cfg)

# ─── HTML Page ────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Circuits Vincennes</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    --bg: #1a1a2e;
    --bg2: #16213e;
    --bg3: #0f3460;
    --accent: #e94560;
    --text: #eaeaea;
    --text2: #aaa;
    --border: #2a2a4a;
    --sidebar-w: 360px;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

  #app { display: flex; height: 100vh; }

  /* ── Sidebar ── */
  #sidebar {
    width: var(--sidebar-w);
    min-width: var(--sidebar-w);
    background: var(--bg2);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    z-index: 10;
  }
  #sidebar-header {
    padding: 14px 16px 10px;
    border-bottom: 1px solid var(--border);
    background: var(--bg3);
  }
  #sidebar-header h1 { font-size: 16px; font-weight: 700; }
  #stats { font-size: 12px; color: var(--text2); margin-top: 4px; }

  #sidebar-search {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }
  #search-input {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    padding: 7px 10px;
    font-size: 13px;
    outline: none;
  }
  #search-input:focus { border-color: var(--accent); }

  #circuit-filters {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    align-items: center;
  }
  .filter-btn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px;
    border-radius: 5px;
    border: none;
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    color: #000;
    transition: opacity 0.2s;
  }
  .filter-btn.inactive { opacity: 0.3; }
  .filter-btn .color-dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: currentColor;
    cursor: pointer;
    position: relative;
  }
  .filter-btn .del-btn {
    margin-left: 2px;
    font-size: 10px;
    opacity: 0.7;
    cursor: pointer;
  }
  .filter-btn .del-btn:hover { opacity: 1; color: #e00; }
  #add-circuit-btn {
    padding: 4px 10px;
    border-radius: 5px;
    border: 1px dashed var(--border);
    background: transparent;
    color: var(--text2);
    cursor: pointer;
    font-size: 12px;
  }
  #add-circuit-btn:hover { border-color: var(--accent); color: var(--text); }

  #street-list {
    flex: 1;
    overflow-y: auto;
    padding: 6px 0;
  }
  #street-list::-webkit-scrollbar { width: 5px; }
  #street-list::-webkit-scrollbar-track { background: var(--bg2); }
  #street-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  .street-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 14px;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background 0.15s;
    font-size: 13px;
  }
  .street-item:hover { background: rgba(255,255,255,0.05); }
  .street-item .circuit-badge {
    font-size: 10px;
    font-weight: 700;
    padding: 2px 5px;
    border-radius: 3px;
    color: #000;
    white-space: nowrap;
  }
  .street-item .rue-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .street-item .nb-colis { font-size: 11px; color: var(--text2); white-space: nowrap; }

  #export-row {
    padding: 10px 12px;
    border-top: 1px solid var(--border);
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    align-items: center;
  }
  #export-row label { font-size: 11px; color: var(--text2); width: 100%; margin-bottom: 2px; }
  .circuit-export-btn {
    padding: 6px 10px;
    border: none;
    border-radius: 5px;
    cursor: pointer;
    font-weight: bold;
    font-size: 11px;
    color: #000;
    margin: 2px;
  }
  #import-btn {
    padding: 6px 10px;
    border-radius: 5px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
    font-size: 11px;
  }
  #import-btn:hover { border-color: var(--accent); }
  #refresh-streets-btn {
    padding: 6px 10px;
    border-radius: 5px;
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
    font-size: 11px;
  }
  #refresh-streets-btn:hover { border-color: #27ae60; }

  /* ── Map ── */
  #map { flex: 1; height: 100vh; }

  /* ── Loading overlay ── */
  #loading {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.65);
    z-index: 9999;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 14px;
    font-size: 16px;
  }
  #loading.show { display: flex; }
  .spinner {
    width: 40px; height: 40px;
    border: 4px solid rgba(255,255,255,0.2);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Popup ── */
  .leaflet-popup-content-wrapper {
    background: var(--bg2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5) !important;
  }
  .leaflet-popup-tip { background: var(--bg2) !important; }
  .leaflet-popup-content { margin: 0 !important; padding: 0 !important; }
  .street-popup { padding: 14px 16px; min-width: 240px; }
  .street-popup h3 { font-size: 14px; margin-bottom: 10px; border-bottom: 1px solid var(--border); padding-bottom: 8px; text-transform: capitalize; }
  .street-popup label { font-size: 12px; color: var(--text2); display: block; margin-bottom: 3px; margin-top: 8px; }
  .street-popup select, .street-popup input[type=number] {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--text);
    padding: 6px 8px;
    font-size: 13px;
    outline: none;
  }
  .street-popup select:focus, .street-popup input:focus { border-color: var(--accent); }
  .popup-btns { display: flex; gap: 8px; margin-top: 12px; }
  .btn-assign {
    flex: 1;
    padding: 8px;
    background: var(--accent);
    border: none;
    border-radius: 5px;
    color: #fff;
    font-weight: 600;
    cursor: pointer;
    font-size: 13px;
  }
  .btn-assign:hover { filter: brightness(1.15); }
  .btn-unassign {
    padding: 8px 12px;
    background: #333;
    border: 1px solid #555;
    border-radius: 5px;
    color: var(--text2);
    cursor: pointer;
    font-size: 13px;
  }
  .btn-unassign:hover { border-color: #e00; color: #e00; }

  /* ── Color picker hidden input ── */
  input[type=color].hidden-picker {
    position: absolute;
    width: 0; height: 0;
    opacity: 0;
    pointer-events: none;
  }
</style>
</head>
<body>
<div id="loading"><div class="spinner"></div><span id="loading-msg">Chargement…</span></div>
<input type="file" id="csv-file-input" accept=".csv,.txt" style="display:none"/>

<div id="app">
  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sidebar-header">
      <h1>🗺 Circuits Vincennes</h1>
      <div id="stats">Chargement…</div>
    </div>

    <div id="sidebar-search">
      <input id="search-input" type="text" placeholder="🔍 Rechercher une rue assignée…"/>
    </div>

    <div id="circuit-filters">
      <!-- filled by JS -->
      <button id="add-circuit-btn">＋ Nouveau circuit</button>
    </div>

    <div id="street-list">
      <!-- filled by JS -->
    </div>

    <div id="export-row">
      <label>Exporter un circuit :</label>
      <!-- filled by JS -->
      <button id="refresh-streets-btn" title="Recharger les rues depuis OpenStreetMap">🔄 Actualiser les rues</button>
      <button id="import-btn" title="Importer un ancien CSV">⬆ Importer CSV</button>
    </div>
  </div>

  <!-- Map -->
  <div id="map"></div>
</div>

<script>
// ─── State ────────────────────────────────────────────────────────────────────
let segments = {};
let circuits  = [];
let colors    = {};
let streets   = {};       // { "rue daumesnil": [[lat,lon],...] }
let visibleCircuits = new Set();

// Leaflet layer groups
let map;
let layerUnassigned;  // L.layerGroup
let layerAssigned;    // L.layerGroup
let polylineMap = {};  // { streetName: L.polyline }
let activePopup = null;
let highlightedPolyline = null;

// ─── Utility ──────────────────────────────────────────────────────────────────
function showLoading(msg='Chargement…') {
  document.getElementById('loading-msg').textContent = msg;
  document.getElementById('loading').classList.add('show');
}
function hideLoading() {
  document.getElementById('loading').classList.remove('show');
}

function capitalize(s) {
  return s.replace(/\b\w/g, c => c.toUpperCase());
}

function getColor(circuit) {
  return colors[circuit] || '#888';
}

function contrastColor(hex) {
  // Return black or white for text contrast
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return (r*299+g*587+b*114)/1000 > 128 ? '#000' : '#fff';
}

// ─── Map init ─────────────────────────────────────────────────────────────────
function initMap() {
  map = L.map('map', { center: [48.8472, 2.4388], zoom: 14 });
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    maxZoom: 19
  }).addTo(map);

  layerUnassigned = L.layerGroup().addTo(map);
  layerAssigned   = L.layerGroup().addTo(map);

  // Click on blank map → reverse geocode
  map.on('click', async (e) => {
    if (activePopup) return; // ignore if popup already open
    const { lat, lng } = e.latlng;
    try {
      const res = await fetch(
        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json`,
        { headers: { 'Accept-Language': 'fr' } }
      );
      const data = await res.json();
      const road = data.address && (data.address.road || data.address.pedestrian || data.address.footway);
      if (road) {
        const key = road.toLowerCase();
        if (polylineMap[key]) {
          openPopupForStreet(key, e.latlng);
        }
      }
    } catch(err) { /* ignore */ }
  });
}

// ─── Polyline helpers ─────────────────────────────────────────────────────────
function styleForStreet(streetName) {
  const seg = segments[streetName];
  if (seg && visibleCircuits.has(seg.circuit)) {
    return { color: getColor(seg.circuit), weight: 5, opacity: 0.85 };
  } else if (seg && !visibleCircuits.has(seg.circuit)) {
    return { color: getColor(seg.circuit), weight: 5, opacity: 0.2 };
  }
  return { color: '#555', weight: 3, opacity: 0.45 };
}

function buildAllPolylines() {
  layerUnassigned.clearLayers();
  layerAssigned.clearLayers();
  polylineMap = {};

  const streetNames = Object.keys(streets);
  streetNames.forEach(streetName => {
    const coords = streets[streetName];
    if (!coords || coords.length === 0) return;

    const style = styleForStreet(streetName);
    const pline = L.polyline(coords, style);

    // Hover
    pline.on('mouseover', function(e) {
      if (this === highlightedPolyline) return;
      const s = styleForStreet(streetName);
      this.setStyle({ weight: s.weight + 2, opacity: 1.0 });
      this.bindTooltip(capitalize(streetName), { sticky: true, className: 'street-tooltip' }).openTooltip(e.latlng);
    });
    pline.on('mouseout', function() {
      if (this === highlightedPolyline) return;
      this.setStyle(styleForStreet(streetName));
      this.unbindTooltip();
    });
    pline.on('click', function(e) {
      L.DomEvent.stopPropagation(e);
      openPopupForStreet(streetName, e.latlng);
    });

    polylineMap[streetName] = pline;
    const seg = segments[streetName];
    if (seg) {
      layerAssigned.addLayer(pline);
    } else {
      layerUnassigned.addLayer(pline);
    }
  });
}

function refreshPolylineStyle(streetName) {
  const pline = polylineMap[streetName];
  if (!pline) return;
  const style = styleForStreet(streetName);
  pline.setStyle(style);

  const seg = segments[streetName];
  // Move between layer groups
  if (seg) {
    layerUnassigned.removeLayer(pline);
    if (!layerAssigned.hasLayer(pline)) layerAssigned.addLayer(pline);
  } else {
    layerAssigned.removeLayer(pline);
    if (!layerUnassigned.hasLayer(pline)) layerUnassigned.addLayer(pline);
  }
}

// ─── Popup ────────────────────────────────────────────────────────────────────
function openPopupForStreet(streetName, latlng) {
  if (activePopup) { activePopup.remove(); activePopup = null; }

  // Highlight
  if (highlightedPolyline) {
    const prev = Object.keys(polylineMap).find(k => polylineMap[k] === highlightedPolyline);
    if (prev) highlightedPolyline.setStyle(styleForStreet(prev));
  }
  const pline = polylineMap[streetName];
  if (pline) {
    const s = styleForStreet(streetName);
    pline.setStyle({ weight: s.weight + 3, opacity: 1.0 });
    highlightedPolyline = pline;
  }

  const seg = segments[streetName] || {};
  const circuitOptions = circuits.map(c =>
    `<option value="${c}" ${seg.circuit===c?'selected':''}>${c}</option>`
  ).join('');

  const content = document.createElement('div');
  content.className = 'street-popup';
  content.innerHTML = `
    <h3>${capitalize(streetName)}</h3>
    <label>Circuit</label>
    <select id="popup-circuit">${circuitOptions}</select>
    <label>Nb colis</label>
    <input type="number" id="popup-nb" min="0" value="${seg.nb_colis||''}" placeholder="—"/>
    <div class="popup-btns">
      <button class="btn-assign" id="popup-assign">✓ Assigner</button>
      ${seg.circuit ? '<button class="btn-unassign" id="popup-unassign">✕ Désassigner</button>' : ''}
    </div>
  `;

  // Stop popup close when clicking inside
  L.DomEvent.disableClickPropagation(content);

  const popup = L.popup({ closeButton: true, autoClose: false, closeOnClick: false, maxWidth: 300 })
    .setLatLng(latlng)
    .setContent(content)
    .openOn(map);

  activePopup = popup;

  map.once('popupclose', () => {
    activePopup = null;
    if (highlightedPolyline) {
      const prev = Object.keys(polylineMap).find(k => polylineMap[k] === highlightedPolyline);
      if (prev) highlightedPolyline.setStyle(styleForStreet(prev));
      highlightedPolyline = null;
    }
  });

  content.querySelector('#popup-assign').addEventListener('click', async () => {
    const circuit = content.querySelector('#popup-circuit').value;
    const nbVal   = content.querySelector('#popup-nb').value;
    const body = { rue: streetName, circuit };
    if (nbVal !== '') body.nb_colis = parseInt(nbVal);
    const res = await fetch('/api/assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    segments = data.segments;
    refreshPolylineStyle(streetName);
    renderSidebar();
    popup.remove(); activePopup = null;
    highlightedPolyline = null;
  });

  const unBtn = content.querySelector('#popup-unassign');
  if (unBtn) {
    unBtn.addEventListener('click', async () => {
      const res = await fetch('/api/unassign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rue: streetName })
      });
      const data = await res.json();
      segments = data.segments;
      refreshPolylineStyle(streetName);
      renderSidebar();
      popup.remove(); activePopup = null;
      highlightedPolyline = null;
    });
  }
}

// ─── Sidebar rendering ────────────────────────────────────────────────────────
function renderFilters() {
  const container = document.getElementById('circuit-filters');
  // Clear all except add button
  const addBtn = document.getElementById('add-circuit-btn');
  container.innerHTML = '';

  circuits.forEach(c => {
    const col = getColor(c);
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (visibleCircuits.has(c) ? '' : ' inactive');
    btn.style.background = col;
    btn.style.color = contrastColor(col);
    btn.dataset.circuit = c;
    btn.innerHTML = `
      <span class="circuit-label">${c}</span>
      <span class="color-dot" title="Changer couleur" data-circuit="${c}">🎨</span>
      <span class="del-btn" title="Supprimer ce circuit" data-circuit="${c}">✕</span>
    `;
    btn.querySelector('.circuit-label').addEventListener('click', () => toggleCircuit(c));
    btn.querySelector('.color-dot').addEventListener('click', (e) => {
      e.stopPropagation();
      openColorPicker(c, btn);
    });
    btn.querySelector('.del-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      deleteCircuit(c);
    });
    container.appendChild(btn);
  });

  container.appendChild(addBtn);

  // Export row
  const exportRow = document.getElementById('export-row');
  // Remove old export btns
  exportRow.querySelectorAll('.circuit-export-btn').forEach(b => b.remove());
  const label = exportRow.querySelector('label');
  circuits.forEach(c => {
    const col = getColor(c);
    const btn = document.createElement('button');
    btn.className = 'circuit-export-btn';
    btn.style.background = col;
    btn.style.color = contrastColor(col);
    btn.textContent = c;
    btn.title = `Exporter circuit ${c}`;
    btn.addEventListener('click', () => {
      window.location.href = `/api/export_circuit/${encodeURIComponent(c)}`;
    });
    exportRow.insertBefore(btn, label.nextSibling);
  });
}

function renderStreetList() {
  const query = document.getElementById('search-input').value.toLowerCase();
  const container = document.getElementById('street-list');
  container.innerHTML = '';

  // Group assigned streets by circuit
  const byCircuit = {};
  circuits.forEach(c => { byCircuit[c] = []; });

  Object.entries(segments).forEach(([rue, info]) => {
    if (!byCircuit[info.circuit]) byCircuit[info.circuit] = [];
    if (!query || rue.includes(query)) {
      byCircuit[info.circuit].push({ rue, info });
    }
  });

  let total = 0;
  circuits.forEach(c => {
    const items = (byCircuit[c] || []).sort((a,b) => a.rue.localeCompare(b.rue));
    items.forEach(({ rue, info }) => {
      total++;
      const col = getColor(c);
      const div = document.createElement('div');
      div.className = 'street-item';
      div.style.borderLeftColor = col;
      div.innerHTML = `
        <span class="circuit-badge" style="background:${col};color:${contrastColor(col)}">${c}</span>
        <span class="rue-name" title="${capitalize(rue)}">${capitalize(rue)}</span>
        ${info.nb_colis ? `<span class="nb-colis">${info.nb_colis}📦</span>` : ''}
      `;
      div.addEventListener('click', () => zoomToStreet(rue));
      container.appendChild(div);
    });
  });

  document.getElementById('stats').textContent =
    `${Object.keys(segments).length} rue(s) assignée(s) · ${Object.keys(streets).length} rues totales`;
}

function renderSidebar() {
  renderFilters();
  renderStreetList();
}

// ─── Actions ──────────────────────────────────────────────────────────────────
function toggleCircuit(c) {
  if (visibleCircuits.has(c)) visibleCircuits.delete(c);
  else visibleCircuits.add(c);
  // Refresh styles for all streets of this circuit
  Object.keys(segments).forEach(rue => {
    if (segments[rue].circuit === c) refreshPolylineStyle(rue);
  });
  renderSidebar();
}

function zoomToStreet(streetName) {
  const pline = polylineMap[streetName];
  if (pline) {
    map.fitBounds(pline.getBounds(), { padding: [40, 40], maxZoom: 17 });
    const center = pline.getBounds().getCenter();
    openPopupForStreet(streetName, center);
  }
}

function openColorPicker(circuit, btnEl) {
  let picker = document.getElementById('hidden-color-picker');
  if (!picker) {
    picker = document.createElement('input');
    picker.type = 'color';
    picker.id = 'hidden-color-picker';
    picker.className = 'hidden-picker';
    document.body.appendChild(picker);
  }
  picker.value = getColor(circuit);
  picker.style.position = 'fixed';
  picker.style.left = '-9999px';
  picker.click();
  picker.oninput = null;
  picker.onchange = async function() {
    const color = picker.value;
    await fetch('/api/update_color', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ circuit, color })
    });
    colors[circuit] = color;
    // Refresh polylines
    Object.keys(segments).forEach(rue => {
      if (segments[rue].circuit === circuit) refreshPolylineStyle(rue);
    });
    renderSidebar();
  };
}

async function deleteCircuit(c) {
  if (!confirm(`Supprimer le circuit ${c} ? Toutes les rues assignées à ce circuit seront désassignées.`)) return;
  const res = await fetch('/api/delete_circuit', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ name: c })
  });
  const cfg = await res.json();
  circuits = cfg.circuits;
  colors = cfg.colors;
  // Reload segments
  const dres = await fetch('/api/data');
  const d = await dres.json();
  segments = d.segments;
  visibleCircuits = new Set(circuits);
  buildAllPolylines();
  renderSidebar();
}

document.getElementById('add-circuit-btn').addEventListener('click', async () => {
  const name = prompt('Nom du nouveau circuit (ex: 549) :');
  if (!name || !name.trim()) return;
  const color = prompt('Couleur hexadécimale (ex: #ff5500) :', '#888888');
  const res = await fetch('/api/add_circuit', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ name: name.trim(), color: color || '#888888' })
  });
  const cfg = await res.json();
  circuits = cfg.circuits;
  colors = cfg.colors;
  visibleCircuits.add(name.trim());
  renderSidebar();
});

document.getElementById('refresh-streets-btn').addEventListener('click', async () => {
  if (!confirm('Recharger toutes les rues depuis OpenStreetMap ? (peut prendre 30s)')) return;
  showLoading('Chargement des rues depuis OpenStreetMap…');
  try {
    const res = await fetch('/api/streets?force=1');
    const data = await res.json();
    if (data.streets) {
      streets = data.streets;
      buildAllPolylines();
      renderSidebar();
    }
    if (data.warning) alert('Avertissement : ' + data.warning);
  } catch(e) {
    alert('Erreur lors du chargement : ' + e.message);
  } finally {
    hideLoading();
  }
});

document.getElementById('import-btn').addEventListener('click', () => {
  document.getElementById('csv-file-input').click();
});

document.getElementById('csv-file-input').addEventListener('change', async function() {
  const file = this.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  showLoading('Import CSV en cours…');
  try {
    const res = await fetch('/api/import_csv', { method: 'POST', body: formData });
    const data = await res.json();
    segments = data.segments;
    buildAllPolylines();
    renderSidebar();
    alert(`Import terminé : ${data.imported} rue(s) importée(s).${data.errors.length ? '\nErreurs : ' + data.errors.join(', ') : ''}`);
  } catch(e) {
    alert('Erreur import : ' + e.message);
  } finally {
    hideLoading();
    this.value = '';
  }
});

document.getElementById('search-input').addEventListener('input', renderStreetList);

// ─── Boot ─────────────────────────────────────────────────────────────────────
async function boot() {
  showLoading('Initialisation de la carte…');
  initMap();

  // Load config + segments
  const dres = await fetch('/api/data');
  const d = await dres.json();
  segments = d.segments;
  circuits = d.circuits;
  colors   = d.colors;
  visibleCircuits = new Set(circuits);

  renderSidebar();

  // Load streets
  showLoading('Chargement des rues (cache ou OpenStreetMap)…');
  try {
    const sres = await fetch('/api/streets');
    const sdata = await sres.json();
    if (sdata.error && !sdata.streets) {
      alert('Impossible de charger les rues : ' + sdata.error);
    } else {
      streets = sdata.streets || {};
      if (sdata.warning) {
        console.warn('Streets warning:', sdata.warning);
      }
    }
  } catch(e) {
    alert('Erreur réseau lors du chargement des rues : ' + e.message);
  }

  buildAllPolylines();
  renderSidebar();
  hideLoading();
}

boot();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, port=5000)
