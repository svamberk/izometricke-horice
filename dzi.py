"""Složí dlaždice z rendereru, sjednotí paletu a vytvoří Deep Zoom pyramidu pro každou variantu.

python dzi.py [varianty...]            čtverec (0, 0) -> site/tiles/<varianta>/...
python dzi.py --chunk 0,-1 [varianty]  další čtverec (průhledné pozadí) -> site/tiles/c0_-1/<varianta>/...
"""
import json, math, os, shutil, sys
from PIL import Image
from geo import PRE, SFX, SITE

Image.MAX_IMAGE_PIXELS = None
TS = 2048           # velké dlaždice = málo souborů (artefakt má limit počtu souborů)
FMT = 'webp'        # bezztrátové WebP (o ~14 % menší než paletové PNG); 'png' pro starý formát


def save(im, path):
    if FMT == 'webp': im.convert('RGBA' if im.mode in ('P', 'RGBA', 'LA') else 'RGB').save(path[:-4] + '.webp', 'WEBP', lossless=True, quality=90, method=4, exact=False)
    else: im.save(path, optimize=True)
OUT = f'{SITE}/tiles'


def build(variant, chunk=None):
    src = f'build/tiles_{variant}{SFX}' if not chunk else f'build/c{chunk[0]}_{chunk[1]}/tiles_{variant}{SFX}'
    info = json.load(open(f'{src}/info.json'))
    T = info['tile']
    alpha = True        # všechny čtverce jsou průhledné vrstvy (skládají se podle hloubky, žádný není podklad)
    img = Image.new('RGBA' if alpha else 'RGB', (info['width'], info['height']), (0, 0, 0, 0) if alpha else (0, 0, 0))
    for j in range(info['rows']):
        for i in range(info['cols']):
            t = Image.open(f'{src}/{i}_{j}.png')
            img.paste(t.convert('RGBA' if alpha else 'RGB'), (i * T, j * T))
    if alpha: return build_alpha(variant, chunk, img, info)
    # jednotná paleta pro celou mapu; paleta se hledá na zmenšenině, pak se aplikuje na celek
    colors = 64 if variant == 'lights' else 160
    sample = img.resize((img.width // 3, img.height // 3), Image.Resampling.NEAREST)
    pal = sample.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    full = img.quantize(palette=pal, dither=Image.Dither.NONE)
    img = full.convert('RGB')
    d0 = f'{OUT}/{variant}'
    shutil.rmtree(d0, ignore_errors=True)
    W, H = img.size
    maxl = math.ceil(math.log2(max(W, H)))
    n = 0
    for lvl in range(maxl, -1, -1):
        s = 2 ** (maxl - lvl)
        w, h = max(1, math.ceil(W / s)), max(1, math.ceil(H / s))
        im = img if s == 1 else img.resize((w, h), Image.Resampling.BOX)
        d = f'{d0}/{lvl}'
        os.makedirs(d, exist_ok=True)
        for c in range(math.ceil(w / TS)):
            for r in range(math.ceil(h / TS)):
                t = im.crop((c * TS, r * TS, min(w, (c + 1) * TS), min(h, (r + 1) * TS)))
                if s == 1 and FMT != 'webp': t = t.quantize(palette=pal, dither=Image.Dither.NONE)
                save(t, f'{d}/{c}_{r}.png')
                n += 1
    size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(d0) for f in fs)
    print(variant, W, 'x', H, 'souborů', n, 'MB', round(size / 1e6, 1))
    return {'Image': {'xmlns': 'http://schemas.microsoft.com/deepzoom/2008', 'Url': f'tiles/{variant}/', 'Format': FMT,
                      'Overlap': '0', 'TileSize': str(TS), 'Size': {'Width': str(W), 'Height': str(H)}}}


# barvy vody (podzim/jaro/léto/zima, světlo a stín) – vždy v paletě, aby se malé vodní plochy neměnily na šedou
WATER = [(64, 124, 190), (92, 196, 230), (72, 132, 196), (186, 210, 228), (150, 168, 184), (58, 96, 136), (236, 240, 240)]


def palette_with_water(sample, colors):
    q = sample.quantize(colors=colors - 1 - 3 * len(WATER), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    p = q.getpalette()[:3 * (colors - 1 - 3 * len(WATER))]
    for r, g, b in WATER:
        for k in (1.0, 0.8, 0.66):         # stupně osvětlení terénu ve stínu
            p += [int(r * k), int(g * k), int(b * k)]
    im = Image.new('P', (1, 1)); im.putpalette(p + [0] * (768 - len(p)))
    return im


def quant_alpha(rgba, colors, pal=None):
    """paletový PNG s jedním průhledným indexem (pixel art má jen plnou nebo žádnou průhlednost)"""
    a = rgba.getchannel('A').point(lambda v: 255 if v > 127 else 0)
    rgb = rgba.convert('RGB')
    q = rgb.quantize(palette=pal, dither=Image.Dither.NONE) if pal else \
        rgb.quantize(colors=colors - 1, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    p = q.getpalette()[:3 * (colors - 1)] + [0, 0, 0]
    q.putpalette(p + [0] * (768 - len(p)))
    q.paste(colors - 1, mask=a.point(lambda v: 255 - v))
    q.info['transparency'] = colors - 1
    return q


def build_alpha(variant, chunk, img, info):
    colors = 64 if variant == 'lights' else 160
    sample = img.convert('RGB').resize((img.width // 3, img.height // 3), Image.Resampling.NEAREST)
    pal = palette_with_water(sample, colors) if variant != 'lights' else sample.quantize(colors=colors - 1, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    name = f'c{chunk[0]}_{chunk[1]}' if chunk else None
    d0 = f'{OUT}/{name}/{variant}' if name else f'{OUT}/{variant}'
    shutil.rmtree(d0, ignore_errors=True)
    W, H = img.size
    maxl = math.ceil(math.log2(max(W, H)))
    n = 0
    for lvl in range(maxl, -1, -1):
        s = 2 ** (maxl - lvl)
        w, h = max(1, math.ceil(W / s)), max(1, math.ceil(H / s))
        im = img if s == 1 else img.resize((w, h), Image.Resampling.BOX)
        d = f'{d0}/{lvl}'
        os.makedirs(d, exist_ok=True)
        for c in range(math.ceil(w / TS)):
            for r in range(math.ceil(h / TS)):
                t = im.crop((c * TS, r * TS, min(w, (c + 1) * TS), min(h, (r + 1) * TS)))
                # zmenšené úrovně bez vlastní palety: kvantizace zmenšeniny měnila vzácné barvy (bazény, voda) na šedou
                # bez palety i v plném rozlišení: vzácné barvy (malé vodní plochy) se v paletě čtverce měnily na šedou
                save(quant_alpha(t, colors, pal), f'{d}/{c}_{r}.png')      # stejná paleta (s vodou) pro všechny úrovně
                n += 1
    size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(d0) for f in fs)
    print(name or 'c0_0', variant, W, 'x', H, 'souborů', n, 'MB', round(size / 1e6, 1))
    return {'Image': {'xmlns': 'http://schemas.microsoft.com/deepzoom/2008', 'Url': f'{PRE}tiles/{name}/{variant}/' if name else f'{PRE}tiles/{variant}/', 'Format': FMT,
                      'Overlap': '0', 'TileSize': str(TS), 'Size': {'Width': str(W), 'Height': str(H)}},
            'x0': info['x0'], 'y1': info['y1']}


if __name__ == '__main__':
    from geo import CHUNKS
    os.makedirs(SITE, exist_ok=True)
    path = f'{SITE}/tiles.json'
    out = json.load(open(path)) if os.path.exists(path) else {}
    args = sys.argv[1:]
    chunk = None
    if args[:1] == ['--chunk']:
        chunk = tuple(int(v) for v in args[1].split(',')); args = args[2:]
    variants = args or ['autumn', 'winter', 'spring', 'summer', 'lights']
    if chunk:
        name = f'c{chunk[0]}_{chunk[1]}'
        ch = out.setdefault('chunks', {}).setdefault(name, {'chunk': list(chunk), 'status': CHUNKS[chunk]})
        ch['status'] = CHUNKS[chunk]
        for v in variants: ch[v] = build(v, chunk)
    else:
        info = json.load(open(f'build/tiles_autumn{SFX}/info.json'))
        out['base'] = {'x0': info['x0'], 'y1': info['y1'], 'mpp': info['mpp'], 'status': CHUNKS[(0, 0)]}
        for v in variants: out[v] = build(v)
    json.dump(out, open(path, 'w'))
