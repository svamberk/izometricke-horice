"""Předzpracování: Overture Maps + Terrarium DEM -> build/scene.json, build/ground_<sezóna>.png, build/lightmap.png, build/info_src.json

python prep.py [složka_s_daty]
"""
import hashlib, json, math, os, random, sys
import numpy as np, pandas as pd, shapely, pyarrow.parquet as pq
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import transform
from PIL import Image, ImageDraw, ImageFilter
import geo
from geo import HALF, HREF

DATA = sys.argv[1] if len(sys.argv) > 1 else 'data'
CHUNK = tuple(int(v) for v in sys.argv[2].split(',')) if len(sys.argv) > 2 else (0, 0)
OUT = geo.chunk_dir(CHUNK)
CX, CY = geo.chunk_center(CHUNK)          # všechno níže je v souřadnicích relativních ke středu čtverce


def to_local(lon, lat):
    x, y = geo.to_local(lon, lat); return x - CX, y - CY


def to_lonlat(x, y):
    return geo.to_lonlat(np.asarray(x) + CX, np.asarray(y) + CY)
os.makedirs(OUT, exist_ok=True)
TEX_RES = 2 * HALF / 8000   # m/px textury terénu (max. velikost textury v GPU je 8192 px)
AREA = box(-HALF, -HALF, HALF, HALF)
# Pás sousedů (MARGIN): budovy a stromy u švu se kreslí do obou čtverců stejně, aby přes šev pokračovaly i se stíny.
# Masky (kde nesmí stát strom) se proto kreslí na rozšířený výřez HE, textura terénu se pak ořízne na AREA.
NB = geo.neighbours(CHUNK)
HE = HALF + geo.MARGIN
MPX = round(geo.MARGIN / TEX_RES)           # šířka pásu v pixelech textury
S2 = 2 * HALF
NBOX = [box(di * S2 - HALF, dj * S2 - HALF, di * S2 + HALF, dj * S2 + HALF) for di, dj in NB]
CLIP = shapely.unary_union([AREA] + NBOX)   # mimo tohle už je okraj mapy
EXT = CLIP.intersection(box(-HE, -HE, HE, HE))
EXT_P = shapely.prepared.prep(EXT)


# upřesnění ze satelitních snímků (build/sat/, odvozené hodnoty): barva a typ střechy, povrch hřišť, obrysy snímků
SAT = json.load(open('assets/satelit.json')) if os.path.exists('assets/satelit.json') else {'b': {}, 'lu': {}, 'zones': []}
# ruční upřesnění podle satelitu a 3D fotek: výška/barvy budov (OSM je často nemá), přístřešky, plochy, autobusy
UP = json.load(open('assets/upresneni.json')) if os.path.exists('assets/upresneni.json') else {'b': {}, 'roofs': [], 'paved': [], 'buses': []}
for _k, _v in UP['b'].items(): SAT['b'][_k] = {**SAT['b'].get(_k, {}), **_v}
# oblasti podle skutečnosti (tuning/oblast.py): stromy z ortofota a DMP, barvy střech, ruční úpravy budov
OBL = json.load(open('assets/oblasti.json')) if os.path.exists('assets/oblasti.json') else {}
for _o in OBL.values():
    for _k, _v in list(_o['b_auto'].items()) + list(_o['b'].items()): SAT['b'][_k] = {**SAT['b'].get(_k, {}), **_v}
# změřené výšky z DMP 1G (ČÚZK, CC BY 4.0): id -> [medián, p90] nad terénem. Nerozhodují o typu ani tvaru střechy, jen o výšce
DMP = json.load(open('assets/dmp_vysky.json')) if os.path.exists('assets/dmp_vysky.json') else {}
if os.path.exists('assets/dmp_rucne.json'): DMP.update({k: v for k, v in json.load(open('assets/dmp_rucne.json')).items() if not k.startswith('_')})   # ověřená měření z kontrolního seznamu
DMP.update(UP.get('dmp', {}))                                 # ruční doplnění měření


def h01(s):
    return int(hashlib.md5(str(s).encode()).hexdigest()[:8], 16) / 0xffffffff


def wkey(x, y):
    """klíč podle polohy ve světových souřadnicích (stejný ve všech čtvercích)"""
    return f'{round(x + CX, 1)},{round(y + CY, 1)}'


def wrng(x, y, salt=''):
    return random.Random(salt + wkey(x, y))


