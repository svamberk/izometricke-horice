# Sloučí změřené barvy fasád z fotek (výstup agentů: měření + kontrola) do assets/upresneni.json ('b': id -> {'wc': [r, g, b]}).
#   python nastroje/fasady_slouc.py VYSLEDEK.json
# Přijme se: kontrola "ok"; "nejiste" jen při jistotě "vysoká"; bez kontroly jen "vysoká"; "spatna_oblast" jen když kontrola
# sama přeměřila zeď (mapa_rgb v důvodu). Ostatní "spatna_*" se zahodí.
# Víc měření jednoho domu: medián v Lab; když se liší o víc než ΔE 25, vezme se to nejjistější.
import json, os, re, sys
from collections import defaultdict
sys.path.insert(0, 'nastroje')
import numpy as np
from strechy_lab import srgb2lab, lab2srgb

raw = json.load(open(sys.argv[1], encoding='utf-8'))
res = raw.get('result', raw) if isinstance(raw, dict) else raw
fot = json.load(open('build/fasady/fotky.json', encoding='utf-8'))
acc, stat = defaultdict(list), defaultdict(int)
for r in res:
    if not r or not r.get('found'): continue
    checks = {(c['foto'], c['id']): c for c in ((r.get('checks') or {}).get('kontroly') or [])}
    for f in r['found']['fotky']:
        for b in f['budovy']:
            c = checks.get((f['foto'], b['id'])); v = c['verdikt'] if c else None
            ok = v == 'ok' or (b['jistota'] == 'vysoká' and v in ('nejiste', None))
            m = re.findall(r'mapa_rgb\D{0,6}\[(\d+),\s*(\d+),\s*(\d+)\]', c['duvod']) if v == 'spatna_oblast' else []
            if m: b = {**b, 'mapa_rgb': [int(x) for x in m[-1]], 'poznamka': b['poznamka'] + ' | přeměřeno kontrolou'}; ok = True   # kontrola sama změřila správnou část zdi
            stat[(v or 'bez kontroly', 'přijato' if ok else 'zamítnuto')] += 1
            if ok and len(b['mapa_rgb']) == 3: acc[b['id']].append((b, f['foto']))
up = json.load(open('assets/upresneni.json', encoding='utf-8')) if os.path.exists('assets/upresneni.json') else {'b': {}, 'roofs': [], 'paved': [], 'buses': []}
detail, used = {}, set()
for i, items in acc.items():
    labs = srgb2lab(np.array([b['mapa_rgb'] for b, _ in items], float))
    med = np.median(labs, 0)
    if len(items) > 1 and np.max(np.linalg.norm(labs[:, None] - labs[None], axis=-1)) > 25:   # nesoulad: nejjistější měření
        best = max(range(len(items)), key=lambda k: (items[k][0]['jistota'] == 'vysoká', -np.linalg.norm(labs[k] - med)))
        med = labs[best]
    wc = lab2srgb(med)
    up['b'][i] = {**up['b'].get(i, {}), 'wc': wc}
    detail[i] = {'wc': wc, 'mereni': [{'foto': n, 'rgb': b['mapa_rgb'], 'jistota': b['jistota'], 'poznamka': b['poznamka']} for b, n in items]}
    used.update(n for _, n in items)
json.dump(up, open('assets/upresneni.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
json.dump(detail, open('build/fasady/prirazeni.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
# atribuce: použité fotky (autor, licence, odkaz)
lines = ['# Zdroje barev fasád', '', 'Barvy fasád běžných domů jsou změřené z volně licencovaných fotek ulic na Wikimedia Commons',
         '(nastroje/fasady_fotky.py, nastroje/fasada_barva.py). Fotky se do mapy ani do repozitáře nevkládají; z každé se vzala jen barva zdi.', '']
for n in sorted(used):
    f = fot[n]; lines.append(f"- [{f['title'].replace('File:', '')}]({f['url']}) – {f['autor']}, {f['licence']}, {f['datum'][:10]}")
open('docs/ZDROJE-FASAD.md', 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
print('verdikty', dict(stat)); print('budov s barvou fasády', len(detail), '| použitých fotek', len(used))
