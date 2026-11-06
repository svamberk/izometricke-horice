# Barvy střech podle ortofota ČÚZK (CC BY 4.0) pro všechny budovy -> assets/satelit.json ('b': id -> {'rc': [r, g, b]})
#   python nastroje/strechy.py       (ortofoto 0,25 m/px se stáhne jednou do build/orto_hr/, pak se jen měří)
# Měří se jen střecha uvnitř půdorysu (zúženého o 0,6 m), bez zeleně a stínů; u šikmé střechy osvětlená strana.
# Ortofoto je světlejší a méně syté než paleta mapy, proto se barva převádí v prostoru Lab (tmavší, sytější).
import json, math, os, subprocess, sys
sys.path.insert(0, '.')
import numpy as np, shapely, pyarrow.parquet as pq
from PIL import Image, ImageDraw
import geo
sys.path.insert(0, 'nastroje')
from strechy_lab import srgb2lab, lab2srgb

Image.MAX_IMAGE_PIXELS = None
M, T = 0.25, 4096; S = T * M                         # 1024 m na dlaždici WMS
D = 'build/orto_hr/'; os.makedirs(D, exist_ok=True)
X0, Y0, X1, Y1 = geo.extent(geo.MARGIN + 60)
NX, NY = math.ceil((X1 - X0) / S), math.ceil((Y1 - Y0) / S)


def to_map(lab):
    """barva z ortofota -> barva palety mapy (ortofoto: červená taška L60 a15 b9, paleta: L42 a40 b32).
    Teplé tóny (červená, hnědá) se zesílí víc, studené (modrošedý plech, břidlice) jen mírně, aby nevznikly modré střechy."""
    L, a, b = lab
    L2 = float(np.clip(0.7 * L, 20, 66))
    a2 = a * (2.5 if a > 0 else 1.4); b2 = b * (2.7 if b > 0 else 1.3)
    ch = math.hypot(a2, b2)
    if ch > 50: a2, b2 = a2 * 50 / ch, b2 * 50 / ch
    return lab2srgb((L2, a2, b2))


def tile(tx, ty):
    f = f'{D}{tx}_{ty}.jpg'
    if not os.path.exists(f):
        lo0, la1 = geo.to_lonlat(X0 + tx * S, Y1 - ty * S); lo1, la0 = geo.to_lonlat(X0 + (tx + 1) * S, Y1 - (ty + 1) * S)
        url = ('https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=0&STYLES='
               f'&CRS=EPSG:4326&BBOX={float(la0)},{float(lo0)},{float(la1)},{float(lo1)}&WIDTH={T}&HEIGHT={T}&FORMAT=image/jpeg')
        subprocess.run(['curl', '-s', '-m', '300', '-o', f + '.tmp', url], check=True); os.replace(f + '.tmp', f)
    return f


if '--premapuj' in sys.argv:
    stat = json.load(open('build/strechy_orto.json'))
    sat = json.load(open('assets/satelit.json'))
    for i, v in stat.items(): sat['b'].setdefault(i, {})['rc'] = to_map(v['lab'])
    json.dump(sat, open('assets/satelit.json', 'w'), separators=(',', ':')); print('přepočteno', len(stat)); sys.exit()
B = pq.read_table('data_all/building.parquet', columns=['id', 'geometry']).to_pylist()
G = shapely.from_wkb([r['geometry'] for r in B])
GL = shapely.make_valid(shapely.transform(G, lambda c: np.column_stack(geo.to_local(c[:, 0], c[:, 1]))))
ER = shapely.buffer(GL, -0.6)                       # bez okrajů (okap, stín zdi, sousední střecha)
tree = shapely.STRtree(GL)
pix = {}
for ty in range(NY):
    for tx in range(NX):
        bx = shapely.box(X0 + tx * S, Y1 - (ty + 1) * S, X0 + (tx + 1) * S, Y1 - ty * S)
        ids = [k for k in tree.query(bx) if not ER[k].is_empty and ER[k].intersects(bx)]
        if not ids: continue
        a = np.asarray(Image.open(tile(tx, ty)).convert('RGB'))
        for k in ids:
            g = ER[k].intersection(bx)
            for p in (g.geoms if hasattr(g, 'geoms') else [g]):
                if p.geom_type != 'Polygon' or p.area < 1: continue
                u = (np.array(p.exterior.coords) - [X0 + tx * S, 0])[:, 0] / M
                v = (Y1 - ty * S - np.array(p.exterior.coords)[:, 1]) / M
                c0, r0 = max(0, int(u.min())), max(0, int(v.min())); c1, r1 = min(T, int(u.max()) + 2), min(T, int(v.max()) + 2)
                if c1 <= c0 or r1 <= r0: continue
                mk = Image.new('L', (c1 - c0, r1 - r0)); ImageDraw.Draw(mk).polygon(list(zip(u - c0, v - r0)), fill=1)
                sel = a[r0:r1, c0:c1][np.asarray(mk) > 0]
                if len(sel): pix.setdefault(k, []).append(sel)
        print('dlaždice', tx, ty, 'budov', len(ids), flush=True)

out, stat = {}, {}
for k, parts in pix.items():
    px = np.concatenate(parts); lab = srgb2lab(px)
    veg = (lab[:, 1] < -6) & (lab[:, 2] > 4)                 # zeleň (koruny přes střechu)
    shadow = lab[:, 0] < 22                                  # stín
    ok = ~veg & ~shadow
    if ok.sum() < 30: continue
    L = lab[ok, 0]; sun = lab[ok][L >= np.percentile(L, 40)]   # osvětlená strana šikmé střechy
    med = np.median(sun, 0)
    out[B[k]['id']] = {'rc': to_map(med)}
    stat[B[k]['id']] = {'lab': [round(float(v), 1) for v in med], 'px': int(len(px)), 'stin': round(float(shadow.mean()), 2), 'zelen': round(float(veg.mean()), 2)}
sat = json.load(open('assets/satelit.json')) if os.path.exists('assets/satelit.json') else {'b': {}, 'lu': {}, 'zones': []}
for i, v in out.items(): sat['b'][i] = {**sat['b'].get(i, {}), **v}
json.dump(sat, open('assets/satelit.json', 'w'), separators=(',', ':'))
json.dump(stat, open('build/strechy_orto.json', 'w'), separators=(',', ':'))
print('barva střechy změřena u', len(out), 'z', len(B), 'budov')
