"""
Application Flask - Gestion interactive des circuits Colissimo
"""
from flask import Flask, jsonify, request, render_template_string, send_file, Response
import csv, json, re, os, math
from collections import defaultdict
import io

app = Flask(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CSV_FILE   = os.path.join(BASE_DIR, 'circuits_vincennes.csv')
CACHE_FILE = os.path.join(BASE_DIR, 'geocache.json')

FUSION = {'542': '542', '543': '542'}   # 542 et 543 = même secteur → "542"

CIRCUITS_FILE    = os.path.join(BASE_DIR, 'circuits_config.json')
EXTRACTION_FILE  = os.path.join(os.path.dirname(BASE_DIR), 'extraction_colissimo.txt')

def mark_in_extraction(rue, circuit, tag):
    """Ajoute un tag [SUPPRIMÉ] ou [MODIFIÉ ...] sur les lignes correspondantes dans extraction_colissimo.txt."""
    if not os.path.exists(EXTRACTION_FILE):
        return
    with open(EXTRACTION_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    modified = []
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 8:
            line_c   = parts[1]
            line_rue = parts[6].lower()
            if line_c == circuit and line_rue == rue.lower() and tag not in line:
                line = line.rstrip('\n') + f'  {tag}\n'
        modified.append(line)
    with open(EXTRACTION_FILE, 'w', encoding='utf-8') as f:
        f.writelines(modified)

DEFAULT_COLORS = {
    '541': '#e74c3c',
    '542': '#3498db',
    '544': '#27ae60',
    '545': '#e67e22',
    '546': '#9b59b6',
    '547': '#1abc9c',
    '548': '#e91e63',
}
DEFAULT_CIRCUITS  = ['541', '542', '544', '545', '546', '547', '548']
ALL_DISTRICTS     = ['d 1','d 2','d 3','d 4','d 5','inconnu']

def load_circuits_config():
    if os.path.exists(CIRCUITS_FILE):
        with open(CIRCUITS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'circuits': DEFAULT_CIRCUITS, 'colors': DEFAULT_COLORS}

def save_circuits_config(cfg):
    with open(CIRCUITS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ─── Chargement des données ───────────────────────────────────────────────────
def load_data():
    rows = []
    with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f, delimiter=';'):
            r['C'] = FUSION.get(r['C'].strip(), r['C'].strip())
            rows.append(r)
    return rows

def save_data(rows):
    fieldnames = ['DATE','C','CIRCUIT','DISTRICT','NB_COLIS','PGEO','RUE','NUMEROS_RUE']
    with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        w.writeheader()
        w.writerows(rows)

def load_geocache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_geocache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def extract_nums(s):
    if not s: return []
    return [int(m) for m in re.findall(r'\d+', s)]

def build_markers(rows, geocache):
    """Agrège par (C, RUE) et construit les marqueurs carte (avec waypoints)."""
    agg = defaultdict(lambda: {'nums': set(), 'raw': [], 'districts': set(), 'rows_idx': []})
    for i, row in enumerate(rows):
        c   = row['C'].strip()
        rue = row['RUE'].strip()
        agg[(c, rue)]['nums'].update(extract_nums(row['NUMEROS_RUE']))
        for tok in row['NUMEROS_RUE'].split(','):
            t = tok.strip()
            if t and t not in agg[(c, rue)]['raw']:
                agg[(c, rue)]['raw'].append(t)
        agg[(c, rue)]['districts'].add(row['DISTRICT'].strip())
        agg[(c, rue)]['rows_idx'].append(i)

    markers = []
    for (c, rue), val in agg.items():
        base_id   = f"{c}|{rue}"
        base_coord = geocache.get(base_id) or geocache.get(rue)
        nums       = sorted(val['nums'])

        shared = {
            'base_id':   base_id,
            'circuit':   c,
            'rue':       rue,
            'nb_min':    min(nums) if nums else '',
            'nb_max':    max(nums) if nums else '',
            'numeros':   ', '.join(val['raw']),
            'districts': list(val['districts']),
            'rows_idx':  val['rows_idx'],
        }

        # Marqueur de base
        markers.append({
            **shared,
            'id':          base_id,
            'lat':         base_coord[0] if base_coord else None,
            'lon':         base_coord[1] if base_coord else None,
            'geocoded':    base_coord is not None,
            'is_waypoint': False,
        })

        # Waypoints supplémentaires : clés circuit|rue|N dans le geocache
        n = 1
        while True:
            wp_key = f"{base_id}|{n}"
            wp_coord = geocache.get(wp_key)
            if wp_coord is None:
                break
            markers.append({
                **shared,
                'id':          wp_key,
                'lat':         wp_coord[0],
                'lon':         wp_coord[1],
                'geocoded':    True,
                'is_waypoint': True,
            })
            n += 1

    return markers

# ═══════════════════════════════════════════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/data')
def api_data():
    cfg = load_circuits_config()
    rows = load_data()
    geocache = load_geocache()
    markers = build_markers(rows, geocache)
    return jsonify({
        'markers':   markers,
        'circuits':  cfg['circuits'],
        'colors':    cfg['colors'],
        'districts': ALL_DISTRICTS,
    })

@app.route('/api/add_circuit', methods=['POST'])
def api_add_circuit():
    body  = request.json
    name  = body.get('name', '').strip()
    color = body.get('color', '#888888').strip()
    if not name:
        return jsonify({'error': 'Nom vide'}), 400
    cfg = load_circuits_config()
    if name in cfg['circuits']:
        return jsonify({'error': 'Circuit déjà existant'}), 400
    cfg['circuits'].append(name)
    cfg['colors'][name] = color
    save_circuits_config(cfg)
    return jsonify({'status': 'ok', 'circuits': cfg['circuits'], 'colors': cfg['colors']})

@app.route('/api/update_color', methods=['POST'])
def api_update_color():
    body  = request.json
    name  = body.get('name', '').strip()
    color = body.get('color', '').strip()
    if not name or not color:
        return jsonify({'error': 'Paramètres manquants'}), 400
    cfg = load_circuits_config()
    if name not in cfg['circuits']:
        return jsonify({'error': 'Circuit introuvable'}), 404
    cfg['colors'][name] = color
    save_circuits_config(cfg)
    return jsonify({'status': 'ok', 'colors': cfg['colors']})

@app.route('/api/delete_circuit', methods=['POST'])
def api_delete_circuit():
    body = request.json
    name = body.get('name', '').strip()
    cfg  = load_circuits_config()
    if name not in cfg['circuits']:
        return jsonify({'error': 'Circuit introuvable'}), 404
    cfg['circuits'].remove(name)
    cfg['colors'].pop(name, None)
    save_circuits_config(cfg)
    return jsonify({'status': 'ok', 'circuits': cfg['circuits'], 'colors': cfg['colors']})

@app.route('/api/add_rue', methods=['POST'])
def api_add_rue():
    """Ajoute une nouvelle rue dans le CSV et tente de la géocoder."""
    import requests as req
    from datetime import date
    body     = request.json
    rue      = body.get('rue', '').strip()
    circuit  = body.get('circuit', '').strip()
    district = body.get('district', '').strip()
    numeros  = body.get('numeros', '').strip()
    if not rue or not circuit:
        return jsonify({'error': 'Rue et circuit obligatoires'}), 400

    geocache = load_geocache()

    # Géocodage automatique si pas encore dans le cache
    geocoded = False
    if rue not in geocache:
        try:
            r = req.get('https://nominatim.openstreetmap.org/search',
                        params={'q': rue + ', Vincennes 94300, France', 'format': 'json', 'limit': 1},
                        headers={'User-Agent': 'VincennesCircuitMap/1.0'}, timeout=8)
            results = r.json()
            if results:
                geo_key = f"{circuit}|{rue}"
                geocache[geo_key] = [float(results[0]['lat']), float(results[0]['lon'])]
                save_geocache(geocache)
                geocoded = True
        except Exception:
            pass

    rows = load_data()
    new_row = {
        'DATE': date.today().strftime('%d/%m/%Y'),
        'C': circuit,
        'CIRCUIT': '',
        'DISTRICT': district,
        'NB_COLIS': '',
        'PGEO': '',
        'RUE': rue,
        'NUMEROS_RUE': numeros,
    }
    rows.append(new_row)
    save_data(rows)
    markers = build_markers(rows, geocache)
    return jsonify({'status': 'ok', 'markers': markers, 'geocoded': geocoded})

@app.route('/api/delete_rue', methods=['POST'])
def api_delete_rue():
    """Supprime toutes les lignes d'une rue/circuit du CSV et marque dans l'extraction."""
    body    = request.json
    rue     = body.get('rue', '').strip()
    circuit = body.get('circuit', '').strip()
    if not rue or not circuit:
        return jsonify({'error': 'Rue et circuit obligatoires'}), 400
    rows = load_data()
    rows = [r for r in rows if not (r['RUE'].strip().lower() == rue.lower() and r['C'].strip() == circuit)]
    save_data(rows)
    mark_in_extraction(rue, circuit, '[SUPPRIMÉ]')
    markers = build_markers(rows, load_geocache())
    return jsonify({'status': 'ok', 'markers': markers})

@app.route('/api/update', methods=['POST'])
def api_update():
    """Met à jour le circuit/district/numéros d'une rue entière ou d'un numéro précis."""
    body       = request.json
    rue        = body.get('rue', '').strip()
    old_c      = body.get('old_circuit', '').strip()
    new_c      = body.get('new_circuit', '').strip()
    new_dist   = body.get('new_district', '').strip()
    only_num   = body.get('only_numero', '')
    new_nums   = body.get('new_numeros', '')   # chaîne vide = pas envoyé
    lat        = body.get('lat')
    lon        = body.get('lon')

    rows = load_data()
    geocache = load_geocache()

    changed = 0
    for row in rows:
        if row['RUE'].strip().lower() != rue.lower():
            continue
        if row['C'].strip() != old_c:
            continue
        # filtre sur numéro spécifique si demandé
        if only_num:
            nums_in_row = row['NUMEROS_RUE']
            pattern = r'(?<![0-9])' + re.escape(str(only_num)) + r'(?![0-9A-Za-z])'
            if not re.search(pattern, nums_in_row):
                continue
        old_nums = row['NUMEROS_RUE']
        row['C']        = new_c
        row['DISTRICT'] = new_dist if new_dist else row['DISTRICT']
        if new_nums:
            row['NUMEROS_RUE'] = new_nums
        changed += 1

    # Marquer dans l'extraction si modification significative
    if changed:
        if new_c != old_c:
            mark_in_extraction(rue, old_c, f'[MODIFIÉ→{new_c}]')
        elif new_nums and new_nums != old_nums:
            mark_in_extraction(rue, old_c, f'[MODIFIÉ nums:{new_nums}]')

    # Mise à jour coordonnées si fournies
    geo_key = f"{new_c}|{rue}"
    if lat and lon and geo_key not in geocache:
        geocache[geo_key] = [float(lat), float(lon)]
        save_geocache(geocache)

    save_data(rows)

    # Retourner les nouveaux marqueurs
    markers = build_markers(load_data(), geocache)
    return jsonify({'status': 'ok', 'changed': changed, 'markers': markers})

@app.route('/api/geocode', methods=['POST'])
def api_geocode():
    """Enregistre manuellement des coordonnées pour une rue (clé = circuit|rue ou rue)."""
    body    = request.json
    rue     = body.get('rue', '').strip()
    circuit = body.get('circuit', '').strip()
    lat     = float(body.get('lat'))
    lon     = float(body.get('lon'))
    geocache = load_geocache()
    key = f"{circuit}|{rue}" if circuit else rue
    geocache[key] = [lat, lon]
    save_geocache(geocache)
    return jsonify({'status': 'ok', 'rue': rue, 'lat': lat, 'lon': lon})

@app.route('/api/geocode_search')
def api_geocode_search():
    """Cherche les coordonnées d'une adresse via Nominatim."""
    import requests as req
    query = request.args.get('q', '')
    try:
        r = req.get('https://nominatim.openstreetmap.org/search',
                    params={'q': query + ', France', 'format': 'json', 'limit': 5},
                    headers={'User-Agent': 'VincennesCircuitMap/1.0'}, timeout=8)
        results = r.json()
        return jsonify([{'display': x['display_name'], 'lat': float(x['lat']), 'lon': float(x['lon'])} for x in results])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/add_waypoint', methods=['POST'])
def api_add_waypoint():
    """Ajoute un waypoint supplémentaire pour une rue/circuit (clé = circuit|rue|N)."""
    body    = request.json
    rue     = body.get('rue', '').strip()
    circuit = body.get('circuit', '').strip()
    lat     = float(body.get('lat'))
    lon     = float(body.get('lon'))
    if not rue or not circuit:
        return jsonify({'error': 'Rue et circuit obligatoires'}), 400
    geocache = load_geocache()
    base_id  = f"{circuit}|{rue}"
    # Trouver le prochain index disponible
    n = 1
    while f"{base_id}|{n}" in geocache:
        n += 1
    wp_key = f"{base_id}|{n}"
    geocache[wp_key] = [lat, lon]
    save_geocache(geocache)
    rows    = load_data()
    markers = build_markers(rows, geocache)
    return jsonify({'status': 'ok', 'key': wp_key, 'markers': markers})

@app.route('/api/reverse_geocode')
def api_reverse_geocode():
    """Géocodage inverse via Nominatim."""
    import requests as req
    lat = request.args.get('lat', '')
    lon = request.args.get('lon', '')
    try:
        r = req.get('https://nominatim.openstreetmap.org/reverse',
                    params={'lat': lat, 'lon': lon, 'format': 'json'},
                    headers={'User-Agent': 'VincennesCircuitMap/1.0'}, timeout=8)
        data = r.json()
        addr    = data.get('address', {})
        rue     = addr.get('road', '')
        numero  = addr.get('house_number', '')
        display = data.get('display_name', '')
        return jsonify({'rue': rue, 'numero': numero, 'display': display})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export_csv')
def api_export_csv():
    rows = load_data()
    si = io.StringIO()
    w = csv.DictWriter(si, fieldnames=['DATE','C','CIRCUIT','DISTRICT','NB_COLIS','PGEO','RUE','NUMEROS_RUE'], delimiter=';')
    w.writeheader(); w.writerows(rows)
    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8-sig'))
    output.seek(0)
    return send_file(output, mimetype='text/csv',
                     as_attachment=True, download_name='circuits_export.csv')

@app.route('/api/export_extraction')
def api_export_extraction():
    """Exporte les données actuelles au format extraction (pipe-separated)."""
    rows = load_data()
    from datetime import date
    lines = [f"DATE | C | CIRCUIT | DISTRICT | NB_COLIS | PGEO | RUE | NUMEROS_RUE\n\n"]
    # Grouper par date et circuit
    groups = defaultdict(list)
    for row in rows:
        key = (row.get('DATE',''), row.get('C',''))
        groups[key].append(row)
    for (d, c), grp in sorted(groups.items()):
        lines.append(f"--- {d} C={c} ---\n")
        for row in grp:
            lines.append(f"{row['DATE']} | {row['C']} | {row['CIRCUIT']} | {row['DISTRICT']} | "
                         f"{row['NB_COLIS']} | {row['PGEO']} | {row['RUE']} | {row['NUMEROS_RUE']}\n")
        lines.append("\n")
    content = ''.join(lines)
    output = io.BytesIO(content.encode('utf-8'))
    output.seek(0)
    return send_file(output, mimetype='text/plain',
                     as_attachment=True, download_name='extraction_export.txt')

@app.route('/api/import', methods=['POST'])
def api_import():
    """Importe un fichier CSV (;) ou TXT (|) et fusionne avec les données existantes."""
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier'}), 400
    f = request.files['file']
    content = f.read().decode('utf-8-sig')
    rows = load_data()
    existing_keys = {(r['RUE'].strip().lower(), r['C'].strip(), r['NUMEROS_RUE'].strip()) for r in rows}
    added = 0

    # Détecter le format
    lines = content.splitlines()
    delimiter = '|' if lines and '|' in lines[0] else ';'

    for line in lines:
        line = line.strip()
        if not line or line.startswith('---') or line.startswith('DATE'):
            continue
        parts = [p.strip() for p in line.split(delimiter)]
        if len(parts) < 8:
            continue
        row = {
            'DATE': parts[0], 'C': parts[1], 'CIRCUIT': parts[2],
            'DISTRICT': parts[3], 'NB_COLIS': parts[4], 'PGEO': parts[5],
            'RUE': parts[6], 'NUMEROS_RUE': parts[7],
        }
        key = (row['RUE'].lower(), row['C'], row['NUMEROS_RUE'])
        if key not in existing_keys:
            rows.append(row)
            existing_keys.add(key)
            added += 1

    save_data(rows)
    markers = build_markers(rows, load_geocache())
    return jsonify({'status': 'ok', 'added': added, 'markers': markers})

# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Circuits Colissimo — Vincennes</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet-search@3.0.2/dist/leaflet-search.min.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'Segoe UI', Arial, sans-serif; display:flex; height:100vh; overflow:hidden; }

/* ── Sidebar ── */
#sidebar {
  width:360px; min-width:320px; background:#1a1a2e; color:#eee;
  display:flex; flex-direction:column; z-index:1000; box-shadow:3px 0 15px rgba(0,0,0,.5);
  transition: width .3s;
}
#sidebar.collapsed { width:0; overflow:hidden; }
#sidebar-header {
  padding:14px 16px; background:#16213e;
  border-bottom:1px solid #0f3460; flex-shrink:0;
}
#sidebar-header h2 { font-size:14px; color:#e94560; margin-bottom:4px; }
#sidebar-header p  { font-size:11px; color:#aaa; }