def ihash(*a):
    """deterministická náhoda 0..1 z celých čísel (numpy, vektorově)"""
    with np.errstate(over='ignore'):
        h = np.uint64(0x9E3779B97F4A7C15)
        for v in a:
            h = h ^ (np.asarray(v).astype(np.int64).astype(np.uint64) * np.uint64(0xBF58476D1CE4E5B9) + np.uint64(0x94D049BB133111EB) + (h << np.uint64(6)) + (h >> np.uint64(2)))
        h = (h ^ (h >> np.uint64(31))) * np.uint64(0xBF58476D1CE4E5B9)
        h = (h ^ (h >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        h = h ^ (h >> np.uint64(31))
    return (h >> np.uint64(11)).astype(np.float64) / float(2 ** 53)


def load(name):
    t = pq.read_table(f'{DATA}/{name}.parquet').to_pandas()
    t = t.sort_values('id', kind='stable').reset_index(drop=True)   # pořadí ze S3 není zaručené
    t['geom'] = t.geometry.map(lambda g: transform(lambda x, y, z=None: to_local(x, y), shapely.from_wkb(g)))
    return t


def polys(g):
    if g.is_empty: return []
    if isinstance(g, Polygon): return [g]
    if hasattr(g, 'geoms'): return [p for q in g.geoms for p in polys(q)]
    return []


def lines(g):
    if g.is_empty: return []
    if isinstance(g, LineString): return [g]
    if hasattr(g, 'geoms'): return [p for q in g.geoms for p in lines(q)]
    return []


def sname(r):
    n = r.get('names')
    return n.get('primary') if isinstance(n, dict) else None


# ---------------- terén ----------------
def dem_sampler():
    import glob
    tiles = {}
    for f in glob.glob(f'{DATA}/dem/*.png'):
        _, x, y = os.path.basename(f)[:-4].split('_')
        a = np.asarray(Image.open(f).convert('RGB')).astype(np.float64)
        tiles[(int(x), int(y))] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    xs = sorted({k[0] for k in tiles}); ys = sorted({k[1] for k in tiles})
    big = np.zeros((len(ys) * 256, len(xs) * 256))
    for (x, y), a in tiles.items():
        big[(y - ys[0]) * 256:(y - ys[0] + 1) * 256, (x - xs[0]) * 256:(x - xs[0] + 1) * 256] = a
    # oprava zdrojových dat: Terrarium má na švech dlaždic SRTM (např. poledník 15° v. d.) úzké pruhy o desítky metrů níž
    # -> body výrazně pod mediánem okolí 7 × 7 se nahradí mediánem (skutečné rokle mají šířku víc pixelů a zůstanou)
    from numpy.lib.stride_tricks import sliding_window_view as _sw
    med = np.median(_sw(np.pad(big, 3, mode='edge'), (7, 7)), axis=(2, 3))
    pit = big < med - 12
    big = np.where(pit, med, big)
    if pit.any(): print('oprava terénu (pruhy ve zdrojových datech)', int(pit.sum()), 'bodů')
    n = 2 ** 14

    def sample(px, py):
        lon, lat = to_lonlat(px, py)
        gx = (lon + 180) / 360 * n * 256 - xs[0] * 256
        gy = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n * 256 - ys[0] * 256
        x0 = np.floor(gx - 0.5).astype(int); y0 = np.floor(gy - 0.5).astype(int)
        fx = gx - 0.5 - x0; fy = gy - 0.5 - y0
        x0 = np.clip(x0, 0, big.shape[1] - 2); y0 = np.clip(y0, 0, big.shape[0] - 2)
        return (big[y0, x0] * (1 - fx) * (1 - fy) + big[y0, x0 + 1] * fx * (1 - fy)
                + big[y0 + 1, x0] * (1 - fx) * fy + big[y0 + 1, x0 + 1] * fx * fy)
    return sample


dem = dem_sampler()
STEP = 5.0
N = int(2 * HALF / STEP) + 1
# mřížka výšek i s pásem sousedů (MS kroků); body leží na společné světové mřížce, takže se sousedé shodují
MS = int(math.ceil(geo.MARGIN / STEP)) + 2
NE = N + 2 * MS
RE = HALF + MS * STEP
gx, gy = np.meshgrid(np.linspace(-RE, RE, NE), np.linspace(RE, -RE, NE))
HX = dem(gx, gy)
from numpy.lib.stride_tricks import sliding_window_view
HX = sliding_window_view(np.pad(HX, 1, mode='edge'), (3, 3)).mean(axis=(2, 3))
HMIN = HREF; HX = HX - HMIN              # společná výška pro všechny čtverce
H = HX[MS:MS + N, MS:MS + N]


def ground(x, y):
    fx = (np.asarray(x) + RE) / STEP; fy = (RE - np.asarray(y)) / STEP
    x0 = np.clip(np.floor(fx).astype(int), 0, NE - 2); y0 = np.clip(np.floor(fy).astype(int), 0, NE - 2)
    tx = np.clip(fx - x0, 0, 1); ty = np.clip(fy - y0, 0, 1)
    return (HX[y0, x0] * (1 - tx) * (1 - ty) + HX[y0, x0 + 1] * tx * (1 - ty)
            + HX[y0 + 1, x0] * (1 - tx) * ty + HX[y0 + 1, x0 + 1] * tx * ty)


print('terén', N, 'x', N, 'převýšení', round(float(H.max()), 1), 'm')

# ---------------- mapa povrchů (třídy) ----------------
# každá třída má barvu podle ročního období, viz SEASON_PAL níže
C = dict(grass=0, garden=1, park=2, meadow=3, forest=4, scrub=5, pitch=6, track=7, sand=8, paved=9,
         industrial=10, parking=11, road=12, marking=13, curb=14, dirt=15, water=16, pool=17, stream=18,
         rail=19, cemetery=20, wetland=21, rock=22, construction=23, footway=24, pool_edge=25, farmland=26,
         cobble=27, grave=28, manhole=29, court_blue=30, zone=31, zone2=32, piste=33, skijump=34,
         wheat=35, wheat2=36, rape=37, rape2=38, maize=39, maize2=40, soil=41, soil2=42)    # pole: plodina + řádky
TW = int(2 * HALF / TEX_RES)
TWE = TW + 2 * MPX                          # rastry včetně pásu sousedů
cls = Image.new('L', (TWE, TWE), C['grass'])
cd = ImageDraw.Draw(cls)
notree = Image.new('L', (TWE, TWE), 0)
nd = ImageDraw.Draw(notree)
DRAWBOX = box(-HE, -HE, HE, HE).buffer(50)


def P(coords):
    return [((x + HE) / TEX_RES, (HE - y) / TEX_RES) for x, y in coords]


def pix(x, y):
    return int((x + HE) / TEX_RES), int((HE - y) / TEX_RES)


def fill(g, c, outline=None, block=False):
    for p in polys(g.intersection(DRAWBOX)):
        cd.polygon(P(p.exterior.coords), fill=c, outline=outline)
        for h in p.interiors: cd.polygon(P(h.coords), fill=C['grass'])
        if block: nd.polygon(P(p.exterior.coords), fill=255)


def line(g, c, w, block=False):
    for l in lines(g.intersection(DRAWBOX)):
        pts = P(l.coords)
        cd.line(pts, fill=c, width=max(1, round(w / TEX_RES)), joint='curve')
        if block: nd.line(pts, fill=255, width=max(1, round((w + 2) / TEX_RES)), joint='curve')


LU = {'residential': 'garden', 'garden': 'garden', 'grass': 'park', 'park': 'park', 'village_green': 'park',
      'recreation_ground': 'park', 'meadow': 'meadow', 'farmland': 'farmland', 'allotments': 'garden',
      'cemetery': 'cemetery', 'pitch': 'pitch', 'track': 'track', 'playground': 'sand', 'stadium': 'pitch',
      'school': 'park', 'kindergarten': 'garden', 'industrial': 'industrial', 'garages': 'industrial',
      'commercial': 'paved', 'retail': 'paved', 'pedestrian': 'cobble', 'plaza': 'cobble',
      'construction': 'construction', 'railway': 'dirt', 'barracks': 'industrial', 'downhill': 'piste', 'ski_jump': 'skijump'}
NOTREE_LU = {'pitch', 'track', 'playground', 'stadium', 'school', 'industrial', 'garages', 'commercial',
             'retail', 'pedestrian', 'plaza', 'construction', 'railway', 'downhill', 'ski_jump'}
lu = load('land_use')
lu['area'] = lu.geom.map(lambda g: g.area)
lu = lu.sort_values('area', ascending=False)
for _, r in lu.iterrows():
    k = SAT['lu'].get(r.id) or LU.get(r['class'])
    if k: fill(r.geom, C[k], block=r['class'] in NOTREE_LU)
# pitch: bílé čáry hřiště. Překrývající se hřiště z dat se sloučí (jinak se čáry kříží); čáry jen u zhruba obdélníkových
pitch_polys = [p for _, r in lu.iterrows() if r['class'] == 'pitch' and SAT['lu'].get(r.id, 'pitch') in ('pitch', 'track', 'court_blue')
               for p in polys(r.geom.intersection(DRAWBOX)) if p.area > 30]
for p in polys(shapely.unary_union(pitch_polys)) if pitch_polys else []:
    o = p.minimum_rotated_rectangle
    if p.area / max(o.area, 1) < 0.86: continue                 # nepravidelný tvar: jen plocha bez čar
    q = o.buffer(-0.6, join_style=2)
    if q.is_empty: continue
    oc = [np.array(c) for c in list(q.exterior.coords)[:4]]
    e1, e2 = oc[1] - oc[0], oc[3] - oc[0]
    if np.linalg.norm(e1) < np.linalg.norm(e2): oc = [oc[0], oc[3], oc[2], oc[1]]; e1, e2 = e2, e1
    L, W = np.linalg.norm(e1), np.linalg.norm(e2); u, v = e1 / L, e2 / W
    if L < 12 or W < 6: continue
    pt = lambda s_, t_: tuple(oc[0] + u * s_ + v * t_)
    cd.polygon(P([pt(0, 0), pt(L, 0), pt(L, W), pt(0, W)]), outline=C['marking'])
    cd.line(P([pt(L / 2, 0), pt(L / 2, W)]), fill=C['marking'], width=1)        # středová čára napříč
    if L > 70 and W > 40:                                                       # fotbal: kruh a vápna
        r_ = 9.15; cx_, cy_ = pt(L / 2, W / 2)
        cd.line(P([(cx_ + r_ * math.cos(k / 24 * math.tau) * u[0] + r_ * math.sin(k / 24 * math.tau) * v[0],
                    cy_ + r_ * math.cos(k / 24 * math.tau) * u[1] + r_ * math.sin(k / 24 * math.tau) * v[1]) for k in range(25)]), fill=C['marking'], width=1)
        for s0, s1 in ((0, 16.5), (L, L - 16.5)):
            w0, w1 = W / 2 - 20.15, W / 2 + 20.15
            cd.line(P([pt(s0, w0), pt(s1, w0), pt(s1, w1), pt(s0, w1)]), fill=C['marking'], width=1)

land = load('land')
LAND = {'forest': 'forest', 'wood': 'forest', 'scrub': 'scrub', 'shrub': 'scrub', 'grassland': 'meadow',
        'wetland': 'wetland', 'bare_rock': 'rock', 'rock': 'rock'}
for _, r in land.iterrows():
    if r['class'] in LAND and r.geom.geom_type != 'Point':
        fill(r.geom, C[LAND[r['class']]])
# sjezdovky jsou v datech jen čáry: vykácený pás (v létě louka, v zimě sníh)
PISTE = [l for _, r in lu.iterrows() if r['class'] == 'downhill' for l in lines(r.geom)]
for l in PISTE: fill(l.buffer(22), C['piste'], block=True)
# les podle ortofota (build/les_maska.png = koruny po 2 m, tuning/les_chyby.py): kde nejsou koruny, je louka a žádné stromy
LESM = None
if os.environ.get('IZO_LES', '1') != '0' and os.path.exists('build/les_maska.png'):   # IZO_LES=0 vypne
    Image.MAX_IMAGE_PIXELS = None
    _MX0, _MY1, _MM = json.load(open('build/les_maska.json'))['x0y1m']     # z nastroje/les.py
    _m = Image.open('build/les_maska.png').crop((round((CX - HE - _MX0) / _MM), round((_MY1 - CY - HE) / _MM),
                                                 round((CX + HE - _MX0) / _MM), round((_MY1 - CY + HE) / _MM))).resize((TWE, TWE), Image.NEAREST)
    LESM = np.asarray(_m) > 0
    cls.paste(C['meadow'], mask=Image.fromarray(((np.asarray(cls) == C['forest']) & ~LESM).astype(np.uint8) * 255))
# ZABAGED (ČÚZK): trvalý travní porost = louka, orná půda = pole s plodinou a řádky ve směru delší strany, sad/zahrada
def _zpoly(rings):
    sh = [list(zip(*[np.asarray(v, float) for v in to_local([q[1] for q in r], [q[0] for q in r])])) for r in rings]
    return shapely.make_valid(Polygon(sh[0], sh[1:]))
ZAB = json.load(open('assets/zabaged.json')) if os.path.exists('assets/zabaged.json') else {'orna': [], 'ttp': [], 'sad': []}
_zb = box(-HE, -HE, HE, HE)
_pole = set(ZAB.get('pole', []))                # travní porost, který je na ortofotu žlutohnědý = pole (tuning/pole.py)
ZTTP = [g for i, r in enumerate(ZAB['ttp']) if i not in _pole for g in [_zpoly(r)] if g.intersects(_zb)]
ZORNA = [g for r in ZAB['orna'] + [ZAB['ttp'][i] for i in sorted(_pole)] for g in [_zpoly(r)] if g.intersects(_zb)]
ZSAD = [g for g in map(_zpoly, ZAB['sad']) if g.intersects(_zb)]
for g in ZTTP: fill(g, C['meadow'])
CROPS = ['wheat'] * 4 + ['rape'] * 2 + ['maize'] * 2 + ['soil'] * 2
HON = 320.0                                     # lány nad 30 ha se dělí na hony (pásy napříč delší osou), každý s vlastní plodinou
for g in ZORNA:
    # plodina, směr řádků i hony podle celého pole (ne podle kusu v tomto čtverci), aby pole na švu čtverců navazovalo
    o = g.minimum_rotated_rectangle; oc = list(o.exterior.coords)[:4]
    e1 = np.subtract(oc[1], oc[0]); e2 = np.subtract(oc[2], oc[1])
    if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1
    u = e1 / np.linalg.norm(e1)
    if u[0] < 0 or (u[0] == 0 and u[1] < 0): u = -u              # stejná orientace ve všech čtvercích
    v = np.array([-u[1], u[0]]); c0 = np.array(g.centroid.coords[0]); key0 = wkey(c0[0] + CX, c0[1] + CY)
    for p in polys(g.intersection(DRAWBOX)):
        if p.area < 400: continue
        pc = np.array(p.exterior.coords) - c0; pu_, pv_ = pc @ u, pc @ v
        strips = range(math.floor(pu_.min() / HON), math.ceil(pu_.max() / HON)) if g.area > 300000 else [None]
        for si in strips:
            q = p if si is None else p.intersection(Polygon([tuple(c0 + u * si * HON + v * (pv_.min() - 1)), tuple(c0 + u * (si + 1) * HON + v * (pv_.min() - 1)),
                                                             tuple(c0 + u * (si + 1) * HON + v * (pv_.max() + 1)), tuple(c0 + u * si * HON + v * (pv_.max() + 1))]))
            if q.is_empty or q.area < 100: continue
            cr = CROPS[int(h01(key0 + 'pole' + ('' if si is None else str(si))) * len(CROPS))]
            fill(q, C[cr])
            k = math.floor(pv_.min() / 2.0) * 2.0
            while k < pv_.max():                         # řádky po 2 m na mřížce celého pole
                l = LineString([tuple(c0 + v * k + u * (pu_.min() - 1)), tuple(c0 + v * k + u * (pu_.max() + 1))]).intersection(q)
                if not l.is_empty: line(l, C[cr + '2'], 0.6)
                k += 2.0

inf = load('infrastructure')
parkings = []
for _, r in inf.iterrows():
    if r['class'] == 'parking' and r.geom.geom_type in ('Polygon', 'MultiPolygon'):
        fill(r.geom, C['parking'], block=True)
        parkings += [(r.id, p) for p in polys(r.geom.intersection(AREA))]
for _, r in inf.iterrows():                             # autobusové nádraží: asfalt bez stání, nástupiště dlážděná
    if r['class'] == 'bus_station' and r.geom.geom_type in ('Polygon', 'MultiPolygon'): fill(r.geom, C['parking'], block=True)
for _, r in inf.iterrows():
    if r['class'] == 'platform' and r.subtype == 'transit' and r.geom.geom_type in ('Polygon', 'MultiPolygon') and r.geom.area < 2000: fill(r.geom, C['paved'], block=True)
for q in UP['paved']: fill(Polygon([to_local(lo, la) for la, lo in q]), C['paved'], block=True)

wat = load('water')
WET = set(geo.MESTO.get('voda_mokrad', []))   # vodní plochy, které jsou podle ortofota zarostlé (Overture id z mesto.json)
for _, r in wat.iterrows():
    g = r.geom
    if g.geom_type in ('Polygon', 'MultiPolygon'):
        if r['id'] in WET: fill(g, C['wetland'])
        elif r['class'] == 'swimming_pool': fill(g, C['pool'], outline=C['pool_edge'], block=True)
        else: fill(g, C['water'], block=True)
    elif r['class'] == 'river':          # jen řeky; potoky v datech často vedou pod domy (zatrubněné) a ve skutečnosti nejsou vidět
        line(g, C['stream'], 6, block=True)

seg = load('segment')
# dálnice ve výstavbě se nekreslí (staveniště je vidět ve využití území)
_uc = lambda r: r['class'] == 'motorway' and any('is_under_construction' in list(f.get('values')) for f in (r.road_flags if r.road_flags is not None else []))
seg = seg[[not _uc(r) for _, r in seg.iterrows()]].reset_index(drop=True)
TUNNEL_MIN = 60.0     # kratší „tunel“ je podjezd pod silnicí nebo tratí: kreslí se jako cesta, nad ní vede most
def tunnel_parts(r):
    """části segmentu mimo tunel (0..1); dlouhé tunely se nekreslí, silnice je pod zemí"""
    fl = r.road_flags if r.road_flags is not None else []
    tn = [f.get('between') for f in fl if 'is_tunnel' in list(f.get('values'))]
    tn = [t for t in tn if (1.0 if t is None else float(t[1]) - float(t[0])) * r.geom.length >= TUNNEL_MIN]
    if not tn: return None
    if any(t is None for t in tn): return []
    out, a = [], 0.0
    for t0, t1 in sorted((float(t[0]), float(t[1])) for t in tn):
        if t0 > a: out.append((a, t0))
        a = max(a, t1)
    if a < 1: out.append((a, 1.0))
    return out
_rows, portals = [], []
for _, r in seg.iterrows():
    tp = tunnel_parts(r)
    if tp is None: _rows.append(r); continue
    for i, (a, b_) in enumerate(tp):
        q = r.copy(); q['geom'] = shapely.ops.substring(r.geom, a, b_, normalized=True); _rows.append(q)
    if r['class'] == 'trunk':                           # portály na koncích tunelu
        for a, b_ in tp:
            for t, sg in ((a, 1), (b_, -1)):
                if 0 < t < 1:
                    p0 = r.geom.interpolate(t, normalized=True); p1 = r.geom.interpolate(max(0, min(1, t + sg * 0.004)), normalized=True)
                    portals.append((p0.x, p0.y, math.atan2(p1.y - p0.y, p1.x - p0.x)))
seg = pd.DataFrame(_rows).reset_index(drop=True)
# mosty a lávky: úsek označený is_bridge se nekreslí do terénu, ale postaví se jako mostovka (scene.bridges)
BRIDGE_MIN = 8.0      # kratší „most“ (propustek nad potokem) zůstane obyčejnou cestou
def bridge_parts(r):
    fl = r.road_flags if isinstance(r.road_flags, (list, np.ndarray)) else []
    br = [f.get('between') for f in fl if 'is_bridge' in list(f.get('values'))]
    if not br: return None
    rng = sorted((0.0, 1.0) if t is None else (float(t[0]), float(t[1])) for t in br)
    out, a = [], 0.0
    for t0, t1 in rng:
        if t0 > a: out.append((a, t0, False))
        out.append((max(a, t0), t1, True)); a = max(a, t1)
    if a < 1: out.append((a, 1.0, False))
    return out
_rows, bridge_segs = [], []
for _, r in seg.iterrows():
    bp = bridge_parts(r)
    if bp is None: _rows.append(r); continue
    for a, b_, isb in bp:
        if b_ - a < 1e-6: continue
        g = shapely.ops.substring(r.geom, a, b_, normalized=True)
        if isb and g.length >= BRIDGE_MIN: bridge_segs.append((g, r)); continue
        q = r.copy(); q['geom'] = g; _rows.append(q)
seg = pd.DataFrame(_rows).reset_index(drop=True)
RW = {'motorway': 18, 'trunk': 12, 'primary': 10, 'secondary': 9, 'tertiary': 8, 'residential': 6, 'unclassified': 6,
      'living_street': 5, 'service': 3.6, 'pedestrian': 4, 'track': 3, 'footway': 1.8, 'path': 1.6,
      'cycleway': 2, 'steps': 2, 'bridleway': 1.6, 'unknown': 3}
def rwidth(r):
    """šířka silnice: RW je obousměrná; jednosměrný pás dvoupásové silnice ~60 %, sjezd (link) jeden pruh 5 m"""
    w = RW.get(r['class'], 3)
    if r['class'] not in ('motorway', 'trunk', 'primary', 'secondary', 'tertiary'): return w
    if str(r.get('subclass')) == 'link': return min(w, 5.0)
    acc = r.get('access_restrictions')
    oneway = acc is not None and not isinstance(acc, float) and any(
        a.get('access_type') == 'denied' and (a.get('when') or {}).get('heading') == 'backward' and a.get('between') is None for a in acc)
    return round(w * 0.6, 1) if oneway else w
RCLS = {'track': 'dirt', 'path': 'dirt', 'bridleway': 'dirt', 'footway': 'footway', 'cycleway': 'footway',
        'steps': 'paved', 'pedestrian': 'cobble'}
DRIVE = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'residential', 'unclassified', 'living_street', 'service'}
roads = []
for _, r in seg.iterrows():
    c = r['class']
    if r.subtype == 'rail': roads.append((0, r.geom, 'rail', 3, sname(r))); continue
    if c in RW: w_ = rwidth(r); roads.append((w_, r.geom, c, w_, sname(r)))
# ---------------- mosty a lávky: výška mostovky a nájezdy ----------------
# Mostovka nad silnicí/tratí/vodou musí mít podjezdnou výšku. Aby to nebyl „skokánek“, nájezd se rozloží do navazující
# cesty se stoupáním nejvýš GRADE (silnice 6 %, trať 3 %, lávka 8 %); ta část cesty se pak nekreslí do terénu, ale jako mostovka.
FOOT = {'footway', 'path', 'cycleway', 'steps', 'pedestrian', 'bridleway', 'track'}
GRADE = {'road': 0.06, 'rail': 0.03, 'foot': 0.08}
roads = [(a_, l_, c_, w_, n_) for a_, g_, c_, w_, n_ in roads for l_ in lines(g_)]
_under = shapely.STRtree([g for _, g, c, w, _n in roads])
_water = shapely.unary_union([r.geom.buffer(4) for _, r in wat.iterrows()] or [Point(1e9, 1e9)])
_rused, _rcut = set(), {}                 # cesty nájezdů tohoto mostu; index -> odříznuté úseky [(a, b)]
def _approach(pt, dv, kind):
    """navazující cesty od konce mostu směrem dv (stejný druh), nejvýš 8 kusů / 200 m: [(index, směr, odkud)]
    směr True = po směru čáry od parametru t, False = proti směru od t (konec mostu může ležet i uprostřed cesty)"""
    out, tot = [], 0.0
    for _ in range(8):
        best = None
        for j in _under.query(Point(pt).buffer(1.0)):
            j = int(j)
            if j in _rused: continue
            _a, l_, c_, _w, _n = roads[j]
            if (c_ == 'rail') != (kind == 'rail') or (kind != 'rail' and (c_ in FOOT) != (kind == 'foot')): continue
            if l_.length < 0.5 or l_.distance(Point(pt)) > 1.0: continue
            t = l_.project(Point(pt))
            for fs in (True, False):
                rest = l_.length - t if fs else t
                if rest < 0.5: continue
                q = l_.interpolate(t + min(5.0, rest) if fs else t - min(5.0, rest)); pa = l_.interpolate(t)
                vx, vy = q.x - pa.x, q.y - pa.y; nv = math.hypot(vx, vy) or 1
                ca_ = (vx * dv[0] + vy * dv[1]) / nv
                if ca_ > {'road': 0.5, 'rail': 0.7, 'foot': -0.1}[kind] and (best is None or ca_ > best[0]): best = (ca_, j, fs, t)
        if best is None: break
        _, j, fs, t = best; _rused.add(j); out.append((j, fs, t))
        l_ = roads[j][1]; tot += (l_.length - t) if fs else t
        e = l_.coords[-1] if fs else l_.coords[0]; e2 = l_.interpolate(max(t, l_.length - 3)) if fs else l_.interpolate(min(t, 3.0))
        vx, vy = e[0] - e2.x, e[1] - e2.y; nv = math.hypot(vx, vy) or 1; dv = (vx / nv, vy / nv); pt = e[:2]
        if tot >= 200: break
    return out
def _oriented(j, fs, t):                   # souřadnice nájezdu ve směru od mostu
    l_ = roads[j][1]
    cs = [c_[:2] for c_ in (shapely.ops.substring(l_, t, l_.length) if fs else shapely.ops.substring(l_, 0, t)).coords]
    return cs if fs else cs[::-1]
def _cut(app, c):                          # odříznout z cest nájezdu délku c od mostu, zbytek zůstane v terénu
    for j, fs, t in app:
        l_ = roads[j][1]; avail = l_.length - t if fs else t
        a, b = (t, min(l_.length, t + c)) if fs else (max(0.0, t - c), t)
        if b - a > 0.3: _rcut.setdefault(j, []).append((a, b))
        c -= avail
        if c <= 0.3: break
# most bývá v datech rozdělený na víc kusů za sebou: spojit, ať mostovka mezi nimi nepřistává na zem
def _bkind(r): return 'rail' if r.subtype == 'rail' else 'foot' if r['class'] in FOOT else 'road'
_bg = {}
for g0, r in bridge_segs: _bg.setdefault(_bkind(r), []).append((g0, r))
bridge_segs = []
for _k, items in _bg.items():
    for l in lines(shapely.ops.linemerge(shapely.MultiLineString([q for g0, _r in items for q in lines(g0)]))):
        bridge_segs.append((l, max(items, key=lambda it: it[0].intersection(l.buffer(0.5)).length)[1]))
DECKS = []                                  # (čára, vzdálenosti, výšky mostovky, výšky terénu, šířka, druh, třída, název)
for g0, r in bridge_segs:
    for l in lines(g0):
        if not l.intersects(DRAWBOX): continue
        c = r['class']
        kind = 'rail' if r.subtype == 'rail' else 'foot' if c in FOOT else 'road'
        w = 4.5 if kind == 'rail' else rwidth(r)
        bc = [q[:2] for q in l.coords]
        def _dir(a, b):
            n_ = math.dist(a, b) or 1; return ((a[0] - b[0]) / n_, (a[1] - b[1]) / n_)
        _rused.clear()
        b0 = l.interpolate(min(3.0, l.length / 2)); b1 = l.interpolate(max(0.0, l.length - 3))
        A0 = _approach(bc[0], _dir(bc[0], (b0.x, b0.y)), kind); A1 = _approach(bc[-1], _dir(bc[-1], (b1.x, b1.y)), kind)
        pre = []
        for j, fs, t in A0: pre += _oriented(j, fs, t)[1:]
        post = []
        for j, fs, t in A1: post += _oriented(j, fs, t)[1:]
        ext = LineString(pre[::-1] + bc + post)
        ds0 = LineString(pre[::-1] + bc[:1]).length if pre else 0.0
        ds1 = ds0 + l.length
        lz = ext.segmentize(4.0); xy = np.array(lz.coords)[:, :2]
        d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]); L = d[-1]
        gz = ground(xy[:, 0], xy[:, 1])
        span = (d >= ds0 - 0.01) & (d <= ds1 + 0.01)
        req = gz + 0.15
        ends = l.boundary.buffer(1.5)
        cross = [_under.geometries[j] for j in _under.query(l) if _under.geometries[j].intersects(l) and not _under.geometries[j].intersects(ends)]
        cross = shapely.unary_union(cross).buffer(w / 2 + 3) if cross else None     # cesty a tratě, které vedou pod mostem
        for i, (x, y) in enumerate(xy):
            if not span[i]: continue
            pt = Point(x, y)
            if cross is not None and cross.contains(pt): req[i] = gz[i] + (6.0 if kind == 'rail' else 5.2)
            elif _water.contains(pt): req[i] = gz[i] + (1.0 if kind == 'foot' else 1.3)
        G = GRADE[kind]
        hr = req - gz                                  # požadovaná výška nad terénem; nájezd ji ubírá o G na metr (cesta dál kopíruje svah)
        hz = np.max(hr[None, :] - G * np.abs(d[:, None] - d[None, :]), axis=1)
        hz = np.minimum(hz, 0.15 + 0.2 * np.minimum(d, L - d))   # konce vždy na terénu (krátký nájezd = strmější)
        z = gz + np.maximum(hz, 0.15)
        si = np.where(span)[0]
        hull = []                                      # v rozpětí mostu horní konvexní obálka (bez propadů)
        for pt_ in zip(d[si], z[si]):
            while len(hull) >= 2 and (hull[-1][0] - hull[-2][0]) * (pt_[1] - hull[-2][1]) - (hull[-1][1] - hull[-2][1]) * (pt_[0] - hull[-2][0]) >= 0: hull.pop()
            hull.append(pt_)
        z[si] = np.interp(d[si], [q[0] for q in hull], [q[1] for q in hull])
        hi = np.where(z - gz > 0.25)[0]                 # oříznout nájezdy tam, kde mostovka leží na terénu
        i0 = min(max(int(hi[0]) - 1, 0) if len(hi) else si[0], si[0]); i1 = max(min(int(hi[-1]) + 1, len(d) - 1) if len(hi) else si[-1], si[-1])
        _cut(A0, ds0 - d[i0]); _cut(A1, d[i1] - ds1)
        lz = LineString(xy[i0:i1 + 1]); d = d[i0:i1 + 1] - d[i0]
        DECKS.append((lz, d, z[i0:i1 + 1], gz[i0:i1 + 1], w, kind, c, sname(r)))
