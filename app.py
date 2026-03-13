"""
Application Flask - Gestion interactive des circuits Vincennes (tracé manuel)
"""
from flask import Flask, jsonify, request, render_template_string, Response
import json, os, re, csv, io, math, time
import requests as http_requests
from collections import defaultdict

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = '/data' if os.path.isdir('/data') else BASE_DIR

SEGMENTS_FILE = os.path.join(DATA_DIR, 'segments.json')
CIRCUITS_FILE = os.path.join(DATA_DIR, 'circuits_config.json')

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

# ─── Init ─────────────────────────────────────────────────────────────────────

def _init_data():
    import shutil
    for fname in ('circuits_config.json',):
        src = os.path.join(BASE_DIR, fname)
        dst = os.path.join(DATA_DIR, fname)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy2(src, dst)
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

def point_to_segment_dist(px, py, ax, ay, bx, by):
    """Distance in meters from point (lat,lon) to segment. Correct lat/lon scaling."""
    lat_m = 111320.0
    lon_m = 111320.0 * math.cos(math.radians((ax + bx) / 2.0))
    # segment vector in metres (lat=x, lon=y)
    dlat = (bx - ax) * lat_m
    dlon = (by - ay) * lon_m
    if dlat == 0 and dlon == 0:
        return math.hypot((px - ax) * lat_m, (py - ay) * lon_m)
    t = ((px - ax) * lat_m * dlat + (py - ay) * lon_m * dlon) / (dlat * dlat + dlon * dlon)
    t = max(0.0, min(1.0, t))
    nx = ax + t * (bx - ax)
    ny = ay + t * (by - ay)
    return math.hypot((px - nx) * lat_m, (py - ny) * lon_m)

def is_near_polyline(lat, lon, coords, max_dist=50):
    if len(coords) == 1:
        c = coords[0]
        lat_m = 111320.0
        lon_m = 111320.0 * math.cos(math.radians(c[0]))
        return math.hypot((lat - c[0]) * lat_m, (lon - c[1]) * lon_m) <= max_dist
    for i in range(len(coords) - 1):
        if point_to_segment_dist(lat, lon, coords[i][0], coords[i][1],
                                  coords[i+1][0], coords[i+1][1]) <= max_dist:
            return True
    return False

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