#search-box {
  padding:10px 12px; background:#16213e;
  border-bottom:1px solid #0f3460; flex-shrink:0;
}
#search-box input {
  width:100%; padding:7px 10px; border-radius:6px;
  border:1px solid #0f3460; background:#0f3460; color:#fff; font-size:13px;
}

/* Filtres circuits */
#filters {
  padding:8px 12px; background:#16213e;
  border-bottom:1px solid #0f3460; flex-shrink:0;
  display:flex; flex-wrap:wrap; gap:5px;
}
.filter-btn {
  padding:3px 9px; border-radius:12px; border:2px solid;
  cursor:pointer; font-size:11px; font-weight:bold;
  background:transparent; color:#fff; transition:.2s;
}
.filter-btn.active { color:#000 !important; }

/* Liste rues */
#street-list {
  flex:1; overflow-y:auto; padding:8px;
}
#street-list::-webkit-scrollbar { width:5px; }
#street-list::-webkit-scrollbar-track { background:#16213e; }
#street-list::-webkit-scrollbar-thumb { background:#0f3460; border-radius:3px; }

.street-item {
  padding:8px 10px; margin:3px 0; border-radius:6px;
  cursor:pointer; border-left:4px solid; transition:.15s;
  background:rgba(255,255,255,.04);
}
.street-item:hover { background:rgba(255,255,255,.1); }
.street-item.selected { background:rgba(255,255,255,.15) !important; }
.street-item .s-name { font-weight:600; font-size:13px; }
.street-item .s-info { font-size:11px; color:#aaa; margin-top:2px; }
.street-item .s-badge {
  display:inline-block; padding:1px 7px; border-radius:10px;
  font-size:10px; font-weight:bold; color:#fff; margin-right:4px;
}
.no-geo { opacity:.5; font-style:italic; }

/* Panneau édition */
#edit-panel {
  background:#16213e; border-top:2px solid #e94560;
  padding:14px; flex-shrink:0; display:none;
}
#edit-panel h3 { color:#e94560; font-size:13px; margin-bottom:10px; }
#edit-panel label { display:block; font-size:11px; color:#aaa; margin-bottom:3px; margin-top:8px; }
#edit-panel select, #edit-panel input {
  width:100%; padding:6px 8px; border-radius:5px;
  border:1px solid #0f3460; background:#0a0a1a; color:#fff; font-size:12px;
}
.edit-actions { display:flex; gap:6px; margin-top:12px; }
.btn {
  flex:1; padding:8px; border:none; border-radius:6px;
  cursor:pointer; font-weight:bold; font-size:12px; transition:.2s;
}
.btn-save   { background:#27ae60; color:#fff; }
.btn-save:hover { background:#2ecc71; }
.btn-cancel { background:#555; color:#fff; }
.btn-cancel:hover { background:#777; }
.btn-geo    { background:#0f3460; color:#e94560; border:1px solid #e94560; font-size:11px; }
.btn-export { background:#e94560; color:#fff; margin:8px 12px; padding:8px; border:none; border-radius:6px; cursor:pointer; font-weight:bold; width:calc(100% - 24px); }

/* Option numéro seul */
#num-filter { margin-top:6px; }
#num-filter label { display:flex; align-items:center; gap:6px; cursor:pointer; }
#num-filter input[type=checkbox] { width:auto; }

/* Map */
#map { flex:1; }

/* Toggle sidebar */
#toggle-btn {
  position:absolute; left:360px; top:50%; transform:translateY(-50%);
  z-index:2000; background:#e94560; color:#fff; border:none;
  width:20px; height:50px; cursor:pointer; border-radius:0 6px 6px 0;
  font-size:16px; transition: left .3s;
}
#toggle-btn.collapsed { left:0; }

/* Toast */
#toast {
  position:fixed; bottom:20px; right:20px; z-index:9999;
  background:#27ae60; color:#fff; padding:10px 18px;
  border-radius:8px; font-size:13px; display:none;
  box-shadow:0 3px 10px rgba(0,0,0,.3);
}

/* Loading */
#loading {
  position:fixed; inset:0; background:rgba(0,0,0,.6);
  display:flex; align-items:center; justify-content:center;
  z-index:9999; color:#fff; font-size:18px;
}

/* Zone panel */
#zone-panel {
  position:absolute; right:10px; top:50%; transform:translateY(-50%);
  z-index:3000; background:#1a1a2e; color:#eee; border-radius:8px;
  padding:16px; min-width:280px; max-height:60vh; overflow-y:auto;
  box-shadow:0 4px 20px rgba(0,0,0,0.5); display:none;
}
#zone-panel h3 { color:#e94560; font-size:13px; margin-bottom:10px; }
#zone-panel .zp-circuit-group { margin-bottom:10px; }
#zone-panel .zp-badge {
  display:inline-block; padding:2px 8px; border-radius:10px;
  font-size:11px; font-weight:bold; color:#fff; margin-bottom:4px;
}
#zone-panel .zp-rue { font-size:12px; padding:2px 0 2px 10px; border-left:2px solid #333; margin:2px 0; }
#zone-panel .zp-actions { display:flex; gap:8px; margin-top:12px; }
#zone-panel .zp-btn {
  flex:1; padding:7px; border:none; border-radius:5px;
  cursor:pointer; font-weight:bold; font-size:12px;
}
</style>
</head>
<body>

