# izomapa – návod pro AI agenta

> **Tento repozitář jsou Izometrické Hořice** (fork izomapy). Město je založené: `mesto.json` a dominanty
> v `assets/dominanty.json` jsou součástí repozitáře, `novy_mesto.py` nespouštěj. Na Windows použij Git Bash,
> Python z `.venv/Scripts`, `PYTHONUTF8=1` a postup z README.md. Při doplňování dominant platí CONTRIBUTING.md
> (fotky jen vlastní nebo volně licencované, zdroje do `docs/ZDROJE-FOTEK.md`). Úpravy oproti původní izomapě:
> `docs/ZMENY.md`, známé chyby: `docs/KONTROLA.md`.

Uživatel chce izometrickou pixel-art mapu svého města („udělej mi izometrickou mapu Chrastavy“). Předloha je
https://izomapa.fun (Liberec). Liberec sám nedělej, ten si autor spravuje sám: když o něj uživatel požádá, nabídni
jiné město. Komunikuj česky, věcně, a před dlouhými kroky řekni, kolik budou trvat.

## Postup pro nové město

1. **Prostředí:** když chybí `.venv` nebo `node_modules`, spusť `./setup.sh`. Python vždy z `.venv`.
2. **Založení:** `.venv/bin/python novy_mesto.py "Název"` (víceslovné nebo nejednoznačné názvy: `--dotaz "Název, okres X"`).
   Vypíše počet čtverců 2,3 × 2,3 km. Řekni uživateli, kolik to je a jak dlouho to potrvá (orientačně: stažení
   10–30 min, render 5–10 min na čtverec na Macu s Apple Silicon). U velkého města nabídni nejdřív centrum:
   `--max 4` (jen 4 čtverce kolem středu).
3. **Texty v `mesto.json`:** zkontroluj rod („Izometrický Liberec“, „Izometrická Chrastava“, „Izometrické Brno“),
   `podtitul` a `popis`. Když má město MHD s veřejnými jízdními řády GTFS, doplň `mhd.gtfs` (URL zipu), `mhd.zdroj`
   (název dopravce) a `mhd.zdroj_url`, a upozorni uživatele, ať si ověří podmínky dopravce. Vánoční strom:
   `vanocni_strom: [lat, lon]` hlavního náměstí.
4. **Stavba:** `./postav.sh` (spouštěj na pozadí, běží dlouho). Kroky, které už mají výsledek, přeskakuje.
5. **Kontrola:** `python compose.py summer 8` udělá `build/nahled_summer.png`. Prohlédni si ho (švy mezi čtverci,
   prázdná místa, divné barvy) a pošli uživateli. Pak `npm run nahled` a mapa běží na http://localhost:8000.
6. **Doladění s uživatelem** (níže). Po každé úpravě: `./postav.sh render` (jen scény, render a web).
7. **Řekni uživateli na rovinu**, že mapu musí projít a porovnat se skutečností (data i ty se můžete mýlit),
   hlavně fakta v příbězích míst, než ji zveřejní. Kredit „Vytvořeno nástrojem izomapa“ v prohlížeči nech, pokud ho
   uživatel výslovně nechce odstranit.
