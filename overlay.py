"""Interaktivní vrstva prohlížeče: obrysy budov, adresy, ulice, komíny, silnice pro dopravu, místa.

python overlay.py  ->  site/overlay.json (společné: kamera, místa, ulice, index adres)
                       site/overlay/<čtverec>.json (budovy, komíny, silnice, voda; prohlížeč je načte až při přiblížení)
"""
import heapq, json, math
import numpy as np
from shapely.geometry import MultiPoint
import os
from geo import CAM_X, CAM_Y, CHUNKS, HALF, KX, KY, LAT0, LON0, MESTO, POHLED, SFX, SITE, SUN, Image2D, chunk_center, chunk_dir, to_local

# souřadnice obrázku = pixely čtverce (0, 0); ostatní čtverce leží mimo (záporné / větší souřadnice)
info = json.load(open(f'build/tiles_autumn{SFX}/info.json'))
im = Image2D(info)
W, H = info['width'], info['height']
OFF = (0.0, 0.0)          # posun aktuálně zpracovávaného čtverce (lokální -> společné souřadnice)


def px(x, y, h):
    a, b, d = im.px(x + OFF[0], y + OFF[1], h)
    return round(float(a), 1), round(float(b), 1), round(float(d), 1)


def chunk_of(x, y):
    """index čtverce pro bod ve společných souřadnicích"""
    return (int(np.floor((x + HALF) / (2 * HALF))), int(np.floor((y + HALF) / (2 * HALF))))


# názvy z Overture places: veřejné instituce dávají budově název, obchody a podniky se ukážou jako „V budově“
NAME_CATS = {'education', 'specialty_school', 'college_university', 'elementary_school', 'place_of_learning', 'preschool', 'high_school',
             'campus_building', 'hospital', 'library', 'museum', 'art_gallery', 'theatre_venue', 'movie_theater', 'cultural_center',
             'christian_place_of_worship', 'jewish_place_of_worship', 'government_office', 'courthouse', 'police_station', 'fire_station',
             'train_station', 'stadium_arena', 'swimming_pool', 'hotel', 'shopping_mall', 'department_store', 'community_center',
             'senior_living_facility', 'zoo', 'music_venue', 'casino', 'amusement_park', 'historic_site', 'event_venue', 'emergency_department'}
LIST_CATS = {'restaurant', 'bar', 'coffee_shop', 'cafe', 'casual_eatery', 'fast_food_restaurant', 'food_and_beverage_store', 'convenience_store',
             'pharmacy_and_drug_store', 'bank_or_credit_union', 'gym', 'fitness_studio', 'fashion_and_apparel_store', 'electronics_store',
             'sporting_goods_store', 'books_music_and_video_store', 'flowers_and_gifts_store', 'brewery', 'bed_and_breakfast', 'lodging',
             'non_alcoholic_beverage_venue', 'dance_club', 'nightlife_venue', 'shopping', 'discount_store', 'toys_and_games_store',
             'hardware_home_and_garden_store', 'gas_station', 'post_office', 'dental_clinic', 'outpatient_care_facility', 'medical_service'} | NAME_CATS
import pyarrow.parquet as pq, shapely, shapely.ops
BIG_SHOP = {'supermarket', 'hypermarket', 'department_store', 'discount_store', 'hardware_home_and_garden_store', 'sporting_goods_store',
            'furniture_store', 'electronics_store', 'shopping_mall', 'grocery_store', 'food_and_beverage_store', 'fashion_and_apparel_store', 'car_dealer'}
PLACES_DB = []
if os.path.exists('data/places.parquet'):
    _pl = pq.read_table('data/places.parquet').to_pandas()
    for _, r in _pl.iterrows():
        if r.confidence < 0.6 or (isinstance(r.operating_status, str) and r.operating_status != 'open'): continue
        cat = r.basic_category if isinstance(r.basic_category, str) else ''
        if cat not in LIST_CATS | BIG_SHOP: continue
        g = shapely.from_wkb(r.geometry); x, y = to_local(g.x, g.y)
        PLACES_DB.append((float(x), float(y), r['names']['primary'], cat, float(r.confidence)))