<div id="loading">⏳ Chargement des données…</div>

<div id="sidebar">
  <div id="sidebar-header">
    <h2>🗺 Circuits Colissimo — Vincennes</h2>
    <p id="stats">Chargement…</p>
  </div>
  <div id="search-box">
    <input id="search" type="text" placeholder="🔍  Rechercher une rue…" oninput="filterList()">
  </div>
  <div id="filters"></div>
  <div id="street-list"></div>

  <!-- Géolocaliser une adresse -->
  <div id="geo-addr-bar" style="padding:8px 12px;background:#16213e;border-top:1px solid #0f3460;flex-shrink:0;">
    <button onclick="toggleGeoAddr()"
      style="width:100%;padding:7px;background:#0f3460;color:#9b59b6;border:1px dashed #9b59b6;
             border-radius:6px;cursor:pointer;font-weight:bold;font-size:12px;">
      🔍 Géolocaliser une adresse
    </button>
    <div id="geo-addr-form" style="display:none;margin-top:8px;">
      <div style="display:flex;gap:5px;margin-bottom:6px;">
        <input id="ga-query" type="text" placeholder="ex: 15 rue daumesnil vincennes"
          style="flex:1;padding:6px 8px;border-radius:5px;border:1px solid #0f3460;
                 background:#0a0a1a;color:#fff;font-size:12px;"
          onkeydown="if(event.key==='Enter') searchGeoAddr()">
        <button onclick="searchGeoAddr()"
          style="padding:6px 10px;background:#9b59b6;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-weight:bold;font-size:12px;">🔍</button>
      </div>
      <div id="ga-results" style="display:none;margin-bottom:6px;max-height:120px;overflow-y:auto;">
      </div>
      <div id="ga-form" style="display:none;">
        <div id="ga-found" style="font-size:11px;color:#9b59b6;margin-bottom:6px;"></div>
        <select id="ga-circuit"
          style="width:100%;padding:6px;border-radius:5px;border:1px solid #0f3460;
                 background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;"></select>
        <select id="ga-district"
          style="width:100%;padding:6px;border-radius:5px;border:1px solid #0f3460;
                 background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;"></select>
        <div style="display:flex;gap:6px;">
          <button onclick="placeGeoAddr()"
            style="flex:1;padding:7px;background:#27ae60;color:#fff;border:none;
                   border-radius:5px;cursor:pointer;font-weight:bold;font-size:12px;">
            ✓ Placer le bullet
          </button>
          <button onclick="cancelGeoAddr()"
            style="flex:1;padding:7px;background:#555;color:#fff;border:none;
                   border-radius:5px;cursor:pointer;font-size:12px;">Annuler</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Nouvelle rue -->
  <div id="new-rue-bar" style="padding:8px 12px;background:#16213e;border-top:1px solid #0f3460;flex-shrink:0;">
    <button onclick="toggleNewRue()"
      style="width:100%;padding:7px;background:#0f3460;color:#27ae60;border:1px dashed #27ae60;
             border-radius:6px;cursor:pointer;font-weight:bold;font-size:12px;">
      ＋ Ajouter une rue
    </button>
    <div id="new-rue-form" style="display:none;margin-top:8px;">
      <input id="nr-nom" type="text" placeholder="Nom de la rue"
        style="width:100%;padding:6px 8px;border-radius:5px;border:1px solid #0f3460;
               background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;">
      <select id="nr-circuit"
        style="width:100%;padding:6px 8px;border-radius:5px;border:1px solid #0f3460;
               background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;">
      </select>
      <select id="nr-district"
        style="width:100%;padding:6px 8px;border-radius:5px;border:1px solid #0f3460;
               background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;">
      </select>
      <input id="nr-numeros" type="text" placeholder="Numéros (optionnel, ex: 1, 3, 5)"
        style="width:100%;padding:6px 8px;border-radius:5px;border:1px solid #0f3460;
               background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;">
      <div style="display:flex;gap:6px;">
        <button onclick="createRue()"
          style="flex:1;padding:7px;background:#27ae60;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-weight:bold;font-size:12px;">
          ✓ Ajouter
        </button>
        <button onclick="toggleNewRue()"
          style="flex:1;padding:7px;background:#555;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-size:12px;">
          Annuler
        </button>
      </div>
    </div>
  </div>

  <!-- Nouveau circuit -->
  <div id="new-circuit-bar" style="padding:8px 12px;background:#16213e;border-top:1px solid #0f3460;flex-shrink:0;">
    <button onclick="toggleNewCircuit()"
      style="width:100%;padding:7px;background:#0f3460;color:#e94560;border:1px dashed #e94560;
             border-radius:6px;cursor:pointer;font-weight:bold;font-size:12px;">
      ＋ Créer un nouveau circuit
    </button>
    <div id="new-circuit-form" style="display:none;margin-top:8px;">
      <input id="nc-name" type="text" placeholder="Nom du circuit (ex: 549, CUSTOM…)"
        style="width:100%;padding:6px 8px;border-radius:5px;border:1px solid #0f3460;
               background:#0a0a1a;color:#fff;font-size:12px;margin-bottom:6px;">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;">
        <label style="font-size:11px;color:#aaa;flex-shrink:0;">Couleur :</label>
        <input id="nc-color" type="color" value="#ff6b35"
          style="width:50px;height:30px;border:none;background:none;cursor:pointer;">
        <div id="nc-presets" style="display:flex;gap:4px;flex-wrap:wrap;"></div>
      </div>
      <div style="display:flex;gap:6px;">
        <button onclick="createCircuit()"
          style="flex:1;padding:7px;background:#27ae60;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-weight:bold;font-size:12px;">
          ✓ Créer
        </button>
        <button onclick="toggleNewCircuit()"
          style="flex:1;padding:7px;background:#555;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-size:12px;">
          Annuler
        </button>
      </div>
    </div>
  </div>

  <div style="display:flex;gap:6px;padding:0 12px 8px;">
    <button onclick="exportCSV()"
      style="flex:1;padding:7px;background:#e94560;color:#fff;border:none;
             border-radius:6px;cursor:pointer;font-weight:bold;font-size:11px;">
      ⬇ CSV
    </button>
    <button onclick="exportExtraction()"
      style="flex:1;padding:7px;background:#0f3460;color:#e94560;border:1px solid #e94560;
             border-radius:6px;cursor:pointer;font-weight:bold;font-size:11px;">
      ⬇ Extraction
    </button>
    <label style="flex:1;padding:7px;background:#27ae60;color:#fff;border:none;
             border-radius:6px;cursor:pointer;font-weight:bold;font-size:11px;
             text-align:center;">
      ⬆ Importer
      <input type="file" id="import-file" accept=".csv,.txt"
        style="display:none" onchange="importFile(this)">
    </label>
  </div>
  <div id="edit-panel">
    <h3>✏️ Modifier l'affectation</h3>
    <p id="edit-rue" style="font-weight:bold;color:#fff;font-size:13px;margin-bottom:4px;"></p>
    <label>Nouveau circuit</label>
    <select id="edit-circuit"></select>
    <label>Nouveau district</label>
    <select id="edit-district"></select>
    <label>Numéros</label>
    <input type="text" id="edit-numeros" placeholder="ex: 1, 3, 5-10">
    <div id="num-filter">
      <label>
        <input type="checkbox" id="chk-num" onchange="toggleNum()">
        Modifier uniquement le numéro :
      </label>
      <input type="text" id="edit-num" placeholder="ex: 5" disabled style="margin-top:4px;">
    </div>
    <div class="edit-actions">
      <button class="btn btn-save"   onclick="saveEdit()">💾 Sauvegarder</button>
      <button class="btn btn-cancel" onclick="closeEdit()">✕ Annuler</button>
    </div>
    <button class="btn btn-geo" style="width:100%;margin-top:8px;" onclick="openGeoSearch()">
      📍 Géocoder cette rue manuellement
    </button>
    <button onclick="deleteRue()"
      style="width:100%;margin-top:6px;padding:8px;background:#c0392b;color:#fff;
             border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:12px;">
      🗑 Supprimer cette rue
    </button>
  </div>
