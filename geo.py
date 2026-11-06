"""Společné souřadnice a kamera (musí odpovídat render.html)."""
import json, math, os
import numpy as np

# Město: mesto.json (vyrobí ho novy_mesto.py), jiný soubor přes proměnnou IZO_MESTO
MESTO = json.load(open(os.environ.get('IZO_MESTO', 'mesto.json'), encoding='utf-8'))
LON0, LAT0 = MESTO['stred']          # střed čtverce (0, 0)
HALF = float(MESTO.get('polovina', 1150.0))   # půl strany čtverce v metrech
HREF = float(MESTO['href'])          # nulová výška terénu (stejná pro všechny čtverce), m n. m.

# Mapa je skládačka čtverců 2·HALF; i roste na východ, j na sever. Čtverec (0, 0) musí být v seznamu.
CHUNKS = {tuple(c): 'hotovo' for c in MESTO['ctverce']}
MARGIN = 80.5           # pás sousedních čtverců, který se kreslí i do tohoto (budovy a stromy přes šev, stíny); = 280 px textury
SAT_OUTLINE = False     # True = purpurové ohraničení zón upřesněných ze satelitních snímků (kontrola)
FLOOR = float(MESTO['dno'])   # spodek řezu zeminou pro všechny čtverce (pod nejnižším místem mapy)


def chunk_center(c):
    return c[0] * 2 * HALF, c[1] * 2 * HALF


def chunk_dir(c):
    return 'build' if tuple(c) == (0, 0) else f'build/c{c[0]}_{c[1]}'


def extent(margin=0.0):
    """obdélník celé mapy v lokálních metrech: x0, y0, x1, y1"""
    i = [c[0] for c in CHUNKS]; j = [c[1] for c in CHUNKS]
    return (min(i) * 2 * HALF - HALF - margin, min(j) * 2 * HALF - HALF - margin,
            max(i) * 2 * HALF + HALF + margin, max(j) * 2 * HALF + HALF + margin)


def bbox_lonlat(margin=300.0):
    """obdélník celé mapy ve stupních: lon0, lon1, lat0, lat1"""
    x0, y0, x1, y1 = extent(margin)
    (a, b), (c, d) = to_lonlat([x0, x1], [y0, y1])
    return float(a), float(b), float(c), float(d)


def chunk_of(x, y):
    """čtverec, do kterého patří bod v souřadnicích čtverce (0, 0)"""
    return int(math.floor((x + HALF) / (2 * HALF))), int(math.floor((y + HALF) / (2 * HALF)))


def neighbours(c):
    """sousedé (i úhlopříčně), kteří jsou v mapě: [(di, dj), ...]"""
    return [(di, dj) for dj in (1, 0, -1) for di in (-1, 0, 1) if (di or dj) and (c[0] + di, c[1] + dj) in CHUNKS]


def skirt_edges(c):
    """okraje bez souseda dostanou řez zeminou"""
    return ''.join(k for k, (di, dj) in (('n', (0, 1)), ('s', (0, -1)), ('w', (-1, 0)), ('e', (1, 0)))
                   if (c[0] + di, c[1] + dj) not in CHUNKS)
KX = math.cos(math.radians(LAT0)) * 111320.0
KY = 110574.0

EL = math.asin(math.tan(math.atan(0.5)))   # 30° -> dimetrie 2:1
# Otočení mapy po 90°: IZO_POHLED 0 = od jihozápadu (výchozí), 1 = od SZ, 2 = od SV, 3 = od JV. Slunce se otáčí s kamerou,
# takže osvětlení (světlá pravá a tmavší levá stěna) vypadá ve všech pohledech stejně. Výstupy pohledu N > 0:
# dlaždice build/.../tiles_<varianta>_pN, web site/pN/ (dlaždice, overlay, náhled, hledání); index.html je společný.
POHLED = int(os.environ.get('IZO_POHLED', '0')) % 4
AZ = math.radians(225 + 90 * POHLED)
SFX = f'_p{POHLED}' if POHLED else ''      # přípona složek s dlaždicemi rendereru
SITE = f'site/p{POHLED}' if POHLED else 'site'   # složka dat pohledu ve webu
PRE = f'p{POHLED}/' if POHLED else ''      # totéž jako cesta v adrese (relativně k index.html)


def rot(e, n):
    """vodorovný vektor (východ, sever) otočený s kamerou oproti výchozímu pohledu od JZ"""
    d = AZ - math.radians(225)
    return e * math.cos(d) + n * math.sin(d), n * math.cos(d) - e * math.sin(d)


SUN = (*rot(0.38, -0.68), 0.62)            # směr ke slunci (východ, sever, nahoru); render.html počítá totéž
CAM_Z = np.array([math.sin(AZ) * math.cos(EL), math.sin(EL), -math.cos(AZ) * math.cos(EL)])
CAM_X = np.cross([0, 1, 0], CAM_Z); CAM_X /= np.linalg.norm(CAM_X)
CAM_Y = np.cross(CAM_Z, CAM_X)


def to_local(lon, lat):
    return (np.asarray(lon) - LON0) * KX, (np.asarray(lat) - LAT0) * KY


def to_lonlat(x, y):
    return np.asarray(x) / KX + LON0, np.asarray(y) / KY + LAT0


def view(x, y, h):
    """lokální (východ, sever, výška) -> souřadnice v rovině kamery + hloubka (menší = blíž)"""
    p = np.stack([np.asarray(x, float), np.asarray(h, float), -np.asarray(y, float)], -1)
    return p @ CAM_X, p @ CAM_Y, -(p @ CAM_Z)


def depth_of(c):
    """hloubka středu čtverce v pohledu kamery (větší = dál), pořadí skládání čtverců"""
    x, y = chunk_center(c)
    return float(view(x, y, 0.0)[2])


class Image2D:
    """převod lokálních souřadnic na pixely výsledného obrázku (z info.json rendereru)"""
    def __init__(self, info):
        self.x0, self.y1, self.mpp = info['x0'], info['y1'], info['mpp']

    def px(self, x, y, h):
        vx, vy, d = view(x, y, h)
        return (vx - self.x0) / self.mpp, (self.y1 - vy) / self.mpp, d