# pojmenované areály z OSM (škola, nemocnice, kasárna, nádraží, sportoviště, nákupní centrum…): název pro budovy bez názvu
AREA_SKIP = {'residential', 'farmland', 'meadow', 'grass', 'allotments', 'village_green', 'forest', 'orchard', 'construction', 'downhill',
             'pedestrian', 'plaza', 'brownfield', 'greenfield', 'landfill', 'quarry', 'garages', 'religious', 'cemetery', 'natural_monument', 'protected'}
AREAS, _seen = [], set()
import glob
for f in sorted(glob.glob('data*/land_use.parquet')):
    for i, c, n, g in zip(*[pq.read_table(f, columns=['id', 'class', 'names', 'geometry']).to_pandas()[k] for k in ('id', 'class', 'names', 'geometry')]):
        if i in _seen or c in AREA_SKIP or not isinstance(n, dict) or not n.get('primary'): continue
        _seen.add(i)
        g = shapely.from_wkb(g)
        if g.geom_type not in ('Polygon', 'MultiPolygon'): continue
        g = shapely.ops.transform(lambda x, y, z=None: to_local(x, y), g)
        AREAS.append((g.area, n['primary'], g))
AREA_TREE = shapely.STRtree([a[2] for a in AREAS]) if AREAS else None
PL_TREE = shapely.STRtree([shapely.geometry.Point(p[0], p[1]) for p in PLACES_DB]) if PLACES_DB else None

ONE_BUILDING = set(MESTO.get('jedna_budova', []))      # dominanty se zónami střech: v prepu samostatné díly, na mapě jedna budova


def merge_parts(bs):
    """díly jedné budovy -> jeden záznam s obalem půdorysu (největší díl dá název a ostatní údaje)"""
    out, parts = [], {}
    for b in bs:
        if b['name'] in ONE_BUILDING: parts.setdefault(b['oid'], []).append(b)
        else: out.append(b)
    for ps in parts.values():
        hull = MultiPoint([q for b in ps for q in b['ring']]).convex_hull
        out.append({**max(ps, key=lambda b: shapely.geometry.Polygon(b['ring']).area), 'ring': [list(q) for q in hull.exterior.coords[:-1]],
                    'addr': sorted({a for b in ps for a in b['addr']}), 'base': min(b['base'] for b in ps), 'top': max(b['top'] for b in ps)})
    return out