</div>

<button id="toggle-btn" onclick="toggleSidebar()">◀</button>
<div id="map"></div>
<div id="toast"></div>

<!-- Zone panel -->
<div id="zone-panel">
  <h3>⬜ Rues dans la zone sélectionnée</h3>
  <div id="zone-results"></div>
  <div class="zp-actions">
    <button class="zp-btn" style="background:#0f3460;color:#eee;" onclick="exportZone()">⬇ Exporter</button>
    <button class="zp-btn" style="background:#555;color:#fff;" onclick="closeZonePanel()">✕ Fermer</button>
  </div>
</div>

<script>
// ─── State ───────────────────────────────────────────────────────────────────
let allMarkers = [], mapLayers = {}, leafletMarkers = [], selected = null;
let activeCircuits = new Set(), COLORS = {}, allCircuits = [], allDistricts = [];
let activeDrag = null;   // { marker, rue, origLat, origLon }
let justDragged = false; // bloque le click post-drag
let waypointTarget = null; // { rue, circuit } pour le mode ajout waypoint
let segmentTarget  = null; // { rue, circuit, pointA: null|{lat,lng} } pour min→max
let zoneMode = false;
let zoneRect = null;
let zoneStart = null;
let zoneMarkersInside = [];

// ─── Map init ────────────────────────────────────────────────────────────────
const map = L.map('map').setView([48.848, 2.439], 14);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap', maxZoom: 19
}).addTo(map);
const markersLayer = L.layerGroup().addTo(map);

// ─── Chargement ───────────────────────────────────────────────────────────────
async function loadData() {
  const res = await fetch('/api/data');
  const d   = await res.json();
  COLORS      = d.colors;
  allCircuits = d.circuits;
  allDistricts= d.districts;
  allCircuits.forEach(c => activeCircuits.add(c));
  buildFilters();
  buildCircuitSelects();
  renderMarkers(d.markers);
  document.getElementById('loading').style.display = 'none';
}

// ─── Filtres ─────────────────────────────────────────────────────────────────
function buildFilters() {
  const div = document.getElementById('filters');
  div.innerHTML = '';
  allCircuits.forEach(c => {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:inline-flex;align-items:center;gap:2px;margin:2px;';

    const btn = document.createElement('button');
    btn.className = 'filter-btn active';
    btn.textContent = c;
    btn.style.borderColor = COLORS[c] || '#888';
    btn.style.backgroundColor = COLORS[c] || '#888';
    btn.style.color = '#000';
    btn.style.margin = '0';
    btn.onclick = () => toggleFilter(c, btn);

    // Color picker
    const colorInput = document.createElement('input');
    colorInput.type  = 'color';
    colorInput.value = COLORS[c] || '#888888';
    colorInput.title = `Changer la couleur du circuit ${c}`;
    colorInput.style.cssText = `width:18px;height:18px;border:none;padding:0;
      background:none;cursor:pointer;border-radius:3px;`;
    colorInput.addEventListener('change', (e) => {
      e.stopPropagation();
      updateCircuitColor(c, e.target.value);
    });

    const del = document.createElement('button');
    del.textContent = '×';
    del.title = `Supprimer le circuit ${c}`;
    del.style.cssText = `padding:1px 5px;border:none;background:#333;color:#aaa;
      border-radius:3px;cursor:pointer;font-size:12px;line-height:1;`;
    del.onclick = (e) => { e.stopPropagation(); deleteCircuit(c); };

    wrap.appendChild(btn);
    wrap.appendChild(colorInput);
    wrap.appendChild(del);
    div.appendChild(wrap);
  });
}
function toggleFilter(c, btn) {
  if (activeCircuits.has(c)) {
    activeCircuits.delete(c);
    btn.classList.remove('active');
    btn.style.backgroundColor = 'transparent';
    btn.style.color = '#fff';
  } else {
    activeCircuits.add(c);
    btn.classList.add('active');
    btn.style.backgroundColor = COLORS[c] || '#888';
    btn.style.color = '#000';
  }
  filterList();
  redrawMap();
}

// ─── Sélécteurs edit ─────────────────────────────────────────────────────────
function buildCircuitSelects() {
  const sel = document.getElementById('edit-circuit');
  sel.innerHTML = allCircuits.map(c =>
    `<option value="${c}" style="background:${COLORS[c]||'#444'}">${c}</option>`
  ).join('');
  const sel2 = document.getElementById('edit-district');
  sel2.innerHTML = allDistricts.map(d => `<option value="${d}">${d}</option>`).join('');
}

// ─── Rendu marqueurs ─────────────────────────────────────────────────────────
function renderMarkers(markers) {
  allMarkers = markers;
  redrawMap();
  buildList(markers);
}

function buildPopupContent(m) {
  const color = COLORS[m.circuit] || '#888';
  const plage = m.nb_min !== '' ? `${m.nb_min} → ${m.nb_max}` : 'N/A';
  const rueEnc = encodeURIComponent(m.rue);
  const btns = allCircuits.map(c => {
    const cc      = COLORS[c] || '#888';
    const isCur   = c === m.circuit;
    const outline = isCur ? `box-shadow:0 0 0 3px #fff,0 0 0 5px ${cc};` : '';
    return `<button
      onclick="assignCircuit('${rueEnc}','${m.circuit}','${c}')"
      style="padding:5px 10px;margin:3px 2px;background:${cc};color:#fff;border:none;
             border-radius:5px;cursor:pointer;font-size:12px;font-weight:bold;
             opacity:${isCur?'1':'0.75'};${outline}">
      ${isCur ? '✓ ' : ''}${c}
    </button>`;
  }).join('');
  const rueCircEnc = encodeURIComponent(m.circuit);
  const popupId    = 'popup-' + m.id.replace(/[^a-z0-9]/gi, '_');
  const wpLabel  = m.is_waypoint ? `<span style="font-size:10px;color:#aaa;margin-left:6px;">(point)</span>` : '';
  const distStr  = m.districts && m.districts.length ? m.districts.join(', ') : '';
  return `
    <div style="min-width:240px;font-family:Arial,sans-serif">
      <div style="border-left:4px solid ${color};padding-left:8px;margin-bottom:10px">
        <span style="color:${color};font-weight:bold;font-size:13px">${m.circuit}</span>
        ${wpLabel}&nbsp;<span style="font-weight:bold">${m.rue}</span><br>
        <span style="color:#555;font-size:11px">N° ${plage}</span>
        ${distStr ? `<span style="color:#888;font-size:11px;margin-left:8px;">· ${distStr}</span>` : ''}
      </div>
      <div style="font-size:11px;color:#888;margin-bottom:4px;font-weight:bold">
        ✏️ Modifier les numéros :
      </div>
      <div style="display:flex;gap:5px;margin-bottom:10px;">
        <input id="${popupId}" type="text" value="${m.numeros}"
          style="flex:1;padding:5px 7px;border-radius:5px;border:1px solid #ccc;font-size:12px;">
        <button onclick="saveNumsFromPopup('${rueEnc}','${rueCircEnc}','${popupId}')"
          style="padding:5px 10px;background:#27ae60;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-size:12px;font-weight:bold;">
          ✓
        </button>
      </div>
      <div style="font-size:11px;color:#888;margin-bottom:5px;font-weight:bold">
        ▶ Changer de circuit :
      </div>
      <div style="display:flex;flex-wrap:wrap;">${btns}</div>
      <div style="display:flex;gap:5px;margin-top:8px;">
        <button onclick="addWaypointMode('${rueEnc}','${rueCircEnc}')"
          style="flex:1;padding:6px;background:#0f3460;color:#3498db;
                 border:1px solid #3498db;border-radius:5px;cursor:pointer;font-weight:bold;font-size:11px;">
          ➕ Point
        </button>
        <button onclick="startSegmentMode('${rueEnc}','${rueCircEnc}')"
          style="flex:1;padding:6px;background:#0f3460;color:#f39c12;
                 border:1px solid #f39c12;border-radius:5px;cursor:pointer;font-weight:bold;font-size:11px;">
          📏 Min→Max
        </button>
      </div>
      <button onclick="deleteRueFromPopup('${rueEnc}','${rueCircEnc}')"
        style="width:100%;margin-top:6px;padding:6px;background:#c0392b;color:#fff;
               border:none;border-radius:5px;cursor:pointer;font-weight:bold;font-size:12px;">
        🗑 Supprimer cette rue
      </button>
    </div>`;
}