@app.route('/api/detect_addresses', methods=['POST'])
def api_detect_addresses():
    body = request.get_json(force=True)
    coords = body.get('coordinates', [])
    if not coords:
        return jsonify({'addresses': [], 'street_name': '', 'count': 0})

    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    pad = 0.0003   # ~33m padding around the drawn line bbox
    s, w, n, e = min(lats)-pad, min(lons)-pad, max(lats)+pad, max(lons)+pad

    # In France, addresses are often on building ways (not nodes) → include both
    # 'out center' gives lat/lon centroid for ways too
    query = f"""[out:json][timeout:25];
(
  node["addr:housenumber"]({s},{w},{n},{e});
  way["addr:housenumber"]({s},{w},{n},{e});
  way["highway"]["name"]({s},{w},{n},{e});
);
out center;"""

    try:
        resp = http_requests.post(OVERPASS_URL, data={'data': query}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as ex:
        return jsonify({'addresses': [], 'street_name': '', 'count': 0, 'error': str(ex)})

    addresses = []
    street_votes = {}

    for el in data.get('elements', []):
        t = el.get('type')
        tags = el.get('tags', {})

        # Get lat/lon: nodes have them directly, ways have a 'center' object
        if t == 'node':
            lat = el.get('lat')
            lon = el.get('lon')
        elif t == 'way':
            center = el.get('center', {})
            lat = center.get('lat')
            lon = center.get('lon')
        else:
            continue

        num = tags.get('addr:housenumber', '').strip()
        street = tags.get('addr:street', '').strip()
        highway_name = tags.get('name', '').strip()

        if num and lat is not None and lon is not None:
            if is_near_polyline(lat, lon, coords, 20):   # 20m = buildings on drawn street only
                addresses.append({'housenumber': num, 'street': street, 'lat': lat, 'lon': lon})
                if street:
                    street_votes[street] = street_votes.get(street, 0) + 1

        elif highway_name and t == 'way' and lat is not None:
            if is_near_polyline(lat, lon, coords, 30):
                street_votes[highway_name] = street_votes.get(highway_name, 0) + 1

    # Deduplicate: same housenumber at approximately same position (node + way both in OSM)
    seen = set()
    unique = []
    for a in addresses:
        # Key = number + position rounded to ~11m
        dedup_key = (a['housenumber'], round(a['lat'], 4), round(a['lon'], 4))
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique.append(a)
    addresses = unique

    # Sort house numbers naturally
    def hn_sort(a):
        m = re.match(r'^(\d+)', a['housenumber'])
        return int(m.group(1)) if m else 9999

    addresses.sort(key=hn_sort)

    street_name = max(street_votes, key=street_votes.get) if street_votes else ''

    return jsonify({
        'addresses': addresses,
        'street_name': street_name,
        'count': len(addresses),
    })

@app.route('/api/assign', methods=['POST'])
def api_assign():
    body = request.get_json(force=True)
    key = body.get('key', '').strip()
    circuit = str(body.get('circuit', '')).strip()
    street_name = body.get('street_name', '').strip()
    nb_colis = body.get('nb_colis', None)
    coordinates = body.get('coordinates', [])
    house_numbers = body.get('house_numbers', [])

    if not circuit:
        return jsonify({'error': 'circuit required'}), 400

    segments = load_segments()
    entry = {
        'circuit': circuit,
        'street_name': street_name,
        'coordinates': coordinates,
        'house_numbers': house_numbers,
    }
    if nb_colis is not None and nb_colis != '':
        try:
            entry['nb_colis'] = int(nb_colis)
        except (ValueError, TypeError):
            pass

    if not key:
        key = str(int(time.time() * 1000))

    segments[key] = entry
    save_segments(segments)
    return jsonify({'segments': segments, 'key': key})

@app.route('/api/unassign', methods=['POST'])
def api_unassign():
    body = request.get_json(force=True)
    key = body.get('key', '').strip()
    if not key:
        return jsonify({'error': 'key required'}), 400
    segments = load_segments()
    segments.pop(key, None)
    save_segments(segments)
    return jsonify({'segments': segments})

@app.route('/api/export_circuit/<circuit>')
def api_export_circuit(circuit):
    segments = load_segments()
    lines = [f"Circuit {circuit} - Export", '='*40]
    total_colis = 0
    nb_segs = 0
    for key, info in sorted(segments.items(), key=lambda x: x[1].get('street_name', '')):
        if info.get('circuit') != circuit:
            continue
        nb_segs += 1
        street = info.get('street_name', key)
        nb = info.get('nb_colis', '')
        hn = info.get('house_numbers', [])
        line = f"{street or key}"
        if hn:
            line += f"  [N°: {', '.join(hn)}]"
        if nb:
            line += f"  ({nb} colis)"
            total_colis += int(nb)
        lines.append(line)
    lines += ['', '='*40,
              f"Total segments : {nb_segs}",
              f"Total colis estimés : {total_colis}"]
    content = '\n'.join(lines)
    return Response(
        content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="circuit_{circuit}.txt"'}
    )

@app.route('/api/export_all')
def api_export_all():
    segments = load_segments()
    cfg = load_circuits_config()
    circuits_order = cfg.get('circuits', [])

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Circuit', 'Rue', 'Numeros', 'Nb_colis'])
    for c in circuits_order:
        for key, info in sorted(segments.items(), key=lambda x: x[1].get('street_name', '')):
            if info.get('circuit') != c:
                continue
            hn = ', '.join(info.get('house_numbers', []))
            nb = info.get('nb_colis', '')
            writer.writerow([c, info.get('street_name', ''), hn, nb])

    content = output.getvalue()
    return Response(
        content.encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="circuits_vincennes.csv"'}
    )

@app.route('/api/import_csv', methods=['POST'])
def api_import_csv():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    content = f.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(content), delimiter=';')
    segments = load_segments()
    imported = 0
    for row in reader:
        try:
            circuit = ''
            rue = ''
            nb_colis = None
            for k, v in row.items():
                kl = k.strip().upper()
                if kl in ('C', 'CIRCUIT'):
                    circuit = str(v).strip()
                elif kl in ('RUE', 'STREET', 'VOIE', 'NOM_RUE'):
                    rue = str(v).strip()
                elif kl in ('NB_COLIS', 'COLIS', 'NOMBRE_COLIS'):
                    try:
                        nb_colis = int(v)
                    except (ValueError, TypeError):
                        pass
            if rue and circuit:
                key = str(int(time.time() * 1000)) + str(imported)
                entry = {'circuit': circuit, 'street_name': rue, 'coordinates': [], 'house_numbers': []}
                if nb_colis is not None:
                    entry['nb_colis'] = nb_colis
                segments[key] = entry
                imported += 1
        except Exception:
            pass
    save_segments(segments)
    return jsonify({'imported': imported, 'segments': segments})

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
    segments = load_segments()
    segments = {k: v for k, v in segments.items() if v.get('circuit') != name}
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

# ─── HTML ─────────────────────────────────────────────────────────────────────

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
  --bg:     #1e2130;
  --bg2:    #252840;
  --bg3:    #1a1c2e;
  --accent: #4f8ef7;
  --text:   #e8eaf0;
  --text2:  #8890a8;
  --border: #333655;
  --sw:     340px;
  --danger: #e74c3c;
  --success:#27ae60;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--text)}
#app{display:flex;height:100vh}