chunks = [c for c in CHUNKS if os.path.exists(f'{chunk_dir(c)}/info_src.json')]
CKEY = {c: f'c{c[0]}_{c[1]}' for c in chunks}
streets, grounds, addr_index = [], [], []
per_chunk = {}
water = {}
bus_route = None
tram_lines, tram_stops = [], []
decks = []                # mostovky silnic a tratí (svět, výška): MHD z jízdních řádů po nich jede nahoře
# linky tramvají, když chybí jízdní řády (site/mhd.json): [konečná, konečná, počet vozů na lince]
TRAM_LINES = [tuple(t) for t in MESTO.get('mhd', {}).get('tramvaje', [])]
for chunk in chunks:
    src = json.load(open(f'{chunk_dir(chunk)}/info_src.json'))
    OFF = chunk_center(chunk)
    K = CKEY[chunk]
    buildings, chimneys, roads = [], [], []

    # budovy: obrys = konvexní obal půdorysu dole a nahoře
    for b in merge_parts(src['buildings']):
        ring = np.array(b['ring'])
        top = b['top'] + (22 if b['cls'] == 'kostel' else 0.6)
        pts = [px(x, y, z)[:2] for x, y in ring for z in (b['base'], top)]
        hull = MultiPoint(pts).convex_hull
        if hull.geom_type != 'Polygon': continue
        cx, cy = ring.mean(axis=0)
        d = px(cx, cy, b['base'])[2]
        church = b['cls'] == 'kostel'
        name, text = b['name'], None
        if b['cls'] == 'kaple':
            name = 'Hřbitovní kaple'
        inside = []
        if PL_TREE is not None:
            poly = shapely.geometry.Polygon([(x + OFF[0], y + OFF[1]) for x, y in ring]).buffer(1.0)
            inside = sorted((PLACES_DB[i] for i in PL_TREE.query(poly) if poly.contains(shapely.geometry.Point(PLACES_DB[i][:2]))), key=lambda p: -p[4])
        bpoly = shapely.geometry.Polygon([(x + OFF[0], y + OFF[1]) for x, y in ring])
        if not name:
            main = [p for p in inside if p[3] in NAME_CATS]
            if main: name = main[0][2]
        if not name and bpoly.area > 600:                     # jediný velký obchod v budově (Globus, Lidl, OBI…)
            shops = list(dict.fromkeys(p[2] for p in inside if p[3] in BIG_SHOP))
            if len(shops) == 1: name = shops[0]
        if not name and AREA_TREE is not None:                # budova v pojmenovaném areálu (ZŠ Lesní, nemocnice, kasárna…)
            c0 = bpoly.representative_point()
            hits = [AREAS[i] for i in AREA_TREE.query(c0) if AREAS[i][2].contains(c0) and AREAS[i][0] < 400000]
            if hits: name = min(hits, key=lambda a: a[0])[1]
        pl = list(dict.fromkeys(p[2] for p in inside if p[2] != name))[:4]
        cxi, cyi = round(float(np.mean([p[0] for p in pts]))), round(float(np.mean([p[1] for p in pts])))
        for a in b['addr']:
            addr_index.append([a, K, cxi, cyi, len(buildings)])
        if name: addr_index.append([name, K, cxi, cyi, len(buildings), 'budova'])
        buildings.append({k: v for k, v in {
            'pl': pl,
            'id': f"{K}:{b['id']}", 'oid': b.get('oid'), 'hull': [[round(x), round(y)] for x, y in list(hull.exterior.coords)[:-1]], 'd': d,
            'name': name, 'text': text, 'cls': b['cls'], 'floors': b['floors'], 'h': None if church else b['h'], 'roof': b['roof'], 'addr': b['addr'],
            'lon': b['lon'], 'lat': b['lat'], 'k': K,
        }.items() if v not in (None, [], '')})

    chimneys += [list(px(x, y, z)[:2]) + [f'{K}:{bid}'] for x, y, z, bid in src['chimneys']]

    # silnice pro animovanou dopravu (pixely obrázku + svět kvůli směru)
    for r in src['roads']:
        p = r['p']
        if len(p) < 2: continue
        roads.append({'c': r['c'], 'k': K, 'p': [list(px(x, y, z)) for x, y, z in p], 'w': [[x + OFF[0], y + OFF[1]] for x, y, z in p]})

    gs = np.array(src['ground_small'])[::2, ::2]     # 41 × 41 stačí na dopravu a klikání
    grounds.append({'cx': OFF[0], 'cy': OFF[1], 'g': [[round(v, 1) for v in row] for row in gs.tolist()]})
    streets += [{'name': s['name'], 'k': K, 'xy': list(px(s['x'], s['y'], s['z'])[:2])} for s in src['streets']]
    def pts(line): return [[round(a, 1), round(b, 1)] for a, b, _ in (px(x, y, z) for x, y, z in line)]
    water[K] = {'ponds': [pts(p) for p in src['water']['ponds']], 'streams': [pts(l) for l in src['water']['streams']]}
    # sjezdovky pro lyžaře (svět, shora dolů) a koleje tramvají (sešijí se přes švy až po všech čtvercích)
    pistes = [[[round(x + OFF[0], 1), round(y + OFF[1], 1), z] for x, y, z in l] for l in src.get('pistes', [])]
    tram_lines += [[(x + OFF[0], y + OFF[1], z) for x, y, z in l] for l in src.get('trams', [])]
    decks += [{'p': [[round(x + OFF[0], 1), round(y + OFF[1], 1), z] for x, y, z in d['p']], 'w': d['w']} for d in src.get('decks', [])]
    tram_stops += [(x + OFF[0], y + OFF[1], n) for x, y, n in src.get('tstops', [])]
    # stromy u silnic, kolejí a sjezdovek: auta, tramvaje a lyžaři se za jejich korunami schovají (svět, z terénu, výška, jehličnan)
    sc_ = json.load(open(f'{chunk_dir(chunk)}/scene.json'))
    T_ = [t for t in sc_['trees'] if (len(t) < 6 or t[5] != 2) and abs(t[0]) <= HALF and abs(t[1]) <= HALF]
    ln_ = [shapely.LineString([(x, y) for x, y, _ in r['p']]) for r in src['roads'] if len(r['p']) > 1] + \
          [shapely.LineString([(x, y) for x, y, _ in l]) for l in src.get('trams', []) + src.get('pistes', []) if len(l) > 1]
    vtrees = []
    if ln_ and T_:
        hit_ = shapely.STRtree(ln_).query(shapely.points([(t[0], t[1]) for t in T_]), predicate='dwithin', distance=9)
        vtrees = [[round(T_[i][0] + OFF[0], 1), round(T_[i][1] + OFF[1], 1), round(T_[i][2], 1), round(T_[i][3], 1), T_[i][4]] for i in sorted(set(hit_[0].tolist()))]
    per_chunk[K] = {'buildings': buildings, 'chimneys': chimneys, 'roads': roads, 'water': water.pop(K), 'pistes': pistes, 'trees': vtrees}