print('mostů', len(DECKS), 'z toho lávek', sum(k[5] == 'foot' for k in DECKS), 'nájezdů', len(_rcut))
def _rest(l_, cuts):                       # cesta bez úseků pod mostovkami
    out, a = [], 0.0
    for c0, c1 in sorted(cuts):
        if c0 - a > 0.3: out.append(shapely.ops.substring(l_, a, c0))
        a = max(a, c1)
    if l_.length - a > 0.3: out.append(shapely.ops.substring(l_, a, l_.length))
    return out
roads = [(a_, q, c_, w_, n_) for j, (a_, l_, c_, w_, n_) in enumerate(roads) for q in (_rest(l_, _rcut[j]) if j in _rcut else [l_])]
roads.sort(key=lambda t: t[0])
# chodníky podél ulic tam, kde v datech není souběžný chodník a nestojí budova
SIDEWALK = {'trunk', 'primary', 'secondary', 'tertiary', 'residential', 'unclassified'}
foot_tree = shapely.STRtree([l for _, g, c, w, _n in roads if c in ('footway', 'pedestrian', 'path', 'cycleway', 'steps') for l in lines(g)])
bld_early = shapely.STRtree([p for g in load('building').geom for p in polys(g)])
for _, g, c, w, _n in roads:
    if c not in SIDEWALK: continue
    for l in lines(g.intersection(DRAWBOX)):
        for side in (-1, 1):
            try: off = l.offset_curve(side * (w / 2 + 1.05))
            except Exception: continue
            for ol in lines(off):
                d = 0.0
                while d < ol.length:
                    piece = shapely.ops.substring(ol, d, min(d + 6, ol.length))
                    mid = piece.interpolate(0.5, normalized=True)
                    fi = foot_tree.query(mid.buffer(3.0)); bi = bld_early.query(mid.buffer(0.9))
                    if len(fi) == 0 and len(bi) == 0 and piece.length > 0.5:
                        line(piece, C['footway'], 1.7, block=True)
                    d += 6
for _, g, c, w, _n in roads:
    if c != 'rail' and w >= 3.5: line(g, C['curb'], w + 0.7, block=True)
for _, g, c, w, _n in roads:
    if c == 'rail':
        line(g, C['dirt'], 3.2, block=True); line(g, C['rail'], 1.4); continue
    line(g, C[RCLS.get(c, 'road')], w, block=w >= 3)
# středová přerušovaná čára
for _, g, c, w, _n in roads:
    if c in ('trunk', 'primary', 'secondary', 'tertiary') or (c == 'motorway' and w >= 7):     # dálnice: čára mezi pruhy, ne na sjezdu
        for l in lines(g.intersection(AREA)):
            d = 0.0
            while d < l.length - 3:
                cd.line(P([l.interpolate(d).coords[0], l.interpolate(d + 3).coords[0]]), fill=C['marking'], width=1)
                d += 9

drive_lines = [(l, w, c, n) for _, g, c, w, n in roads if c in DRIVE for l in lines(g)]
# kanály (poklopy) a stopčáry u vjezdu na důležitější silnici
RANK = {'service': 0, 'living_street': 1, 'residential': 2, 'unclassified': 2, 'tertiary': 3, 'secondary': 4, 'primary': 5, 'trunk': 6, 'motorway': 7}
nodes_rank = {}
for l, w, c, n in drive_lines:
    for pt in (l.coords[0], l.coords[-1]):
        nodes_rank.setdefault((round(pt[0]), round(pt[1])), []).append(RANK[c])
for l, w, c, n in drive_lines:
    if l.length < 10 or not AREA.buffer(-5).intersects(l): continue
    if w >= 5:
        d = 18.0
        while d < l.length - 5:
            q = l.interpolate(d); a_ = l.interpolate(d - 1); b_ = l.interpolate(d + 1)
            ang = math.atan2(b_.y - a_.y, b_.x - a_.x)
            x, y = q.x + math.cos(ang - math.pi / 2) * w / 4, q.y + math.sin(ang - math.pi / 2) * w / 4
            r_ = 0.38 / TEX_RES
            cx_, cy_ = P([(x, y)])[0]
            cd.ellipse([cx_ - r_, cy_ - r_, cx_ + r_, cy_ + r_], fill=C['manhole'])
            d += 38
    for end in (0, 1):
        pt = l.coords[-1] if end else l.coords[0]
        ranks = nodes_rank.get((round(pt[0]), round(pt[1])), [])
        if len(ranks) < 3 or max(ranks) <= RANK[c] or RANK[c] == 0: continue
        d = l.length - 3.5 if end else 3.5
        q = l.interpolate(d); a_ = l.interpolate(max(0, d - 1)); b_ = l.interpolate(min(l.length, d + 1))
        tx, ty = (b_.x - a_.x), (b_.y - a_.y); tl = math.hypot(tx, ty) or 1; tx, ty = tx / tl, ty / tl
        if not end: tx, ty = -tx, -ty            # směr jízdy ke křižovatce
        rx, ry = ty, -tx                          # pravá strana
        cd.line(P([(q.x, q.y), (q.x + rx * (w / 2 - 0.3), q.y + ry * (w / 2 - 0.3))]), fill=C['marking'], width=max(1, round(0.45 / TEX_RES)))
all_lines = [(l, w, c, n) for _, g, c, w, n in roads if c != 'rail' for l in lines(g)]

def deck_line(D):
    lz, d, z = D[0], D[1], D[2]
    return [[round(x, 1), round(y, 1), round(float(h), 2)] for (x, y), h in zip(np.array(lz.coords)[:, :2], z)]
drive_tree = shapely.STRtree([l for l, *_ in drive_lines])
all_tree = shapely.STRtree([l for l, *_ in all_lines])


def nearest_dir(pt, tree, items, maxd):
    i = tree.nearest(pt)
    if i is None: return None
    l = items[i][0]
    if l.distance(pt) > maxd: return None
    d = l.project(pt)
    a = l.interpolate(max(0, d - 1)); b = l.interpolate(min(l.length, d + 1))
    return math.atan2(b.y - a.y, b.x - a.x), items[i]


