"""Náhled složené mapy z dlaždic rendereru (build/.../tiles_<varianta>) – kontrola švů bez DZI a webu.

python compose.py [varianta] [měřítko] [výstup.png] [i,j ...]   např. python compose.py autumn 8 build/nahled.png
Čtverce se skládají od nejvzdálenějšího od kamery (geo.depth_of), stejně jako v prohlížeči. Pohled: IZO_POHLED.
"""
import json, os, sys
from PIL import Image
from geo import CHUNKS, SFX, chunk_dir, depth_of

Image.MAX_IMAGE_PIXELS = None
variant = sys.argv[1] if len(sys.argv) > 1 else 'autumn'
scale = int(sys.argv[2]) if len(sys.argv) > 2 else 8
out = sys.argv[3] if len(sys.argv) > 3 else f'build/nahled_{variant}.png'
only = {tuple(int(v) for v in a.split(',')) for a in sys.argv[4:]}


def src(c):
    return f'build/tiles_{variant}{SFX}' if tuple(c) == (0, 0) else f'{chunk_dir(c)}/tiles_{variant}{SFX}'


chunks = [c for c in CHUNKS if os.path.exists(f'{src(c)}/info.json') and (not only or c in only)]
chunks.sort(key=lambda c: -depth_of(c))
infos = {c: json.load(open(f'{src(c)}/info.json')) for c in chunks}
mpp = infos[chunks[0]]['mpp']
gx0 = min(i['x0'] for i in infos.values()); gy1 = max(i['y1'] for i in infos.values())
W = max(round((i['x0'] - gx0) / mpp) + i['width'] for i in infos.values())
H = max(round((gy1 - i['y1']) / mpp) + i['height'] for i in infos.values())
BG = (0, 0, 0, 0) if os.environ.get('TRANSPARENT') else (27, 35, 51, 255)
img = Image.new('RGBA', (W // scale + 1, H // scale + 1), BG)
for c in chunks:
    i = infos[c]; T = i['tile']; ox = round((i['x0'] - gx0) / mpp); oy = round((gy1 - i['y1']) / mpp)
    for r in range(i['rows']):
        for k in range(i['cols']):
            f = f'{src(c)}/{k}_{r}.png'
            if not os.path.exists(f): continue
            t = Image.open(f).convert('RGBA')
            if scale > 1: t = t.resize((T // scale, T // scale), Image.Resampling.BOX)
            img.alpha_composite(t, ((ox + k * T) // scale, (oy + r * T) // scale))
img.save(out)
json.dump({'x0': gx0, 'y1': gy1, 'mpp': mpp * scale, 'w': img.size[0], 'h': img.size[1]}, open(out.rsplit('.', 1)[0] + '.json', 'w'))
print(out, img.size, 'čtverce', chunks, 'počátek', gx0, gy1)