# místa s krátkým popisem (fakta z dat + ověřené zdroje)
# místa s krátkým příběhem: mista.json [{"nazev", "lat", "lon", "text"}, ...] (fakta z ověřených zdrojů)
PLACES = [(m['nazev'], m['lat'], m['lon'], m['text']) for m in json.load(open('mista.json', encoding='utf-8'))] if os.path.exists('mista.json') else []
def gz(x, y):
    for gr in grounds:
        lx, ly = x - gr['cx'], y - gr['cy']
        if abs(lx) <= HALF and abs(ly) <= HALF:
            n = len(gr['g']) - 1
            c = int(round((lx + HALF) / (2 * HALF) * n)); r = int(round((HALF - ly) / (2 * HALF) * n))
            return float(gr['g'][min(max(r, 0), n)][min(max(c, 0), n)])
    return 0.0


# co je potřeba dodat ručně (fotky): klíčové objekty z assets/dominanty.json, žluté značky v režimu „vč. rozpracovaného“
NEEDS = []     # fotky ke všem dominantám jsou dodané (dřív žluté značky 📷)
places = []
for name, lat, lon, text in PLACES:
    x, y = to_local(lon, lat)
    c = chunk_of(float(x), float(y))
    if c not in CKEY: continue
    OFF = (0.0, 0.0)
    places.append({'name': name, 'text': text, 'lon': lon, 'lat': lat, 'k': CKEY[c], 'xy': list(px(float(x), float(y), gz(x, y))[:2])})

# sousední mapy (upstream izomapa): mesto.json nebo assets/upresneni.json "sousedi": [{nazev, url, lat, lon, cil: [lat, lon], text}]
_UPS = json.load(open('assets/upresneni.json', encoding='utf-8')) if os.path.exists('assets/upresneni.json') else {}
SOUS_RAW = _UPS.get('sousedi') or MESTO.get('sousedi') or []
neighbors = []
for s in SOUS_RAW if isinstance(SOUS_RAW, list) else []:
    name, url = s.get('nazev') or s.get('name'), s.get('url') or s.get('href')
    if not name or not url or 'lat' not in s or 'lon' not in s: continue
    lat, lon = float(s['lat']), float(s['lon'])
    x, y = to_local(lon, lat); c = chunk_of(float(x), float(y)); k = CKEY.get(c)
    if not k and chunks: k = CKEY[min(chunks, key=lambda ch: (ch[0] * 2 * HALF - x) ** 2 + (ch[1] * 2 * HALF - y) ** 2)]
    if not k: continue
    OFF = (0.0, 0.0)
    ix, iy, _ = px(float(x), float(y), gz(x, y))
    item = {'name': name, 'url': url, 'lat': lat, 'lon': lon, 'k': k, 'xy': [ix, iy], 'target': s.get('target', '_self')}
    if s.get('text'): item['text'] = s['text']
    if 'cil' in s and len(s['cil']) >= 2: item['target_lat'], item['target_lon'] = float(s['cil'][0]), float(s['cil'][1])
    neighbors.append(item)

