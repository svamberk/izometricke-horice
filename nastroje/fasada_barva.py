# Barva fasády z vyznačené části fotky: python nastroje/fasada_barva.py FOTKA x0 y0 x1 y1 [x0 y0 x1 y1 ...]
# Souřadnice v pixelech fotky (levý horní a pravý dolní roh, víc obdélníků = jedna fasáda). Vynechá okna a stíny (tmavé),
# oblohu a odlesky (velmi světlé) a zeleň; vrátí medián zbytku a barvu převedenou do pastelové palety fasád mapy.
# --nahled VÝSTUP.jpg uloží fotku s vyznačenými obdélníky (kontrola, že měřím zeď, ne okna nebo stín).
import json, math, sys
sys.path.insert(0, 'nastroje')
import numpy as np
from PIL import Image
from strechy_lab import srgb2lab, lab2srgb


def to_map(lab):
    """barva z fotky -> fasáda v mapě (paleta zdí: světlé pastely kolem L85)"""
    L, a, b = lab
    L2 = float(np.clip(0.55 * L + 45, 62, 94)); a2, b2 = a * 0.9, b * 0.9
    ch = math.hypot(a2, b2)
    if ch > 45: a2, b2 = a2 * 45 / ch, b2 * 45 / ch
    return lab2srgb((L2, a2, b2))


def measure(path, boxes):
    im = np.asarray(Image.open(path).convert('RGB'))
    px = np.concatenate([im[max(0, y0):y1, max(0, x0):x1].reshape(-1, 3) for x0, y0, x1, y1 in boxes])
    lab = srgb2lab(px)
    L = lab[:, 0]
    ok = (L > np.percentile(L, 30)) & (L < 97) & ~((lab[:, 1] < -8) & (lab[:, 2] > 5))
    if ok.sum() < 50: return {'chyba': 'málo pixelů fasády', 'pixelu': int(len(px))}
    med = np.median(lab[ok], 0)
    return {'foto_rgb': lab2srgb(med), 'lab': [round(float(v), 1) for v in med], 'mapa_rgb': to_map(med),
            'podil_pouzitych': round(float(ok.mean()), 2), 'pixelu': int(len(px))}


if __name__ == '__main__':
    args = sys.argv[2:]; nahled = None
    if '--nahled' in args: k = args.index('--nahled'); nahled = args[k + 1]; args = args[:k] + args[k + 2:]
    v = list(map(int, args)); boxes = [v[i:i + 4] for i in range(0, len(v), 4)]
    res = measure(sys.argv[1], boxes)
    if nahled:
        from PIL import ImageDraw
        im = Image.open(sys.argv[1]).convert('RGB'); d = ImageDraw.Draw(im)
        for x0, y0, x1, y1 in boxes: d.rectangle([x0, y0, x1, y1], outline=(255, 0, 255), width=3)
        if 'mapa_rgb' in res: d.rectangle([10, 10, 90, 60], fill=tuple(res['mapa_rgb']), outline=(0, 0, 0))
        im.save(nahled, quality=88); res['nahled'] = nahled
    print(json.dumps(res, ensure_ascii=False))