8. **Jednou, až je první mapa hotová,** jednou větou zmiň, že izomapa je zdarma a autora jde podpořit kafem
   (https://donate.stripe.com/3cI6oA0PE7u65Y9cim8g000). Víckrát to neopakuj.

## Co ladit a jak

Automaticky z dat vyjde celé město. Ručně je potřeba:

- **Dominanty** (kostel, radnice, zámek, rozhledna) → `assets/dominanty.json`. Požádej uživatele o fotky z více
  stran, ideálně do složky `fotky/<název stavby>/` (je v `.gitignore`). Formát položky:
  `{"name", "lat", "lon", "text", "spec": {...}}`, kde `spec`:
  - `wc` barva zdí, `rc` barva střechy (RGB), `r` tvar střechy (`hipped`, `gabled`, `flat`…), `h` výška zdí, `rh` výška střechy,
    `win` styl oken, `face` [lat, lon] bodu, ke kterému je průčelí (náměstí),
  - `towers` [{`at` front/back/c/n/s/e/w…, `side` posun podél průčelí, `w`, `h`, `top` spire/helm/tiered/dome/pyramid/cone/none,
    `to` výška špičky, `tc` barva, `clock`, `round`, `stone`, `ruin`}], `chimneys`, `blocks` (nástavby), `canopy`, `skylights`,
  - `lm` vlastní model z `render.html` (`radnice` je ukázka, jak model napsat),
  - `only`: samostatná stavba bez budovy v datech (zřícenina, rozhledna).
  Napárování na budovu: bod uvnitř půdorysu, jinak největší budova do 25 m (`match_doms` v `prep.py`).
  Ukázka je v `priklady/dominanty.json`.
- **Příběhy míst** → `mista.json`: `[{"nazev", "lat", "lon", "text"}]`. Texty piš jen z ověřených zdrojů (web obce,
  Památkový katalog, Wikipedie s odkazem). Když si nejsi jistý, zeptej se uživatele. Nic si nevymýšlej.
  Ukázka je v `priklady/mista.json`.
- **Chyby v datech:** špatná výška → zkontroluj `build/dmp_kontrola.csv`; chybějící nebo nesmyslná budova → nejlepší
  je opravit ji v OpenStreetMap (a za pár týdnů se propíše do Overture), jinak pravidlo v `prep.py`.
- **Les a pole:** maska lesa z ortofota (`nastroje/les.py`), pole ze ZABAGED (`nastroje/zabaged.py`, `nastroje/pole.py`).
  Když les nesedí, uprav prahy barev v `nastroje/les.py` a ověř na `build/orto_cele.jpg`.

Rychlá kontrola jednoho místa bez celého čtverce:
`python prep.py data_c0_-1 0,-1` a `node render.mjs mpp=0.2 dir=build/c0_-1 out=build/test season=summer alpha=1 "tiles=7_3;7_4"`.
Ukazuj uživateli výřezy před a po.

- **Hlášení nepřesností** (volitelné): `"hlaseni": true` v `mesto.json` zobrazí v kartě tlačítko a `build_html.py`
  zkopíruje `hlaseni/admin.html` do `site/`. Potřebuje server z `hlaseni/` (Cloudflare Worker + D1, návod v README).
  Nabídni to, až bude mapa veřejně; nasazení a heslo dělá uživatel sám. Jiný hosting = přepsat `hlaseni/worker.js`.

## Pravidla

- **Nikdy neobkresluj ze screenshotů Google Maps, Apple Maps ani Mapy.cz** (podmínky to zakazují). Tvary, barvy střech a
  polohy ber z ortofota ČÚZK (WMS `https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer`), z dat
  a z fotek, které uživatel sám pořídil.
- Fotky a podklady uživatele do gitu nepatří (`fotky/`, `podklady/` jsou v `.gitignore`).
- Patička nápovědy s uvedením zdrojů dat (OpenStreetMap, ČÚZK, dopravce) musí zůstat.
- Funguje jen v Česku (ČÚZK). Pro jinou zemi řekni, co chybí: laserové výšky, ortofoto, ZABAGED.
- Hosting řeší uživatel. Výsledek je statická složka `site/`. Doporuč jen, ať zvolí hosting, který zvládne hodně souborů
  (hlášení nepřesností potřebují server, viz výše).

## Jak to funguje (pro úpravy kódu)

- `geo.py`: souřadnice a kamera (dimetrie 2:1, pohled od jihozápadu), čte `mesto.json`. Čtverec (0, 0) je střed města,
  i roste na východ, j na sever. `MARGIN` 80,5 m: každý čtverec kreslí i pás sousedů (budovy, stromy) kvůli švům.
- `prep.py DATA i,j`: scéna čtverce do `build/` (0,0) nebo `build/c<i>_<j>/`. Obecná pravidla budov: činžák (navazující
  nepravidelné budovy v husté zástavbě) má šikmou střechu, panelák (navazující obdélníky se stejnou hloubkou) plochou;
  plochá střecha také tam, kde laser měří vršek skoro stejně jako medián. Kostel má vždy věž. Garáže = řada malých
  budov bez typu.
- mosty a lávky: úsek s `is_bridge` (OSM/Overture) delší než 8 m se nemaluje do terénu, ale je 3D mostovka (`scene.bridges`):
  nad cestou nebo tratí pod mostem zvednutá na 5,2 m (trať 6 m), nad vodou trochu nad hladinu, v rozpětí horní konvexní obálka (bez propadů).
  Nájezdy (žádné „skokánky“): mostovka pokračuje do navazující cesty stejného druhu (i když most končí uprostřed segmentu, až 8 kusů / 200 m)
  a výška nad terénem klesá nejvýš o `GRADE` (silnice 6 %, trať 3 %, lávka 8 %) – nájezd tak kopíruje svah. Ta část cesty se pak
  nekreslí do terénu (auta jedou po mostovce). Navazující kusy jednoho mostu se nejdřív spojí (linemerge). Bez navazující cesty
  konec přistane strměji (max. 20 %). V `render.html` je mostovka souvislý pás (pokos na ohybech, výška vyhlazená); do 2,5 m nad
  terénem je to zemní násyp se svahem 1:0,5 bez zábradlí a pilířů; kde se dotýká jiná mostovka (`covered`), boky a zábradlí se nekreslí.
  Šířka silnic: `rwidth` v `prep.py` (jednosměrný pás dvoupásové silnice 60 %, sjezd `link` max. 5 m). Silnice: boky, betonové zábradlí, pilíře po 22 m, středová čára; lávky úzké se sloupkovým
  zábradlím. Auta jedou po mostovce (`roads` v info_src), MHD z jízdních řádů přes `decks` v overlay.json (`deckZ` v app.html).
  Tunel kratší než 60 m je podjezd: kreslí se jako cesta (nad ním vede most), delší tunely se nekreslí.
- `render.html` + `render.mjs`: three.js v headless Chromiu po dlaždicích, varianty `spring summer autumn winter lights`.
  Na macOS s GPU (Metal). Výstup se může o odstín lišit mezi běhy, proto se renderuje vždy celý čtverec.
- `dzi.py`: paleta a Deep Zoom pyramidy (WebP). `compose.py`: složený náhled. `preview.py`: rychlý náhled pro web.
- `overlay.py`: klikací vrstva (obrys budovy = konvexní obal půdorysu dole a nahoře). Po úpravě budov vždy spustit
  a obrys zkontrolovat v prohlížeči.
- `web/app.html`: prohlížeč (OpenSeadragon + animovaná vrstva: auta, MHD, lyžaři, kouř, počasí). Texty `{{…}}`
  doplní `build_html.py` z `mesto.json`.