# přechody pro chodce (zebry)
for _, r in inf.iterrows():
    if r['class'] != 'crossing' or not AREA.contains(r.geom): continue
    nd_ = nearest_dir(r.geom, drive_tree, drive_lines, 6)
    if not nd_: continue
    ang, (l, w, *_rest) = nd_
    q = l.interpolate(l.project(r.geom))
    ux, uy = math.cos(ang), math.sin(ang); vx, vy = -uy, ux
    k = -w / 2 + 0.5
    while k < w / 2 - 0.3:
        cx, cy = q.x + vx * k, q.y + vy * k
        corners = [(cx + ux * s * 1.5 + vx * t * 0.25, cy + uy * s * 1.5 + vy * t * 0.25) for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        cd.polygon(P(corners), fill=C['marking'])
        k += 1.0

# parkovací stání + zaparkovaná auta
cars = []
for pid, p in parkings:
    if p.area < 120: continue
    crng = random.Random(f'{pid}{wkey(p.centroid.x, p.centroid.y)}')
    o = p.minimum_rotated_rectangle
    oc = [np.array(c) for c in list(o.exterior.coords)[:4]]
    e1, e2 = oc[1] - oc[0], oc[3] - oc[0]
    if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1; oc = [oc[0], oc[3], oc[2], oc[1]]
    L, W = np.linalg.norm(e1), np.linalg.norm(e2)
    u, v = e1 / L, e2 / W
    rows = [(0, 5)] if W < 11 else [(0, 5), (W - 5, W)]
    if W >= 22: rows += [(W / 2 - 5, W / 2), (W / 2, W / 2 + 5)]
    pp = shapely.prepared.prep(p.buffer(-0.3))
    for r0, r1 in rows:
        s = 0.5
        while s + 2.5 < L:
            a = oc[0] + u * s + v * r0; b = oc[0] + u * s + v * r1
            if pp.contains(Point(*a)) and pp.contains(Point(*b)):
                cd.line(P([tuple(a), tuple(b)]), fill=C['marking'], width=1)
            c = oc[0] + u * (s + 1.25) + v * (r0 + r1) / 2
            if pp.contains(Point(*c)) and crng.random() < 0.58:
                cars.append([float(c[0]), float(c[1]), math.atan2(v[1], v[0]), crng.randrange(8)])
            s += 2.5
# auta podél ulic
for l, w, c, n in drive_lines:
    if c not in ('residential', 'unclassified', 'living_street', 'tertiary'): continue
    d = 4.0
    crng = wrng(*l.coords[0], 'car')
    while d < l.length - 4:
        if crng.random() < 0.07:
            a = l.interpolate(d - 1); b = l.interpolate(d + 1)
            ang = math.atan2(b.y - a.y, b.x - a.x)
            off = w / 2 - 1.1
            q = l.interpolate(d)
            x, y = q.x + math.cos(ang - math.pi / 2) * off, q.y + math.sin(ang - math.pi / 2) * off
            if AREA.contains(Point(x, y)): cars.append([x, y, ang, crng.randrange(8)])
        d += 7

# ---------------- budovy ----------------
bld = load('building')
# jen budovy z OpenStreetMap: samotné odhady Microsoft ML Buildings jsou hlavně kůlny a garáže, často posunuté nebo zdvojené
bld = bld[bld.sources.map(lambda s: s is not None and any(x['dataset'] == 'OpenStreetMap' for x in s))].reset_index(drop=True)
for i, r in bld.iterrows():                             # výšky z upřesnění mají přednost před daty
    if 'h' in UP['b'].get(r.id, {}): bld.at[i, 'height'] = UP['b'][r.id]['h']
if len(bld) and UP['roofs']:                            # přístřešky nástupišť (nejsou v OSM jako budovy)
    t0 = bld.iloc[0]; rows = []
    for i, q in enumerate(UP['roofs']):
        g = LineString([to_local(lo, la) for la, lo in q['line']]).buffer(q['w'] / 2, cap_style='flat', join_style='mitre')
        rows.append({**t0.to_dict(), 'id': f'up-roof-{i}', 'class': 'roof', 'subtype': None, 'names': None, 'height': q['h'], 'num_floors': float('nan'), 'roof_shape': None, 'geom': g, 'geometry': None})
    bld = pd.concat([bld, pd.DataFrame(rows)], ignore_index=True)
WALLS = [(232, 220, 192), (241, 234, 216), (217, 201, 163), (230, 211, 179), (214, 208, 196), (239, 230, 207),
         (216, 185, 143), (226, 196, 160), (205, 214, 206)]
PANEL = [(232, 226, 208), (213, 217, 222), (236, 236, 230), (222, 224, 222), (226, 199, 154), (169, 195, 201), (238, 232, 218), (204, 210, 214)]   # sídliště hlavně bílá a světle šedá
ROOF_P = [(168, 68, 47), (150, 60, 44), (142, 58, 42), (107, 59, 47), (74, 74, 82), (63, 74, 90), (156, 90, 60), (128, 52, 40)]
ROOF_F = [(141, 141, 141), (122, 122, 122), (156, 154, 146), (110, 112, 116)]
PITCHED_CLS = {'church', 'house', 'detached', 'semidetached_house', 'residential', 'terrace', 'farm', 'chapel', 'bungalow',
               'villa', 'cabin', 'religious', 'civic', 'hut', 'barn', 'farm_auxiliary', 'kindergarten', 'allotment_house'}
FARM_CLS = {'barn', 'farm_auxiliary', 'cowshed', 'stable', 'sty'}    # stodoly, kravíny: hala s nízkou sedlovou střechou
FLAT_CLS = {'garage', 'garages', 'apartments', 'industrial', 'warehouse', 'commercial', 'retail', 'school',
            'university', 'hospital', 'office', 'carport', 'parking', 'service', 'transportation', 'sports_hall',
            'sports_centre', 'greenhouse', 'roof'}
ROOF_MAP = {'gabled': 'gabled', 'hipped': 'hipped', 'half_hipped': 'hipped', 'pyramidal': 'pyramidal',
            'saltbox': 'gabled', 'gambrel': 'gabled', 'skillion': 'gabled', 'mansard': 'hipped', 'flat': 'flat',
            'round': 'flat', 'dome': 'pyramidal'}
CLS_CZ = {'house': 'rodinný dům', 'detached': 'rodinný dům', 'residential': 'obytný dům', 'apartments': 'bytový dům',
          'garage': 'garáž', 'garages': 'garáže', 'industrial': 'průmyslová budova', 'warehouse': 'sklad',
          'civic': 'občanská stavba', 'school': 'škola', 'university': 'univerzita', 'retail': 'obchod',
          'commercial': 'komerční budova', 'church': 'kostel', 'chapel': 'kaple', 'kindergarten': 'mateřská škola',
          'shed': 'kůlna', 'barn': 'stodola', 'service': 'technická budova', 'office': 'kanceláře',
          'semidetached_house': 'dvojdům', 'terrace': 'řadový dům', 'bungalow': 'bungalov', 'hotel': 'hotel',
          'dormitory': 'kolej', 'hospital': 'zdravotnictví', 'greenhouse': 'skleník', 'roof': 'přístřešek',
          'farm_auxiliary': 'hospodářská budova', 'farm': 'statek', 'cowshed': 'kravín', 'stable': 'stáj', 'sty': 'chlév',
          'cabin': 'chata', 'hut': 'chatka', 'allotment_house': 'zahradní chatka', 'static_caravan': 'mobilní dům',
          'transportation': 'dopravní stavba', 'train_station': 'nádraží', 'shelter': 'přístřešek', 'carport': 'přístřešek pro auto',
          'transformer_tower': 'trafostanice', 'fire_station': 'hasičská zbrojnice', 'religious': 'náboženská stavba',
          'villa': 'vila', 'sports_hall': 'sportovní hala', 'parking': 'parkovací dům', 'government': 'úřad', 'public': 'veřejná budova',
          'college': 'vyšší odborná škola', 'kiosk': 'stánek', 'toilets': 'toalety', 'pavilion': 'altán', 'bunker': 'bunkr'}


def hexcol(s, fallback):
    if not isinstance(s, str): return fallback
    s = s.strip()
    if s.startswith('#') and len(s) == 7:
        try: return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError: return fallback
    named = {'white': (240, 238, 232), 'red': (168, 68, 47), 'brown': (110, 70, 50), 'grey': (130, 130, 130),
             'gray': (130, 130, 130), 'black': (60, 60, 64), 'yellow': (232, 208, 140), 'beige': (226, 210, 176),
             'darkred': (128, 46, 36), 'green': (80, 120, 80), 'blue': (80, 100, 140), 'orange': (204, 120, 60)}
    return named.get(s.lower(), fallback)


def max_rect(M):
    """největší obdélník jedniček v binární matici -> (r0, c0, r1, c1) včetně"""
    best = (0, None); h = np.zeros(M.shape[1], int)
    for r in range(M.shape[0]):
        h = np.where(M[r], h + 1, 0)
        st = []
        for c in range(M.shape[1] + 1):
            cur = h[c] if c < M.shape[1] else 0
            start = c
            while st and st[-1][1] >= cur:
                s0, hh = st.pop()
                if hh * (c - s0) > best[0]: best = (hh * (c - s0), (r - hh + 1, s0, r, c - 1))
                start = s0
            st.append((start, cur))
    return best[1]


def roof_parts(p, ang):
    """rozloží půdorys na obdélníky (tvar L/T/U) -> [[cx, cy, úhel, L, W], ...]"""
    cs = 0.6
    c0 = p.centroid
    q = shapely.affinity.rotate(p, -ang, origin=c0, use_radians=True)
    minx, miny, maxx, maxy = q.bounds
    xs = np.arange(minx + cs / 2, maxx, cs); ys = np.arange(miny + cs / 2, maxy, cs)
    if len(xs) < 3 or len(ys) < 3: return []
    XX, YY = np.meshgrid(xs, ys)
    M = shapely.contains_xy(q, XX, YY)
    total = M.sum(); out = []
    for _ in range(4):
        r = max_rect(M)
        if r is None: break
        r0, c0_, r1, c1 = r
        area = (r1 - r0 + 1) * (c1 - c0_ + 1)
        if area < max(0.14 * total, 20 / cs ** 2): break
        w_, h_ = (c1 - c0_ + 1) * cs, (r1 - r0 + 1) * cs
        if min(w_, h_) < 3.0: M[r0:r1 + 1, c0_:c1 + 1] = False; continue
        cx = (xs[c0_] + xs[c1]) / 2; cy = (ys[r0] + ys[r1]) / 2
        pt = shapely.affinity.rotate(Point(cx, cy), ang, origin=c0, use_radians=True)
        a = ang if w_ >= h_ else ang + math.pi / 2
        out.append([round(pt.x, 2), round(pt.y, 2), round(a, 4), round(max(w_, h_), 2), round(min(w_, h_), 2)])
        M[r0:r1 + 1, c0_:c1 + 1] = False
    return out


addr = load('address')
addr = addr[addr.geom.map(lambda g: EXT_P.contains(g))]
addr_pts = list(addr.geom)
addr_tree = shapely.STRtree(addr_pts)
# adresa bez ulice (vesnice, osady): název osady + číslo popisné, např. „Chlum 74“
addr_txt = [f"{s} {n}" if isinstance(s, str) else f"{c} {n}" if isinstance(c, str) else str(n) for s, n, c in zip(addr.street, addr.number, addr.postal_city)]

buildings, binfo, chimneys = [], [], []
bmask = Image.new('L', (TWE, TWE), 0); bmd = ImageDraw.Draw(bmask)
# centrum a další městské čtverce: činžáky v sevřených blocích (dům sdílí zeď se sousedem) mají šikmé střechy;
# rozhoduje čtverec, kterému budova patří (těžiště), aby vypadala stejně i v sousedním čtverci


# ---------------- dominanty (assets/dominanty.json, popis podle fotek) ----------------
DOMS = [d for d in json.load(open('assets/dominanty.json', encoding='utf-8')) if d.get('spec')] if os.path.exists('assets/dominanty.json') else []
for d in DOMS:
    d['xy'] = [float(v) for v in to_local(d['lon'], d['lat'])]
    if 'face' in d['spec']: d['spec']['face_xy'] = [float(v) for v in to_local(d['spec']['face'][1], d['spec']['face'][0])]
DOM_BY_ID = {}                       # Overture id budovy -> dominanta


def match_doms():
    """budova dominanty = obsahuje bod, jinak největší do 25 m; u 'names' všechny budovy s tím názvem; 'glass' = všechny v okruhu"""
    idx = {i: r for i, r in zip(range(len(bld)), bld.itertuples())}
    geoms = list(bld.geom)
    tree = shapely.STRtree(geoms)
    for d in DOMS:
        sp = d['spec']; pt = Point(*d['xy'])
        if sp.get('only'): continue
        if 'names' in sp:
            for k, r in idx.items():
                if isinstance(r.names, dict) and (r.names.get('primary') or '').startswith(sp['names']): DOM_BY_ID[r.id] = d
            continue
        if 'glass' in sp:
            for k in tree.query(pt.buffer(sp['glass'])): DOM_BY_ID.setdefault(idx[k].id, {**d, 'spec': {'rc': [176, 204, 214], 'r': 'flat', 'win': 14}})
            continue
        hit = [k for k in tree.query(pt.buffer(25)) if geoms[k].area > 60]
        inside = [k for k in hit if geoms[k].buffer(1.0).contains(pt)]
        cand = inside or sorted(hit, key=lambda k: -geoms[k].area)
        if cand: DOM_BY_ID[idx[cand[0]].id] = d


def dom_pos(p, at, sp, d, w, side=0.0, t=0.5, inset=0.6):
    """poloha věže / komína na půdorysu: světová strana, průčelí k bodu 'face', střed, podél osy, nebo bod dominanty"""
    c = p.centroid
    if at == 'pt': return d['xy'][0], d['xy'][1], 0.0
    o = p.minimum_rotated_rectangle; cs = list(o.exterior.coords)[:3]
    e1 = np.subtract(cs[1], cs[0]); e2 = np.subtract(cs[2], cs[1])
    ax = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2; L = float(np.linalg.norm(ax)); ax = ax / L
    ang = math.atan2(ax[1], ax[0])
    if at == 'c': return c.x + ax[0] * side, c.y + ax[1] * side, ang
    if at == 'along': return c.x + ax[0] * (t - 0.5) * L, c.y + ax[1] * (t - 0.5) * L, ang
    if at in ('front', 'back'):
        # průčelí = strana opsaného obdélníku natočená k bodu 'face' (náměstí); věže v řadě podél ní, odsazené dovnitř
        fx, fy = sp.get('face_xy', [c.x, c.y - 50])
        if at == 'back': fx, fy = 2 * c.x - fx, 2 * c.y - fy
        rc = list(o.exterior.coords)[:4]
        best = None
        for k in range(4):
            a0, a1 = np.array(rc[k]), np.array(rc[(k + 1) % 4]); m = (a0 + a1) / 2
            nrm = m - np.array([c.x, c.y]); nl = np.linalg.norm(nrm) or 1; nrm /= nl
            dv = np.array([fx - c.x, fy - c.y]); dv /= (np.linalg.norm(dv) or 1)
            sc = float(nrm @ dv)
            if best is None or sc > best[0]: best = (sc, a0, a1, m, nrm)
        _, a0, a1, m, nrm = best
        tg = (a1 - a0) / (np.linalg.norm(a1 - a0) or 1)
        x, y = m - nrm * (w / 2 + inset) + tg * side
        return float(x), float(y), math.atan2(tg[1], tg[0])
    dirs = {'n': (0, 1), 's': (0, -1), 'e': (1, 0), 'w': (-1, 0), 'ne': (1, 1), 'nw': (-1, 1), 'se': (1, -1), 'sw': (-1, -1)}
    vx, vy = dirs[at]; vl = math.hypot(vx, vy); vx, vy = vx / vl, vy / vl
    pts = np.array(p.exterior.coords); k = int(np.argmax(pts[:, 0] * vx + pts[:, 1] * vy))
    x, y = pts[k]
    inside = Point(x - vx * (w / 2 + 0.4), y - vy * (w / 2 + 0.4))
    x, y = (inside.x, inside.y) if p.contains(inside) else (x + (c.x - x) * 0.15, y + (c.y - y) * 0.15)
    return x + ax[0] * side, y + ax[1] * side, ang


def facade_points(p, sp, ls):
    """body na stranách opsaného obdélníku (průčelí k bodu 'face', 'left', 'right' při pohledu na průčelí) -> [x, y, úhel ven];
    'sides' = posuny od středu průčelí v m, nebo 'n' bodů rovnoměrně včetně nároží"""
    c = p.centroid; o = p.minimum_rotated_rectangle; rc = list(o.exterior.coords)[:4]
    fx, fy = sp.get('face_xy', [c.x, c.y - 50]); dv = np.array([fx - c.x, fy - c.y]); dv /= (np.linalg.norm(dv) or 1)
    eds = []
    for k in range(4):
        a0, a1 = np.array(rc[k]), np.array(rc[(k + 1) % 4]); m = (a0 + a1) / 2
        nrm = m - np.array([c.x, c.y]); nrm /= (np.linalg.norm(nrm) or 1); eds.append((float(nrm @ dv), a0, a1, nrm))
    fi = max(range(4), key=lambda k: eds[k][0])
    named = {'front': eds[fi], 'left': eds[(fi + 1) % 4], 'right': eds[(fi + 3) % 4]}
    for nm, dv_ in (('n', (0, 1)), ('s', (0, -1)), ('e', (1, 0)), ('w', (-1, 0))):     # nebo podle světové strany
        named[nm] = eds[max(range(4), key=lambda k: float(eds[k][3] @ np.array(dv_)))]
    out = []
    for nm in ls.get('on', ['front']):
        _, a0, a1, nrm = named[nm]; L = float(np.linalg.norm(a1 - a0)); tg = (a1 - a0) / (L or 1); m = (a0 + a1) / 2
        if ls.get('sides') is not None and nm == 'front': sides = ls['sides']
        elif ls.get('sp'):                 # mezi okenní osy (stejné rozteče jako okna v shaderu: osy vystředěné na stěně)
            nw = math.floor((L - 0.6) / ls['sp']); off = (L - nw * ls['sp']) / 2; sides = [off + k * ls['sp'] - L / 2 for k in range(nw + 1)]
        else: sides = [(-0.5 + i / (ls.get('n', 2) - 1)) * (L - ls.get('w', 0.6)) for i in range(ls.get('n', 2))]
        for sd in sides:
            x, y = m + tg * sd
            out.append([round(float(x), 2), round(float(y), 2), round(math.atan2(nrm[1], nrm[0]), 4)])
    return out


def unhole(p):
    """budova s dvorem -> kusy bez děr (renderer kreslí jen vnější obrys); řez vede dvorem napříč delší osou"""
    if not p.is_valid: p = p.buffer(0)
    out, todo = [], polys(p)
    while todo:
        q = todo.pop()
        hs = [h for h in q.interiors if Polygon(h).area > 4]          # malé světlíky se zaplní
        if not hs or len(out) > 40: out.append(Polygon(q.exterior)); continue
        c = Polygon(hs[0]).centroid
        cs = list(q.minimum_rotated_rectangle.exterior.coords)[:3]
        e1 = np.subtract(cs[1], cs[0]); e2 = np.subtract(cs[2], cs[1])
        a = e2 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e1
        a = a / (np.linalg.norm(a) or 1) * 1e4
        cut = LineString([(c.x - a[0], c.y - a[1]), (c.x + a[0], c.y + a[1])])
        parts = [g for g in polys(shapely.ops.split(q, cut)) if g.area > 1]
        if len(parts) < 2: out.append(Polygon(q.exterior)); continue
        todo += parts
    return out


def L_obb_ok(p):
    # obří nebo hodně protáhlé bloky (haly, obchodní domy) nejsou činžák
    o = p.minimum_rotated_rectangle; cs = list(o.exterior.coords)[:3]
    a, b = math.dist(cs[0], cs[1]), math.dist(cs[1], cs[2])
    return max(a, b) < 70

match_doms()
print('dominant napárováno na budovy', len(DOM_BY_ID))
bpolys, bh = [], []
bdmp = []
for i_, g, h_, f_ in zip(bld.id, bld.geom, bld.height, bld.num_floors):
    for p in polys(g):
        bdmp.append(DMP[i_][0] if i_ in DMP else None)
        bpolys.append(p); bh.append(h_ if h_ == h_ and h_ else (f_ * 2.9 if f_ == f_ and f_ else None))
btree = shapely.STRtree(bpolys)
bcent = shapely.STRtree([p.centroid for p in bpolys]); barea = np.array([p.area for p in bpolys])


def density(pt, r=70.0):
    """podíl zastavěné plochy v okruhu r (podle těžišť budov); činžovní bloky ~0,25, vesnice ~0,06"""
    q = pt.buffer(r)
    return float(barea[[i for i in bcent.query(q) if q.contains(bcent.geometries[i])]].sum()) / q.area


def group_med(pb, med):
    """společná změřená výška navazujících sekcí"""
    seen, todo = set(), [i for i in btree.query(pb) if bpolys[i].intersects(pb)]
    while todo and len(seen) < 60:
        i = todo.pop()
        if i in seen: continue
        seen.add(i); q = bpolys[i].buffer(0.8)
        todo += [j for j in btree.query(q) if j not in seen and bpolys[j].intersects(q)]
    v = [bdmp[i] for i in seen if bdmp[i]]
    return float(np.median(v)) if v else med


def rect_dims(p):
    cs = list(p.minimum_rotated_rectangle.exterior.coords)[:3]
    a, b = math.dist(cs[0], cs[1]), math.dist(cs[1], cs[2])
    return p.area / max(a * b, 1e-6), min(a, b)


def group_key(pb):
    """klíč skupiny navazujících budov (nejmenší souřadnice) – stejná barva sekcí jednoho domu ve všech čtvercích"""
    seen, todo = set(), [i for i in btree.query(pb) if bpolys[i].intersects(pb)]
    while todo and len(seen) < 60:
        i = todo.pop()
        if i in seen: continue
        seen.add(i); q = bpolys[i].buffer(0.8)
        todo += [j for j in btree.query(q) if j not in seen and bpolys[j].intersects(q)]
    m = min((bpolys[i].bounds[0], bpolys[i].bounds[1]) for i in seen)
    return wkey(*m)


def panel_like(pb):
    """sekce panelového domu: navazující přesné obdélníky se stejnou hloubkou a výškou (činžáky jsou nepravidelné)"""
    nb_ = [i for i in btree.query(pb) if bpolys[i].intersects(pb)]
    if len(nb_) < 2: return False
    dims = [rect_dims(bpolys[i]) for i in nb_]
    if min(f for f, _ in dims) < 0.95: return False
    ws = [w for _, w in dims]
    if max(ws) - min(ws) > 1.5 or min(ws) < 8: return False
    hs = [bh[i] for i in nb_ if bh[i]]
    if hs and (max(hs) - min(hs) > 3.5 or min(hs) < 11): return False
    return True
def garage_row(p):
    """řada aspoň 3 navazujících malých budov bez typu (do 45 m²) = garáže (OSM je často nemá označené)"""
    seen, todo = set(), [i for i in btree.query(p.buffer(0.8)) if barea[i] < 45 and bpolys[i].intersects(p.buffer(0.8))]
    while todo and len(seen) < 3:
        i = todo.pop()
        if i in seen: continue
        seen.add(i); q = bpolys[i].buffer(0.8)
        todo += [j for j in btree.query(q) if j not in seen and barea[j] < 45 and bpolys[j].intersects(q)]
    return len(seen) >= 3
def zone_split(pieces, zones):
    """části dominanty podle zón ze satelitu (pořadí = priorita) -> (díly, zóna dílu nebo None)"""
    out, zof = [], []
    for p in pieces:
        rem = p
        for z in zones:
            if '_P' not in z: z['_P'] = Polygon([to_local(lon, lat) for lat, lon in z['poly']]).buffer(0)
            q = rem.intersection(z['_P'])
            for g in polys(q):
                for u in unhole(g):
                    if u.area > 4: out.append(u); zof.append(z)
            rem = rem.difference(z['_P'])
        for g in polys(rem):
            for u in unhole(g):
                if u.area > 4: out.append(u); zof.append(None)
    return out, zof


ROOF_CARS = []


def roof_deco(p, deco, top, key):
    """výbava střechy z popisu dominanty -> [[druh, x0, y0, x1, y1, šířka, výška, r, g, b], ...]
    druh 0 = shedy (řady pilové střechy), 1 = pás (světlík, rám skla), 2 = fotovoltaika, 3 = valený oblouk,
    4 = sedlový hřeben (střecha ze štítů, nástavec) s pásem světlíků; 'park' přidá auta na střechu.
    Čáry: 'lines' (lat, lon), 'rel' (zlomky delší a kratší osy půdorysu), 'axis': n (n pruhů podél delší osy), jinak řady 'a' + 'gap'"""
    out = []
    o = p.minimum_rotated_rectangle; oc = list(o.exterior.coords)[:3]
    e1 = np.subtract(oc[1], oc[0]); e2 = np.subtract(oc[2], oc[1])
    if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1
    OL, OW = float(np.linalg.norm(e1)), float(np.linalg.norm(e2)); ou, ov = e1 / OL, e2 / OW
    O0 = np.array(o.centroid.coords[0]) - ou * OL / 2 - ov * OW / 2
    rel = lambda s_, t_: tuple(O0 + ou * s_ * OL + ov * t_ * OW)
    for d in deco:
        k = d['k']
        if 'axis' in d:
            n = d['axis']; d = {**d, 'w': OW / n * d.get('fill', 1.0)}
            segs = [LineString([rel(-0.1, (i + 0.5) / n), rel(1.1, (i + 0.5) / n)]) for i in range(n)]
        elif 'rel' in d:
            segs = [LineString([rel(a_, b_), rel(c_, d_)]) for a_, b_, c_, d_ in d['rel']]
        elif 'lines' in d:
            segs = [LineString([to_local(l[1], l[0]), to_local(l[3], l[2])]) for l in d['lines']]
        else:
            a = math.radians(d['a']) if 'a' in d else math.atan2(ou[1], ou[0]); c = p.centroid; R = math.sqrt(p.area) * 3 + 50
            ux, uy, nx, ny = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
            segs = [LineString([(c.x + nx * t - ux * R, c.y + ny * t - uy * R), (c.x + nx * t + ux * R, c.y + ny * t + uy * R)])
                    for t in np.arange(-R + d['gap'] / 2, R, d['gap'])]
        inner = p.buffer(-max(0.6, d.get('w', 0) / 2 + 0.3) if k == 'solar' else -0.6)   # panely nesmí přečnívat přes okraj
        for sg in segs:
            for q in polys_lines(sg.intersection(inner)):
                if q.length < 1.5: continue
                (x0, y0), (x1, y1) = q.coords[0], q.coords[-1]
                if k == 'park':
                    L = q.length; ux, uy = (x1 - x0) / L, (y1 - y0) / L
                    rng = random.Random(key + f'{x0:.0f}{y0:.0f}')
                    for side in (-1, 1):
                        for t in np.arange(1.6, L - 1.6, 2.6):
                            cx, cy = x0 + ux * t - uy * side * 4.2, y0 + uy * t + ux * side * 4.2
                            if inner.contains(Point(cx, cy)) and rng.random() < d.get('p', 0.72):
                                ROOF_CARS.append([cx, cy, top, math.atan2(ux, -uy) + (math.pi if rng.random() < 0.5 else 0), rng.randrange(8)])
                    for side in (-1, 1):       # vodorovné značení: okraj řady stání a čáry mezi stáními
                        out.append([1, round(x0 - uy * side * 6.6, 2), round(y0 + ux * side * 6.6, 2), round(x1 - uy * side * 6.6, 2),
                                    round(y1 + ux * side * 6.6, 2), 0.25, 0.04, 226, 226, 220])
                        for t in np.arange(0.3, L - 0.3, 2.6):
                            ax_, ay_ = x0 + ux * t - uy * side * 1.8, y0 + uy * t + ux * side * 1.8
                            bx_, by_ = x0 + ux * t - uy * side * 6.6, y0 + uy * t + ux * side * 6.6
                            if inner.contains(Point(ax_, ay_)) and inner.contains(Point(bx_, by_)):
                                out.append([1, round(ax_, 2), round(ay_, 2), round(bx_, 2), round(by_, 2), 0.2, 0.04, 222, 222, 216])
                    continue
                kk = {'saw': 0, 'strip': 1, 'solar': 2, 'barrel': 3, 'ridge': 4}[k]
                w = d.get('w', d.get('gap', 4))
                cs = d.get('cs', [d.get('c', [200, 200, 200])]); col = cs[len(out) % len(cs)]
                out.append([kk, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2), round(w, 2), d.get('h', 0.6), *col, *d.get('c2', [])])
    return out