cam = {'x0': info['x0'], 'y1': info['y1'], 'mpp': info['mpp'], 'X': list(map(float, CAM_X)), 'Y': list(map(float, CAM_Y)),
       'half': HALF, 'grounds': grounds, 'sun': list(SUN), 'pohled': POHLED}
# vánoční strom na náměstí (zima): mesto.json vanocni_strom = [lat, lon]
nam = None
if MESTO.get('vanocni_strom'):
    _x, _y = to_local(MESTO['vanocni_strom'][1], MESTO['vanocni_strom'][0]); OFF = (0.0, 0.0)
    nam = list(px(float(_x), float(_y), gz(_x, _y))[:2])
cam.update({'lon0': LON0, 'lat0': LAT0, 'kx': KX, 'ky': KY})
needs = []
for name, lat, lon, text in NEEDS:
    x, y = to_local(lon, lat); OFF = (0.0, 0.0); c = chunk_of(float(x), float(y))
    if c in CKEY: needs.append({'name': name, 'text': text, 'k': CKEY[c], 'xy': list(px(float(x), float(y), gz(x, y) + 15)[:2])})
os.makedirs(f'{SITE}/overlay', exist_ok=True)
for K, v in per_chunk.items():
    json.dump(v, open(f'{SITE}/overlay/{K}.json', 'w'), separators=(',', ':'), ensure_ascii=False)
# názvy obcí a osad nad zástavbou, zobrazují se jen při oddálení. Osada = postal_city adres (u adres bez ulice je to
# skutečná osada: Chlum, Doubrava, Chloumek…), popisek nad místem s nejvíc adresami do 150 m; a = počet adres / 100 (velikost písma)
districts = []
_SKIP = set(MESTO.get('skryte_casti', []))      # názvy, které se na mapě nemají ukazovat
_grp = {}
for _a in pq.read_table('data_all/address.parquet', columns=['geometry', 'postal_city']).to_pylist():
    if not _a['postal_city'] or _a['postal_city'] in _SKIP: continue
    _pt = shapely.from_wkb(_a['geometry']); _x, _y = (float(v) for v in to_local(_pt.x, _pt.y))
    if chunk_of(_x, _y) in CKEY: _grp.setdefault(_a['postal_city'], []).append((_x, _y))
for nm, pts in _grp.items():
    if len(pts) < 8: continue
    P_ = np.array(pts); d2 = ((P_[:, None, :] - P_[None, :, :]) ** 2).sum(-1)
    k = int(np.argmax((d2 < 150 ** 2).sum(1))); cx_, cy_ = P_[d2[k] < 150 ** 2].mean(0)
    OFF = (0.0, 0.0)
    districts.append({'name': nm, 'xy': list(px(cx_, cy_, gz(cx_, cy_))[:2]), 'a': round(len(pts) / 100, 2)})