function makeIcon(color) {
  const cur = dragMode ? 'grab' : 'pointer';
  return L.divIcon({
    className: '',
    html: `<div style="width:18px;height:18px;border-radius:50%;
                background:${color};border:2px solid #fff;
                box-shadow:0 1px 4px rgba(0,0,0,.4);cursor:${cur};"></div>`,
    iconSize:   [18, 18],
    iconAnchor: [9, 9],
    popupAnchor:[0, -10],
  });
}

function makeWaypointIcon(color) {
  const cur = dragMode ? 'grab' : 'pointer';
  return L.divIcon({
    className: '',
    html: `<div style="width:12px;height:12px;border-radius:50%;
                background:${color};opacity:0.85;border:2px dashed #fff;
                box-shadow:0 1px 4px rgba(0,0,0,.4);cursor:${cur};"></div>`,
    iconSize:   [12, 12],
    iconAnchor: [6, 6],
    popupAnchor:[0, -8],
  });
}

function redrawMap() {
  markersLayer.clearLayers();
  leafletMarkers = [];
  allMarkers.filter(m => activeCircuits.has(m.circuit) && m.lat).forEach(m => {
    const color  = COLORS[m.circuit] || '#888';
    const icon   = m.is_waypoint ? makeWaypointIcon(color) : makeIcon(color);
    const marker = L.marker([m.lat, m.lon], {
      icon: icon,
      draggable: dragMode,
    });

    marker.on('dragstart', (e) => {
      const ll = e.target.getLatLng();
      activeDrag = { marker: e.target, rue: m.rue, origLat: ll.lat, origLon: ll.lng };
      map.closePopup();
    });
    marker.on('dragend', async (e) => {
      activeDrag = null;
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 300);
      const { lat, lng } = e.target.getLatLng();
      await fetch('/api/geocode', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({rue: m.rue, circuit: m.circuit, lat, lon: lng})
      });
      const idx = allMarkers.findIndex(x => x.id === m.id);
      if (idx !== -1) { allMarkers[idx].lat = lat; allMarkers[idx].lon = lng; }
      showToast(`📍 "${m.rue}" repositionné`);
    });
    marker.bindPopup(() => buildPopupContent(m), {maxWidth: 320, autoPan: true});

    marker._markerId = m.id;
    markersLayer.addLayer(marker);
    leafletMarkers.push(marker);
  });
}

// ─── Changement de circuit en 1 clic ─────────────────────────────────────────
async function assignCircuit(rueEnc, oldC, newC) {
  if (oldC === newC) { map.closePopup(); return; }
  const rue = decodeURIComponent(rueEnc);
  const res = await fetch('/api/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rue, old_circuit: oldC, new_circuit: newC, new_district: ''})
  });
  const d = await res.json();
  map.closePopup();
  if (d.error) { showToast('❌ ' + d.error); return; }
  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  showToast(`✅ "${rue}" → Circuit ${newC}  (${d.changed} ligne(s) modifiée(s))`);
}

// ─── Liste sidebar ────────────────────────────────────────────────────────────
function buildList(markers) {
  const q = document.getElementById('search').value.toLowerCase();
  const list = document.getElementById('street-list');
  list.innerHTML = '';
  const filtered = markers.filter(m =>
    activeCircuits.has(m.circuit) &&
    !m.is_waypoint &&
    (m.rue.toLowerCase().includes(q) || m.circuit.includes(q))
  );
  filtered.sort((a, b) => a.circuit.localeCompare(b.circuit) || a.rue.localeCompare(b.rue));
  filtered.forEach(m => {
    const color = COLORS[m.circuit] || '#888';
    const plage = m.nb_min !== '' ? `n°${m.nb_min}→${m.nb_max}` : '';
    const div = document.createElement('div');
    div.className = 'street-item' + (!m.geocoded ? ' no-geo' : '');
    div.style.borderLeftColor = color;
    div.dataset.id = m.id;
    div.innerHTML = `
      <div class="s-name">
        <span class="s-badge" style="background:${color}">${m.circuit}</span>
        ${m.rue} ${!m.geocoded ? '📍?' : ''}
      </div>
      <div class="s-info">${plage} &nbsp;|&nbsp; ${m.districts.join(', ')}</div>`;
    div.onclick = () => selectStreet(m.id);
    list.appendChild(div);
  });
  document.getElementById('stats').textContent =
    `${filtered.length} rues affichées · ${markers.filter(m=>!m.geocoded).length} sans coordonnées`;
}

function filterList() { buildList(allMarkers); }

// ─── Sélection d'une rue ──────────────────────────────────────────────────────
function selectStreet(id, fromMap = false) {
  selected = allMarkers.find(m => m.id === id);
  if (!selected) return;

  // highlight liste
  document.querySelectorAll('.street-item').forEach(el => el.classList.remove('selected'));
  const el = document.querySelector(`.street-item[data-id="${id}"]`);
  if (el) { el.classList.add('selected'); el.scrollIntoView({block:'nearest'}); }

  // centrer carte
  if (selected.lat) map.setView([selected.lat, selected.lon], 16);

  // panneau édition
  document.getElementById('edit-panel').style.display = 'block';
  document.getElementById('edit-rue').textContent = selected.rue;
  document.getElementById('edit-circuit').value  = selected.circuit;
  document.getElementById('edit-district').value = selected.districts[0] || 'd 1';
  document.getElementById('edit-numeros').value  = selected.numeros || '';
  document.getElementById('edit-num').value = '';
  document.getElementById('chk-num').checked = false;
  document.getElementById('edit-num').disabled = true;
}

function closeEdit() {
  document.getElementById('edit-panel').style.display = 'none';
  selected = null;
}

function toggleNum() {
  document.getElementById('edit-num').disabled = !document.getElementById('chk-num').checked;
}

// ─── Sauvegarde ───────────────────────────────────────────────────────────────
async function saveEdit() {
  if (!selected) return;
  const newC    = document.getElementById('edit-circuit').value;
  const newDist = document.getElementById('edit-district').value;
  const onlyNum = document.getElementById('chk-num').checked
                  ? document.getElementById('edit-num').value.trim() : '';

  const newNums = document.getElementById('edit-numeros').value.trim();
  const res = await fetch('/api/update', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      rue: selected.rue, old_circuit: selected.circuit,
      new_circuit: newC, new_district: newDist, only_numero: onlyNum,
      new_numeros: newNums
    })
  });
  const d = await res.json();
  showToast(`✅ ${d.changed} ligne(s) modifiée(s) — ${selected.rue} → ${newC}`);
  renderMarkers(d.markers);
  closeEdit();
}

// ─── Géocodage manuel ─────────────────────────────────────────────────────────
function openGeoSearch() {
  if (!selected) return;
  const query = prompt(`Rechercher les coordonnées de :\n"${selected.rue}"\n\nEntrer une adresse précise :`,
                       `${selected.rue}, Vincennes 94300`);
  if (!query) return;
  fetch(`/api/geocode_search?q=${encodeURIComponent(query)}`)
    .then(r => r.json())
    .then(results => {
      if (!results || results.length === 0) { alert('Adresse non trouvée.'); return; }
      const choices = results.map((r,i) => `${i+1}. ${r.display}`).join('\n');
      const idx = parseInt(prompt(`Résultats :\n${choices}\n\nChoisir (1-${results.length}) :`, '1')) - 1;
      if (isNaN(idx) || idx < 0 || idx >= results.length) return;
      const {lat, lon} = results[idx];
      fetch('/api/geocode', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rue: selected.rue, lat, lon})
      }).then(() => { showToast(`📍 ${selected.rue} géocodé`); loadData(); });
    });
}