def polys_lines(g):
    if g.is_empty: return []
    if g.geom_type == 'LineString': return [g]
    return [q for q in getattr(g, 'geoms', []) if q.geom_type == 'LineString']


TEN_WALLS = [(232, 206, 150), (226, 186, 160), (206, 214, 180), (236, 226, 200), (214, 196, 170), (204, 202, 196),
             (238, 214, 170), (220, 176, 146), (196, 208, 214), (230, 220, 210)]
TEN_ROOFS = [(70, 72, 80), (96, 64, 52), (150, 62, 44), (60, 64, 72), (128, 60, 46), (84, 88, 96)]
# výlohy: budovy s obchodem, restaurací nebo službou podle Overture places (bod v půdorysu, jinak nejbližší budova do 8 m)
SHOP_TOP = ('shopping', 'food_and_drink')                         # Overture taxonomy: obchody a gastro
SHOP_NOT = ('brewery', 'gas_station', 'warehouse_club_store', 'vehicle_dealer', 'manufacturer')
SHOP_SVC = ('personal_or_beauty_service', 'financial_service', 'bank_or_credit_union', 'travel_service', 'printing_service',
            'shipping_or_delivery_service', 'pharmacy_and_drug_store', 'wellness_service', 'vision_or_eye_care_clinic')
SHOP_IDS = set()
if os.path.exists('data/places.parquet'):
    _bg = list(bld.geom); _bt = shapely.STRtree(_bg); _bid = list(bld.id)
    for q in pq.read_table('data/places.parquet', columns=['geometry', 'basic_category', 'taxonomy', 'confidence']).to_pylist():
        cat = q['basic_category'] or ''; top = ((q['taxonomy'] or {}).get('hierarchy') or [''])[0]
        if (q['confidence'] or 0) < 0.6 or cat in SHOP_NOT or not (top in SHOP_TOP or cat in SHOP_SVC): continue
        _pt = shapely.from_wkb(q['geometry']); _P = Point(*(float(v) for v in to_local(_pt.x, _pt.y)))
        hit = [k for k in _bt.query(_P, predicate='intersects')] or sorted(_bt.query(_P, predicate='dwithin', distance=8), key=lambda k: _bg[k].distance(_P))[:1]
        SHOP_IDS.update(_bid[k] for k in hit)
