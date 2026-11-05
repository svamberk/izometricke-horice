# Jak přispět do Izometrických Hořic

Díky, že chcete pomoct. Mapu nejlíp vylepší lidi, kteří Hořice a okolí znají. Každá změna jde přes
**pull request** a před sloučením ji zkontroluje správce repozitáře – do hlavní větve se zapisuje jen po schválení.

## 1. Chyba v datech → opravte ji v OpenStreetMap

Chybějící dům, cesta, která neexistuje, rybník, který je ve skutečnosti mokřad, les místo louky – to všechno pochází
z [OpenStreetMap](https://www.openstreetmap.org/). Oprava přímo tam je nejlepší: pomůže všem mapám, které OSM používají,
a do Hořic se propíše při další stavbě (data jdou přes Overture Maps, zpoždění je pár týdnů).
Seznam podezřelých míst, která jsme našli, je v [docs/KONTROLA.md](docs/KONTROLA.md).

Když v OSM opravit nejde (třeba je to sporné), napište issue s polohou (odkaz z mapy – tlačítko sdílet – nebo lat/lon)
a popisem, ideálně s vlastní fotkou.

## 2. Dominanty (kostely, věže, výrazné stavby)

Obecné budovy se tvoří automaticky. Stavby, podle kterých lidi město poznají, mají ruční popis v
[assets/dominanty.json](assets/dominanty.json). Formát je popsaný v [CLAUDE.md](CLAUDE.md) (oddíl „Co ladit a jak“)
a ukázky jsou přímo v souboru – barvy zdí a střechy, tvar a výška střechy, věže (špice, barokní báň, kupole,
jehlan, příhradová konstrukce), hodiny, komíny.

**Podklady:**
- vlastní fotky (do složky `fotky/<název stavby>/`, ta se do gitu nedává) nebo volně licencované fotky
  z [Wikimedia Commons](https://commons.wikimedia.org/) – autora a licenci zapište do [docs/ZDROJE-FOTEK.md](docs/ZDROJE-FOTEK.md),
- ortofoto ČÚZK a laserové výšky – `python nastroje/dom_info.py <název> <lat> <lon>` vypíše půdorys, výšky z laseru
  (i kde je nejvyšší bod, tedy věž) a uloží výřez ortofota,
- fakta z Památkového katalogu, webu města nebo Wikipedie.

**Nikdy** neobkreslujte ze screenshotů Google Maps, Street View, Apple Maps ani Mapy.cz – podmínky těchto služeb to zakazují.

**Ověření:** po úpravě stačí přestavět čtverec, kde stavba stojí:

```sh
python prep.py data 0,0        # čtverec (0,0) = centrum; jinak např. python prep.py data_c-1_1 -1,1
./render_chunk.sh 0,0 summer   # jen letní varianta
python nastroje/qa_tools.py vyrez 0,0 summer 50.36694 15.63208
```

Do pull requestu přiložte výřez před a po a fotku nebo odkaz, podle kterého jste stavbu popsali.

## 3. Úpravy kódu

Pravidla generování jsou v `prep.py` (scéna: budovy, stromy, pole, silnice) a `render.html` (3D modely a shadery).
Změna pravidla ovlivní celou mapu, proto u pull requestu popište, co se změní, a přiložte výřezy z více míst.
Obecně užitečné opravy stojí za to nabídnout i do původního nástroje
[adamklatovsky/izomapa](https://github.com/adamklatovsky/izomapa).

## Pravidla

- Patička se zdroji dat (OpenStreetMap, ČÚZK) a odkaz „Vytvořeno nástrojem izomapa“ zůstávají.
- Fotky ani stažená data (`data*/`, `build/`, `site/`) do gitu nepatří.
- Kód je pod licencí AGPL-3.0.