/* ── Sidebar ── */
#sidebar{width:var(--sw);min-width:var(--sw);background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;z-index:10}
#sb-head{padding:12px 14px 10px;border-bottom:1px solid var(--border);background:var(--bg3)}
#sb-head h1{font-size:15px;font-weight:700;color:var(--accent)}
#sb-stats{font-size:11px;color:var(--text2);margin-top:3px}
#sb-search{padding:8px 12px;border-bottom:1px solid var(--border)}
#search-input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:6px 10px;font-size:13px;outline:none}
#search-input:focus{border-color:var(--accent)}
#circuit-filters{padding:8px 12px;border-bottom:1px solid var(--border);display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.filter-btn{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:5px;border:none;cursor:pointer;font-size:12px;font-weight:700;transition:opacity .2s}
.filter-btn.off{opacity:.3}
.filter-btn .lbl{pointer-events:none}
.filter-btn .ico{font-size:10px;cursor:pointer}
.filter-btn .ico:hover{opacity:.7}
#add-circuit-btn{padding:3px 9px;border-radius:5px;border:1px dashed var(--border);background:transparent;color:var(--text2);cursor:pointer;font-size:12px}
#add-circuit-btn:hover{border-color:var(--accent);color:var(--text)}
#street-list{flex:1;overflow-y:auto;padding:4px 0}
#street-list::-webkit-scrollbar{width:4px}
#street-list::-webkit-scrollbar-track{background:var(--bg2)}
#street-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.seg-item{display:flex;align-items:center;gap:7px;padding:6px 12px;cursor:pointer;border-left:3px solid transparent;transition:background .12s;font-size:13px}
.seg-item:hover{background:rgba(255,255,255,.04)}
.seg-badge{font-size:10px;font-weight:700;padding:2px 5px;border-radius:3px;white-space:nowrap}
.seg-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.seg-meta{font-size:11px;color:var(--text2);white-space:nowrap}
.seg-del{font-size:13px;opacity:0.3;cursor:pointer;padding:0 2px;flex-shrink:0}
.seg-del:hover{opacity:1}
#export-row{padding:8px 12px;border-top:1px solid var(--border);display:flex;flex-wrap:wrap;gap:4px;align-items:center}
#export-row .row-lbl{font-size:10px;color:var(--text2);width:100%;margin-bottom:2px}
.exp-btn{padding:5px 9px;border:none;border-radius:4px;cursor:pointer;font-weight:700;font-size:11px}
.act-btn{padding:5px 9px;border-radius:4px;border:1px solid var(--border);background:var(--bg);color:var(--text);cursor:pointer;font-size:11px}
.act-btn:hover{border-color:var(--accent)}

/* ── Map wrapper ── */
#map-wrap{flex:1;position:relative;overflow:hidden}
#map{width:100%;height:100%}

/* ── Map toolbar ── */
#map-toolbar{
  position:absolute;top:12px;left:50%;transform:translateX(-50%);
  z-index:500;
  display:flex;gap:6px;
  background:rgba(255,255,255,.93);
  border-radius:8px;
  padding:6px 10px;
  box-shadow:0 2px 12px rgba(0,0,0,.2);
}
#map-toolbar button{
  padding:6px 14px;border-radius:6px;border:none;cursor:pointer;
  font-size:13px;font-weight:600;transition:background .15s;
}
#btn-draw{background:#4f8ef7;color:#fff}
#btn-draw:hover{background:#2e70e0}
#btn-draw.active{background:#f59e0b;color:#fff}
#btn-finish{background:var(--success);color:#fff}
#btn-finish:hover{filter:brightness(1.1)}
#btn-cancel-draw{background:#e5e7eb;color:#333}
#btn-cancel-draw:hover{background:#d1d5db}
#draw-hint{
  position:absolute;bottom:30px;left:50%;transform:translateX(-50%);
  z-index:500;
  background:rgba(0,0,0,.7);color:#fff;
  padding:7px 14px;border-radius:20px;
  font-size:12px;pointer-events:none;
}

