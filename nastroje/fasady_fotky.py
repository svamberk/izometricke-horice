# Podklady pro barvy fasád z volně licencovaných fotek ulic na Wikimedia Commons (hlavně průzkum Petr1888, 7/2020, CC BY-SA 4.0).
#   python nastroje/fasady_fotky.py stahni SEZNAM.json   -> fotky/commons_ulice/<n>.jpg + build/fasady/fotky.json (autor, licence, poloha)
#   python nastroje/fasady_fotky.py mapky                -> build/fasady/mapa_<n>.png + kandidati_<n>.json (domy kolem místa focení)
# SEZNAM.json: {pageid: {"title", "lat", "lon", "ii": {"extmetadata": {...}}}} (výstup dotazu geosearch + imageinfo).
# Wikimedia: pomalu (2 s mezi dotazy), standardní šířka náhledu 1280 px, při 429 čekat podle Retry-After.
import json, math, os, re, sys, time, urllib.parse, urllib.request
sys.path.insert(0, '.')
UA = {'User-Agent': 'izomapa-horice/1.0 (https://github.com/svamberk/izometricke-horice)'}
F, B = 'fotky/commons_ulice', 'build/fasady'
os.makedirs(F, exist_ok=True); os.makedirs(B, exist_ok=True)


def get(url, tries=8):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500: raise
            wait = int(e.headers.get('Retry-After') or 30 * (i + 1)); print('čekám', wait, 's (HTTP', e.code, ')', flush=True); time.sleep(wait)
    raise RuntimeError('nedostupné: ' + url)


