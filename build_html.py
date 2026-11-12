"""Z web/app.html a mesto.json vyrobí site/index.html (texty, meta, sitemap).
Pokud existuje složka obce/*.json, vyrobí pro každou obec i site/index_<obec>.html a site/obce/<obec>/og.jpg."""
import html, json, os, re
from urllib.parse import urljoin
from PIL import Image
import geo
from geo import MESTO


def og_obrazek(out='site/og.jpg', W=1200, H=630, dolu=1, latlon=None):
    """náhled pro sdílení (og:image) 1200×630: výřez středu města z letních dlaždic (o úroveň menší než plné rozlišení)"""
    t = json.load(open('site/tiles.json')); base = t['base']
    x, y = geo.to_local(latlon[1], latlon[0]) if latlon else (0, 0)
    c = geo.chunk_of(float(x), float(y)) if latlon else (0, 0)
    s = (t.get('chunks') or {}).get(f'c{c[0]}_{c[1]}', {}).get('summer') if c != (0, 0) else None
    s = s or t['summer']; I = s['Image']
    url = 'site/' + I['Url'].rstrip('/')
    T = int(I['TileSize']); SW, SH = int(I['Size']['Width']), int(I['Size']['Height'])
    lv = max(int(n) for n in os.listdir(url) if n.isdigit()) - dolu; k = 2 ** dolu
    vx, vy, _ = geo.view(x, y, 0)
    cx, cy = (vx - s.get('x0', base['x0'])) / base['mpp'] / k, (s.get('y1', base['y1']) - vy) / base['mpp'] / k
    lw, lh = -(-SW // k), -(-SH // k)
    x0 = int(min(max(cx - W / 2, 0), max(lw - W, 0))); y0 = int(min(max(cy - H / 2, 0), max(lh - H, 0)))
    img = Image.new('RGB', (W, H), (27, 33, 48))
    for i in range(x0 // T, (x0 + W) // T + 1):
        for j in range(y0 // T, (y0 + H) // T + 1):
            f = f'{url}/{lv}/{i}_{j}.webp'
            if os.path.exists(f):
                q = Image.open(f).convert('RGBA'); img.paste(q, (i * T - x0, j * T - y0), q)
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    img.save(out, quality=85)


SABLONA = open('web/app.html', encoding='utf-8').read()


def ikony_a_manifest(M):
    """ikona (izometrická kostka), ikony pro přidání na plochu a manifest: mapu jde přidat na plochu jako aplikaci"""
    from PIL import ImageDraw
    os.makedirs('site', exist_ok=True)
    tvar = [('#6fa84a', [(36, 15), (61, 27.5), (36, 40), (11, 27.5)]), ('#8a6242', [(11, 27.5), (36, 40), (36, 63), (11, 50.5)]),
            ('#684832', [(36, 40), (61, 27.5), (61, 50.5), (36, 63)])]
    def ikona(size, maskable, out):
        S = size * 4; im = Image.new('RGBA', (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        if maskable: d.rectangle([0, 0, S, S], fill='#1b2333'); k = S / 72 * 0.72; o = S * 0.14   # maskable: obsah v bezpečné zóně
        else: d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 10 / 72, fill='#1b2333'); k = S / 72; o = 0
        for barva, body in tvar: d.polygon([(o + x * k, o + y * k) for x, y in body], fill=barva)
        im.resize((size, size), Image.LANCZOS).save(out, optimize=True)
    if not os.path.exists('site/favicon.svg'):            # vlastní ikony města se nepřepisují
        open('site/favicon.svg', 'w').write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="10" fill="#1b2333"/>'
                                            + ''.join(f'<path d="M{"L".join(f"{x} {y}" for x, y in b)}z" fill="{c}"/>' for c, b in tvar) + '</svg>')
    for size, maskable, out in [(180, False, 'apple-touch-icon.png'), (192, False, 'icon-192.png'), (512, False, 'icon-512.png'),
                                (192, True, 'icon-maskable-192.png'), (512, True, 'icon-maskable-512.png')]:
        if not os.path.exists(f'site/{out}'): ikona(size, maskable, f'site/{out}')
    json.dump({'name': M['titulek'], 'short_name': M['nazev'], 'description': M['popis'], 'lang': 'cs', 'start_url': './', 'scope': './',
               'display': 'standalone', 'orientation': 'any', 'background_color': '#1b2333', 'theme_color': '#1b2333',
               'icons': [{'src': f'icon-{s}.png', 'sizes': f'{s}x{s}', 'type': 'image/png'} for s in (192, 512)]
                        + [{'src': f'icon-maskable-{s}.png', 'sizes': f'{s}x{s}', 'type': 'image/png', 'purpose': 'maskable'} for s in (192, 512)]},
              open('site/manifest.webmanifest', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
E = lambda t: html.escape(t or '', quote=True)


def postav_stranku(M, html_out='site/index.html', og_cesta='og.jpg', je_obec=False, page_url=None):
    site_url = (M.get('url') or '').rstrip('/')          # plná adresa webu (kvůli og:image a sitemap); prázdné = bez nich
    page_url = page_url or (site_url + '/' if site_url else '')
    obrazek_url = urljoin(site_url + '/', og_cesta) if site_url else ''
    mhd = M.get('mhd') or {}
    pohled = M.get('pohled')
    strom = M.get('vanocni_strom')
    fill = {
        'TITULEK': E(M['titulek']), 'POPIS': E(M['popis']), 'MESTO': E(M['nazev']), 'H1_A': E(M['h1'][0]), 'H1_B': E(M['h1'][1]),
        'PODTITUL': E(M['podtitul']),
        'HLASENI': 'true' if M.get('hlaseni') else 'false',
        'VITR': 'true' if M.get('vitr') else 'false',
        'POCASI': 'true' if M.get('pocasi') else 'false',
        'LETADLA': 'true' if M.get('letadla', False) else 'false',
        'POHLED': json.dumps(pohled if isinstance(pohled, list) and len(pohled) >= 2 else None),
        'DOMU_CELEK': 'true' if M.get('domu_celek') else 'false',
        'STROM': json.dumps(strom if isinstance(strom, list) and len(strom) == 2 else None),
        'OBEC': json.dumps(M.get('nazev') if je_obec else None, ensure_ascii=False),
        'VLAK': json.dumps({'vychozi': (M.get('vlak') or {}).get('vychozi', 'regionova') if isinstance(M.get('vlak'), dict) else M.get('vlak', 'regionova'), 'linky': M['vlak'].get('linky', {}) if isinstance(M.get('vlak'), dict) else {}}),
        'DOPRAVA_ZDROJ': ', intenzity dopravy © <a href="https://www.rsd.cz/silnice-a-dalnice/scitani-dopravy" target="_blank" rel="noopener">ŘSD ČR</a> (Celostátní sčítání dopravy)' if M.get('doprava') else '',
        'RUIAN_ZDROJ': ', typy budov z RÚIAN' if os.path.exists('assets/ruian_budovy.json') else '',
        'VITR_ZDROJ': ', ' + ('počasí a vítr' if M.get('pocasi') and M.get('vitr') else 'počasí' if M.get('pocasi') else 'vítr') + ' © <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a> (CC BY 4.0)' if M.get('vitr') or M.get('pocasi') else '',
        'LETADLA_ZDROJ': ', letadla © <a href="https://adsb.lol/" target="_blank" rel="noopener">adsb.lol</a> (ODbL)' if M.get('letadla', False) else '',
        'MHD_ZDROJ': f', jízdní řády MHD © <a href="{E(mhd["zdroj_url"])}" target="_blank" rel="noopener">{E(mhd["zdroj"])}</a>'
                     if mhd.get('gtfs') and mhd.get('zdroj') else '',
    }
    app = re.sub(r'\{\{(\w+)\}\}', lambda m: fill[m.group(1)], SABLONA)
    og = f'''<meta property="og:image" content="{E(obrazek_url)}">
<meta property="og:url" content="{E(page_url)}">
<link rel="canonical" href="{E(page_url)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{E(obrazek_url)}">
''' if site_url else ''
    LD = json.dumps({'@context': 'https://schema.org', '@type': 'WebApplication', 'name': M['titulek'], 'description': M['popis'],
                     'inLanguage': 'cs', 'applicationCategory': 'TravelApplication', 'operatingSystem': 'Web',
                     'spatialCoverage': {'@type': 'Place', 'name': M['nazev']},
                     'isBasedOn': ['https://www.openstreetmap.org/', 'https://overturemaps.org/', 'https://geoportal.cuzk.cz/']},
                    ensure_ascii=False)
    head = f'''<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1b2333">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="apple-mobile-web-app-title" content="{E(M['nazev'])}">
<meta property="og:title" content="{E(M['titulek'])}">
<meta property="og:description" content="{E(M['popis'])}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{E(M['titulek'])}">
<meta property="og:locale" content="cs_CZ">
{og}<script type="application/ld+json">{LD}</script>
<style>:root{{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}body{{margin:0;font:14px system-ui}}[hidden]{{display:none!important}}</style>
'''
    body_start = app.index('</style>') + len('</style>')
    page = head + app[:body_start] + '\n</head>\n<body>\n' + app[body_start:] + '\n</body>\n</html>\n'
    open(html_out, 'w', encoding='utf-8').write(page)
    print(html_out, len(page))
    # og:image: vyrobí se jednou ze středu města/obce; vlastní soubor (1200×630) se nepřepisuje
    og_out = f'site/{og_cesta}'
    latlon = (pohled[:2] if isinstance(pohled, list) and len(pohled) >= 2 else strom if isinstance(strom, list) and len(strom) == 2 else None)
    if (site_url or je_obec) and not os.path.exists(og_out) and os.path.exists('site/tiles.json'):
        og_obrazek(og_out, latlon=latlon); print(og_out)


ikony_a_manifest(MESTO)
postav_stranku(MESTO)
SITE_URL = (MESTO.get('url') or '').rstrip('/')
open('site/robots.txt', 'w').write('User-agent: *\nAllow: /\n' + (f'Sitemap: {SITE_URL}/sitemap.xml\n' if SITE_URL else ''))
if SITE_URL:
    open('site/sitemap.xml', 'w').write(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{SITE_URL}/</loc></url></urlset>\n')
if MESTO.get('hlaseni'):                              # volitelná administrace hlášení (server v hlaseni/)
    open('site/admin.html', 'w', encoding='utf-8').write(open('hlaseni/admin.html', encoding='utf-8').read())

if os.path.isdir('obce'):
    for f in sorted(os.listdir('obce')):
        if not f.endswith('.json'): continue
        slug = f[:-5]
        obec = json.load(open(f'obce/{f}', encoding='utf-8'))
        obec_url = obec.get('url') or (urljoin(SITE_URL + '/', f'index_{slug}.html') if SITE_URL else None)
        postav_stranku({**MESTO, **obec, 'url': SITE_URL}, html_out=f'site/index_{slug}.html', og_cesta=f'obce/{slug}/og.jpg', je_obec=True, page_url=obec_url)