/* ── Modal ── */
#modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
  z-index:9000;align-items:center;justify-content:center;
}
#modal-overlay.show{display:flex}
#modal-box{
  background:var(--bg2);border:1px solid var(--border);
  border-radius:10px;padding:20px;width:340px;max-height:90vh;
  overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,.5);
}
#modal-box h3{font-size:15px;font-weight:700;margin-bottom:14px;color:var(--accent)}
.m-label{font-size:11px;color:var(--text2);display:block;margin-bottom:3px;margin-top:10px}
.m-input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:5px;color:var(--text);padding:7px 9px;font-size:13px;outline:none}
.m-input:focus{border-color:var(--accent)}
#modal-hn-list{
  max-height:120px;overflow-y:auto;background:var(--bg);border:1px solid var(--border);
  border-radius:5px;padding:6px 8px;font-size:12px;color:var(--text2);
  display:flex;flex-wrap:wrap;gap:4px;
}
.hn-tag{
  display:inline-flex;align-items:center;gap:3px;
  background:var(--bg3);border:1px solid var(--border);border-radius:3px;
  padding:2px 5px;font-size:11px;color:var(--text);
}
.hn-tag .hn-x{
  cursor:pointer;color:var(--text2);font-size:10px;line-height:1;
}
.hn-tag .hn-x:hover{color:var(--danger)}
#hn-add-row{display:flex;gap:5px;margin-top:5px}
#hn-add-input{
  flex:1;background:var(--bg);border:1px solid var(--border);border-radius:4px;
  color:var(--text);padding:4px 7px;font-size:12px;outline:none;
}
#hn-add-input:focus{border-color:var(--accent)}
#hn-add-btn{
  padding:4px 10px;background:var(--accent);border:none;border-radius:4px;
  color:#fff;cursor:pointer;font-size:12px;font-weight:600;
}
#modal-count{font-size:11px;color:var(--text2);margin-top:4px}
.modal-btns{display:flex;gap:8px;margin-top:16px}
#modal-save{flex:1;padding:9px;background:var(--accent);border:none;border-radius:6px;color:#fff;font-weight:700;cursor:pointer;font-size:13px}
#modal-save:hover{filter:brightness(1.1)}
#modal-delete{padding:9px 12px;background:#333;border:1px solid #555;border-radius:6px;color:#aaa;cursor:pointer;font-size:13px}
#modal-delete:hover{border-color:var(--danger);color:var(--danger)}
#modal-cancel{padding:9px 12px;background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text2);cursor:pointer;font-size:13px}
#modal-cancel:hover{border-color:var(--text)}
#modal-loading{text-align:center;padding:10px;color:var(--text2);font-size:12px}

/* ── Leaflet overrides ── */
.leaflet-popup-content-wrapper{background:#fff!important;color:#222!important;border-radius:7px!important;box-shadow:0 4px 16px rgba(0,0,0,.2)!important}
.leaflet-popup-tip{background:#fff!important}
.addr-marker-icon{
  width:8px!important;height:8px!important;
  border-radius:50%;border:2px solid #fff;
  box-shadow:0 1px 3px rgba(0,0,0,.3);
}
</style>
</head>
<body>
<input type="file" id="csv-file-input" accept=".csv,.txt" style="display:none"/>

<!-- Segment edit/create modal -->
<div id="modal-overlay">
  <div id="modal-box">
    <h3 id="modal-title">Nouveau segment</h3>
    <span class="m-label">Rue</span>
    <input class="m-input" id="modal-street" type="text" placeholder="Nom de la rue…"/>
    <span class="m-label">Numéros (cliquer ✕ pour supprimer)</span>
    <div id="modal-hn-list"><em style="color:var(--text2)">—</em></div>
    <div id="hn-add-row">
      <input id="hn-add-input" type="text" placeholder="Ajouter un n° (ex: 12bis)"/>
      <button id="hn-add-btn">＋</button>
    </div>
    <div id="modal-count"></div>
    <span class="m-label">Circuit</span>
    <select class="m-input" id="modal-circuit"></select>
    <span class="m-label">Nb colis estimé</span>
    <input class="m-input" id="modal-nb" type="number" min="0" placeholder="0"/>
    <div id="modal-loading" style="display:none">⏳ Détection des adresses…</div>
    <div class="modal-btns">
      <button id="modal-save">✓ Enregistrer</button>
      <button id="modal-delete" style="display:none">🗑 Supprimer</button>
      <button id="modal-cancel">Annuler</button>
    </div>
  </div>
</div>

<div id="app">
  <!-- Sidebar -->
  <div id="sidebar">
    <div id="sb-head">
      <h1>Circuits Vincennes</h1>
      <div id="sb-stats">Chargement…</div>
    </div>
    <div id="sb-search"><input id="search-input" type="text" placeholder="🔍 Rechercher…"/></div>
    <div id="circuit-filters"><button id="add-circuit-btn">＋ Nouveau circuit</button></div>
    <div id="street-list"></div>
    <div id="export-row">
      <span class="row-lbl">Exporter par circuit :</span>
      <button class="act-btn" id="export-all-btn" title="Exporter tous les circuits en CSV">⬇ Tout exporter</button>
      <button class="act-btn" id="import-btn">⬆ Importer CSV</button>
    </div>
  </div>

  <!-- Map -->
  <div id="map-wrap">
    <div id="map"></div>
    <div id="map-toolbar">
      <button id="btn-draw">✏ Tracer un segment</button>
      <button id="btn-finish" style="display:none">✓ Terminer le tracé</button>
      <button id="btn-cancel-draw" style="display:none">✕ Annuler</button>
    </div>
    <div id="draw-hint" style="display:none">Cliquez · Double-clic ou "Terminer" pour finir</div>
  </div>
</div>

<script>
// ─── State ────────────────────────────────────────────────────────────────────
let segments = {};
let circuits  = [];
let colors    = {};
let visibleCircuits = new Set();

let map;
let segmentLayers = {};   // key → { pline, addrMarkers[] }

// Drawing state
let drawMode = false;
let drawPoints = [];      // [{lat,lon}]
let drawPolyline = null;  // L.polyline in progress
let drawDotMarkers = [];  // L.circleMarker for each point

// Modal state
let modalMode = null;     // 'create' | 'edit'
let modalKey  = null;
let modalCoords = [];
let modalAddresses = [];

// ─── Utils ────────────────────────────────────────────────────────────────────
function getColor(c){ return colors[c] || '#888' }
function contrastColor(hex){
  const r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return (r*299+g*587+b*114)/1000>150?'#000':'#fff';
}
function capitalize(s){ return s.replace(/\b\w/g,c=>c.toUpperCase()) }

// ─── Map ──────────────────────────────────────────────────────────────────────
function initMap(){
  map = L.map('map',{center:[48.8472,2.4388],zoom:15,doubleClickZoom:false});
  L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',{
    attribution:'&copy; OpenStreetMap &copy; CARTO',maxZoom:20
  }).addTo(map);

  map.on('click', onMapClick);
  map.on('dblclick', onMapDblClick);

  // Prevent toolbar clicks from reaching the map
  L.DomEvent.disableClickPropagation(document.getElementById('map-toolbar'));
}