print('budov s obchodem', len(SHOP_IDS))
squares = [r.geom for _, r in lu.iterrows() if (sname(r) or '').startswith('náměstí doktora Edvarda Beneše')]
GND_DOM = {}
for _, r in bld.iterrows():
    if r.is_underground is True: continue
    pieces = [u for g in polys(r.geom) for u in unhole(g)]
    dom0 = DOM_BY_ID.get(r.id)
    zof = [None] * len(pieces); gnd_dom = None
    if dom0 and dom0['spec'].get('zones'):
        xy_ = np.array([c for q in pieces for c in q.exterior.coords]); gnd_dom = float(ground(xy_[:, 0], xy_[:, 1]).mean())   # jedna úroveň pro všechny díly
        GND_DOM[dom0['name']] = gnd_dom
        pieces, zof = zone_split(pieces, dom0['spec']['zones'])
    tw_piece = None
    if dom0 and pieces:
        pt0 = Point(*dom0['xy']); inside = [k for k, q in enumerate(pieces) if q.buffer(2).contains(pt0)]
        tw_piece = inside[0] if inside else max(range(len(pieces)), key=lambda k: pieces[k].area)
    for pi, p in enumerate(pieces):
        if not EXT.intersects(p): continue
        cen = p.centroid
        own = AREA.contains(cen)
        if not own and not CLIP.contains(cen): continue         # těžiště mimo mapu: budova nikomu nepatří
        if not CLIP.buffer(-1.0).contains(p): p = p.intersection(CLIP.buffer(-1.0))   # ořez jen na okraji mapy
        if p.geom_type != 'Polygon': continue
        URBAN = True        # Hořice: i centrum (čtverec 0,0) je sevřená městská zástavba; v Liberci byl centrální čtverec vyřazený
        p = p.simplify(0.2)
        if p.area < 6 or not p.is_valid: continue
        p = shapely.geometry.polygon.orient(p, 1.0)
        bid = len(buildings)
        c = r['class'] if isinstance(r['class'], str) else ''
        nm = sname(r)
        key = r.id; area = p.area
        if not c and area < 45 and garage_row(p): c = 'garages'
        floors = r.num_floors if r.num_floors == r.num_floors and r.num_floors else None
        hgt = r.height if r.height == r.height and r.height else None
        lm = None
        if c == 'chapel': lm = 'chapel'
        if c in ('church', 'cathedral') and area > 60: lm = 'church'       # každý kostel má věž
        _dm = DOM_BY_ID.get(r.id)
        if _dm and _dm['spec'].get('lm'): lm = _dm['spec']['lm']          # vlastní model z dominanty (např. radnice)
        hits = addr_tree.query(p.buffer(1.5))
        adrs = sorted({addr_txt[i] for i in hits if p.buffer(1.5).contains(addr_pts[i])})
        rs = ROOF_MAP.get(r.roof_shape if isinstance(r.roof_shape, str) else '', None)
        # paneláky a vyšší bytové domy mají ploché střechy
        if not lm and (c == 'apartments' or (floors and floors >= 5) or (hgt and hgt >= 14)): rs = 'flat'
        tenement = panel = False
        if URBAN and not lm:
            pb = p.buffer(0.8)
            attached = sum(1 for i in btree.query(pb) if bpolys[i].intersects(pb)) >= 2
            tall = (floors and floors >= 9) or (hgt and hgt >= 28)
            if attached and panel_like(pb): rs = 'flat'; panel = True
            elif attached and not tall and area < 1500 and L_obb_ok(p) and density(cen) >= 0.12 and c in ('', 'residential', 'apartments', 'house', 'civic', 'commercial', 'retail', 'hotel', 'office', 'yes', 'school'):
                tenement = True
                rs0 = ROOF_MAP.get(r.roof_shape if isinstance(r.roof_shape, str) else '', None)
                rs = rs0 if rs0 and rs0 != 'flat' else ('gabled' if h01(key + 't') < 0.75 else 'hipped')   # podle fotek centra: hlavně sedlové se štíty
        # tvar střechy podle měření DMP: plochá střecha má vršek (p90) skoro stejně vysoko jako medián, šikmá má hřeben výš
        if not lm and r.id in DMP and area > 80 and DMP[r.id][0] >= 6 and DMP[r.id][1] - DMP[r.id][0] < 0.4:
            rs = 'flat'
            if tenement or (DMP[r.id][0] >= 11 and c in ('', 'residential', 'apartments', 'yes')): tenement = False; panel = True
        if lm == 'radnice': rs = 'hipped'
        if URBAN and not lm and rs not in (None, 'flat') and area > 2500: rs = 'flat'
        if c == 'roof': rs = 'flat'                     # přístřešek (nástupiště, zastávka): střecha na sloupech
        so = SAT['b'].get(r.id, {})
        dom = DOM_BY_ID.get(r.id); dsp = dom['spec'] if dom else None
        if dsp:
            lm = 'dom'; tenement = panel = False
            if 'r' in dsp: rs = 'flat' if dsp['r'] == 'flat' else dsp['r']
        if not lm and so.get('rs') == 'flat': rs = 'flat'; tenement = False
        # šikmá ze snímku jen u menších domů: velké a vysoké budovy mají na snímku vlastní stín a techniku (falešný hřeben)
        if not lm and not panel and so.get('rs') == 'pitched' and rs in (None, 'flat') and area < 700 and c not in FLAT_CLS \
                and not (floors and floors >= 6) and not (hgt and hgt >= 18):
            rs = 'hipped' if h01(key + 't') < 0.6 else 'gabled'
        if rs is None:
            if c in FLAT_CLS or area > 650: rs = 'flat'
            elif c in PITCHED_CLS or (not c and 25 < area < 400): rs = 'gabled' if h01(key) < 0.62 else 'hipped'
            else: rs = 'flat'
        if not lm and so.get('r'): rs = so['r']; tenement = panel = False
        # zemědělské haly a kruhové půdorysy (sila, nádrže, jímky) nejsou činžáky ani paneláky
        farm = c in FARM_CLS and area > 150 and not lm
        rnd = not lm and area < 600 and 4 * math.pi * area / p.length ** 2 > 0.88
        if rnd: rs = 'flat'; tenement = panel = False
        elif farm:
            tenement = panel = False
            if not (r.id in DMP and DMP[r.id][1] - DMP[r.id][0] < 0.3): rs = 'gabled'
        obb = p.minimum_rotated_rectangle
        oc = list(obb.exterior.coords)[:4]
        e1 = np.subtract(oc[1], oc[0]); e2 = np.subtract(oc[2], oc[1])
        if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1
        L, Wd = float(np.linalg.norm(e1)), float(np.linalg.norm(e2))
        ang = math.atan2(e1[1], e1[0])
        parts = []
        if rs != 'flat':
            parts = [[round(obb.centroid.x, 2), round(obb.centroid.y, 2), round(ang, 4), round(L, 2), round(Wd, 2)]] \
                if area / max(obb.area, 1) > 0.9 else roof_parts(p, ang)
            if not parts: rs = 'flat'
        roofh = min(max(pp[4] for pp in parts) / 2 * math.tan(math.radians(40)), 7.0) if parts else 0.0
        if farm or (c in ('industrial', 'warehouse') and area > 400 and not lm): roofh = min(roofh, 3.5)   # velké haly: mírný sklon
        if tenement:
            roofh = min(roofh, 6.5)
            total = (hgt if hgt and hgt > 9 else (floors or (2 if area < 130 else 4)) * 3.2 + 0.6) + roofh * 0.6   # malé řadovky 2 patra
        elif hgt: total = hgt
        elif floors: total = floors * 2.9 + 0.4 + roofh * 0.8
        else:
            total = {'garage': 2.6, 'garages': 2.6, 'shed': 2.4, 'carport': 2.4, 'apartments': 16, 'industrial': 7,
                     'warehouse': 7, 'school': 12, 'university': 14, 'civic': 9, 'commercial': 9, 'retail': 6}.get(c)
            if total is None: total = 2.8 if area < 30 else (6.0 + roofh) if area < 300 else 9.0
        if dsp and 'rh' in dsp and parts: roofh = float(dsp['rh'])
        eave = max(2.2, total - roofh) if rs != 'flat' else max(2.2, total)
        if dsp and 'h' in dsp: eave = float(dsp['h'])
        xs_, ys_ = np.array(p.exterior.coords).T
        zs = ground(xs_, ys_)
        base, gnd = float(zs.min()) - 0.6, float(zs.mean())
        if c in ('garage', 'garages', 'carport', 'shed'): style = 2
        elif c == 'apartments' or (floors and floors >= 4) or total > 11: style = 1
        elif c in ('industrial', 'warehouse', 'commercial', 'retail'): style = 3
        else: style = 0
        if tenement: style = 11
        if panel: style = 1
        if farm: style = 15
        if rnd: style = 5
        if r.id in DMP and not dsp and not lm and c not in ('garage', 'garages', 'carport', 'shed', 'roof'):
            med, top = DMP[r.id]
            if panel: med = group_med(p.buffer(0.8), med)   # sekce jednoho paneláku stejně vysoké
            if rs == 'flat': total = eave = med
            else:
                # šikmá střecha: body laseru leží rovnoměrně mezi okapem a hřebenem -> výška střechy ~ 2,5 × (p90 − medián)
                roofh = min(roofh, max(1.5, 2.5 * (top - med)))
                total = max(top, med + 1); eave = max(2.2, total - roofh)
        if c == 'roof': style = 12; total = hgt if hgt and hgt < 12 else 4.5; eave = total
        if c in ('retail', 'commercial') and area > 1500 and not tenement: style = 3     # obchodní centra: pásy oken
        pal = TEN_WALLS if style == 11 else PANEL if style == 1 else WALLS
        wc = hexcol(r.facade_color, pal[int(h01((group_key(p.buffer(0.8)) if panel else key) + 'w') * 1000) % len(pal)])
        if style == 2: wc = (196, 190, 178)
        if rnd: wc = (200, 198, 192)                    # silo / nádrž: beton
        rp = ROOF_F if rs == 'flat' else TEN_ROOFS if tenement else ROOF_P
        rc = hexcol(r.roof_color, rp[int(h01(key + 'r') * 1000) % len(rp)])
        if 'rc' in so and not lm: rc = tuple(so['rc'])
        if 'wc' in so and not lm: wc = tuple(so['wc'])
        if dsp:
            if 'wc' in dsp: wc = tuple(dsp['wc'])
            if 'rc' in dsp: rc = tuple(dsp['rc'])
            if 'win' in dsp: style = dsp['win']
        ring = [[round(x, 2), round(y, 2)] for x, y in list(p.exterior.coords)[:-1]]
        b = {'p': ring, 'b': round(base, 2), 'g': round(gnd, 2), 'e': round(gnd + eave, 2), 'r': rs, 'rh': round(roofh, 2),
             'parts': parts, 'wc': wc, 'rc': rc, 's': style, 'id': bid}
        if lm: b['lm'] = lm
        if not lm and style in (0, 1, 11):
            # výška patra = zeď / počet podlaží na zdi (podlaží z RÚIAN přes OSM; podkroví ve šikmé střeše se nepočítá)
            n = floors
            if n and rs != 'flat' and eave / n < 2.5: n -= 1
            if not n: n = round(eave / 3.0)
            if n >= 1: b['fh'] = round(min(4.6, max(2.4, eave / n)), 2)
            if style in (0, 1) and (r.id in SHOP_IDS or so.get('shop')): b['shop'] = 1     # obchod v přízemí: výlohy (places nebo upřesnění)
        if dsp:
            b['dn'] = dom['name']
            if dsp.get('canopy'): b['cn'] = dsp['canopy']
            for blk in dsp.get('blocks', []):
                o = p.minimum_rotated_rectangle; cs = list(o.exterior.coords)[:3]
                e1 = np.subtract(cs[1], cs[0]); e2 = np.subtract(cs[2], cs[1])
                if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1
                bl, bw = float(np.linalg.norm(e1)) * blk['l'], float(np.linalg.norm(e2)) * blk['w']
                b.setdefault('blk', []).append([round(p.centroid.x, 2), round(p.centroid.y, 2), round(math.atan2(e1[1], e1[0]), 4),
                                                round(bl, 2), round(bw, 2), round(gnd + blk['h'], 2), list(blk.get('rc', rc))])
            for tw in (dsp.get('towers', []) if pi == tw_piece else []):
                x, y, a = dom_pos(pieces[pi], tw['at'], dsp, dom, tw['w'], tw.get('side', 0.0), tw.get('t', 0.5), tw.get('in', 0.6))
                if not own and not AREA.contains(Point(x, y)) and not EXT.contains(Point(x, y)): continue
                b.setdefault('tw', []).append({'x': round(x, 2), 'y': round(y, 2), 'a': round(a, 4), 'w': tw['w'], 'h': round(gnd + tw['h'], 2),
                    'to': round(gnd + tw.get('to', tw['h']), 2), 'top': tw['top'], 'tc': tw.get('tc', [90, 96, 104]), 'wc': list(tw.get('wc', wc)),
                    'clock': tw.get('clock', 0), 'round': tw.get('round', 0), 'stone': tw.get('stone', 0), 'ruin': tw.get('ruin', 0), 'b': round(base, 2)})
            if dsp.get('lisenes') and pi == tw_piece:            # lizény (pilastry) s fiálami nebo vázami nad římsou
                ls = dsp['lisenes']; b['lisp'] = [ls.get('w', 0.6), ls.get('d', 0.25), round(gnd + ls.get('z0', 0.0), 2), round(gnd + eave + ls.get('dz', 0.0), 2),
                                                  ls.get('top', 'none'), ls.get('th', 1.2), list(ls.get('col', wc))]
                b['lis'] = facade_points(p, dsp, ls)
            for bl in (dsp.get('balconies', []) if pi == tw_piece else []):   # balkony na průčelí
                fx_, fy_, fa = facade_points(p, dsp, {'sides': [bl['side']], 'on': ['front']})[0]
                b.setdefault('bal', []).append([fx_, fy_, fa, bl.get('w', 3.0), round(gnd + bl['z'], 2), bl.get('d', 0.9)])
            if dsp.get('skylights') and pi == tw_piece:          # světlíky na ploché střeše
                inner = p.buffer(-6.0)
                if not inner.is_empty:
                    minx, miny, maxx, maxy = inner.bounds; b['rt'] = []
                    for gx in np.arange(minx, maxx, 13):
                        for gy in np.arange(miny, maxy, 11):
                            if inner.contains(Point(gx, gy)) and h01(f'sk{gx:.0f}{gy:.0f}') < 0.45: b['rt'].append([round(gx, 2), round(gy, 2), 1, round(ang, 3)])
            for ch in (dsp.get('chimneys', []) if pi == tw_piece else []):
                x, y, a = dom_pos(p, ch['at'], dsp, dom, ch['r'] * 2, ch.get('side', 0.0))
                b.setdefault('chs', []).append([round(x, 2), round(y, 2), round(base, 2), round(gnd + ch['h'], 2), ch['r'], list(ch['col'])])
        if lm == 'church' and 'Antonín' not in (nm or ''):
            # menší kostel = nižší loď a věž (velké kostely mají výchozí 11 / 31 / 47 m)
            tw = min(31.0, 10 + L * 0.5)
            b['cht'] = [round(min(11.0, 5 + L * 0.15), 1), round(tw, 1), round(tw + min(16.0, 4 + L * 0.3), 1)]
        # technologie a světlíky na velkých plochých střechách (jen městské čtverce)
        if URBAN and rs == 'flat' and style != 2 and area > 300 and not lm and not farm and not rnd:
            inner = p.buffer(-2.0)
            if not inner.is_empty:
                trng = random.Random(key)
                minx, miny, maxx, maxy = inner.bounds
                rt = []
                for _ in range(int(min(90, area / 60))):
                    x_, y_ = trng.uniform(minx, maxx), trng.uniform(miny, maxy)
                    if inner.contains(Point(x_, y_)):
                        rt.append([round(x_, 2), round(y_, 2), trng.choice((0, 0, 1, 1, 2)), round(ang, 3)])
                b['rt'] = rt
        if lm == 'radnice' and squares:
            # průčelí s věžemi = bod obrysu nejblíž náměstí, tečna podél fasády
            sq = squares[0].centroid
            near = shapely.ops.nearest_points(p.exterior, sq)[0]
            dd = p.exterior.project(near)
            a_ = p.exterior.interpolate(dd - 2); b_ = p.exterior.interpolate(dd + 2)
            b['rad'] = [round(near.x, 2), round(near.y, 2), round(math.atan2(b_.y - a_.y, b_.x - a_.x), 4),
                        round(math.atan2(sq.y - near.y, sq.x - near.x), 4)]
            b['wc'] = (226, 212, 176); b['rc'] = (58, 66, 72); b['e'] = round(gnd + 17.5, 2)
        # komín u rodinných domů a vikýř u větších střech
        if parts and style in (0, 1, 11) and not lm:
            cx, cy, a, pl, pw = max(parts, key=lambda q: q[3] * q[4])
            s_ = (h01(key + 'c') - 0.5) * pl * 0.6; t_ = (0.12 + 0.18 * h01(key + 'd')) * pw * (1 if h01(key + 'e') > 0.5 else -1)
            hx, hy = cx + math.cos(a) * s_ - math.sin(a) * t_, cy + math.sin(a) * s_ + math.cos(a) * t_
            ridge = gnd + eave + roofh
            top = ridge - abs(t_) / (pw / 2 + 0.45) * roofh + 1.1
            b['ch'] = [round(hx, 2), round(hy, 2), round(top, 2)]
            if own: chimneys.append([hx, hy, top, bid])
            if rs == 'gabled' and pl > 9 and pw > 6.5 and h01(key + 'v') < 0.55:
                b['dm'] = 1
        if dsp:                                  # díly podle zón ze satelitu a výbava střechy
            z = zof[pi]; zs = z or {}
            g0 = gnd if gnd_dom is None else gnd_dom
            b['e'] = round(g0 + zs.get('h', b['e'] - gnd), 2); b['g'] = round(g0, 2)
            if 'rc' in zs: b['rc'] = tuple(zs['rc'])
            if 'wc' in zs: b['wc'] = tuple(zs['wc'])
            if 'win' in zs: b['s'] = zs['win']
            if z: b['r'] = 'flat'; b['parts'] = []; b['rh'] = 0.0; b.pop('rt', None)
            deco = zs.get('deco') if z else dsp.get('deco')
            if deco: b['dz'] = roof_deco(p, deco, b['e'], key + str(pi))
        if not own: b['gh'] = 1                  # budova sousedního čtverce (jen kvůli švu)
        buildings.append(b)
        bmd.polygon(P(p.exterior.coords), fill=255)
        if not own: continue
        lon, lat = to_lonlat(p.centroid.x, p.centroid.y)
        # obrys při najetí myší: skutečný vršek vykreslené budovy (zóny, výbava střechy, věže, modely)
        top_, ring_ = gnd + eave + roofh, ring
        if dsp:
            top_ = b['e'] + (b['rh'] if b['r'] != 'flat' else 0.0)
            if b.get('dz'): top_ = max(top_, b['e'] + max(q[6] for q in b['dz']))
        binfo.append({'id': bid, 'oid': key, 'name': nm, 'cls': CLS_CZ.get(c, 'budova'), 'floors': floors,
                      'h': round(total, 1), 'roof': rs, 'addr': adrs, 'lon': round(float(lon), 6), 'lat': round(float(lat), 6),
                      'ring': ring_, 'top': round(top_, 2), 'base': round(base, 2)})
# samostatné stavby dominant (zřícenina, rozhledna): jen věž na bodu dominanty
for d in DOMS:
    sp = d['spec']
    if not sp.get('only'): continue
    x, y = d['xy']
    if not EXT_P.contains(Point(x, y)): continue
    own = AREA.contains(Point(x, y)); g = float(ground(x, y))
    b = {'p': [[round(x - 0.1, 2), round(y - 0.1, 2)], [round(x + 0.1, 2), round(y - 0.1, 2)], [round(x + 0.1, 2), round(y + 0.1, 2)]],
         'b': round(g - 0.6, 2), 'g': round(g, 2), 'e': round(g, 2), 'r': 'flat', 'rh': 0.0, 'parts': [], 'wc': (150, 146, 136), 'rc': (150, 146, 136),
         's': 4, 'id': len(buildings), 'lm': 'dom', 'dn': d['name'], 'only': 1, 'tw': []}
    if sp.get('model'):                  # vlastní model z render.html (natočení: k bodu 'face', jinak 'a' ve stupních)
        fxy = sp.get('face_xy')
        b['mdl'] = sp['model']; b['ma'] = round(math.atan2(fxy[1] - y, fxy[0] - x) if fxy else math.radians(sp.get('a', 0.0)), 4)
        b['wc'] = tuple(sp.get('wc', b['wc']))
    for tw in sp.get('towers', []):
        b['tw'].append({'x': round(x, 2), 'y': round(y, 2), 'a': 0.0, 'w': tw['w'], 'h': round(g + tw['h'], 2), 'to': round(g + tw.get('to', tw['h']), 2),
                        'top': tw['top'], 'tc': tw.get('tc', [90, 96, 104]), 'wc': list(sp.get('wc', [150, 146, 136])), 'clock': tw.get('clock', 0), 'round': tw.get('round', 0),
                        'stone': tw.get('stone', 0), 'ruin': tw.get('ruin', 0), 'b': round(g - 0.6, 2)})
    if not own: b['gh'] = 1
    buildings.append(b)
    r_ = 14 + sp['towers'][0]['w'] if sp.get('towers') else sp.get('clear', 6); nd.ellipse([*P([(x - r_, y + r_)])[0], *P([(x + r_, y - r_)])[0]], fill=255)   # bez stromů kolem
# hráze nádrží: kamenná zeď s korunou (v datech polygon), budova bez oken
for _, r in inf.iterrows():
    if r['class'] != 'dam' or r.geom.geom_type not in ('Polygon', 'MultiPolygon'): continue
    for p in polys(r.geom):
        if not EXT.intersects(p) or p.area < 20: continue
        p = shapely.geometry.polygon.orient(p.simplify(0.3), 1.0)
        xs_, ys_ = np.array(p.exterior.coords).T; zs = ground(xs_, ys_)
        own = AREA.contains(p.centroid)
        b = {'p': [[round(x, 2), round(y, 2)] for x, y in list(p.exterior.coords)[:-1]], 'b': round(float(zs.min()) - 0.6, 2),
             'g': round(float(zs.mean()), 2), 'e': round(float(zs.max()) + 1.2, 2), 'r': 'flat', 'rh': 0.0, 'parts': [],
             'wc': (150, 146, 136), 'rc': (172, 168, 158), 's': 5, 'id': len(buildings)}
        if not own: b['gh'] = 1
        for d in DOMS:                     # dominanta na hrázi (věžičky přehrady Harcov)
            if 'towers' in d['spec'] and not d['spec'].get('only') and p.buffer(30).contains(Point(*d['xy'])) and r.id not in DOM_BY_ID:
                b['lm'] = 'dom'; b['dn'] = d['name']; b['tw'] = []
                for tw in d['spec']['towers']:
                    x, y, a = dom_pos(p, tw['at'], d['spec'], d, tw['w'], tw.get('side', 0.0), tw.get('t', 0.5))
                    g2 = float(ground(x, y))
                    b['tw'].append({'x': round(x, 2), 'y': round(y, 2), 'a': round(a, 4), 'w': tw['w'], 'h': round(b['e'] + tw['h'], 2),
                                    'to': round(b['e'] + tw.get('to', tw['h']), 2), 'top': tw['top'], 'tc': tw.get('tc', [90, 96, 104]),
                                    'wc': [150, 146, 136], 'clock': 0, 'round': tw.get('round', 0), 'stone': 1, 'ruin': 0, 'b': round(b['e'] - 0.5, 2)})
        buildings.append(b); bmd.polygon(P(p.exterior.coords), fill=255)
