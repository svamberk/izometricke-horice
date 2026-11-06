# Změny oproti původní izomapě

Fork vychází z [adamklatovsky/izomapa](https://github.com/adamklatovsky/izomapa) (commit `b9df8e4`, 4. 11. 2026).
Obecně užitečné opravy (bezpečnost, Windows, dálnice) stojí za to nabídnout i do původního repozitáře.

## Bezpečnost

- **`render.mjs`: dočasný server pro renderer poslouchal na všech síťových rozhraních a šlo z něj číst libovolný
  soubor z disku** (cesta s `..%2f` prošla). Teď poslouchá jen na `127.0.0.1` a vydává jen soubory ze složky projektu.
- `render.mjs`: cesta k linuxovému Chromiu `/opt/pw-browsers/...` se na Windows vyhodnotila jako `C:\opt\...`,
  kam může zapisovat každý uživatel. Teď se použije jen na Linuxu.

## Windows

- `render.mjs`: na Windows renderuje grafickou kartou přes Direct3D 11 (`--use-angle=d3d11`). Původně padal na
  softwarový SwiftShader, řádově pomaleji.
- `postav.sh`: Python prostředí `.venv/Scripts` (Windows) i `.venv/bin`, `PYTHONUTF8=1` (jinak výpisy a soubory
  s češtinou padají na kódování cp1250).

## Generování mapy (`prep.py`, `render.html`)

- **Dálnice (`motorway`)** se dřív nekreslily vůbec (D35 chyběla, kreslily se jen „visící“ zábradlí jejích mostů).
  Teď: šířka podle počtu pásů, sjezdy jednopruhové, čára mezi pruhy, auta, bez lamp a chodníků. Úseky
  `is_under_construction` se nekreslí.
- **Příhradová ocelová věž** (`"top": "lattice"` u věže dominanty) – rozhledny a vysílače: zužující se nohy, vzpěry,
  plošina se zábradlím a červenobílý stožár.
- Samostatné objekty dominant (`"only": 1`) berou barvu zdí ze spec `"wc"` a umí hodiny.
- **Pole**: plodina, směr řádků a dělení se počítají z celého pole, ne z kusu ve čtverci – na švech čtverců pole
  navazují. Lány nad 30 ha se dělí na hony po 320 m s vlastní plodinou.
- **Lampy** jen v zástavbě (do 40 m od budovy), ne podél silnic v polích a lesích.
- **Silnice v lese**: stromy jihozápadně od silnice se sníží nebo vynechají, aby koruny při pohledu kamery
  nezakrývaly vozovku.
- **Zemědělské budovy** (stodoly, kravíny) jako haly s nízkou sedlovou střechou, ne jako paneláky nebo kanceláře
  s klimatizacemi. **Kruhové půdorysy** (sila, nádrže, jímky) jako betonové válce s plochou střechou.
- Výška šikmé střechy z laseru: když měření ukazuje nízkou střechu (p90 − medián), střecha se sníží (dřív měly
  široké haly 7m střechy nad 2m zdmi).
- **Sochy** (OSM `artwork`) i mimo hřbitovy – Hořice jsou město soch. Body uvnitř budov (reliéfy) se vynechají.
- Areály škol jako trávník se stromy místo holé dlažby.
- Vodní plochy, které jsou podle ortofota zarostlé, jde v `mesto.json` (`"voda_mokrad": [id z Overture]`)
  vykreslit jako mokřad (kaliště cukrovaru u Dobré Vody).
- Mostovky silnic jsou v zimě odklizené jako silnice na terénu.
- `render.mjs` předává okraje s řezem zeminou jen na vyžádání, jinak platí scéna (strany bez souseda),
  jak uvádí `render_chunk.sh`.

- **Okna podle podlaží**: domy, činžáky a paneláky mají výšku pásu oken podle počtu podlaží (z dat nebo z laseru),
  ne pevných 2,8–3,2 m. **Obchody v přízemí** (výlohy, v noci rozsvícené) u budov, kde je podle Overture Places
  obchod nebo služba (bez čerpacích stanic, skladů, výroby).
- `URBAN = True`: sevřená městská zástavba i v centrálním čtverci (Hořice).
- Adresy ve vesnicích bez pojmenovaných ulic jako „Chlum 56“ (dřív jen číslo). Typy budov česky
  (hospodářská budova, chata, trafostanice…), neznámý typ jako „budova“.

## Barvy střech a fasád

- **Střechy** (`nastroje/strechy.py`): barva každé budovy změřená z ortofota ČÚZK (0,25 m/px; osvětlená část
  střechy bez zeleně a stínu) a převedená do palety mapy. Výsledek v `assets/satelit.json` (`rc`).
- **Fasády** (`nastroje/fasady_fotky.py`, `nastroje/fasada_barva.py`, `nastroje/fasady_slouc.py`): barva zdí
  asi 280 domů změřená z volně licencovaných fotek ulic na Wikimedia Commons. Každou fotku prošel agent (směr
  pohledu, přiřazení domů k půdorysům, výběr čisté zdi bez oken a stínu) a druhý agent měření zkontroloval;
  zamítnuté (špatný dům, zeď ve stínu) se nepoužily. Výsledek v `assets/upresneni.json` (`wc`), zdroje fotek
  v [ZDROJE-FASAD.md](ZDROJE-FASAD.md). Street View, Mapy.cz ani jiné panoramy se nepoužívají (podmínky to zakazují).

## Otáčení mapy

- Čtyři pohledy po 90°: `IZO_POHLED` = 0 od jihozápadu (výchozí, jako originál), 1 od SZ, 2 od SV, 3 od JV
  (`geo.py` `POHLED`, `render.html` parametr `az`). **Slunce se otáčí s kamerou**, takže je osvětlení (světlá pravá
  stěna, stíny) ve všech pohledech stejné.
- Na směru kamery závisí i pořadí skládání čtverců (`geo.depth_of`, `compose.py`, prohlížeč), snížení stromů u lesních
  silnic (`prep.py`), prosklená strana shedových střech a viditelné stěny jedoucích aut.
- `./postav_pohledy.sh` postaví pohledy 1–3: dlaždice do `build/.../tiles_<varianta>_pN`, web do `site/pN/`
  (dlaždice, overlay, náhled, hledání; `index.html` a `mhd.json` jsou společné). `set_status.py` zapíše do `tiles.json`
  seznam hotových pohledů.
- Prohlížeč: tlačítka ⟲ ⟳ a střelka severu (klepnutí = zpět na výchozí pohled). Otočení načte stránku znovu se stejným
  středem a přiblížením, pohled je v odkazu (`#leto-sv@50.368,15.632,1`). Bez všech čtyř pohledů se tlačítka neukážou.

## Prohlížeč (`overlay.py`)

- **Závodní motorky**: `assets/okruh.json` (trasa okruhu z OSM, `nastroje/okruh.py`) -> `overlay.json` `moto`. V prohlížeči
  jezdí 8 motorek dokola ve směru závodu, rychlost podle poloměru zatáček (brzdění a zrychlení), každá jinou stopou.
  Patří k vrstvě Doprava, v zimě nejezdí. Hořice: 300 zatáček Gustava Havla, po směru hodinových ručiček.

- **Názvy obcí a částí** nad mapou (podle adres; místo s nejhustší zástavbou části), Hořice větším písmem.
  Úvodní pohled tak ukazuje „Hořice“, ne název náměstí.

## Nástroje

- `nastroje/dom_info.py` – podklady k dominantě (půdorys, laser, ortofoto, výřez renderu).
- `nastroje/qa_tools.py` – mozaiky čtverců pro kontrolu, převod pixelu dlaždice na souřadnice, výřezy.
- `nastroje/ftp_nahraj.py` – nahrání webu na FTP(S) hosting s vsftpd (znovupoužití TLS relace).
- `nastroje/strechy.py`, `nastroje/strechy_lab.py` – barvy střech z ortofota (Lab, převod do palety).
- `nastroje/fasady_fotky.py` (stažení fotek z Commons přes API, mapky kandidátů), `nastroje/fasada_barva.py`
  (barva zdi z vyznačené části fotky), `nastroje/fasady_slouc.py` (sloučení měření do `assets/upresneni.json`).
