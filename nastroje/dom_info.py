# Podklady k dominantě: půdorys (Overture), laser DMP 1G (výšky nad terénem, kde leží nejvyšší body = věž), ortofoto ČÚZK
# a výřez současného renderu.
#   python nastroje/dom_info.py <název> <lat> <lon> [--only]
#   -> vypíše JSON a uloží build/dominanty/<název>_info.json, <název>_orto.jpg (0,1 m/px, sever nahoře), <název>_pred.png
import glob, json, math, os, subprocess, sys
sys.path.insert(0, os.getcwd())
import numpy as np, laspy, shapely, pyarrow.parquet as pq
from shapely.geometry import Point
from pyproj import Transformer
from PIL import Image
import geo

OUT = 'build/dominanty'; os.makedirs(OUT, exist_ok=True)
import argparse
ap = argparse.ArgumentParser(); ap.add_argument('slug'); ap.add_argument('lat', type=float); ap.add_argument('lon', type=float); ap.add_argument('--only', action='store_true')
A = ap.parse_args()

# budovy (Overture, bez duplicit)
B = {}
for f in sorted(glob.glob('data*/building.parquet')):
    for r in pq.read_table(f, columns=['id', 'geometry', 'height', 'num_floors', 'class', 'roof_shape', 'names']).to_pylist():
        B.setdefault(r['id'], r)
ids = list(B); G = shapely.from_wkb([B[i]['geometry'] for i in ids])
GL = shapely.transform(G, lambda c: np.column_stack(geo.to_local(c[:, 0], c[:, 1])))   # lokální metry
tree = shapely.STRtree(GL)

T = Transformer.from_crs(4326, 5514, always_xy=True)
SHEETS = []
for f in sorted(glob.glob('build/dmp/*.laz')):
    with laspy.open(f) as fh: h = fh.header; SHEETS.append((f, h.mins[0], h.mins[1], h.maxs[0], h.maxs[1]))


def lidar(poly_ll, pt_ll, only):
    """body DMP v okolí: výšky budovy nad terénem a poloha nejvyšších bodů"""
    kx = lambda g: shapely.transform(g, lambda c: np.column_stack(T.transform(c[:, 0], c[:, 1])))
    area = kx(poly_ll) if poly_ll is not None else kx(Point(*pt_ll)).buffer(8)
    x0, y0, x1, y1 = area.buffer(20).bounds
    X, Y, Z, C = [], [], [], []
    for f, a, b, c, d in SHEETS:
        if c < x0 or a > x1 or d < y0 or b > y1: continue
        l = laspy.read(f); m = (l.x >= x0) & (l.x <= x1) & (l.y >= y0) & (l.y <= y1)
        X += list(np.array(l.x)[m]); Y += list(np.array(l.y)[m]); Z += list(np.array(l.z)[m]); C += list(np.array(l.classification)[m])
    X, Y, Z, C = map(np.array, (X, Y, Z, C))
    if not len(X): return None
    P = shapely.points(X, Y)
    inn = shapely.contains(area.buffer(0.5), P)
    ring = shapely.contains(area.buffer(15), P) & ~shapely.contains(area.buffer(2), P) & (C == 2)
    g0 = float(np.median(Z[ring])) if ring.any() else float(np.min(Z[inn])) if inn.any() else float(np.min(Z))
    z = Z[inn] - g0
    if not len(z): return {'ground_abs': round(g0, 1)}
    top = z >= np.percentile(z, 97)
    # nejvyšší body v lokálních metrech (relativně k těžišti půdorysu): kde je věž
    tx, ty = Transformer.from_crs(5514, 4326, always_xy=True).transform(X[inn][top], Y[inn][top])
    lx, ly = geo.to_local(np.array(tx), np.array(ty))
    return {'ground_abs': round(g0, 1), 'pts': int(inn.sum()), 'classes': {int(k): int(v) for k, v in zip(*np.unique(C[inn], return_counts=True))},
            'p50': round(float(np.percentile(z, 50)), 1), 'p90': round(float(np.percentile(z, 90)), 1),
            'p99': round(float(np.percentile(z, 99)), 1), 'max': round(float(z.max()), 1),
            'top3pct_lonlat': [round(float(np.mean(tx)), 6), round(float(np.mean(ty)), 6)],
            '_top_local': [float(np.mean(lx)), float(np.mean(ly))]}


def orto(slug, cx, cy, r):
    """ortofoto ČÚZK kolem bodu (lokální m), 0,1 m/px"""
    f = f'{OUT}/{slug}_orto.jpg'
    if os.path.exists(f): return f
    (lo0, lo1), (la0, la1) = geo.to_lonlat([cx - r, cx + r], [cy - r, cy + r])
    n = int(2 * r / 0.1)
    subprocess.run(['curl', '-s', '-m', '120', '-o', f, 'https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer?SERVICE=WMS&VERSION=1.3.0'
                    f'&REQUEST=GetMap&LAYERS=0&STYLES=&CRS=EPSG:4326&BBOX={la0},{lo0},{la1},{lo1}&WIDTH={n}&HEIGHT={n}&FORMAT=image/jpeg'], check=True)
    return f