hk = [r.geom for _, r in lu.iterrows() if r['class'] == 'barracks' and 'Horní' in (sname(r) or '')]
if hk:
    gates = sorted([r.geom for _, r in inf.iterrows() if r['class'] == 'gate' and r.geom.geom_type == 'Point' and hk[0].buffer(5).contains(r.geom)], key=lambda g: g.x)[:2]
    if gates:
        gx_, gy_ = sum(g.x for g in gates) / len(gates), sum(g.y for g in gates) / len(gates)
        cand = [b for b in buildings if hk[0].contains(shapely.geometry.Polygon(b['p']).centroid) and shapely.geometry.Polygon(b['p']).area > 300]
        if cand:
            hq = min(cand, key=lambda b: shapely.geometry.Polygon(b['p']).centroid.distance(Point(gx_, gy_)))
            hq['lm'] = 'hq'; hq.pop('ch', None); hq.pop('dm', None)
            if not hq['parts']:
                pq_ = shapely.geometry.Polygon(hq['p']); o = pq_.minimum_rotated_rectangle; oc = list(o.exterior.coords)[:4]
                e1 = np.subtract(oc[1], oc[0]); e2 = np.subtract(oc[2], oc[1])
                if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1
                hq['parts'] = [[round(o.centroid.x, 2), round(o.centroid.y, 2), round(math.atan2(e1[1], e1[0]), 4), round(float(np.linalg.norm(e1)), 2), round(float(np.linalg.norm(e2)), 2)]]
                hq['r'] = 'hipped'; hq['rh'] = round(min(np.linalg.norm(e2) / 2 * math.tan(math.radians(40)), 7), 2)
                hq['e'] = round(hq['e'] - 0.0, 2)
            print('velitelství kasáren', hq['id'])
print('budov', len(buildings), 'z toho od sousedů', sum(1 for b in buildings if b.get('gh')), 'komínů', len(chimneys))

# ---------------- mobiliář, lampy, oplocení ----------------
props = {'bench': [], 'lamp': [], 'bin': [], 'recycling': [], 'shelter': [], 'tower': [], 'pole': []}
lamp_pts = []
for _, r in inf.iterrows():
    g = r.geom; k = r['class']
    if g.geom_type != 'Point' or not AREA.contains(g): continue
    z = float(ground(g.x, g.y))
    nd_ = nearest_dir(g, all_tree, all_lines, 15)
    ang = nd_[0] if nd_ else h01(wkey(g.x, g.y)) * 6.28
    if k == 'bench': props['bench'].append([g.x, g.y, z, ang])
    elif k == 'street_lamp': props['lamp'].append([g.x, g.y, z, ang]); lamp_pts.append(g)
    elif k in ('waste_basket', 'waste_disposal'): props['bin'].append([g.x, g.y, z, ang])
    elif k == 'recycling': props['recycling'].append([g.x, g.y, z, ang])
    elif k == 'power_tower': props['tower'].append([g.x, g.y, z, ang])
    elif k == 'power_pole': props['pole'].append([g.x, g.y, z, ang])
    elif k in ('bus_stop', 'platform'):
        if any(math.hypot(g.x - s[0], g.y - s[1]) < 14 for s in props['shelter']): continue
        nd2 = nearest_dir(g, drive_tree, drive_lines, 20)
        props['shelter'].append([g.x, g.y, z, nd2[0] if nd2 else ang])
# lampy podél ulic (v datech jich je jen pár desítek)
lamp_tree = shapely.STRtree(lamp_pts) if lamp_pts else None
bm_arr = np.asarray(bmask)
for l, w, c, n in drive_lines:
    if c in ('service', 'motorway'): continue          # dálnice bez lamp
    d, side = 12.0, 1
    while d < l.length - 5:
        q = l.interpolate(d)
        a_ = l.interpolate(max(0, d - 1)); b_ = l.interpolate(min(l.length, d + 1))
        ang = math.atan2(b_.y - a_.y, b_.x - a_.x)
        off = (w / 2 + 1.3) * side
        x, y = q.x + math.cos(ang + math.pi / 2) * off, q.y + math.sin(ang + math.pi / 2) * off
        px_, py_ = pix(x, y)
        ok = AREA.contains(Point(x, y)) and 0 <= px_ < TWE and 0 <= py_ < TWE and bm_arr[py_, px_] == 0
        ok = ok and len(btree.query(Point(x, y), predicate='dwithin', distance=40)) > 0   # jen v zástavbě, ne podél silnic v polích a lesích
        if ok and lamp_tree is not None:
            i = lamp_tree.nearest(Point(x, y)); ok = lamp_pts[i].distance(Point(x, y)) > 18
        if ok and not any(abs(x - s[0]) < 12 and abs(y - s[1]) < 12 for s in props['lamp'][-6:]):
            props['lamp'].append([x, y, float(ground(x, y)), ang])
        d += 34; side = -side

# popelnice před rodinnými domy, u chodníku na straně domu
props['wheelie'] = []
res_lines = [(l, w) for l, w, c, n in drive_lines if c in ('residential', 'unclassified', 'living_street', 'tertiary')]
res_tree = shapely.STRtree([l for l, w in res_lines])
for b in buildings:
    if b['s'] != 0 or b.get('lm') or b.get('gh'): continue
    pg = shapely.geometry.Polygon(b['p'])
    c0 = pg.centroid
    if pg.area < 50 or h01('bin' + wkey(c0.x, c0.y)) > 0.55: continue
    brng = wrng(c0.x, c0.y, 'bin')
    i = res_tree.nearest(c0)
    l, w = res_lines[i]
    if l.distance(c0) > 28: continue
    q = l.interpolate(l.project(c0))
    dx, dy = c0.x - q.x, c0.y - q.y; dl = math.hypot(dx, dy) or 1; dx, dy = dx / dl, dy / dl
    a_ = l.interpolate(max(0, l.project(q) - 1)); b_ = l.interpolate(min(l.length, l.project(q) + 1))
    ang = math.atan2(b_.y - a_.y, b_.x - a_.x)
    for k in range(brng.choice((1, 2, 2, 3))):
        sx = (k - 0.5) * 0.75
        x = q.x + dx * (w / 2 + 2.4) + math.cos(ang) * sx; y = q.y + dy * (w / 2 + 2.4) + math.sin(ang) * sx
        if AREA.contains(Point(x, y)):
            props['wheelie'].append([x, y, float(ground(x, y)), math.atan2(dy, dx), (0, 0, 1, 2)[k % 4] if k else brng.choice((0, 0, 3))])

lines_out = {'fence': [], 'hedge': [], 'wall': [], 'power': [], 'lift': []}
for _, r in inf.iterrows():
    k = r['class']
    kk = {'fence': 'fence', 'hedge': 'hedge', 'wall': 'wall', 'retaining_wall': 'wall', 'power_line': 'power', 'minor_line': 'power',
          'chair_lift': 'lift', 'drag_lift': 'lift', 't-bar': 'lift', 'platter': 'lift', 'cable_car': 'lift', 'gondola': 'lift'}.get(k)
    if not kk: continue
    for l in lines(r.geom.intersection(AREA)):
        l = l.segmentize(4.0)
        xs_, ys_ = np.array(l.coords).T
        lines_out[kk].append([[round(x, 2), round(y, 2), round(float(zz), 2)] for x, y, zz in zip(xs_, ys_, ground(xs_, ys_))])
for k in props: props[k] = [[round(v, 3) for v in q] for q in props[k]]
cars = [[round(x, 2), round(y, 2), round(float(ground(x, y)), 2), round(a, 3), ci] for x, y, a, ci in cars]
cars += [[round(x, 2), round(y, 2), round(z, 2), round(a, 3), ci] for x, y, z, a, ci in ROOF_CARS if AREA.contains(Point(x, y))]   # parkoviště na střechách
buses = []
for a, e, k in UP['buses']:
    x1, y1 = to_local(a[1], a[0]); x2, y2 = to_local(e[1], e[0]); x, y = (x1 + x2) / 2, (y1 + y2) / 2
    if AREA.contains(Point(x, y)): buses.append([round(x, 2), round(y, 2), round(float(ground(x, y)), 2), round(math.atan2(y2 - y1, x2 - x1), 3), k])
portals = [[round(x, 2), round(y, 2), round(float(ground(x, y)), 2), round(a, 3)] for x, y, a in portals if AREA.contains(Point(x, y))]
print({k: len(v) for k, v in props.items()}, 'aut', len(cars), {k: len(v) for k, v in lines_out.items()})

# ---------------- stromy ----------------
excl = np.maximum(np.asarray(notree), np.asarray(bmask.filter(ImageFilter.MaxFilter(13))))


def free(x, y):
    px_, py_ = pix(x, y)
    return 0 <= px_ < TWE and 0 <= py_ < TWE and excl[py_, px_] == 0


def free_v(xs, ys):
    px_ = ((xs + HE) / TEX_RES).astype(int); py_ = ((HE - ys) / TEX_RES).astype(int)
    ok = (px_ >= 0) & (px_ < TWE) & (py_ >= 0) & (py_ < TWE)
    out = np.zeros(len(xs), bool)
    out[ok] = excl[py_[ok], px_[ok]] == 0
    return out


trees = []


def scatter(g, spacing, conifer, hmin, hmax, kind=0):
    """stromy na světové mřížce s náhodným posunem podle indexu buňky -> sousední čtverce mají stejné stromy"""
    sid = int(round(spacing * 10))
    for p in polys(g.intersection(EXT)):
        minx, miny, maxx, maxy = p.bounds
        kx = np.arange(math.floor((minx + CX) / spacing), math.ceil((maxx + CX) / spacing) + 1)
        ky = np.arange(math.floor((miny + CY) / spacing), math.ceil((maxy + CY) / spacing) + 1)
        KX, KY = [a.ravel() for a in np.meshgrid(kx, ky)]
        tx = KX * spacing - CX + (ihash(KX, KY, sid, 1) - 0.5) * 0.9 * spacing
        ty = KY * spacing - CY + (ihash(KX, KY, sid, 2) - 0.5) * 0.9 * spacing
        m = shapely.contains_xy(p, tx, ty)
        m[m] = free_v(tx[m], ty[m])
        hh = hmin + ihash(KX, KY, sid, 3) * (hmax - hmin)
        cf = ihash(KX, KY, sid, 4) < conifer
        for i in np.nonzero(m)[0]:
            trees.append((float(tx[i]), float(ty[i]), float(hh[i]), 1 if cf[i] else 0, kind))


for _, r in land.iterrows():
    c = r['class']; g = r.geom
    if c in ('forest', 'wood') and g.geom_type != 'Point': scatter(g, 4.2, 0.55, 12, 24)
    elif c in ('scrub', 'shrub') and g.geom_type != 'Point': scatter(g, 5, 0.1, 2, 4, 1)
    elif c == 'tree' and g.geom_type == 'Point':
        if EXT_P.contains(g): trees.append((g.x, g.y, 8 + h01('t' + wkey(g.x, g.y)) * 8, 0, 0))
    elif c == 'tree_row':
        for l in lines(g):
            d = 0
            while d < l.length:
                q = l.interpolate(d)
                if EXT_P.contains(q): trees.append((q.x, q.y, 8 + h01('r' + wkey(q.x, q.y)) * 6, 0, 0))
                d += 7
DENS = {'park': 11, 'cemetery': 9, 'residential': 16, 'garden': 12, 'grass': 20, 'village_green': 18, 'school': 22,
        'recreation_ground': 20, 'allotments': 14, 'kindergarten': 14}
for _, r in lu.iterrows():
    s = DENS.get(r['class'])
    if s: scatter(r.geom, s, 0.25, 6, 15)
# řídké stromy všude kromě luk, polí a pastvin (tam ve skutečnosti stromy nejsou)
OPEN = shapely.unary_union([g for c, g in zip(lu['class'], lu.geom) if c in ('meadow', 'farmland', 'grass', 'village_green', 'downhill', 'pitch')]
                           + [g for c, g in zip(land['class'], land.geom) if c in ('grassland', 'meadow') and g.geom_type != 'Point'] + ZTTP + ZORNA)
scatter(EXT.difference(OPEN.buffer(0)) if not OPEN.is_empty else EXT, 26, 0.3, 6, 14)
if LESM is not None:                         # strom jen tam, kde je na ortofotu koruna; v zahradách a zástavbě pár řídkých i bez ní
    _sp = shapely.prepared.prep(shapely.unary_union(ZSAD + [r.geom for _, r in lu.iterrows() if r['class'] in ('residential', 'garden', 'allotments')]).buffer(0)
                                  .difference(shapely.unary_union(ZORNA + ZTTP).buffer(0)))
    def _ok(t):
        px_, py_ = pix(t[0], t[1])
        if not (0 <= px_ < TWE and 0 <= py_ < TWE) or LESM[py_, px_]: return True
        return h01('z' + wkey(t[0], t[1])) < 0.3 and _sp.contains(Point(t[0], t[1]))
    trees = [t for t in trees if _ok(t)]
for _o in OBL.values():                      # oblast podle skutečnosti: náhodné stromy pryč, místo nich změřené
    _q = shapely.prepared.prep(Polygon([to_local(lo, la) for la, lo in _o['poly']]))
    trees = [t for t in trees if not _q.contains(Point(t[0], t[1]))]
    for la, lo, hh, con in _o['trees']:
        x, y = (float(v) for v in to_local(lo, la))
        if EXT_P.contains(Point(x, y)) and free(x, y): trees.insert(0, (x, y, hh, con, 0))
seen = set(); T = []
for t in trees:
    k = (round((t[0] + CX) / 3.0), round((t[1] + CY) / 3.0))
    if k in seen: continue
    seen.add(k); T.append(t)
tz = ground(np.array([t[0] for t in T]), np.array([t[1] for t in T]))
trees = [[round(t[0], 2), round(t[1], 2), round(float(z), 2), round(t[2], 1), t[3], t[4]] for t, z in zip(T, tz)]
# stromy v lese nesmí zakrýt silnici (kamera geo.AZ pod úhlem geo.EL): strom mezi kamerou a silnicí se sníží, aby jeho koruna
# nesahala přes vozovku; když by měl být nižší než 5 m, skryje se (kind 2 – pořadí stromů v seznamu zůstává)
_rd = [l.buffer(w / 2 + 0.5) for _, g, c, w, _n in roads if c in ('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified')
       for l in lines(g.intersection(EXT.buffer(60)))]
_fo = [r.geom for _, r in land.iterrows() if r['class'] in ('forest', 'wood') and r.geom.geom_type in ('Polygon', 'MultiPolygon')]
if _rd and _fo and trees:
    _rdu = shapely.unary_union(_rd); _fou = shapely.unary_union(_fo).buffer(0); shapely.prepare(_rdu); shapely.prepare(_fou)
    _k = 1.0 / math.tan(geo.EL); _dx, _dy = -math.sin(geo.AZ), -math.cos(geo.AZ)   # po zemi od kamery
    _x = np.array([t[0] for t in trees]); _y = np.array([t[1] for t in trees]); _h = np.array([t[3] for t in trees])
    _reach = _h * _k + 2.5                                         # kam po zemi sahá koruna v pohledu kamery (+ poloměr koruny)
    _rays = shapely.linestrings(np.stack([np.stack([_x, _y], 1), np.stack([_x + _reach * _dx, _y + _reach * _dy], 1)], 1))
    _cand = np.nonzero(shapely.contains_xy(_fou, _x, _y) & shapely.intersects(_rays, _rdu))[0]
    _dist = shapely.distance(shapely.points(_x[_cand], _y[_cand]), shapely.intersection(_rays[_cand], _rdu))
    for i, dd in zip(_cand, _dist):
        hn = (dd - 2.5) / _k
        if hn < 5: trees[i][5] = 2
        elif hn < trees[i][3]: trees[i][3] = round(float(hn), 1)
    print('stromů sníženo/skryto u silnic v lese', len(_cand))
print('stromů', len(trees))

# ---------------- hřbitovy: hroby, náhrobky, sochy ----------------
# na hřbitovech prořídit stromy, aby byly vidět hroby (ostatní stromy zůstávají na místě)
cem_union = shapely.unary_union([r.geom for _, r in lu.iterrows() if r['class'] == 'cemetery'])
cem_prep = shapely.prepared.prep(cem_union)
# skryté stromy zůstávají v seznamu (kind = 2), aby se v rendereru neposunulo pořadí náhodných barev
# na hřbitovech prořídit stromy (70 % skrýt), aby byly vidět hroby
trees = [t[:5] + [2] if cem_prep.contains(Point(t[0], t[1])) and h01(wkey(t[0], t[1])) >= 0.3 else t for t in trees]
nograve = shapely.geometry.GeometryCollection()
trees = [t for t in trees if EXT_P.contains(Point(t[0], t[1]))]
print('skrytých stromů na hřbitovech', sum(t[5] == 2 for t in trees))
grng = random.Random(7)
graves, statues = [], []
tree_pts = shapely.STRtree([Point(t[0], t[1]) for t in trees if t[5] != 2])
blockers = shapely.unary_union([l.buffer(w / 2 + 0.9) for l, w, c, n in all_lines]
                               + [shapely.geometry.Polygon(b['p']).buffer(1.5) for b in buildings]
                               + [l.buffer(1.2) for _, r in inf.iterrows() if r['class'] in ('wall', 'fence') for l in lines(r.geom)])