// ─── Export / Import ─────────────────────────────────────────────────────────
function exportCSV()        { window.open('/api/export_csv'); }
function exportExtraction() { window.open('/api/export_extraction'); }

async function importFile(input) {
  if (!input.files.length) return;
  const formData = new FormData();
  formData.append('file', input.files[0]);
  input.value = '';
  showToast('⏳ Import en cours…');
  const res = await fetch('/api/import', { method: 'POST', body: formData });
  const d   = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }
  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  showToast(`✅ ${d.added} ligne(s) importée(s)`);
}

// ─── Nouveau circuit ──────────────────────────────────────────────────────────
const PRESET_COLORS = ['#e74c3c','#3498db','#27ae60','#e67e22','#9b59b6',
                       '#1abc9c','#e91e63','#f39c12','#2c3e50','#16a085',
                       '#8e44ad','#d35400','#c0392b','#7f8c8d','#f1c40f'];

function initPresets() {
  const div = document.getElementById('nc-presets');
  PRESET_COLORS.forEach(c => {
    const b = document.createElement('div');
    b.style.cssText = `width:18px;height:18px;background:${c};border-radius:3px;cursor:pointer;border:2px solid transparent;`;
    b.title = c;
    b.onclick = () => {
      document.getElementById('nc-color').value = c;
      div.querySelectorAll('div').forEach(x => x.style.borderColor = 'transparent');
      b.style.borderColor = '#fff';
    };
    div.appendChild(b);
  });
}

function toggleNewCircuit() {
  const f = document.getElementById('new-circuit-form');
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
  if (f.style.display === 'block') document.getElementById('nc-name').focus();
}

async function createCircuit() {
  const name  = document.getElementById('nc-name').value.trim();
  const color = document.getElementById('nc-color').value;
  if (!name) { showToast('⚠️ Entrer un nom de circuit'); return; }

  const res = await fetch('/api/add_circuit', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, color})
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }

  COLORS = d.colors;
  allCircuits = d.circuits;
  activeCircuits.add(name);
  buildFilters();
  buildCircuitSelects();
  document.getElementById('nc-name').value = '';
  toggleNewCircuit();
  showToast(`✅ Circuit "${name}" créé`);
}

async function updateCircuitColor(c, color) {
  const res = await fetch('/api/update_color', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: c, color})
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }
  COLORS = d.colors;
  buildFilters();
  buildCircuitSelects();
  redrawMap();
  buildList(allMarkers);
  showToast(`🎨 Circuit ${c} — couleur mise à jour`);
}

async function deleteCircuit(c) {
  if (!confirm(`Supprimer le circuit "${c}" ?\n(Les rues affectées resteront dans les données mais sans ce circuit.)`)) return;
  const res = await fetch('/api/delete_circuit', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: c})
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }
  COLORS = d.colors;
  allCircuits = d.circuits;
  activeCircuits.delete(c);
  buildFilters();
  buildCircuitSelects();
  showToast(`🗑 Circuit "${c}" supprimé`);
}

// ─── Suppression d'une rue ───────────────────────────────────────────────────
async function deleteRue() {
  if (!selected) return;
  const rue     = selected.rue;
  const circuit = selected.circuit;
  if (!confirm(`Supprimer "${rue}" du circuit ${circuit} ?\nCette action est irréversible.`)) return;
  const res = await fetch('/api/delete_rue', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rue, circuit})
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }
  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  closeEdit();
  showToast(`🗑 "${rue}" supprimée — marquée [SUPPRIMÉ] dans l'extraction`);
}

async function saveNumsFromPopup(rueEnc, circuitEnc, inputId) {
  const rue     = decodeURIComponent(rueEnc);
  const circuit = decodeURIComponent(circuitEnc);
  const newNums = document.getElementById(inputId).value.trim();
  const res = await fetch('/api/update', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rue, old_circuit: circuit, new_circuit: circuit,
                          new_district: '', new_numeros: newNums})
  });
  const d = await res.json();
  map.closePopup();
  if (d.error) { showToast('❌ ' + d.error); return; }
  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  showToast(`✅ Numéros de "${rue}" mis à jour`);
}

async function deleteRueFromPopup(rueEnc, circuitEnc) {
  const rue     = decodeURIComponent(rueEnc);
  const circuit = decodeURIComponent(circuitEnc);
  if (!confirm(`Supprimer "${rue}" du circuit ${circuit} ?\nCette action est irréversible.`)) return;
  map.closePopup();
  const res = await fetch('/api/delete_rue', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rue, circuit})
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }
  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  showToast(`🗑 "${rue}" supprimée — marquée [SUPPRIMÉ] dans l'extraction`);
}

// ─── Nouvelle rue ────────────────────────────────────────────────────────────
function toggleNewRue() {
  const f = document.getElementById('new-rue-form');
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
  if (f.style.display === 'block') {
    // Remplir les selects avec circuits et districts actuels
    document.getElementById('nr-circuit').innerHTML =
      allCircuits.map(c => `<option value="${c}">${c}</option>`).join('');
    document.getElementById('nr-district').innerHTML =
      allDistricts.map(d => `<option value="${d}">${d}</option>`).join('');
    document.getElementById('nr-nom').focus();
  }
}

async function createRue() {
  const rue      = document.getElementById('nr-nom').value.trim();
  const circuit  = document.getElementById('nr-circuit').value;
  const district = document.getElementById('nr-district').value;
  const numeros  = document.getElementById('nr-numeros').value.trim();
  if (!rue) { showToast('⚠️ Entrer un nom de rue'); return; }

  const res = await fetch('/api/add_rue', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rue, circuit, district, numeros})
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }

  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  document.getElementById('nr-nom').value = '';
  document.getElementById('nr-numeros').value = '';
  toggleNewRue();
  // Centrer et sélectionner la nouvelle rue sur la carte
  const newMarker = allMarkers.find(m => m.rue.toLowerCase() === rue.toLowerCase() && m.circuit === circuit);
  if (newMarker && newMarker.lat) {
    map.setView([newMarker.lat, newMarker.lon], 17);
    const lm = leafletMarkers.find(l => l._markerId === newMarker.id);
    if (lm) lm.openPopup();
  }
  const geoMsg = d.geocoded ? ' 📍 géocodée automatiquement' : ' (📍? pas de coordonnées)';
  showToast(`✅ Rue "${rue}" ajoutée au circuit ${circuit}${geoMsg}`);
}

// ─── Toggle sidebar ───────────────────────────────────────────────────────────
function toggleSidebar() {
  const sb  = document.getElementById('sidebar');
  const btn = document.getElementById('toggle-btn');
  sb.classList.toggle('collapsed');
  btn.classList.toggle('collapsed');
  btn.textContent = sb.classList.contains('collapsed') ? '▶' : '◀';
  setTimeout(() => map.invalidateSize(), 310);
}

// ─── Toast ────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

// ─── Boutons flottants carte ──────────────────────────────────────────────────
const mapBtns = document.createElement('div');
mapBtns.style.cssText = `position:absolute;top:10px;right:10px;z-index:2000;
  display:flex;flex-direction:column;gap:6px;`;
document.getElementById('map').appendChild(mapBtns);

// Bouton mode déplacement
let dragMode = false;
const dragBtn = document.createElement('button');
dragBtn.textContent = '✋ Déplacer';
dragBtn.title = 'Activer/désactiver le déplacement des bullets';
dragBtn.style.cssText = `padding:8px 14px;background:#0f3460;color:#f39c12;
  border:1px solid #f39c12;border-radius:6px;cursor:pointer;font-weight:bold;font-size:13px;`;
dragBtn.onclick = () => {
  dragMode = !dragMode;
  dragBtn.style.background = dragMode ? '#f39c12' : '#0f3460';
  dragBtn.style.color      = dragMode ? '#000'    : '#f39c12';
  map.getContainer().style.cursor = dragMode ? 'grab' : '';
  if (!dragMode) { activeDrag = null; }
  // Redessiner pour mettre à jour le curseur des icônes
  redrawMap();
  showToast(dragMode ? '✋ Mode déplacement activé — Échap pour annuler' : '✋ Mode déplacement désactivé');
};
mapBtns.appendChild(dragBtn);

// Bouton mode placement
let createMode = false;
const createBtn = document.createElement('button');
createBtn.id = 'create-mode-btn';
createBtn.textContent = '📍 Placer une rue';
createBtn.style.cssText = `padding:8px 14px;background:#0f3460;color:#e94560;
  border:1px solid #e94560;border-radius:6px;cursor:pointer;font-weight:bold;font-size:13px;`;
createBtn.onclick = () => {
  createMode = !createMode;
  createBtn.style.background = createMode ? '#e94560' : '#0f3460';
  createBtn.style.color      = createMode ? '#fff'    : '#e94560';
  map.getContainer().style.cursor = createMode ? 'crosshair' : '';
  if (!createMode) map.closePopup();
};
mapBtns.appendChild(createBtn);

// Bouton mode zone
const zoneBtn = document.createElement('button');
zoneBtn.textContent = '⬜ Encadrer zone';
zoneBtn.style.cssText = `padding:8px 14px;background:#0f3460;color:#3498db;
  border:1px solid #3498db;border-radius:6px;cursor:pointer;font-weight:bold;font-size:13px;`;
