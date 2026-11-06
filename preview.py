"""Rychlý náhled celé mapy pro prohlížeč (site/preview/<sezóna>.webp + .json), zobrazí se dřív než dlaždice.

python preview.py   po přerenderování a dzi.py, před ./deploy.sh
"""
import json, os, subprocess, sys
from PIL import Image
from geo import SITE

os.makedirs(f'{SITE}/preview', exist_ok=True)
for v in ['spring', 'summer', 'autumn', 'winter']:
    out = f'{SITE}/preview/{v}.webp'
    subprocess.run([sys.executable, 'compose.py', v, '16', out], check=True, env={**os.environ, 'TRANSPARENT': '1'})
    im = Image.open(out); m = json.load(open(f'{SITE}/preview/{v}.json'))
    w = 2400; h = round(im.height * w / im.width); k = im.width / w
    im.resize((w, h), Image.Resampling.BOX).save(out, 'WEBP', quality=82, method=6)
    m.update({'mpp': m['mpp'] * k, 'w': w, 'h': h}); json.dump(m, open(f'{SITE}/preview/{v}.json', 'w'))
    print(v, w, h, os.path.getsize(out) // 1000, 'kB')
