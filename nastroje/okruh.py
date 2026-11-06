# Trasa závodního okruhu pro motorky v prohlížeči: python nastroje/okruh.py  ->  assets/okruh.json
# Hořice: 300 zatáček Gustava Havla, silnice II/501 (z Hořic k Lukavci), III/3003 (Dachovská spojka) a II/300 (Dachova –
# Maixnerova – Hankovo náměstí), délka 5,15 km, jezdí se po směru hodinových ručiček (cs.wikipedia.org/wiki/300_zatáček_Gustava_Havla,
# uzavírky podle Policie ČR). Silnice a odbočky z OpenStreetMap (Overpass API), trasa = nejkratší cesta mezi body WP v pořadí závodu.
import json, math, heapq, os, sys, urllib.parse, urllib.request
sys.path.insert(0, os.getcwd())
from geo import to_local

NAZEV = '300 zatáček Gustava Havla'
REFS = {'300', '501', '3003'}                       # čísla silnic okruhu (OSM ref)
BBOX = (50.355, 15.60, 50.395, 15.66)
WP = [(50.3703, 15.6368),                           # Hankovo náměstí
      (50.3790, 15.6287),                           # II/501 (Čsl. armády) do kopce k Vodojemu a Křížku
      (50.38452, 15.62665),                         # odbočka na Dachovskou spojku (III/3003)
      (50.38437, 15.63757),                         # konec spojky v Dachově
      (50.3800, 15.6395)]                           # II/300 Maixnerova zpět do Hořic
MOTOREK = 8

q = f'[out:json][timeout:90];way["highway"~"^(primary|secondary|tertiary|unclassified|residential)(_link)?$"]{BBOX};out body geom;'
req = urllib.request.Request('https://overpass-api.de/api/interpreter', data=urllib.parse.urlencode({'data': q}).encode(),
                             headers={'User-Agent': 'izomapa-horice/1.0 (https://github.com/svamberk/izometricke-horice)'})
d = json.load(urllib.request.urlopen(req, timeout=120))
pos, adj = {}, {}
for e in d['elements']:
    ns, g, t = e['nodes'], e['geometry'], e['tags']
    k = 1.0 if t.get('ref') in REFS or t['highway'].endswith('_link') else 4.0     # přednost silnicím okruhu
    for n, p in zip(ns, g): pos[n] = (*map(float, to_local(p['lon'], p['lat'])), p['lat'], p['lon'])
    for a, b in zip(ns, ns[1:]):
        w = math.dist(pos[a][:2], pos[b][:2]) * k
        adj.setdefault(a, []).append((b, w)); adj.setdefault(b, []).append((a, w))


def near(lat, lon):
    x, y = map(float, to_local(lon, lat))
    return min(pos, key=lambda n: (pos[n][0] - x) ** 2 + (pos[n][1] - y) ** 2)


def path(a, b):
    dist, prev, h = {a: 0}, {}, [(0, a)]
    while h:
        dd, u = heapq.heappop(h)
        if u == b: break
        if dd > dist[u]: continue
        for v, w in adj.get(u, []):
            if dd + w < dist.get(v, 1e18): dist[v] = dd + w; prev[v] = u; heapq.heappush(h, (dd + w, v))
    out = [b]
    while out[-1] != a: out.append(prev[out[-1]])
    return out[::-1]


nodes = [near(*w) for w in WP]
loop = [nodes[0]]
for a, b in zip(nodes, nodes[1:] + nodes[:1]): loop += path(a, b)[1:]
pts = [pos[n][:2] for n in loop]
L = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:])) / 2
print(NAZEV, 'délka', round(L), 'm, bodů', len(loop), '|', 'po směru hodinových ručiček' if area < 0 else 'PROTI SMĚRU (oprav pořadí WP)')
json.dump({'nazev': NAZEV, 'motorek': MOTOREK, 'zdroj': 'OpenStreetMap (ODbL)', 'delka_m': round(L),
           'trasa': [[round(pos[n][2], 7), round(pos[n][3], 7)] for n in loop]},
          open('assets/okruh.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