zoneBtn.onclick = () => {
  zoneMode = !zoneMode;
  zoneBtn.style.background = zoneMode ? '#3498db' : '#0f3460';
  zoneBtn.style.color      = zoneMode ? '#000'    : '#3498db';
  map.getContainer().style.cursor = zoneMode ? 'crosshair' : '';
  // Rendre les markers transparents aux événements souris pendant le dessin
  map.getPanes().markerPane.style.pointerEvents = zoneMode ? 'none' : '';
  map.getPanes().shadowPane.style.pointerEvents = zoneMode ? 'none' : '';
  map.dragging[zoneMode ? 'disable' : 'enable']();
  if (!zoneMode && zoneRect) { map.removeLayer(zoneRect); zoneRect = null; }
  showToast(zoneMode ? '⬜ Cliquez-glissez pour délimiter une zone' : '⬜ Mode zone désactivé');
};
mapBtns.appendChild(zoneBtn);

// Zone drawing
map.getContainer().addEventListener('mousedown', function(e) {
  if (!zoneMode) return;
  // Only left click, and ignore if on a marker/popup
  if (e.button !== 0) return;
  if (e.target !== map.getContainer() && !e.target.classList.contains('leaflet-tile') &&
      !e.target.closest('.leaflet-tile-pane') && !e.target.closest('.leaflet-overlay-pane')) return;
  const startPt = map.mouseEventToLatLng(e);
  zoneStart = startPt;
  if (zoneRect) { map.removeLayer(zoneRect); zoneRect = null; }

  function onMouseMove(ev) {
    if (!zoneStart) return;
    const cur = map.mouseEventToLatLng(ev);
    const bounds = L.latLngBounds(zoneStart, cur);
    if (zoneRect) map.removeLayer(zoneRect);
    zoneRect = L.rectangle(bounds, {color:'#3498db',weight:2,fillOpacity:0.1}).addTo(map);
  }
  function onMouseUp(ev) {
    map.getContainer().removeEventListener('mousemove', onMouseMove);
    map.getContainer().removeEventListener('mouseup', onMouseUp);
    if (!zoneStart || !zoneRect) { zoneStart = null; return; }
    const cur = map.mouseEventToLatLng(ev);
    const bounds = L.latLngBounds(zoneStart, cur);
    zoneStart = null;
    // Find markers inside
    zoneMarkersInside = allMarkers.filter(m =>
      !m.is_waypoint && m.lat && bounds.contains([m.lat, m.lon])
    );
    showZonePanel(zoneMarkersInside);
    // Exit zone mode
    zoneMode = false;
    zoneBtn.style.background = '#0f3460';
    zoneBtn.style.color      = '#3498db';
    map.getContainer().style.cursor = '';
    map.getPanes().markerPane.style.pointerEvents = '';
    map.getPanes().shadowPane.style.pointerEvents = '';
    map.dragging.enable();
  }
  map.getContainer().addEventListener('mousemove', onMouseMove);
  map.getContainer().addEventListener('mouseup',   onMouseUp);
});

function showZonePanel(markers) {
  const panel = document.getElementById('zone-panel');
  const res   = document.getElementById('zone-results');
  if (!markers.length) {
    showToast('⬜ Aucune rue dans cette zone');
    if (zoneRect) { map.removeLayer(zoneRect); zoneRect = null; }
    return;
  }
  // Group by circuit
  const groups = {};
  markers.forEach(m => {
    if (!groups[m.circuit]) groups[m.circuit] = [];
    groups[m.circuit].push(m);
  });
  res.innerHTML = Object.entries(groups).sort().map(([c, ms]) => {
    const color = COLORS[c] || '#888';
    const rueList = ms.sort((a,b) => a.rue.localeCompare(b.rue)).map(m => {
      const nums = m.numeros ? `<span style="color:#aaa;font-size:10px;"> — ${m.numeros}</span>` : '';
      return `<div class="zp-rue">${m.rue}${nums}</div>`;
    }).join('');
    return `<div class="zp-circuit-group">
      <span class="zp-badge" style="background:${color}">${c}</span>
      ${rueList}
    </div>`;
  }).join('');
  panel.style.display = 'block';
}

function closeZonePanel() {
  document.getElementById('zone-panel').style.display = 'none';
  if (zoneRect) { map.removeLayer(zoneRect); zoneRect = null; }
  zoneMarkersInside = [];
}

function exportZone() {
  if (!zoneMarkersInside.length) return;
  const groups = {};
  zoneMarkersInside.forEach(m => {
    if (!groups[m.circuit]) groups[m.circuit] = [];
    groups[m.circuit].push(m);
  });
  let txt = 'Rues dans la zone sélectionnée\n\n';
  Object.entries(groups).sort().forEach(([c, ms]) => {
    txt += `Circuit ${c}:\n`;
    ms.sort((a,b) => a.rue.localeCompare(b.rue)).forEach(m => {
      const nums = m.numeros ? ` (${m.numeros})` : '';
      txt += `  - ${m.rue}${nums}\n`;
    });
    txt += '\n';
  });
  const blob = new Blob([txt], {type:'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'zone_export.txt';
  a.click();
}

// ─── Segment min→max ─────────────────────────────────────────────────────────
function startSegmentMode(rueEnc, circuitEnc) {
  map.closePopup();
  segmentTarget = { rue: decodeURIComponent(rueEnc), circuit: decodeURIComponent(circuitEnc), pointA: null };
  map.getContainer().style.cursor = 'crosshair';
  showToast(`📏 "${segmentTarget.rue}" — Cliquez le point MIN (début de rue)`);
}

async function handleSegmentClick(lat, lng) {
  if (!segmentTarget) return;
  // Reverse geocode pour récupérer le numéro
  let numero = '';
  try {
    const rg = await fetch(`/api/reverse_geocode?lat=${lat}&lon=${lng}`);
    const d  = await rg.json();
    numero = d.numero || '';
  } catch(e) {}

  if (!segmentTarget.pointA) {
    // Premier clic → point MIN
    segmentTarget.pointA = { lat, lng, numero };
    // Sauvegarder comme waypoint "|start"
    const baseId = `${segmentTarget.circuit}|${segmentTarget.rue}`;
    await fetch('/api/geocode', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({rue: segmentTarget.rue, circuit: segmentTarget.circuit + '_start',
                            lat, lon: lng})
    });
    showToast(`📏 Min marqué (n°${numero || '?'}) — Cliquez maintenant le point MAX`);
  } else {
    // Deuxième clic → point MAX
    const { pointA, rue, circuit } = segmentTarget;
    await fetch('/api/geocode', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({rue, circuit: circuit + '_end', lat, lon: lng})
    });

    // Calculer et mettre à jour la plage de numéros
    const nums = [pointA.numero, numero].filter(n => n && /\d/.test(n));
    if (nums.length === 2) {
      const n1 = parseInt(pointA.numero), n2 = parseInt(numero);
      const [nMin, nMax] = [Math.min(n1,n2), Math.max(n1,n2)];
      // Mettre à jour les numéros via API
      await fetch('/api/update', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({rue, old_circuit: circuit, new_circuit: circuit,
                              new_district: '', new_numeros: `${nMin} → ${nMax}`})
      });
    }
    // Ajouter waypoints min+max visuels
    await fetch('/api/add_waypoint', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({rue, circuit, lat: pointA.lat, lon: pointA.lng})
    });
    const res = await fetch('/api/add_waypoint', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({rue, circuit, lat, lon: lng})
    });
    const d = await res.json();
    allMarkers = d.markers;
    redrawMap(); buildList(allMarkers);

    segmentTarget = null;
    map.getContainer().style.cursor = '';
    showToast(`✅ Segment défini : n°${nums[0] || '?'} → n°${numero || '?'}`);
  }
}

// ─── Waypoint functions ───────────────────────────────────────────────────────
function addWaypointMode(rueEnc, circuitEnc) {
  map.closePopup();
  waypointTarget = { rue: decodeURIComponent(rueEnc), circuit: decodeURIComponent(circuitEnc) };
  map.getContainer().style.cursor = 'crosshair';
  showToast('📍 Cliquez sur la carte pour ajouter un point');
}

async function addWaypointAt(lat, lng) {
  if (!waypointTarget) return;
  const { rue, circuit } = waypointTarget;
  waypointTarget = null;
  map.getContainer().style.cursor = createMode ? 'crosshair' : '';
  const res = await fetch('/api/add_waypoint', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ rue, circuit, lat, lon: lng })
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }
  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);
  showToast(`✅ Waypoint ajouté pour "${rue}"`);
}

// Escape : annuler le drag en cours
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && activeDrag) {
    activeDrag.marker.setLatLng([activeDrag.origLat, activeDrag.origLon]);
    activeDrag = null;
    justDragged = true;
    setTimeout(() => { justDragged = false; }, 300);
    showToast('↩ Déplacement annulé');
  }
  if (e.key === 'Escape' && createMode) {
    createMode = false;
    createBtn.style.background = '#0f3460';
    createBtn.style.color      = '#e94560';
    map.getContainer().style.cursor = '';
    map.closePopup();
  }
  if (e.key === 'Escape' && segmentTarget) {
    segmentTarget = null;
    map.getContainer().style.cursor = '';
    showToast('↩ Segment annulé');
  }
  if (e.key === 'Escape' && waypointTarget) {
    waypointTarget = null;
    map.getContainer().style.cursor = '';
    showToast('↩ Ajout waypoint annulé');
  }
  if (e.key === 'Escape' && zoneMode) {
    zoneMode = false;
    zoneBtn.style.background = '#0f3460';
    zoneBtn.style.color      = '#3498db';
    map.getContainer().style.cursor = '';
    map.getPanes().markerPane.style.pointerEvents = '';
    map.getPanes().shadowPane.style.pointerEvents = '';
    map.dragging.enable();
    if (zoneRect) { map.removeLayer(zoneRect); zoneRect = null; }
  }
});

