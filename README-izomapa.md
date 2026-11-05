# izomapa

Izometrická pixel-art mapa českého města z otevřených dat, ve stylu SimCity 2000. Celé město s budovami, výškami
změřenými laserem, lesy, poli, ročními obdobími, nocí, počasím a MHD podle jízdních řádů.

**Živá ukázka: [izomapa.fun](https://izomapa.fun)**, izometrický Liberec. Otevřete si ji a projděte, jak hotová mapa
vypadá a co umí (roční období, noc, počasí, jezdící MHD, klikací budovy).

[![Izometrický Liberec na izomapa.fun – klikněte pro živou mapu](priklady/liberec.webp)](https://izomapa.fun)

Liberec si dál vyvíjím a starám se o něj sám, proto si dál vyvíjím a starám se o něj sám, proto
tu nejsou jeho ručně vyladěné stavby, příběhy míst ani hra Najdi Vildu. Prosím, nedělejte další Liberec. Postavte si
svoje město. Tenhle repozitář je přesně na to.

Autor: [Adam Klatovský](https://www.adamklatovsky.cz/). Je to zdarma. Když vám to ušetří práci,
[kupte mi kafe ☕](https://donate.stripe.com/3cI6oA0PE7u65Y9cim8g000). Jak to vzniklo, popisuju v článku
[Jak jsem s AI postavil izometrický Liberec](https://www.adamklatovsky.cz/blog/jak-jsem-s-ai-postavil-izometricky-liberec).

![Izometrická Chrastava, postavená tímhle repem bez ručních úprav](priklady/chrastava.webp)

*Chrastava u Liberce: 9 čtverců, postaveno od nuly za zhruba hodinu, bez jediné ruční úpravy.*

## Jak si udělat svoje město

Počítá se s tím, že to budete dělat s AI agentem. Vyzkoušené je to s [Claude Code](https://claude.com/claude-code),
jiní agenti (třeba Codex) by měli zvládnout totéž, ale nezkoušel jsem to. V repu je `CLAUDE.md` (a `AGENTS.md` s odkazem
na něj), návod přímo pro AI. Stačí repo stáhnout, otevřít ho v agentovi a napsat:

```sh
git clone https://github.com/adamklatovsky/izomapa.git
cd izomapa
```

> Udělej mi izometrickou mapu Chrastavy.

Místo Chrastavy napište svoje město. Agent založí město, stáhne data, postaví mapu a ukáže vám náhled. Potom s ním ladíte to, co z dat poznat nejde:
kostely, radnici, zámek, příběhy míst.

> **Pozor: výsledek je potřeba projít.** Otevřená data mají chyby a AI je dělá taky: budova na špatném místě,
> nesmyslná výška, les tam, kde je louka, nebo vymyšlený detail v příběhu místa. Mapu si projděte čtvrť po čtvrti
> a porovnejte se skutečností. Nejlíp to zvládne někdo, kdo město dobře zná. Než ji pustíte ven, ověřte hlavně
> texty a fakta.

Bez AI to jde taky:

```sh
./setup.sh                          # první spuštění: Python prostředí, Node, Chromium
python novy_mesto.py "Chrastava"    # najde hranici obce a zapíše mesto.json
./postav.sh                         # stáhne data a postaví celé město do složky site/
npm run nahled                      # náhled na http://localhost:8000
```

## K čemu se to hodí

- hravá mapa obce na web města, infocentra, školy nebo spolku,
- výstava, kronika, výuka zeměpisu a místní historie,
- vlastní projekt pro radost: najít svůj dům, přepnout na zimu, ukázat to sousedům,
- naučit se, co všechno se dá postavit z otevřených dat a s AI.

## K čemu se to nehodí

- **Navigace a přesné měření.** Je to vizualizace, ne digitální dvojče. Lampy, auta, část stromů a hroby jsou
  doplněné podle typu území, ne podle skutečnosti.
- **Mimo Česko** (bez úprav). Výšky budov, ortofoto a pole jsou z dat ČÚZK. V zahraničí poběží základ z OpenStreetMap,
  ale bez laserových výšek a bez lesa a polí podle skutečnosti.
- **Velká města na slabém počítači.** Renderuje se na grafické kartě. Malé město je hotové za hodinu, krajské město
  zabere den a desítky GB.
- **Zázrak na jedno kliknutí.** Automaticky vyjde celé město, ale dominanty (kostel, radnice, zámek) budou obecné,
  dokud je s AI nedoladíte podle fotek.

## Co vyjde automaticky a co je ruční práce

| automaticky z dat | ručně (s AI, podle fotek a ortofota) |
|---|---|
| všechny budovy z OpenStreetMap s půdorysem | tvar dominant: věže, kopule, barvy (`assets/dominanty.json`) |
| skutečné výšky budov a tvar střech (laser ČÚZK) | příběhy míst (`mista.json`), jen když o ně požádáte |
| les jen tam, kde opravdu je (ortofoto ČÚZK) | oprava chyb v OpenStreetMap |
| pole s plodinou podle ročního období, louky (ZABAGED) | vlastní modely zvláštních staveb |
| terén, voda, silnice, koleje, sjezdovky | |
| mosty a lávky ve 3D s pozvolnými nájezdy, krátké podjezdy | |
| 4 roční období, noc, déšť, sníh, mlha | |
| auta, MHD podle jízdních řádů (GTFS), lyžaři | |
| klikací budovy s adresou, hledání, názvy částí obce | |
| | hlášení nepřesností od lidí s administrací (volitelný server, `hlaseni/`) |

**Mosty a lávky** jsou skutečné 3D mostovky: nad silnicí nebo tratí mají podjezdnou výšku a nájezdy na ně vedou
pozvolna po navazující silnici (stoupání do 6 %, u trati 3 %), takže nevypadají jako skokánky. Auta i MHD po nich
jezdí nahoře. Mostovka je souvislý pás (bez zubů a mezer na ohybech), nízké nájezdy jsou zemní násyp s travnatým
svahem a souběžné mostovky (dva jednosměrné pásy) nemají zábradlí mezi sebou. Jednosměrný pás dvoupásové silnice
je užší než obousměrná silnice a sjezdy mají jeden pruh. Kratší podjezdy pod silnicí nebo tratí jsou vidět jako cesta, nad kterou vede most.

**Příběhy míst se samy negenerují.** Mapa je bez nich úplně v pořádku. Vzniknou jen tehdy, když o ně agenta
požádáte. Návod mu ukládá psát jen z ověřených zdrojů (web obce, Památkový katalog, Wikipedie) a nic si nevymýšlet,
ale i tak se může splést, proto fakta před zveřejněním ověřte.

## Data a licence

| zdroj | co z něj je | licence, povinnost |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) přes [Overture Maps](https://overturemaps.org/) | budovy, adresy, ulice, voda, využití území, místa | ODbL: uvést „© přispěvatelé OpenStreetMap“ |
| [ČÚZK](https://geoportal.cuzk.cz/): DMP 1G | výšky budov a tvar střech (laserové skenování) | CC BY 4.0: uvést „© ČÚZK“ |
| ČÚZK: ortofoto | kde je les, barvy polí | CC BY 4.0 |
| ČÚZK: ZABAGED | orná půda, louky, sady | CC BY 4.0 |
| [Terrarium](https://registry.opendata.aws/terrain-tiles/) (AWS Open Data) | terén | s uvedením zdroje |
| jízdní řády GTFS dopravce | MHD | **podmínky konkrétního dopravce si ověřte** |

Prohlížeč má zdroje dat v patičce nápovědy, nechte je tam.

**Nikdy neobkreslujte z Google Maps, Apple Maps ani Mapy.cz.** Jejich podmínky to zakazují. Prohlížet si je jako
vodítko je v pořádku. Tvary a barvy berte z ortofota ČÚZK a z vlastních fotek.

Kód je pod licencí **AGPL-3.0** (soubor `LICENSE`). Kdo ho upraví a pustí jako web, musí zveřejnit i svoje úpravy.

## Co potřebujete

- Mac s Apple Silicon (M1 a novější), nebo PC s grafickou kartou. Bez GPU (v cloudu) to jde taky, ale řádově pomaleji.
- Python 3.9+, Node 18+.
- Disk: malé město jednotky GB, krajské město desítky GB.
- Hosting pro statický web. Výsledek je obyčejná složka `site/` s tisíci obrázků, nahrajete ji kamkoli, kde zvládnou
  hodně souborů. Kam a jak, je na vás.

## Jak to funguje

Žádná AI tu nekreslí obrázky. Mapa je vlastní 3D scéna (three.js) vyrenderovaná v prohlížeči bez okna a styl dělají
shadery: stupňované světlo, obrysy, světlé hrany a sjednocená paleta. Mezi dlaždicemi proto nejsou švy a nic se
nevymýšlí. AI (Claude) pomáhá psát pravidla, dívá se na rendery a opravuje, co nesedí.

Město je skládačka čtverců 2,3 × 2,3 km. Každý čtverec se připraví a vyrenderuje zvlášť a sousedé si navzájem
kreslí pás přes šev, takže na sebe navazují.

```
novy_mesto.py   hranice obce -> mesto.json (střed, čtverce, výšky, texty)
fetch_data.py   OpenStreetMap/Overture + terén, místa, části obce -> data*/
dmp_fetch.py    listy laserového skenování ČÚZK -> build/dmp/
dmp_vysky.py    změřené výšky budov -> assets/dmp_vysky.json
nastroje/       les.py (ortofoto, maska lesa), zabaged.py (pole, louky), pole.py (pole podle barvy)
prep.py         data -> scéna čtverce (budovy, stromy, textury terénu podle ročního období)
render.html     renderer (three.js), render.mjs ho pouští po dlaždicích v Chromiu
dzi.py          dlaždice -> Deep Zoom pyramidy (WebP) do site/tiles/
overlay.py      klikací vrstva: obrysy budov, adresy, ulice, místa, doprava
mhd.py          jízdní řády GTFS -> site/mhd.json
hlaseni/        volitelný server: hlášení nepřesností a administrace /admin
web/app.html    prohlížeč, build_html.py z něj udělá site/index.html
```

## Hlášení nepřesností od lidí (volitelné)

Na mapě si lidé rychle všimnou, co nesedí. Proto je k dispozici i sběr připomínek: v kartě budovy nebo místa je
tlačítko **Nahlásit nepřesnost**, člověk napíše pár slov a vy je pak vidíte v jednoduché administraci na `/admin`
(seskupené podle budovy, s počtem hlášení, odkazem „Na mapě“ a stavy nové / vyřešené / zamítnuté).

Statický web sám hlášení uložit neumí, potřebuje malý server. Hotový je ve složce `hlaseni/` pro Cloudflare Workers
s databází D1 (na malou mapu stačí bezplatný tarif). Jinde to jde taky, `hlaseni/worker.js` je krátký a agent ho
přepíše pro jiný hosting.

1. V `mesto.json` nastavte `"hlaseni": true` a spusťte `python build_html.py` (v mapě se objeví tlačítko a do `site/`
   přibude `admin.html`).
2. `cp hlaseni/wrangler.example.jsonc wrangler.jsonc`, doplňte název a `npx wrangler d1 create izomapa-hlaseni`
   (vypíše `database_id`, to patří do `wrangler.jsonc`).
3. `npx wrangler d1 migrations apply izomapa-hlaseni --remote`
4. `npx wrangler secret put ADMIN_PASSWORD` (heslo do administrace) a `npx wrangler deploy`.

Ukládá se jen text hlášení, místo na mapě a otisk (hash) IP adresy kvůli limitu 3 hlášení za hodinu, ne IP samotná.
Bez `"hlaseni": true` se tlačítko nezobrazí a web zůstane čistě statický.

## Známé pasti

- **Chyby v OpenStreetMap** se do mapy propíšou: kostel na špatném místě, potok pod domy, rybník jako asfalt.
  Oprava je nejlepší přímo v OpenStreetMap, pomůže to všem.
- **Laserové výšky jsou pár let staré.** Novostavby v nich chybí, ty mají výšku z OpenStreetMap nebo odhad.
  Budovy s pochybným měřením jsou v `build/dmp_kontrola.csv`.
- **Švy mezi čtverci.** Po úpravě stavby do 80 m od hranice čtverce přerenderujte i souseda.
- **Celý čtverec, ne pár dlaždic.** Grafická karta může vrátit o odstín jiné barvy, proto se renderuje celý čtverec.
- **Obrys budovy při najetí myší** se počítá z dat. Když změníte tvar nebo výšku stavby, spusťte `overlay.py`
  a obrys zkontrolujte.

## Podpora a kafe

Repozitář dávám k dispozici, jak je. Podporu neposkytuju, na issues a pull requesty se podívám, až budu mít čas.

Když vám repo ušetřilo práci, můžete mi [koupit kafe ☕](https://donate.stripe.com/3cI6oA0PE7u65Y9cim8g000).
Budu rád, když v mapě necháte nenápadný odkaz „Vytvořeno nástrojem izomapa“ (je v O projektu a v nápovědě).

Prosím, nepojmenovávejte svoje projekty „Izomapa“ a nepoužívejte domény izomapa.fun a izomapa.cz. Pojmenujte je po svém městě.
