# Nástroje pro kontrolu renderu (spouštět z kořene projektu venv pythonem):
#   python nastroje/qa_tools.py mozaiky                 -> build/qa/<čtverec>_<varianta>_<sl>_<ř>.jpg (léto 1:4 po 6 dílech, zima a noc 1:8)
#   python nastroje/qa_tools.py kde <i,j> <tile_i> <tile_j> <px> <py>   -> lat, lon bodu v dlaždici (1024 px, 0,2 m/px) na terénu
#   python nastroje/qa_tools.py vyrez <i,j> <season> <lat> <lon> [w h] -> build/qa/vyrez_<lat>_<lon>_<season>.png (plné rozlišení)
import glob, json, math, os, sys
sys.path.insert(0, os.getcwd())
import numpy as np
from PIL import Image
import geo

Q = 'build/qa'; os.makedirs(Q, exist_ok=True)


def tiles_dir(c, season):
    return os.path.join(geo.chunk_dir(c), f'tiles_{season}')


def mosaic(c, season, scale):
    d = tiles_dir(c, season); info = json.load(open(os.path.join(d, 'info.json'))); T = info['tile'] // scale
    big = Image.new('RGB', (info['cols'] * T, info['rows'] * T), (30, 34, 44))
    for f in glob.glob(os.path.join(d, '*_*.png')):
        i, j = map(int, os.path.basename(f)[:-4].split('_'))
        im = Image.open(f).convert('RGBA'); bg = Image.new('RGBA', im.size, (30, 34, 44, 255)); bg.alpha_composite(im)
        big.paste(bg.convert('RGB').resize((T, T), Image.LANCZOS), (i * T, j * T))
    return big, T


def dem_height(lon, lat):
    """výška terénu (m n. m.) z dlaždic Terrarium z14 stažených ve data*/dem"""
    z = 14; n = 2 ** z
    fx = (lon + 180) / 360 * n; fy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    tx, ty = int(fx), int(fy)
    for f in glob.glob(f'data*/dem/{z}_{tx}_{ty}.png'):
        a = np.asarray(Image.open(f).convert('RGB')).astype(float)
        px, py = min(255, int((fx - tx) * 256)), min(255, int((fy - ty) * 256))
        r, g, b = a[py, px]; return r * 256 + g + b / 256 - 32768
    return geo.HREF


def kde(c, ti, tj, px, py):
    info = json.load(open(os.path.join(tiles_dir(c, 'summer'), 'info.json'))); T = info['tile']
    vx = info['x0'] + (ti * T + px) * info['mpp']; vy = info['y1'] - (tj * T + py) * info['mpp']
    h = 0.0
    for _ in range(8):                      # průsečík paprsku kamery s terénem
        # vx = x*CX0 + h*CX1 - y*CX2 ; vy = x*CY0 + h*CY1 - y*CY2
        A = np.array([[geo.CAM_X[0], -geo.CAM_X[2]], [geo.CAM_Y[0], -geo.CAM_Y[2]]])
        rhs = np.array([vx - h * geo.CAM_X[1], vy - h * geo.CAM_Y[1]])
        x, y = np.linalg.solve(A, rhs)
        lon, lat = geo.to_lonlat(x, y); h = dem_height(float(lon), float(lat)) - geo.HREF
    return float(lat), float(lon), round(h, 1)


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'mozaiky':
        for c in sorted(geo.CHUNKS):
            name = f'c{c[0]}_{c[1]}'
            big, T = mosaic(c, 'summer', 4); W, H = big.size
            for sx in range(3):
                for sy in range(2):
                    big.crop((sx * W // 3, sy * H // 2, (sx + 1) * W // 3, (sy + 1) * H // 2)).save(f'{Q}/{name}_summer_{sx}{sy}.jpg', quality=85)
            for v in ('winter', 'lights'):
                big, T = mosaic(c, v, 8); W, H = big.size
                for sx in range(2): big.crop((sx * W // 2, 0, (sx + 1) * W // 2, H)).save(f'{Q}/{name}_{v}_{sx}.jpg', quality=85)
            print(name, flush=True)
        print('Díl summer_<sx><sy>: 1:4, dlaždice 256 px; díl sx pokrývá sloupce dlaždic sx*6..sx*6+5, sy řádky sy*5..sy*5+4 (pro 18x10 dlaždic).')
    elif cmd == 'kde':
        c = tuple(map(int, sys.argv[2].split(','))); print(kde(c, *map(int, sys.argv[3:7])))
    elif cmd == 'vyrez':
        c = tuple(map(int, sys.argv[2].split(','))); season = sys.argv[3]; lat, lon = float(sys.argv[4]), float(sys.argv[5])
        w, h = (int(sys.argv[6]), int(sys.argv[7])) if len(sys.argv) > 7 else (900, 700)
        d = tiles_dir(c, season); info = json.load(open(os.path.join(d, 'info.json'))); I = geo.Image2D(info); T = info['tile']
        x, y = geo.to_local(lon, lat); px, py, _ = I.px(x, y, dem_height(lon, lat) - geo.HREF + 5)
        x0, y0 = int(px - w / 2), int(py - h / 2); i0, j0, i1, j1 = x0 // T, y0 // T, (x0 + w) // T, (y0 + h) // T
        big = Image.new('RGBA', ((i1 - i0 + 1) * T, (j1 - j0 + 1) * T), (30, 34, 44, 255))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                f = os.path.join(d, f'{i}_{j}.png')
                if os.path.exists(f): big.alpha_composite(Image.open(f).convert('RGBA'), ((i - i0) * T, (j - j0) * T))
        out = f'{Q}/vyrez_{lat:.5f}_{lon:.5f}_{season}.png'
        big.crop((x0 - i0 * T, y0 - j0 * T, x0 - i0 * T + w, y0 - j0 * T + h)).save(out); print(out)