map.on('click', async function(e) {
  if (justDragged) return;
  const { lat, lng } = e.latlng;

  // Priorité 1 : mode segment min→max
  if (segmentTarget) {
    await handleSegmentClick(lat, lng);
    return;
  }

  // Priorité 2 : mode ajout waypoint
  if (waypointTarget) {
    await addWaypointAt(lat, lng);
    return;
  }

  // Priorité 3 : mode création rue
  if (!createMode) return;

  const circuitOpts = allCircuits.map(c =>
    `<option value="${c}" style="background:${COLORS[c]||'#444'}">${c}</option>`).join('');
  const districtOpts = allDistricts.map(d => `<option value="${d}">${d}</option>`).join('');

  // Afficher le popup d'abord, puis pré-remplir via reverse geocode
  const popup = L.popup({ maxWidth: 280, closeButton: true })
    .setLatLng(e.latlng)
    .setContent(`
      <div style="font-family:Arial,sans-serif;padding:4px;">
        <div style="font-weight:bold;color:#e94560;margin-bottom:8px;font-size:13px;">
          📍 Nouvelle rue ici
        </div>
        <input id="cp-rue" type="text" placeholder="Nom de la rue…"
          style="width:100%;padding:5px 7px;border-radius:5px;border:1px solid #ccc;
                 font-size:12px;margin-bottom:2px;box-sizing:border-box;">
        <div id="cp-rue-hint" style="font-size:10px;color:#888;margin-bottom:6px;display:none;">
          (détecté automatiquement)
        </div>
        <select id="cp-circuit" style="width:100%;padding:5px;border-radius:5px;
          border:1px solid #ccc;font-size:12px;margin-bottom:6px;">${circuitOpts}</select>
        <select id="cp-district" style="width:100%;padding:5px;border-radius:5px;
          border:1px solid #ccc;font-size:12px;margin-bottom:6px;">${districtOpts}</select>
        <input id="cp-nums" type="text" placeholder="Numéros (optionnel)"
          style="width:100%;padding:5px 7px;border-radius:5px;border:1px solid #ccc;
                 font-size:12px;margin-bottom:8px;box-sizing:border-box;">
        <button onclick="createRueAt(${lat},${lng})"
          style="width:100%;padding:7px;background:#27ae60;color:#fff;border:none;
                 border-radius:5px;cursor:pointer;font-weight:bold;font-size:12px;">
          ✓ Créer le bullet
        </button>
      </div>`)
    .openOn(map);

  setTimeout(() => {
    const inp = document.getElementById('cp-rue');
    if (inp) inp.focus();
  }, 100);

  // Appel reverse geocode en arrière-plan
  try {
    const rgRes = await fetch(`/api/reverse_geocode?lat=${lat}&lon=${lng}`);
    const rg    = await rgRes.json();
    const rueEl  = document.getElementById('cp-rue');
    const numsEl = document.getElementById('cp-nums');
    const hint   = document.getElementById('cp-rue-hint');
    if (rueEl && rg.rue) {
      rueEl.value = rg.rue;
      if (hint) hint.style.display = 'block';
    }
    if (numsEl && rg.numero && !numsEl.value) {
      numsEl.value = rg.numero;
    }
  } catch (err) { /* silencieux */ }
});

async function createRueAt(lat, lng) {
  const rue     = document.getElementById('cp-rue').value.trim();
  const circuit = document.getElementById('cp-circuit').value;
  const district= document.getElementById('cp-district').value;
  const numeros = document.getElementById('cp-nums').value.trim();
  if (!rue) { showToast('⚠️ Entrer un nom de rue'); return; }

  // Sauvegarder les coordonnées dans le geocache d'abord
  await fetch('/api/geocode', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rue, circuit, lat, lon: lng})
  });

  const res = await fetch('/api/add_rue', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({rue, circuit, district, numeros})
  });
  const d = await res.json();
  map.closePopup();
  if (d.error) { showToast('❌ ' + d.error); return; }

  allMarkers = d.markers;
  redrawMap();
  buildList(allMarkers);

  // Désactiver le mode création
  createMode = false;
  createBtn.style.background = '#0f3460';
  createBtn.style.color      = '#e94560';
  map.getContainer().style.cursor = '';

  // Centrer sur le nouveau bullet
  map.setView([lat, lng], 17);
  const newMarker = leafletMarkers.find(lm => {
    const m = allMarkers.find(m => m.id === lm._markerId);
    return m && m.rue.toLowerCase() === rue.toLowerCase();
  });
  if (newMarker) newMarker.openPopup();
  showToast(`✅ "${rue}" placé sur la carte`);
}

// ─── Géolocaliser une adresse ─────────────────────────────────────────────────
let geoAddrSelected = null; // { rue, numero, lat, lon }

function toggleGeoAddr() {
  const f = document.getElementById('geo-addr-form');
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
  if (f.style.display === 'block') {
    document.getElementById('ga-circuit').innerHTML =
      allCircuits.map(c => `<option value="${c}">${c}</option>`).join('');
    document.getElementById('ga-district').innerHTML =
      allDistricts.map(d => `<option value="${d}">${d}</option>`).join('');
    document.getElementById('ga-results').style.display = 'none';
    document.getElementById('ga-form').style.display = 'none';
    document.getElementById('ga-query').focus();
  }
}

async function searchGeoAddr() {
  const q = document.getElementById('ga-query').value.trim();
  if (!q) return;
  const res = await fetch(`/api/geocode_search?q=${encodeURIComponent(q)}`);
  const results = await res.json();
  const div = document.getElementById('ga-results');
  if (!results.length || results.error) {
    div.innerHTML = `<div style="color:#e94560;font-size:11px;padding:4px;">Adresse non trouvée</div>`;
    div.style.display = 'block';
    return;
  }
  div.innerHTML = results.map((r, i) => `
    <div onclick="selectGeoAddr(${i})" data-lat="${r.lat}" data-lon="${r.lon}"
         data-display="${r.display.replace(/"/g,'&quot;')}"
         style="padding:5px 8px;font-size:11px;cursor:pointer;border-bottom:1px solid #0f3460;
                color:#eee;background:#0a0a1a;border-radius:3px;margin-bottom:2px;"
         onmouseover="this.style.background='#1a1a2e'" onmouseout="this.style.background='#0a0a1a'">
      📍 ${r.display}
    </div>`).join('');
  div.style.display = 'block';
}

function selectGeoAddr(idx) {
  const items = document.getElementById('ga-results').querySelectorAll('[data-lat]');
  const el = items[idx];
  const lat  = parseFloat(el.dataset.lat);
  const lon  = parseFloat(el.dataset.lon);
  const disp = el.dataset.display;

  // Extraire rue et numéro depuis l'affichage
  const parts = disp.split(',');
  const first = parts[0].trim();
  const numMatch = first.match(/^(\d+[A-Za-z]*)\s+(.+)/);
  const rue    = numMatch ? numMatch[2].trim() : first;
  const numero = numMatch ? numMatch[1] : '';

  geoAddrSelected = { rue, numero, lat, lon, display: disp };

  document.getElementById('ga-results').style.display = 'none';
  document.getElementById('ga-found').textContent = `📍 ${disp}`;
  document.getElementById('ga-form').style.display = 'block';

  // Centrer la carte sur l'adresse trouvée
  map.setView([lat, lon], 17);
  // Marqueur temporaire
  if (window._tmpGeoMarker) map.removeLayer(window._tmpGeoMarker);
  window._tmpGeoMarker = L.circleMarker([lat, lon], {
    radius: 10, color: '#9b59b6', weight: 3, fillColor: '#9b59b6', fillOpacity: 0.5
  }).addTo(map);
}

async function placeGeoAddr() {
  if (!geoAddrSelected) return;
  const { rue, numero, lat, lon } = geoAddrSelected;
  const circuit  = document.getElementById('ga-circuit').value;
  const district = document.getElementById('ga-district').value;

  // Sauvegarder coordonnées
  await fetch('/api/geocode', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ rue, circuit, lat, lon })
  });
  // Créer la rue dans le CSV
  const res = await fetch('/api/add_rue', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ rue, circuit, district, numeros: numero })
  });
  const d = await res.json();
  if (d.error) { showToast('❌ ' + d.error); return; }

  if (window._tmpGeoMarker) { map.removeLayer(window._tmpGeoMarker); window._tmpGeoMarker = null; }
  allMarkers = d.markers;
  redrawMap(); buildList(allMarkers);
  cancelGeoAddr();
  showToast(`✅ "${rue}" géolocalisé et ajouté au circuit ${circuit}`);
}

function cancelGeoAddr() {
  if (window._tmpGeoMarker) { map.removeLayer(window._tmpGeoMarker); window._tmpGeoMarker = null; }
  geoAddrSelected = null;
  document.getElementById('geo-addr-form').style.display = 'none';
  document.getElementById('ga-query').value = '';
  document.getElementById('ga-results').style.display = 'none';
  document.getElementById('ga-form').style.display = 'none';
}

initPresets();
loadData();
</script>
</body>
</html>"""

@app.route('/')
def index():
    return Response(HTML, mimetype='text/html')

if __name__ == '__main__':
    print("\n  ✅ App lancée → http://localhost:5000\n")
    app.run(debug=True, port=5000)