def stahni(seznam):
    meta = json.load(open(seznam, encoding='utf-8'))
    street = [v for v in meta.values() if re.search(r'ulice|nám[ěe]st', v['title']) or any('Streets in' in c or 'Squares in' in c for c in v.get('cats', []))]
    street.sort(key=lambda v: v['title'])
    out = json.load(open(f'{B}/fotky.json', encoding='utf-8')) if os.path.exists(f'{B}/fotky.json') else {}
    titles = [v['title'] for v in street if v['title'] not in {o['title'] for o in out.values()}]
    for k in range(0, len(titles), 40):                     # adresy náhledů po 40 souborech
        q = urllib.parse.urlencode({'action': 'query', 'format': 'json', 'prop': 'imageinfo', 'iiprop': 'url', 'iiurlwidth': 1280,
                                    'titles': '|'.join(titles[k:k + 40])})
        pages = json.loads(get('https://commons.wikimedia.org/w/api.php?' + q))['query']['pages']
        urls = {p['title']: p['imageinfo'][0]['thumburl'] for p in pages.values() if 'imageinfo' in p}
        time.sleep(2)
        for v in street:
            if v['title'] not in urls: continue
            n = f'{len(out) + 1:03d}'; f = f'{F}/{n}.jpg'
            open(f, 'wb').write(get(urls[v['title']])); time.sleep(2)
            em = v['ii']['extmetadata']; txt = lambda key: re.sub('<[^>]+>', '', em.get(key, {}).get('value', '')).strip()
            out[n] = {'title': v['title'], 'lat': v['lat'], 'lon': v['lon'], 'datum': txt('DateTimeOriginal'), 'autor': txt('Artist'),
                      'licence': txt('LicenseShortName'), 'url': 'https://commons.wikimedia.org/wiki/' + urllib.parse.quote(v['title'].replace(' ', '_')),
                      'soubor': f, 'ulice': re.sub(r'^.*?ulice\s+|\s+\d+\.\w+$|\.\w+$', '', v['title'].replace('File:', ''))}
            json.dump(out, open(f'{B}/fotky.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(n, v['title'], flush=True)


def mapky():
    import numpy as np, shapely, pyarrow.parquet as pq
    from PIL import Image, ImageDraw, ImageFont
    import geo
    fot = json.load(open(f'{B}/fotky.json', encoding='utf-8'))
    bt = pq.read_table('data_all/building.parquet', columns=['id', 'geometry', 'class', 'names']).to_pylist()
    G = shapely.transform(shapely.from_wkb([r['geometry'] for r in bt]), lambda c: np.column_stack(geo.to_local(c[:, 0], c[:, 1])))
    tree = shapely.STRtree(G)
    ad = pq.read_table('data_all/address.parquet', columns=['geometry', 'number', 'street']).to_pylist()
    AP = shapely.transform(shapely.from_wkb([a['geometry'] for a in ad]), lambda c: np.column_stack(geo.to_local(c[:, 0], c[:, 1])))
    atree = shapely.STRtree(AP)
    sg = [s for s in pq.read_table('data_all/segment.parquet', columns=['geometry', 'names', 'subtype']).to_pylist() if s['names'] and s['subtype'] == 'road']
    SG = shapely.transform(shapely.from_wkb([s['geometry'] for s in sg]), lambda c: np.column_stack(geo.to_local(c[:, 0], c[:, 1])))
    stree = shapely.STRtree(SG)
    try: font = ImageFont.truetype('arial.ttf', 15); small = ImageFont.truetype('arial.ttf', 12)
    except OSError: font = small = ImageFont.load_default()
    R, PXM = 90.0, 4.0                                       # okolí ±90 m, 4 px na metr
    for n, f in fot.items():
        cx, cy = (float(v) for v in geo.to_local(f['lon'], f['lat']))
        P = lambda x, y: ((x - cx + R) * PXM, (cy - y + R) * PXM)
        im = Image.new('RGB', (int(2 * R * PXM), int(2 * R * PXM)), (245, 245, 240)); d = ImageDraw.Draw(im)
        area = shapely.box(cx - R, cy - R, cx + R, cy + R)
        for k in stree.query(area):
            for l in (SG[k].geoms if hasattr(SG[k], 'geoms') else [SG[k]]):
                d.line([P(x, y) for x, y in l.coords], fill=(190, 190, 190), width=int(6 * PXM / 2))
        cand = []
        for k in sorted(tree.query(area), key=lambda k: G[k].distance(shapely.Point(cx, cy))):
            g = G[k]
            if g.distance(shapely.Point(cx, cy)) > 70 or g.area < 25: continue
            i = len(cand) + 1
            for p in (g.geoms if hasattr(g, 'geoms') else [g]):
                d.polygon([P(x, y) for x, y in p.exterior.coords], fill=(214, 222, 236), outline=(60, 70, 110))
            nums = sorted({str(ad[a]['number']) for a in atree.query(g.buffer(2)) if g.buffer(2).contains(AP[a]) and ad[a]['number']})
            c = g.representative_point(); dx, dy = c.x - cx, c.y - cy
            cand.append({'i': i, 'id': bt[k]['id'], 'cisla': nums, 'vzdalenost_m': round(math.hypot(dx, dy)),
                         'smer': ['V', 'SV', 'S', 'SZ', 'Z', 'JZ', 'J', 'JV'][int(((math.degrees(math.atan2(dy, dx)) + 22.5) % 360) // 45)]})
            d.text(P(c.x, c.y), str(i), fill=(180, 20, 20), font=font, anchor='mm')
            if nums: d.text((P(c.x, c.y)[0], P(c.x, c.y)[1] + 13), '/'.join(nums), fill=(30, 30, 30), font=small, anchor='mm')
        named = {}
        for k in stree.query(area):
            nm = (sg[k]['names'] or {}).get('primary')
            if nm and nm not in named and SG[k].intersects(area): named[nm] = SG[k].intersection(area).interpolate(0.5, normalized=True)
        for nm, pt in named.items(): d.text(P(pt.x, pt.y), nm, fill=(90, 90, 90), font=small, anchor='mm')
        ox, oy = P(cx, cy); d.ellipse([ox - 7, oy - 7, ox + 7, oy + 7], fill=(230, 30, 30)); d.text((ox + 10, oy - 18), 'FOTOGRAF', fill=(230, 30, 30), font=font)
        d.text((10, 8), f'S ↑   {f["title"]}   (okolí ±{int(R)} m)', fill=(0, 0, 0), font=font)
        im.save(f'{B}/mapa_{n}.png'); json.dump(cand, open(f'{B}/kandidati_{n}.json', 'w', encoding='utf-8'), ensure_ascii=False)
        ph = Image.open(f['soubor']).convert('RGB'); g = ImageDraw.Draw(ph)          # fotka s mřížkou po 100 px (souřadnice pro měření)
        for x in range(0, ph.width, 100):
            g.line([(x, 0), (x, ph.height)], fill=(255, 255, 0), width=1); g.text((x + 2, 2), str(x), fill=(255, 255, 0), font=small)
        for y in range(0, ph.height, 100):
            g.line([(0, y), (ph.width, y)], fill=(255, 255, 0), width=1); g.text((2, y + 2), str(y), fill=(255, 255, 0), font=small)
        ph.save(f'{B}/mrizka_{n}.jpg', quality=85)
    print('mapky', len(fot))


if __name__ == '__main__':
    if sys.argv[1] == 'stahni': stahni(sys.argv[2])
    elif sys.argv[1] == 'mapky': mapky()