function onMapClick(e){
  if(!drawMode) return;
  drawPoints.push([e.latlng.lat, e.latlng.lng]);
  updateDrawPreview();
}

function onMapDblClick(e){
  if(!drawMode) return;
  L.DomEvent.stopPropagation(e);
  // A dblclick fires 2 click events first → remove both extra points
  if(drawPoints.length >= 2) { drawPoints.pop(); drawPoints.pop(); }
  else { drawPoints = []; }
  finishDraw();
}

function updateDrawPreview(){
  const pts = drawPoints.map(p => L.latLng(p[0], p[1]));
  if(drawPolyline){
    drawPolyline.setLatLngs(pts);
  } else {
    drawPolyline = L.polyline(pts, {color:'#f59e0b', weight:4, dashArray:'8 5'}).addTo(map);
  }
  // Vertex dots
  drawDotMarkers.forEach(m=>m.remove());
  drawDotMarkers = [];
  drawPoints.forEach(p => {
    const m = L.circleMarker([p[0],p[1]],{radius:5,color:'#fff',fillColor:'#f59e0b',fillOpacity:1,weight:2}).addTo(map);
    drawDotMarkers.push(m);
  });
}

function clearDraw(){
  if(drawPolyline){ drawPolyline.remove(); drawPolyline=null; }
  drawDotMarkers.forEach(m=>m.remove());
  drawDotMarkers=[];
  drawPoints=[];
}

// ─── Draw mode toggle ─────────────────────────────────────────────────────────
document.getElementById('btn-draw').addEventListener('click', ()=>{
  if(drawMode){ cancelDraw(); } else { startDraw(); }
});
document.getElementById('btn-finish').addEventListener('click', finishDraw);
document.getElementById('btn-cancel-draw').addEventListener('click', cancelDraw);

function startDraw(){
  drawMode = true;
  drawPoints = [];
  document.getElementById('btn-draw').textContent = '⬛ Arrêter';
  document.getElementById('btn-draw').classList.add('active');
  document.getElementById('btn-finish').style.display = 'inline-block';
  document.getElementById('btn-cancel-draw').style.display = 'inline-block';
  document.getElementById('draw-hint').style.display = 'block';
  map.getContainer().style.cursor = 'crosshair';
}

function cancelDraw(){
  drawMode = false;
  clearDraw();
  document.getElementById('btn-draw').textContent = '✏ Tracer un segment';
  document.getElementById('btn-draw').classList.remove('active');
  document.getElementById('btn-finish').style.display = 'none';
  document.getElementById('btn-cancel-draw').style.display = 'none';
  document.getElementById('draw-hint').style.display = 'none';
  map.getContainer().style.cursor = '';
}

async function finishDraw(){
  if(drawPoints.length < 2){
    alert('Tracez au moins 2 points pour définir un segment.');
    return;
  }
  const coords = [...drawPoints];
  cancelDraw();  // exit draw mode but keep coords
  openCreateModal(coords);
}