for _, r in lu.iterrows():
    if r['class'] != 'cemetery': continue
    for p in polys(r.geom.intersection(AREA)):
        inner = p.buffer(-1.5)
        if inner.is_empty: continue
        pp = shapely.prepared.prep(inner.difference(blockers).difference(nograve.buffer(1.0)))
        o = p.minimum_rotated_rectangle
        oc = [np.array(q) for q in list(o.exterior.coords)[:4]]
        e1, e2 = oc[1] - oc[0], oc[3] - oc[0]
        if np.linalg.norm(e1) < np.linalg.norm(e2): e1, e2 = e2, e1; oc = [oc[0], oc[3], oc[2], oc[1]]
        L, W = np.linalg.norm(e1), np.linalg.norm(e2)
        u, v = e1 / L, e2 / W
        ang = math.atan2(v[1], v[0])                    # hrob leží podél kratší osy hřbitova
        jewish = 'Žid' in (sname(r) or '')
        row = 1.0
        while row + 2.2 < W:
            col = 0.8
            while col + 1.0 < L:
                c0 = oc[0] + u * (col + 0.5) + v * (row + 1.0)
                corners = [oc[0] + u * (col + a) + v * (row + b) for a, b in ((0, 0), (1.0, 0), (1.0, 2.0), (0, 2.0))]
                if all(pp.contains(Point(*q)) for q in corners) and grng.random() < (0.75 if jewish else 0.9):
                    near = tree_pts.query(Point(*c0).buffer(1.6))
                    if len(near) == 0:
                        if not jewish: cd.polygon(P([tuple(q) for q in corners]), fill=C['grave'])
                        head = oc[0] + u * (col + 0.5) + v * (row + 1.9)
                        graves.append([round(float(head[0]), 2), round(float(head[1]), 2), round(float(ground(*head)), 2),
                                       round(ang, 3), round(grng.uniform(0.55, 1.15) if not jewish else grng.uniform(0.5, 0.9), 2), grng.randrange(4)])
                col += 1.45
            row += 3.1
for _, r in inf.iterrows():
    g = r.geom
    if r['class'] == 'artwork' and g.geom_type == 'Point' and AREA.contains(g):
        # sochy na hřbitovech i jinde (Hořice = město soch); bod uvnitř budovy (reliéf, výzdoba) se vynechá
        if not any(bpolys[i].contains(g) for i in btree.query(g)):
            statues.append([round(g.x, 2), round(g.y, 2), round(float(ground(g.x, g.y)), 2), 0.0])
props['planter'] = props['slab'] = props['pavilion'] = props['shed'] = []
props['grave'] = graves
props['statue'] = statues
props['wheelie'] = [[round(v, 3) for v in q] for q in props['wheelie']]
print('hrobů', len(graves), 'soch', len(statues))

# ---------------- textury podle ročního období ----------------
SEASON_PAL = {
    'summer': {'grass': (106, 160, 74), 'garden': (118, 168, 80), 'park': (104, 164, 72), 'meadow': (140, 172, 86),
               'forest': (66, 112, 56), 'scrub': (92, 128, 62), 'pitch': (80, 156, 72), 'track': (178, 92, 72),
               'sand': (214, 192, 138), 'paved': (196, 186, 160), 'industrial': (156, 152, 142), 'parking': (120, 120, 124),
               'road': (92, 92, 98), 'marking': (236, 232, 214), 'curb': (176, 172, 162), 'dirt': (170, 146, 102),
               'water': (64, 124, 190), 'pool': (92, 196, 230), 'stream': (72, 132, 196), 'rail': (88, 84, 82),
               'cemetery': (88, 132, 70), 'wetland': (100, 146, 92), 'rock': (150, 146, 136), 'construction': (164, 132, 96),
               'footway': (214, 206, 186), 'pool_edge': (236, 240, 240), 'farmland': (168, 172, 96), 'cobble': (200, 188, 164), 'grave': (156, 152, 144), 'manhole': (66, 66, 70),
               'court_blue': (70, 112, 170), 'zone': (255, 0, 220), 'zone2': (0, 230, 230),
               'piste': (150, 178, 92), 'skijump': (98, 160, 150),
               'wheat': (214, 186, 106), 'wheat2': (194, 162, 86), 'rape': (150, 148, 82), 'rape2': (128, 126, 70),
               'maize': (80, 134, 58), 'maize2': (58, 106, 44), 'soil': (110, 142, 66), 'soil2': (128, 104, 70)},
}
sp = dict(SEASON_PAL['summer'])
sp.update({'grass': (122, 176, 80), 'garden': (132, 184, 86), 'park': (118, 180, 78), 'meadow': (150, 188, 90),
           'forest': (78, 128, 62), 'farmland': (132, 150, 90),
           'wheat': (104, 146, 72), 'wheat2': (88, 128, 62), 'rape': (238, 214, 60), 'rape2': (216, 194, 50),
           'maize': (138, 106, 74), 'maize2': (112, 86, 60), 'soil': (130, 98, 68), 'soil2': (106, 80, 56)})
SEASON_PAL['spring'] = sp
au = dict(SEASON_PAL['summer'])
au.update({'grass': (122, 154, 72), 'garden': (128, 158, 76), 'park': (120, 156, 70), 'meadow': (156, 160, 88),
           'forest': (92, 104, 58), 'scrub': (124, 116, 64), 'farmland': (176, 150, 96), 'cemetery': (104, 128, 70),
           'wheat': (204, 182, 126), 'wheat2': (164, 132, 88), 'rape': (112, 150, 74), 'rape2': (98, 134, 64),
           'maize': (152, 126, 86), 'maize2': (124, 98, 66), 'soil': (122, 92, 62), 'soil2': (98, 72, 50)})
SEASON_PAL['autumn'] = au
wi = dict(SEASON_PAL['summer'])
snow = (234, 239, 246)
wi.update({k: snow for k in ('grass', 'garden', 'park', 'meadow', 'pitch', 'sand', 'cemetery', 'farmland', 'construction', 'wetland', 'track', 'rock', 'piste', 'skijump',
                             'wheat', 'rape', 'maize', 'soil')})
wi.update({k: (212, 218, 228) for k in ('wheat2', 'rape2', 'maize2', 'soil2')})             # brázdy prosvítají sněhem
wi.update({'forest': (214, 222, 232), 'scrub': (220, 226, 234), 'paved': (222, 226, 232), 'industrial': (206, 210, 216),
           'parking': (176, 180, 188), 'road': (104, 106, 114), 'marking': (190, 192, 196), 'curb': (226, 230, 236),
           'dirt': (214, 214, 218), 'water': (186, 210, 228), 'pool': (150, 168, 184), 'stream': (58, 96, 136),
           'footway': (226, 230, 236), 'cobble': (206, 210, 218), 'pool_edge': (230, 234, 238), 'grave': (222, 226, 232), 'manhole': (90, 92, 98)})
SEASON_PAL['winter'] = wi

# kontrolní ohraničení zón upřesněných podle snímků (geo.SAT_OUTLINE, po kontrole vypnout)
if getattr(geo, 'SAT_OUTLINE', False):
    for fine, col in ((False, C['zone2']), (True, C['zone'])):     # tyrkys = jen barvy, purpur = barvy i typy střech
        zs = [Polygon(list(zip(*to_local(*np.array(z['ring']).T)))) for z in SAT['zones'] if bool(z.get('fine')) == fine]
        if not zs: continue
        for zp in polys(shapely.unary_union(zs)):
            cd.line(P(list(zp.exterior.coords)), fill=col, width=max(1, round(3.0 / TEX_RES)))
cls_a = np.asarray(cls)[MPX:MPX + TW, MPX:MPX + TW]     # textura jen vlastního čtverce
noise = np.random.default_rng(1).random((TW, TW), dtype=np.float32)
noise2 = np.random.default_rng(2).random((TW, TW), dtype=np.float32)
NATURAL = [C[k] for k in ('grass', 'garden', 'park', 'meadow', 'forest', 'scrub', 'cemetery', 'wetland', 'farmland',
                          'wheat', 'wheat2', 'rape', 'rape2', 'maize', 'maize2', 'soil', 'soil2')]
nat = np.isin(cls_a, NATURAL)
for season, pal in SEASON_PAL.items():
    lut = np.zeros((256, 3), np.uint8)
    for k, v in C.items(): lut[v] = pal[k]
    img = lut[cls_a].astype(np.int16)
    # pixel-art zrnitost trávy / sněhu
    dark = nat & (noise > 0.93); light = nat & (noise < 0.05)
    img[dark] -= 14 if season != 'winter' else 10
    img[light] += 14 if season != 'winter' else 6
    if season == 'spring':   # pampelišky a sedmikrásky
        img[nat & (noise2 < 0.006)] = (240, 214, 70)
        img[nat & (noise2 > 0.995)] = (246, 246, 240)
    if season == 'autumn':   # spadané listí
        img[nat & (noise2 < 0.006)] = (196, 120, 46)
        img[nat & (noise2 > 0.997)] = (170, 74, 40)
    if season == 'winter':   # sníh i na okrajích silnic
        road = cls_a == C['road']
        edge = road & (noise2 < 0.06)
        img[edge] = (200, 204, 212)
    asph = np.isin(cls_a, [C['road'], C['parking']])
    img[asph & (noise > 0.97)] += 8
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(f'{OUT}/ground_{season}.png')
    print('textura', season)

# světelná mapa pro noc (kužely světla lamp na zemi), 0,5 m/px
LR = 0.5; LW = int(2 * HALF / LR)
lm_img = np.zeros((LW, LW), np.float32)
R = 14
yy, xx = np.mgrid[-R * 2:R * 2 + 1, -R * 2:R * 2 + 1]
blob = np.clip(1 - np.hypot(xx, yy) / (R * 2), 0, 1) ** 1.6
for x, y, *_r in props['lamp']:
    cx, cy = int((x + HALF) / LR), int((HALF - y) / LR)
    x0, y0 = cx - R * 2, cy - R * 2
    xs0, ys0 = max(0, x0), max(0, y0); xs1, ys1 = min(LW, x0 + blob.shape[1]), min(LW, y0 + blob.shape[0])
    if xs1 <= xs0 or ys1 <= ys0: continue
    lm_img[ys0:ys1, xs0:xs1] += blob[ys0 - y0:ys1 - y0, xs0 - x0:xs1 - x0]
Image.fromarray((np.clip(lm_img, 0, 1) * 255).astype(np.uint8)).save(f'{OUT}/lightmap.png')

TOP = max([b['e'] + b['rh'] for b in buildings] + [t[2] + t[3] for t in trees] + [float(H.max())])
json.dump({'chunk': CHUNK, 'center': [CX, CY], 'half': HALF, 'step': STEP, 'n': N, 'hmin': HMIN, 'heights': [round(float(v), 2) for v in H.ravel()],
           'ms': MS, 'hext': [round(float(v), 2) for v in HX.ravel()], 'nb': NB, 'margin': geo.MARGIN, 'skirt': geo.skirt_edges(CHUNK),
           'floor': geo.FLOOR, 'top': round(TOP, 1),
           'buildings': buildings, 'trees': trees, 'props': props, 'cars': cars, 'buses': buses, 'portals': portals, 'lines': lines_out,
           'bridges': [{'p': [[*q, round(float(g_), 2)] for q, g_ in zip(deck_line(D), D[3])], 'w': D[4], 'k': D[5], 'c': D[6]} for D in DECKS if D[0].intersects(EXT)]},
          open(f'{OUT}/scene.json', 'w'), separators=(',', ':'))

# podklady pro interaktivní vrstvu prohlížeče
streets = {}
for l, w, c, n in all_lines:
    if n:
        streets.setdefault(n, []).append(l)
street_out = []
for n, ls in streets.items():
    m = shapely.MultiLineString(ls).intersection(AREA)
    if m.is_empty: continue
    pt = m.interpolate(0.5, normalized=True) if m.geom_type == 'LineString' else max(lines(m), key=lambda q: q.length).interpolate(0.5, normalized=True)
    street_out.append({'name': n, 'x': round(pt.x, 1), 'y': round(pt.y, 1), 'z': round(float(ground(pt.x, pt.y)), 1)})
graph = [{'p': [[round(x, 1), round(y, 1), round(float(z), 1)] for (x, y), z in zip(l.coords, ground(*np.array(l.coords).T))],
          'c': c, 'n': n} for l0, w, c, n in drive_lines for l in lines(l0.intersection(AREA.intersection(CLIP.buffer(-4)))) if l.length > 2]   # auta jen uvnitř mapy, přes šev ano
graph += [{'p': deck_line(D), 'c': D[6], 'n': D[7]} for D in DECKS if D[6] in DRIVE and AREA.intersects(D[0])]   # po mostech ve výšce mostovky
stops = [{'name': sname(r), 'x': r.geom.x, 'y': r.geom.y} for _, r in inf.iterrows()
         if r['class'] == 'bus_stop' and AREA.contains(r.geom) and sname(r)]
def zline(l):
    l = l.segmentize(3.0); xs_, ys_ = np.array(l.coords).T
    return [[round(x, 1), round(y, 1), round(float(z), 1)] for x, y, z in zip(xs_, ys_, ground(xs_, ys_))]
water_out = {'ponds': [], 'streams': []}
for _, r in wat.iterrows():
    g = r.geom.intersection(AREA)
    if g.is_empty: continue
    if g.geom_type in ('Polygon', 'MultiPolygon'):
        for p in polys(g):
            if p.area > 15: water_out['ponds'].append(zline(p.exterior) + ([] if r['class'] != 'swimming_pool' else []))
    elif r['class'] == 'river':
        water_out['streams'] += [zline(l) for l in lines(g)]
piste_out = []                                 # pro lyžaře v prohlížeči: vždy shora dolů
for l in PISTE:
    for q in lines(l.intersection(AREA)):
        if q.length < 30: continue
        z = zline(q); piste_out.append(z if z[0][2] >= z[-1][2] else z[::-1])
TRAM = [l for _, r in seg.iterrows() if r.subtype == 'rail' and r['class'] == 'tram' for l in lines(r.geom)]
tram_out = [zline(q) for l in TRAM for q in lines(l.intersection(AREA)) if q.length > 1] + \
           [deck_line(D) for D in DECKS if D[6] == 'tram' and AREA.intersects(D[0])]
_tt = shapely.STRtree(TRAM) if TRAM else None
tstop_out = [[round(r.geom.x, 1), round(r.geom.y, 1), sname(r)] for _, r in inf.iterrows() if _tt is not None and r['class'] == 'stop_position'
             and r.geom.geom_type == 'Point' and sname(r) and AREA.contains(r.geom) and len(_tt.query(r.geom.buffer(4)))]
walk_out = [zline(l) for l, w, c, n in all_lines if c in ('footway', 'path', 'pedestrian', 'cycleway') and l.intersects(AREA)
            for l in [l.intersection(AREA)] if l.geom_type == 'LineString' and l.length > 8]
json.dump({'buildings': binfo, 'chimneys': chimneys, 'streets': street_out, 'roads': graph, 'stops': stops, 'water': water_out, 'walks': walk_out, 'pistes': piste_out, 'trams': tram_out, 'tstops': tstop_out,
           'decks': [{'p': deck_line(D), 'w': D[4]} for D in DECKS if D[5] != 'foot' and AREA.intersects(D[0])],
           'lamps': props['lamp'], 'ground_small': [[round(float(ground(x, y)), 1) for x in np.linspace(-HALF, HALF, 81)]
                                                     for y in np.linspace(HALF, -HALF, 81)]},
          open(f'{OUT}/info_src.json', 'w'), separators=(',', ':'), ensure_ascii=False)
print('hotovo')