def render_crop(slug, lat, lon, hrel, w=700, h=560):
    x, y = geo.to_local(lon, lat); c = geo.chunk_of(float(x), float(y)); d = os.path.join(geo.chunk_dir(c), 'tiles_summer')
    info = json.load(open(os.path.join(d, 'info.json'))); I = geo.Image2D(info); Tt = info['tile']
    px, py, _ = I.px(x, y, hrel)
    x0, y0 = int(px - w / 2), int(py - h / 2); i0, j0, i1, j1 = x0 // Tt, y0 // Tt, (x0 + w) // Tt, (y0 + h) // Tt
    big = Image.new('RGBA', ((i1 - i0 + 1) * Tt, (j1 - j0 + 1) * Tt), (0, 0, 0, 0))
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            f = os.path.join(d, f'{i}_{j}.png')
            if os.path.exists(f): big.alpha_composite(Image.open(f).convert('RGBA'), ((i - i0) * Tt, (j - j0) * Tt))
    big.crop((x0 - i0 * Tt, y0 - j0 * Tt, x0 - i0 * Tt + w, y0 - j0 * Tt + h)).save(f'{OUT}/{slug}_pred.png')
    return {'chunk': list(c), 'tiles': ';'.join(f'{i}_{j}' for i in range(i0, i1 + 1) for j in range(j0, j1 + 1))}


L = [(A.slug, A.slug, A.lat, A.lon, int(A.only))]
res = {}
for slug, name, lat, lon, only in L:
    x, y = (float(v) for v in geo.to_local(lon, lat)); pt = Point(x, y)
    o = {'name': name, 'lat': lat, 'lon': lon, 'only': only}
    poly_ll = None
    if not only:
        hit = [k for k in tree.query(pt.buffer(25)) if GL[k].area > 60]
        inside = [k for k in hit if GL[k].buffer(1.0).contains(pt)]
        cand = inside or sorted(hit, key=lambda k: -GL[k].area)
        if cand:
            k = cand[0]; r = B[ids[k]]; p = GL[k]; poly_ll = G[k]
            ob = p.minimum_rotated_rectangle; cs = list(ob.exterior.coords)[:3]
            e1 = np.subtract(cs[1], cs[0]); e2 = np.subtract(cs[2], cs[1])
            if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1
            o['building'] = {'overture_id': ids[k], 'matched': 'inside' if inside else 'nearest-largest', 'class': r['class'], 'name': (r['names'] or {}).get('primary'),
                             'osm_height': r['height'], 'floors': r['num_floors'], 'roof_shape': r['roof_shape'], 'area_m2': round(p.area),
                             'obb_long_m': round(float(np.linalg.norm(e1)), 1), 'obb_short_m': round(float(np.linalg.norm(e2)), 1),
                             'long_axis_deg_from_east': round(math.degrees(math.atan2(e1[1], e1[0])) % 180, 1),
                             'vertices': len(p.exterior.coords) - 1, 'centroid_lonlat': [round(float(v), 6) for v in geo.to_lonlat(p.centroid.x, p.centroid.y)]}
            cx, cy = p.centroid.x, p.centroid.y; rr = max(30, math.sqrt(p.area) * 1.2)
        else:
            o['building'] = None
    if poly_ll is None: cx, cy, rr = x, y, 30
    lz = lidar(poly_ll, (lon, lat), only)
    if lz and '_top_local' in lz and poly_ll is not None:
        dx, dy = lz['_top_local'][0] - cx, lz['_top_local'][1] - cy
        o['tower_hint'] = f'nejvyšší 3 % bodů leží {math.hypot(dx, dy):.1f} m od těžiště směrem {["V", "SV", "S", "SZ", "Z", "JZ", "J", "JV"][int(((math.degrees(math.atan2(dy, dx)) + 22.5) % 360) // 45)]}'
    if lz: lz.pop('_top_local', None)
    o['lidar_m_above_ground'] = lz
    o['orto'] = orto(slug, cx, cy, rr); o['orto_m_per_px'] = 0.1; o['orto_halfwidth_m'] = round(rr, 1)
    hrel = (lz['ground_abs'] - geo.HREF + 8) if lz else 25
    try:
        o['render'] = render_crop(slug, lat, lon, hrel); o['render_crop'] = f'build/dominanty/{slug}_pred.png'
    except Exception as e:
        o['render'] = None; o['render_crop'] = f'(render čtverce ještě není: {e})'
    res[slug] = o
    print(json.dumps(o, ensure_ascii=False, indent=1), flush=True)
    json.dump(o, open(f'{OUT}/{slug}_info.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