// ─── Modal ────────────────────────────────────────────────────────────────────
function openCreateModal(coords){
  modalMode   = 'create';
  modalKey    = null;
  modalCoords = coords;
  modalAddresses = [];

  document.getElementById('modal-title').textContent = 'Nouveau segment';
  document.getElementById('modal-street').value = '';
  document.getElementById('modal-nb').value = '';
  document.getElementById('modal-delete').style.display = 'none';
  document.getElementById('modal-hn-list').innerHTML = '<em style="color:var(--text2)">Détection en cours…</em>';
  document.getElementById('modal-count').textContent = '';
  fillCircuitSelect('');
  document.getElementById('modal-loading').style.display = 'block';
  showModal();

  // Detect addresses async
  fetch('/api/detect_addresses',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({coordinates: coords})
  }).then(r=>r.json()).then(data=>{
    document.getElementById('modal-loading').style.display = 'none';
    modalAddresses = data.addresses || [];
    if(data.street_name && !document.getElementById('modal-street').value)
      document.getElementById('modal-street').value = data.street_name;
    // Estimate nb_colis
    if(!document.getElementById('modal-nb').value && modalAddresses.length)
      document.getElementById('modal-nb').value = modalAddresses.length;
    renderHNList(modalAddresses);
  }).catch(()=>{
    document.getElementById('modal-loading').style.display = 'none';
    renderHNList([]);
  });
}

function openEditModal(key){
  const seg = segments[key];
  if(!seg) return;
  modalMode = 'edit';
  modalKey  = key;
  modalCoords = seg.coordinates || [];
  modalAddresses = (seg.house_numbers||[]).map(n=>({housenumber:n,street:seg.street_name||''}));

  document.getElementById('modal-title').textContent = 'Modifier le segment';
  document.getElementById('modal-street').value = seg.street_name || '';
  document.getElementById('modal-nb').value = seg.nb_colis || '';
  document.getElementById('modal-delete').style.display = 'inline-block';
  document.getElementById('modal-loading').style.display = 'none';
  fillCircuitSelect(seg.circuit);
  renderHNList(modalAddresses);
  showModal();
}

function fillCircuitSelect(selected){
  const sel = document.getElementById('modal-circuit');
  sel.innerHTML = circuits.map(c=>`<option value="${c}"${c===selected?' selected':''}>${c}</option>`).join('');
}

function renderHNList(addresses){
  const box = document.getElementById('modal-hn-list');
  if(!addresses.length){
    box.innerHTML = '<em style="color:var(--text2)">Aucun numéro</em>';
    document.getElementById('modal-count').textContent = '';
    return;
  }
  box.innerHTML = '';
  addresses.forEach((a, i) => {
    const tag = document.createElement('span');
    tag.className = 'hn-tag';
    tag.innerHTML = `${a.housenumber}<span class="hn-x" title="Supprimer">✕</span>`;
    tag.querySelector('.hn-x').addEventListener('click', ()=>{
      modalAddresses.splice(i, 1);
      renderHNList(modalAddresses);
      // Update nb_colis estimate
      const nbField = document.getElementById('modal-nb');
      if(nbField.value == addresses.length) nbField.value = modalAddresses.length;
    });
    box.appendChild(tag);
  });
  document.getElementById('modal-count').textContent =
    `${addresses.length} numéro(s)`;
}

// Add number manually
document.getElementById('hn-add-btn').addEventListener('click', addHNManual);
document.getElementById('hn-add-input').addEventListener('keydown', e=>{
  if(e.key === 'Enter'){ e.preventDefault(); addHNManual(); }
});
function addHNManual(){
  const val = document.getElementById('hn-add-input').value.trim();
  if(!val) return;
  modalAddresses.push({housenumber: val, street: '', lat: 0, lon: 0});
  renderHNList(modalAddresses);
  document.getElementById('modal-nb').value = modalAddresses.length;
  document.getElementById('hn-add-input').value = '';
  document.getElementById('hn-add-input').focus();
}

function showModal(){ document.getElementById('modal-overlay').classList.add('show'); }
function hideModal(){ document.getElementById('modal-overlay').classList.remove('show'); }

document.getElementById('modal-cancel').addEventListener('click', hideModal);
document.getElementById('modal-overlay').addEventListener('click', e=>{
  if(e.target===document.getElementById('modal-overlay')) hideModal();
});

document.getElementById('modal-save').addEventListener('click', async ()=>{
  const circuit    = document.getElementById('modal-circuit').value;
  const street_name= document.getElementById('modal-street').value.trim();
  const nb         = document.getElementById('modal-nb').value;
  const house_numbers = modalAddresses.map(a=>a.housenumber);

  const body = {
    circuit, street_name,
    coordinates: modalCoords,
    house_numbers,
  };
  if(nb !== '') body.nb_colis = parseInt(nb)||0;
  if(modalMode==='edit' && modalKey) body.key = modalKey;

  const res = await fetch('/api/assign',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)
  });
  const data = await res.json();
  segments = data.segments;
  // Re-draw the assigned segment on map
  const key = data.key || modalKey;
  if(segmentLayers[key]) removeSegmentLayer(key);
  drawSegmentOnMap(key, segments[key]);
  renderSidebar();
  hideModal();
});