print('obcí a osad', len(districts), sorted((d['name'] for d in districts)))
# tramvaje: graf kolejí přes všechny čtverce, linky = nejkratší cesta mezi konečnými (TRAM_LINES), tam a zpět
OFF = (0.0, 0.0)
tnodes, tadj = {}, {}
tk = lambda x, y: (round(x), round(y))
for l in tram_lines:
    for (x0, y0, z0), (x1, y1, z1) in zip(l, l[1:]):
        a_, b_ = tk(x0, y0), tk(x1, y1)
        if a_ == b_: continue
        tnodes[a_] = (x0, y0, z0); tnodes[b_] = (x1, y1, z1); w_ = math.hypot(x1 - x0, y1 - y0)
        tadj.setdefault(a_, []).append((b_, w_)); tadj.setdefault(b_, []).append((a_, w_))
def tpath(a_, b_):
    dist, prev, q = {a_: 0}, {}, [(0, a_)]
    while q:
        d, u = heapq.heappop(q)
        if u == b_: break
        if d > dist[u]: continue
        for v, w_ in tadj.get(u, []):
            if d + w_ < dist.get(v, 1e18): dist[v] = d + w_; prev[v] = u; heapq.heappush(q, (d + w_, v))
    if b_ not in dist: return None
    out = [b_]
    while out[-1] != a_: out.append(prev[out[-1]])
    return out[::-1]
def tnear(x, y): return min(tnodes, key=lambda k: (tnodes[k][0] - x) ** 2 + (tnodes[k][1] - y) ** 2)
TSTOP = {}
for x, y, n in tram_stops:
    if n: TSTOP.setdefault(n, (x, y))
trams = []
for a_n, b_n, n_ in TRAM_LINES:
    if a_n not in TSTOP or b_n not in TSTOP: print('tramvaj: chybí zastávka', a_n, b_n); continue
    pth = tpath(tnear(*TSTOP[a_n]), tnear(*TSTOP[b_n]))
    if not pth: print('tramvaj: nespojeno', a_n, b_n); continue
    pts = [tnodes[k] for k in pth]
    on = [(x, y) for x, y, _ in pts]
    st = [n for n, (sx_, sy_) in TSTOP.items() if min((sx_ - x) ** 2 + (sy_ - y) ** 2 for x, y in on) < 64]
    trams.append({'w': [[round(x, 1), round(y, 1)] for x, y, z in pts], 'p': [list(px(x, y, z)) for x, y, z in pts], 'n': n_,
                  'stops': [list(px(TSTOP[n][0], TSTOP[n][1], 0)[:2]) + [n] for n in st]})
    print('tramvaj', a_n, '–', b_n, round(sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(on, on[1:]))), 'm', len(st), 'zastávek')
# závodní okruh (assets/okruh.json z nastroje/okruh.py): motorky v prohlížeči jezdí po trase ve směru závodu
moto = None
if os.path.exists('assets/okruh.json'):
    _ok = json.load(open('assets/okruh.json', encoding='utf-8'))
    _w = [tuple(float(v) for v in to_local(lon, lat)) for lat, lon in _ok['trasa']]
    moto = {'nazev': _ok['nazev'], 'n': _ok.get('motorek', 8), 'w': [[round(x, 1), round(y, 1)] for x, y in _w]}   # body obrazu dopočítá prohlížeč
    print('okruh', _ok['nazev'], len(_w), 'bodů,', moto['n'], 'motorek')
json.dump({'w': W, 'h': H, 'cam': cam, 'districts': districts, 'needs': needs, 'xmas': nam, 'chunks': {CKEY[c]: CHUNKS[c] for c in chunks}, 'bus': bus_route, 'trams': trams, 'decks': decks, 'moto': moto,
           'streets': streets, 'places': places, 'neighbors': neighbors},
          open(f'{SITE}/overlay.json', 'w'), separators=(',', ':'), ensure_ascii=False)
json.dump(addr_index, open(f'{SITE}/search.json', 'w'), separators=(',', ':'), ensure_ascii=False)   # adresy pro hledání, načte se až při psaní
print('čtverců', len(chunks), 'budov', sum(len(v['buildings']) for v in per_chunk.values()), 'adres', len(addr_index),
      'ulic', len(streets), 'míst', len(places), 'sousedů', len(neighbors), 'kB', os.path.getsize(f'{SITE}/overlay.json') // 1000)