document.getElementById('modal-delete').addEventListener('click', async ()=>{
  if(!modalKey) return;
  if(!confirm('Supprimer ce segment ?')) return;
  const res = await fetch('/api/unassign',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key:modalKey})
  });
  const data = await res.json();
  segments = data.segments;
  removeSegmentLayer(modalKey);
  renderSidebar();
  hideModal();
});

// ─── Segment map rendering ────────────────────────────────────────────────────
function drawSegmentOnMap(key, seg){
  if(!seg || !seg.coordinates || seg.coordinates.length < 2) return;
  const col = getColor(seg.circuit);
  const visible = visibleCircuits.has(seg.circuit);

  const pline = L.polyline(seg.coordinates.map(c=>L.latLng(c[0],c[1])),{
    color: col, weight:5, opacity: visible ? 0.9 : 0.2
  }).addTo(map);

  // Hover
  pline.on('mouseover', function(){
    this.setStyle({weight:8,opacity:1});
    const name = seg.street_name ? capitalize(seg.street_name) : key;
    this.bindTooltip(name,{sticky:true}).openTooltip();
  });
  pline.on('mouseout', function(){
    this.setStyle({weight:5,opacity: visibleCircuits.has(seg.circuit)?0.9:0.2});
    this.unbindTooltip();
  });
  pline.on('click', function(e){
    L.DomEvent.stopPropagation(e);
    openEditModal(key);
  });
  // Right-click → delete directly
  pline.on('contextmenu', async function(e){
    L.DomEvent.stopPropagation(e);
    const name = seg.street_name ? capitalize(seg.street_name) : key;
    if(!confirm(`Supprimer "${name}" ?`)) return;
    const res = await fetch('/api/unassign',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
    const data = await res.json();
    segments = data.segments;
    removeSegmentLayer(key);
    renderSidebar();
  });

  const addrMarkers = [];

  segmentLayers[key] = { pline, addrMarkers };
}

function removeSegmentLayer(key){
  const layer = segmentLayers[key];
  if(!layer) return;
  layer.pline.remove();
  layer.addrMarkers.forEach(m=>m.remove());
  delete segmentLayers[key];
}

function rebuildAllSegments(){
  Object.keys(segmentLayers).forEach(removeSegmentLayer);
  Object.entries(segments).forEach(([key, seg]) => drawSegmentOnMap(key, seg));
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────
function renderFilters(){
  const container = document.getElementById('circuit-filters');
  const addBtn = document.getElementById('add-circuit-btn');
  container.innerHTML = '';

  circuits.forEach(c => {
    const col = getColor(c);
    const txt = contrastColor(col);
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (visibleCircuits.has(c)?'':' off');
    btn.style.background = col;
    btn.style.color = txt;
    btn.innerHTML =
      `<span class="lbl" data-c="${c}">${c}</span>` +
      `<span class="ico" title="Couleur" data-col="${c}">🎨</span>` +
      `<span class="ico" title="Supprimer" data-del="${c}">✕</span>`;
    btn.querySelector('[data-c]').addEventListener('click', ()=>toggleCircuit(c));
    btn.querySelector('[data-col]').addEventListener('click', e=>{ e.stopPropagation(); openColorPicker(c,btn); });
    btn.querySelector('[data-del]').addEventListener('click', e=>{ e.stopPropagation(); deleteCircuit(c); });
    container.appendChild(btn);
  });
  container.appendChild(addBtn);

  // Export buttons
  const row = document.getElementById('export-row');
  row.querySelectorAll('.exp-btn').forEach(b=>b.remove());
  const lbl = row.querySelector('.row-lbl');
  circuits.forEach(c=>{
    const col = getColor(c);
    const btn = document.createElement('button');
    btn.className = 'exp-btn';
    btn.style.background = col;
    btn.style.color = contrastColor(col);
    btn.textContent = c;
    btn.title = `Exporter circuit ${c}`;
    btn.addEventListener('click', ()=>{ window.location.href=`/api/export_circuit/${encodeURIComponent(c)}`; });
    row.insertBefore(btn, lbl.nextSibling);
  });
}

function renderStreetList(){
  const q = document.getElementById('search-input').value.toLowerCase();
  const container = document.getElementById('street-list');
  container.innerHTML = '';

  const byC = {};
  circuits.forEach(c=>{ byC[c]=[]; });

  Object.entries(segments).forEach(([key, info])=>{
    const name = info.street_name || key;
    if(!byC[info.circuit]) byC[info.circuit]=[];
    if(!q || name.toLowerCase().includes(q))
      byC[info.circuit].push({key, info, name});
  });

  circuits.forEach(c=>{
    (byC[c]||[]).sort((a,b)=>a.name.localeCompare(b.name)).forEach(({key,info,name})=>{
      const col = getColor(c);
      const div = document.createElement('div');
      div.className = 'seg-item';
      div.style.borderLeftColor = col;
      const nb = info.nb_colis;
      const hn = (info.house_numbers||[]).length;
      div.innerHTML =
        `<span class="seg-badge" style="background:${col};color:${contrastColor(col)}">${c}</span>` +
        `<span class="seg-name" title="${capitalize(name)}">${capitalize(name)}</span>` +
        `<span class="seg-meta">${nb?nb+'📦':''}${hn?' '+hn+'🏠':''}</span>` +
        `<span class="seg-del" title="Supprimer" data-key="${key}">🗑</span>`;
      div.addEventListener('click', e=>{
        if(e.target.classList.contains('seg-del')) return;
        zoomToSegment(key);
      });
      div.querySelector('.seg-del').addEventListener('click', async e=>{
        e.stopPropagation();
        if(!confirm(`Supprimer "${capitalize(name)}" ?`)) return;
        const res = await fetch('/api/unassign',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
        const data = await res.json();
        segments = data.segments;
        removeSegmentLayer(key);
        renderSidebar();
      });
      container.appendChild(div);
    });
  });

  const total = Object.keys(segments).length;
  const totalColis = Object.values(segments).reduce((s,i)=>s+(i.nb_colis||0),0);
  document.getElementById('sb-stats').textContent =
    `${total} segment(s) · ${totalColis} colis estimés`;
}

function renderSidebar(){ renderFilters(); renderStreetList(); }

// ─── Actions ──────────────────────────────────────────────────────────────────
function toggleCircuit(c){
  if(visibleCircuits.has(c)) visibleCircuits.delete(c);
  else visibleCircuits.add(c);
  // Update opacity for this circuit
  Object.entries(segmentLayers).forEach(([key, layer])=>{
    if(segments[key] && segments[key].circuit===c){
      layer.pline.setStyle({opacity: visibleCircuits.has(c)?0.9:0.2});
    }
  });
  renderSidebar();
}

function zoomToSegment(key){
  const layer = segmentLayers[key];
  if(layer){
    map.fitBounds(layer.pline.getBounds(),{padding:[60,60],maxZoom:18});
    openEditModal(key);
  }
}

function openColorPicker(circuit, btnEl){
  let p = document.getElementById('_cp');
  if(!p){ p=document.createElement('input');p.type='color';p.id='_cp';p.style.cssText='position:fixed;left:-999px;opacity:0';document.body.appendChild(p); }
  p.value = getColor(circuit);
  p.click();
  p.onchange = async ()=>{
    const color = p.value;
    await fetch('/api/update_color',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({circuit,color})});
    colors[circuit] = color;
    Object.entries(segmentLayers).forEach(([key,layer])=>{
      if(segments[key]&&segments[key].circuit===circuit) layer.pline.setStyle({color});
    });
    renderSidebar();
  };
}

async function deleteCircuit(c){
  if(!confirm(`Supprimer le circuit ${c} et tous ses segments ?`)) return;
  const res = await fetch('/api/delete_circuit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:c})});
  const cfg = await res.json();
  circuits = cfg.circuits; colors = cfg.colors;
  const rd = await fetch('/api/data'); const d = await rd.json();
  segments = d.segments;
  visibleCircuits = new Set(circuits);
  rebuildAllSegments();
  renderSidebar();
}

document.getElementById('add-circuit-btn').addEventListener('click', async ()=>{
  const name = prompt('Nom du circuit (ex: 549) :');
  if(!name||!name.trim()) return;
  const color = prompt('Couleur hexadécimale :', '#888888');
  const res = await fetch('/api/add_circuit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim(),color:color||'#888888'})});
  const cfg = await res.json();
  circuits=cfg.circuits; colors=cfg.colors;
  visibleCircuits.add(name.trim());
  renderSidebar();
});

document.getElementById('export-all-btn').addEventListener('click', ()=>{ window.location.href='/api/export_all'; });
document.getElementById('import-btn').addEventListener('click', ()=>document.getElementById('csv-file-input').click());
document.getElementById('csv-file-input').addEventListener('change', async function(){
  const file=this.files[0]; if(!file) return;
  const fd=new FormData(); fd.append('file',file);
  const res = await fetch('/api/import_csv',{method:'POST',body:fd});
  const data = await res.json();
  segments=data.segments; rebuildAllSegments(); renderSidebar();
  alert(`Import : ${data.imported} segment(s) importé(s).`);
  this.value='';
});

document.getElementById('search-input').addEventListener('input', renderStreetList);

// ─── Boot ─────────────────────────────────────────────────────────────────────
async function boot(){
  initMap();
  const res = await fetch('/api/data');
  const d = await res.json();
  segments=d.segments; circuits=d.circuits; colors=d.colors;
  visibleCircuits = new Set(circuits);
  rebuildAllSegments();
  renderSidebar();
}
boot();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, port=5000)
