# MomentumMate – handbok

**Version 2026-11-23. Utgångspunkt för utvecklingsarbete 3.0.**
Läsordning inför varje nytt utvecklingssteg: `docs/HANDBOK.md` (detta dokument) → `SPEC.md` (regelverket, facit) → `STATUS.md` (läget just nu) → `TESTPLAN_2.0.md` (prövade och planerade tester).
Samma fyra dokument ligger i claude.ai-projektet *MomentumMate* (mappen `claude/`) och i repots rot/`docs/`. Repot är sanningen för kod; projektet är sanningen för beslut.

---

## 1. Vad MomentumMate är

MomentumMate är en **regelstyrd momentumstrategi för aktier med en iPhone-app som beslutsstöd**, byggd oktober–november 2026 av Helene (ägare, fattar alla beslut om strategi och handel) och Claude (bygger, testar, dokumenterar).

Verktyget består av tre lager:

1. **Strategin** – modell 2.0, en månadsvis momentumrotation över ca 2 300 aktier i Norden, USA och Europa. Varje regel som kan påverka ett köp eller en försäljning är validerad i ett tioårigt backtest och står i `SPEC.md` med körning och datum. Allt annat är information.
2. **Datamaskineriet** – Python-skript som körs i GitHub Actions: skanning av hela universumet tre gånger per dag, daglig datakontroll, daglig institutionell radar och analytikerdata, intradagskurser när jobbet startas, backtest på beställning. Resultaten publiceras som JSON-filer i GitHub-repot `MomentumMate/MomentumMate`.
3. **Appen** – en enda fil, `index.html`, som körs som webbapp på GitHub Pages (`momentummate.github.io/MomentumMate/`) och läggs på hemskärmen i iPhone. Den läser JSON-filerna, håller portföljerna lokalt i telefonen och räknar ut förslaget enligt SPEC. Ingen server, inget konto, ingen databas – all portföljdata ligger i telefonens localStorage (export/import finns).

Det appen **gör**: rankar universumet, visar vilka av dina innehav som klarar hållregeln, räknar ut hela månadsprövningen (sälj, likavikta, köp) i kronor och antal, loggar varje rad och följer upp avkastningen mot modellportföljen och index. Det appen **inte gör**: handlar. Varje order läggs för hand hos mäklaren och registreras sedan i appen.

Handelsrytm: **en gång i månaden**, på månadens sista stängning räknas förslaget; hela listan handlas nästa handelsdag. Däremellan tittar man hur ofta man vill men agerar inte.

---

## 2. Arkitektur

### 2.1 Komponenter (allt i repots rot)

| Fil | Roll |
|---|---|
| `index.html` | Hela appen (HTML + CSS + JS, ~5 000 rader). Versionssträng `APPV` (t.ex. `2026-11-20-l`); appen jämför mot startsidan och erbjuder omladdning. |
| `scanner.py` | Skanningen: hämtar listor och kurser, räknar poäng, bygger `data.json`, kurshistorik per aktie (gren `hist`), daghandelslista och live-kurser (gren `live`). |
| `backtest.py` | Backtestet (10 år, EODHD, P3-basis). Strategitester via `--strat`, paritetstest mot appen via `--tie`/`--parity`. |
| `datacheck.py` | Daglig datakontroll → `datacheck.json`/`.md`. Rött = spärr i appen. |
| `radar.py` | Institutionell radar (13F, 13D/G, ARK, FI-insyn, SEC Form 4) → `radar.json`, `cusip_map.json`. |
| `analysts.py` | Riktkurser och upp-/nedgraderingar (Yahoo) för hela universumet → `analysts.json`, historik i `analysts_hist.json`. |
| `parity_test.js` | Kör appens förslagsmotor och en oberoende referens skriven från SPEC på samma data; röd körning = appen avviker från SPEC. |
| `SPEC.md`, `TESTPLAN_2.0.md`, `STATUS.md`, `docs/HANDBOK.md` | Dokumentationen (se avsnitt 0). |
| `holdings.txt` | Aktier som ska finnas i universumet även om de inte fångas av någon indexlista. |
| `markets_extra.json` | Egna börser/bevakningslistor som ägaren lagt till i appen. |
| `radar_watch.json` | Förvaltarna radarn skuggar (24 st), redigeras i appen. |
| `live_watch.json` | Alla portföljers innehav, skrivs av appen; styr vilka aktier live-jobbet och analytikerjobbet hämtar. |
| `lists_cache.json`, `lookup.json`, `cusip_map.json` | Cacher (indexlistor med valuta/ISIN, uppslag, CUSIP→ticker). |

### 2.2 Datafiler appen läser

| Fil (gren) | Innehåll | Uppdateras |
|---|---|---|
| `data.json` (main) | Alla aktier: kurs, poäng `sc2`, Clenow `cl`, trendkvalitet `tq`, överhettning `heat`, avstånd till SMA200 `d200`, 12-1, SMA50/200, RSI, ATR, omsättning, valuta `ccy`, sektor `sec`, börsvärde `mc`, likviditetsflagga `liqok`, daghandelsfält; rankning per marknad; växelkurser `fx`; indexserier `idx` (OMXSPI, MSCI World via URTH, USD/SEK, 5 år); statistik. | Skanning 3×/dag |
| `h/<ticker>.json` (hist) | Kurshistorik per aktie: 2 år dagligen (OHLCV), 5 år, dagens 5-minutersstaplar (`1d`), senaste veckan (`1w`). | Skanning |
| `daytrade.json` (live) | Live-kurser (`quotes`), dagens 5-minutersstaplar (`intra`) för portföljinnehav + bevakning + topp 20 + kandidater, daghandelskandidater. | Live-jobbet (startas från appen; GitHub kör schemat sporadiskt) |
| `datacheck.json` (main) | Datakontrollens utfall per aktie och marknad. | Varje natt efter handelsdag |
| `radar.json` (main) | Radar. | Varje morgon |
| `analysts.json`, `analysts_hist.json` (main) | Riktkurser för hela universumet (prioriterade dagligen, resten roterande, `asof`), riktkursändring per analyshus 30 d (`pt30`); historik i `_hist`. | Varje morgon (`analysts.yml`) |
| `news.json` (news) | Nyhetsflöde (RSS + Google News) för marknad och innehav. | Var 15:e minut |

### 2.3 GitHub Actions (Python 3.13)

GitHub kör schemalagda jobb timmar sent eller inte alls. Därför har de viktiga jobben **många reservtider och en grind** som släpper igenom en körning per pass:

| Workflow | Schema | Grind |
|---|---|---|
| `scan.yml` | 12 körtider | En körning per pass: 12:30, 14:15, 21:30 UTC (Norden-stängning, USA-öppning, USA-stängning) + ikapp-körning nattetid när < 80 % av en marknad har senaste stängningen. Konfliktsäker commit. Startar datakontrollen om den blir röd. |
| `datacheck.yml` | 6 körtider | En per dygn, natten efter handelsdag. |
| `radar.yml` | 4 körtider | En per dygn. Kör `radar.py`. Kräver hemligheten `RADAR_CONTACT` (SEC kräver kontakt i User-Agent). |
| `analysts.yml` | 4 körtider | En per dygn (2026-11-23). `analysts.py`: prioriterade + 600 roterande; ändring av `.github/analysts_request.txt` ger fyllnadskörning av hela universumet. |
| `test3.yml` | vid ändring av `.github/test3_request.txt` | Strategitester 3.0 parallellt; resultat på grenarna `test3-<namn>`. |
| `daytrade.yml` | var 5:e min 12–21 UTC (i praktiken 1–2 ggr/dag) | Startas från appen ("Hämta dagens staplar"). |
| `news.yml` | var 15:e min | – |
| `backtest.yml` | manuell | Val `strat` (gap/breadth/sector/invvol/all), parametrar; timeout 300 min. Resultat i loggen (facit) och `backtest.json` på gren `backtest`. |
| `parity.yml` | vid ändring av index.html, parity_test.js, SPEC.md, data.json | Röd = appen avviker från SPEC. |
| `diagnos.yml`, `lookup.yml` | manuell / från appen | Diagnos av tickers eller 13F-filer; uppslag av ny aktie. |

Appen kan starta skanning, live-jobb, datakontroll, radar och uppslag direkt (kräver GitHub-token i Inställningar). Token används också för att skriva `holdings.txt`, `markets_extra.json`, `radar_watch.json` och `live_watch.json`.

### 2.4 Datakällor och deras gränser

| Källa | Används till | Begränsning |
|---|---|---|
| **EODHD** (betald plan) | Dagsstängningar hela universumet, symbollistor (valuta, ISIN), växelkurser | Intradag och fundamenta ingår inte. Nordiska stängningar kan släpa 1–3 timmar → 80 %-regeln. |
| **Yahoo** via yfinance | Indexlistor, kontroll mot EODHD, 5-minutersstaplar, riktkurser/graderingar | Stryper vid hela universumet; fungerar från Actions för urval (~30–120 bolag). |
| **Finnhub** (gratis) | Live-kurser i appen (nyckel i Inställningar) | Staplar är betalfunktion (403). |
| **SEC EDGAR** | 13F, 13D/G, Form 4 | User-Agent "Namn e-post" krävs; GitHub-domän avvisas. 13F släpar 45 dagar efter kvartal. |
| **OpenFIGI** | CUSIP → ticker | Cache i `cusip_map.json`. |
| **ARK** | Dagliga fondinnehav | ARKW/ARKF/ARKX-filerna på ark-funds.com uppdateras inte (märkta, utanför signalerna). |
| **FI insynsregistret** | Nordisk insyn (CSV-export, ISIN → ticker) | 14 dagar bakåt. |
| **Google News/RSS** | Nyheter | – |

---

## 3. Strategin – modell 2.0 (sammanfattning av SPEC)

SPEC.md är facit; detta är sammanfattningen. Versionstabell: 1.0 (2026-11-04, 12-1 med tak 150) → **2.0 (2026-11-05, Clenow 120 d)**.

**Mål (§ 1).** Högsta tillväxt över tid med en regelstyrd strategi utan diskretionära bedömningar. Priset är djupa nedgångar (backtest −43 %, räkna med −50 %). Risknivån styrs av hur stor del av kapitalet som ligger i strategin, inte i modellen.

**Universum och data (§ 2).** Ca 2 300 aktier: OMXS/OMXC/OSE/OMXH, S&P 500/400/600, Nasdaq-100, Dow, ADR, STOXX 600 m.fl. Stängningskurser; signal på senaste stängning, tidigaste handel nästa dag. Minst tolv månaders historik. Likviditetsgolv: 20-dagars omsättning under 3 miljoner i handelsvalutan → poäng 0.

**Poäng 0–100 (§ 3).**
- Momentum 80 %: **Clenow-måttet** = årstaktad lutning i regression av ln(stängning) mot tid över 120 handelsdagar × R², uttryckt i procent, skalat 0–150 → 0–100.
- Trendkvalitet 20 %: kurs > SMA50 (30), kurs > SMA200 (30), SMA50 > SMA200 (20), SMA200 stigande (20).
- Överhettningsavdrag: upp till 25 poäng vid > 25 % på en månad och/eller RSI > 80.
- Lika poäng: högre Clenow; exakt lika: ticker i bokstavsordning. Bara aktier över SMA200 rankas som köpkandidater.
- 12-1 (kalendermånader) används i hållregeln och som information.

**Portföljstorlek (§ 4, § 8).** Kapital ≥ 161 000 kr: **20 innehav**, alla marknader, målvikt 5 %. Under: **kostnadsläge, 7 innehav, bara nordiska**, målvikt 14,3 %. Minsta rad 1 500 kr. Kapitalet räknas inklusive nytt kapital.

**Köp (§ 5).** Vid månadsskiftet, när det finns lediga platser: högst rankade i topp 20 (topp 7 i kostnadsläge) som inte ägs, i ren rankningsordning. Alla platser fylls; kassa placeras alltid om kandidater finns. Nytt kapital utan lediga platser fylls på till målvikt, lägst vikt först.

**Håll och sälj (§ 6).** Behåll så länge 12-1 > 0 **och** kurs > SMA200. Prövas en gång per månad på sista handelsdagens stängning; bryts ett villkor säljs hela innehavet. Mellan prövningarna ger ett brutet villkor bara ⏳. Rankningsplats, RSI, vinst, innehavstid, sektor påverkar inte.

**Likaviktning (§ 7).** Sista handelsdagen i mars/juni/september/december: innehav > 1,25 × målvikt minskas, < 0,75 × målvikt ökas (om hållregeln klaras). Inget tak per aktie mellan kvartalen.

**Nynoterade (§ 9).** Under tolv månaders historik: BEHÅLL "inget underlag", fylls inte på, får aldrig KÖP/ÖKA.

**Tidsregler (§ 10).** Räkna om en gång på månadsskiftets stängning; handla hela listan nästa dag (Norden förmiddag, USA efter 15:30). Räkna inte om flera gånger samma dag.

**Förväntan (§ 14).** Backtest 2015–2026: 42,8 % per år, Sharpe 1,20, Calmar 1,00, nedgång −43 %. Överlevnadsbias mätt (nedre gräns): 44 → 36 % när S&P 500-medlemmar bara får vara kandidater från inträdesdatum. **Räkna med hälften: 20–25 % per år, enskilda år −30 till +100 %, nedgång omkring −50 %.** Kraschfönster: 2.0 faller mindre i snabba nedgångar (2015–16, 2018), mer i utdragna (2022, våren 2025).

**Förkastat (§ 11), i korthet.** Stopp −15 %, band 10/30, daglig prövning, gapsäljning (tre gånger: #82, strat 2026-11-18), stå kvar genom krasch, rotation, trötta innehav, volatilitetsjusterat momentum som poäng, 6-1/3-1/blandat, volymbekräftelse, RSI/MACD-filter, fundamenta, sektortak, vikt efter styrka, jämnhet, vinstmål, tak per aktie, likviditetsgolv i kronor, Clenow 60/250 d, 15/25/30 innehav, riskbudget efter bredd, omvänd volatilitet.

**Paritet (§ 13, § 18).** Backtest och app ska göra samma sak; tabellen i § 18 listar varje punkt (fyllnad T+1, köptillfälle, 12-1 i kalendermånader, Clenow-fönster, likviditetsgolv, felkurser lagas). Paritetstestet körs automatiskt vid varje ändring. Kvar att harmonisera: avknoppningar.

---

## 4. Appens logik

### 4.1 Lagring och tillstånd
- `P.ps[]` – portföljer: innehav `h` (ticker, antal, GAV, datum), transaktioner `tx`, regler, plan (senaste förslaget), månads-/kvartalsmarkörer, kassauppgift, insättningar/uttag `flows`, demo-objekt, logg.
- `P.snap` – dagliga ögonblicksbilder av värde per portfölj (grund för avkastning och riskpanel; kräver att appen öppnas regelbundet).
- `P.alloc` – kapital utanför strategin (allokeringsvyn).
- `Ui` – vyläge (flikar, öppna rader). Inställningar (avgifter, Finnhub-nyckel, GitHub-token, intro) i separata nycklar.
- Export/import av hela portföljdatat som JSON.

### 4.2 Beslutsmotorn
1. **Profil** (`profileFor`): kapital = portföljvärde + kassa att placera → fullt läge (20) eller kostnadsläge (7, nordiskt).
2. **Hållprövning** per innehav: 12-1 > 0 och kurs > SMA200 (ur `data.json`); nynoterade undantas. Öppet månadsfönster (dagen efter sista handelsdagen t.o.m. den 3:e; omräkning inom tio dagar) avgör om SÄLJ-rader skapas eller bara ⏳.
3. **Likaviktning** när kvartalsfönstret är öppet (inom 45 dagar): MINSKA/ÖKA mot band 0,75–1,25 × målvikt.
4. **Köp**: lediga platser fylls från rankningen (topp 20/7, ej ägda, ej sålda i samma förslag), belopp = målvikt × portföljvärde; nytt kapital utan platser → påfyllning.
5. **Förklaring per rad** (`explainRow`): SPEC-paragraf, poängdelar (Clenow → p, trendkvalitet → p, överhettning), hållvillkorens värden, plats, viktband.
6. **Spärr**: om datakontrollen är röd, inte körts natten efter senaste handelsdag, eller skanningen saknar senaste stängningen för en marknad (80 %-regeln räknad i appen) visas "FÖRSLAG EJ VALIDERAT – DATAPROBLEM".
7. Två knappar: **"Räkna om hela prövningen på dagens data"** (månadsskiftet) och **"Räkna om bara likaviktning och kassa"** (kvartal/nytt kapital).

### 4.3 Transaktionsloggen
Varje förslagsrad loggas när den räknas (status öppen) och följs till genomförd (antal, kurs, datum, courtage enligt avgiftsinställningen), delvis genomförd, avstådd (✕ + orsak) eller ersatt. Affärer utanför förslaget loggas som avvikelse. Loggen är det enda out-of-sample-underlaget. Avkastning: tidsviktad ur ögonblicksbilderna, rensad för registrerade insättningar/uttag (annars approximation med nettoköp); jämförs med OMXSPI och MSCI World i SEK och med demoportföljen. "Institutionellt stöd" fylls av radarn.

### 4.4 Demoportföljen
Ren modellportfölj utan avsteg: startkapital, placerar kassan själv vid nästa signal, utför varje order vid första skanning efter signalen till skanningens kurs med courtage (avgiftsinställningen) och slippage (0,05 % + 2 % × √(belopp/ADV), tak 3 %). Gör månadsprövning och likaviktning automatiskt. Räknas inte in i "Alla portföljer". Den är jämförelsenormen i loggen.

### 4.5 Avgifter och valuta
Avgiftsinställning: courtageplan (Avanza-nivåer m.fl.), växlingspåslag 0,266 % (uppmätt), minimicourtage 29 kr på utländska poster (grunden för 161 000-gränsen). Växelkurser från EODHD i `data.json`.

---

## 5. Funktioner i appen, vy för vy

### Hem
Modulerna: Portfölj (summa, dagens förändring), Förslag (antal rader, status), Börsdesk, Daghandel, Radar, Nyheter, datastatus (senaste skanning per marknad, datakontroll, live-jobb – med startlänkar), versionskontroll.

### Portfölj
- **Rullista** över portföljer + "Alla portföljer" + "Ny demoportfölj". Översikten visar varje portfölj som kort (värde, dagens, sedan start) och längst ner **ALLOKERING**: kapital i strategin mot kapital utanför (redigerbart), andel och vad en −50 %-nedgång i strategin betyder för hela kapitalet.
- **Portföljvy**: hero (värde, dagens, avkastning, kassa), **innehavskort** (nummer = vikt, flagga + ticker, bolagsnamn, trendpil, värde/avkastning/kurs med dagsförändring, momentumstapel; utfällt: innehav med antal/GAV, graf, analytiker, signal, hållvillkor, nivåer, handelsplan, nyheter, radar, knappar).
- **Förslag** (månadsprövningen) med rad för rad: SÄLJ/MINSKA/ÖKA/KÖP/BEHÅLL, belopp, antal, courtage, förklaring, ✕ för att avstå med orsak, "Registrera" vid genomförd affär.
- **RISK** (SPEC § 15): exponering per marknad/valuta/sektor, vikt mot målvikt och band, volatilitet 60 d, nedgång från topp (nu och största), värsta/bästa dag, hållvillkorens läge, koncentrationsvarningar, marknadsbredd (andel över SMA200, positivt 12-1, upp i dag, per marknad).
- **Portföljmeny**: Köp/sälj/flytta innehav, Innehav till skannern (`holdings.txt`), Transaktionslogg, Insättningar/uttag, Regler, Avgifter, Kassa, Import/export, Byt namn/ta bort.
- **Demoportfölj**: DEMO-tagg, hero inkl. kassa, rader "⏳ automatiskt"/"✓ utförd", egen logg och jämförelse.

### Börsdesk
- **Rullista**: Alla marknader, varje börs (Stockholm, Köpenhamn, Oslo, Helsingfors, USA, Europa, egna), Lägg till börs, Ändra ordning.
- Rad: antal aktier · andel över SMA200 · snittpoäng topp 25 · antal daghandelskandidater.
- **Flikar Momentum 25 / Daghandel / Alla** – samma på Alla marknader och per marknad. Momentum 25 = topplistan (2.0), Daghandel = kandidater och nästan-kandidater för marknaden, Alla = alla aktier sorterade efter poäng, 40 i taget.
- **Aktiekortet** (samma överallt): sidhuvud som i portföljen; utfällt: graf med intervall **1D / 1V / 1M / 3M / 1Y / 2Y / 5Y**, ljusstakar/linje, volym, SMA50/200, stöd/motstånd, handelsplan (entry, stopp, mål ur ATR), hårkors; 1D ritas över hela handelsdagen (öppning–stängning i marknadens tid, svenska klockslag) och fylls på när staplar kommer; grafhuvudet visar samma kurs som sidhuvudet. Därunder Analytiker (konsensusriktkurs, hög/låg, antal, rekommendation, uppsida, riktkurs mot 30 dagar, upp-/nedgraderingar 60 d), signal och hållvillkor, poängdelar, nivåer, nyheter, radar, "+ Portfölj"/"Handla idag".
- Ny aktie: sök → "Slå upp" (Yahoo via Actions) → "Skicka till skannern".

### Daghandel (egen sida)
Kandidater över alla marknader med strikthet (antal kriterier av 5: över gårdagens high, över SMA200, över förmarknadens high, över öppning, förmarknadsvolym ≥ 50 000), struktur (vänta/bryt/…), PM-vol, rel.vol, tekniska larm (SMA50-brott, RSI, Fibonacci), handelsplan per aktie. Information – ingår inte i strategin.

### Radar (institutionell radar, SPEC § 12)
- **Rullista**: Alla investerare + varje förvaltare/fond i bokstavsordning. Investerarsida: senaste rapport, innehav med vikter, händelser (ny/ökat/minskat/ut), 13F-NT-notering, "lämnar inte längre"-flagga.
- **Flikar** (Alla investerare): **Konsensus** (flera köpt samma aktie), **Flöde** (ARK dagligen, 13D/G), **Mest ägda** (antal ägare), **Ackumulering** (ägs av ≥ 3, ≥ 2 ökar/ny, ingen minskar), **Nya köp**, **Ökade innehav**, **Övertygelse** (vikt ≥ 8 %), **Tidiga signaler** (börsvärde < 50 mdr SEK hos flera), **Sektorer** (utfällbara: aktier med köpare/säljare, investerare med sina affärer), **Aktivister**. Rader fälls ut med gul trekant; tickern öppnar aktiens huvudsida. Insyn (FI + Form 4) räknas separat.
- **Förvaltare att skugga**: redigerbar lista (`radar_watch.json`), "Kör radarn nu". Info-ark förklarar källor och eftersläpning.
- Radarn är information. Som avgörare inom topp 20 testas den tidigast 2027 (kräver ett års egen logg).

### Nyheter
Marknadsnyheter och nyheter per innehav/intressanta aktier, var 15:e minut.

### Sök
Fritext över universumet; träff leder till aktiekortet; ej funnen → uppslag via Actions.

### Inställningar
Avgifter, Finnhub-nyckel, GitHub-token (kopplar appen till repot), intro av/på, export/import, versionsinfo, startlänkar för jobben, datastatus.

---

## 6. Validering och tester – vad som avgjorts

| Datum | Körning | Fråga | Svar |
|---|---|---|---|
| 2026-10 | – | Stopp, band, daglig prövning, vinstmål, rotation, volym, RSI/MACD, fundamenta | Alla sämre → ute |
| 2026-11-02 | crash | Gapsäljning, stopp, stå kvar | Sämre |
| 2026-11-04 | – | Likaviktning, trötta innehav, jämnhet, vikt efter styrka | Likaviktning +2 pe; övriga ute |
| 2026-11-05 | #72 | T+1, slippage, likviditetsgolv i kronor | −2/−1 pe; golv i kronor faller |
| 2026-11-05 | #73 | P3-basis (köp vid månadsslut, kalender-12-1) | Ny referens 34,1 % / 1,11 / −44 % |
| 2026-11-05 | #74/#76/#79 | Clenow som poäng/avgörare, fönster 60–250 d | Clenow 90–180 d vinner; **120 d = 2.0** |
| 2026-11-05 | #78/#83 | Kostnadsläge 5–10 innehav | 7 |
| 2026-11-05 | #80 | Fullt läge 15–30 | 20 står kvar |
| 2026-11-05 | #81 | Överlevnadsbias (inträdesdatum) | 44 → 36 %; bara S&P 500 32 → 17,5 % |
| 2026-11-05 | #82 | Kraschfönster, förskjuten prövning | 2.0 bättre i snabba, sämre i utdragna; inom bruset |
| 2026-11-18 | strat ×4 | Gapsäljning 22/25/30, riskbudget efter bredd, sektortak 6/8, omvänd volatilitet | **Ingen klarar beslutsregeln (TESTPLAN § 9)** |
| 2026-11-23 | test3 | T5 efterförsäljning, T6 SMA200-buffert, T7 rekyl i upptrend, T8 sektor-rea | Försäljningarna är inte onödiga (T6 stryks); T7 faller; T8 för få signaler. **Ingen ändring (TESTPLAN_3.0)** |

Beslutsregel (gäller varje test): Sharpe och Calmar högre än A på hela perioden; Sharpe inte lägre i båda halvorna; håller med dubbla kostnader och förskjutet kvartal; CAGR faller högst 2 pe (4 för riskbudget); nedgång inte djupare. Regeln skrivs före körningen och ändras inte efteråt.

---

## 7. Beslut (ägaren) och arbetssätt

**Beslut**
- 2026-11-05: modell 2.0; kostnadsläge 7, fullt 20; datakontroll som spärr.
- 2026-11-06: under utvecklingsfasen inga nya köp mellan prövningarna; säljrader följs; likaviktning genomförs.
- 2026-11-09: handel pausad i befintliga portföljer till månadsprövningen 30 nov; portföljerna töms inte; demoportfölj; radar som information.
- 2026-11-11/16: radarn i Börsdesk-stil; 24 förvaltare, inga banker/privatpersoner, Form 4 i stället; riktkurser som information.
- 2026-11-18: fyra strategitester körda – ingen ändring; radar/revideringar som avgörare 2027; överlevnadsbias/Börsdata avvaktar.
- 2026-11-20: högerblocket på kort: två rader när aktien inte ägs; riktkurser bara på aktiekortet.

**Arbetssätt**
1. Varje regel som kan påverka köp/sälj står i SPEC med körning och datum. Allt annat är information och får aldrig ändra en rad i förslaget.
2. Beslutsregel skrivs före varje test; en variant i taget; parametrar väljs mitt i det vinnande intervallet; en CAGR-skillnad under 4 pe är inte ett resultat.
3. Referens: 2.0 på P3-basis. Paritetstestet går automatiskt; röd = appen avviker.
4. Handel: en gång på månadsskiftets stängning, hela listan nästa dag; varje rad loggas eller avstås med orsak.
5. Appversioner: inte många i snabb följd (Pages cachar 10 min). Commit-meddelanden på svenska, med attribution.
6. Ägarens e-post skickas aldrig till externa tjänster från Claude; hemligheter läggs av ägaren i GitHub.

---

## 8. Öppet läge inför 3.0

**Uppföljning**
- 30 nov: första månadsprövningen under 2.0 – förslag, förklaringar, logg, ✕-flödet, demoportföljens automatik.
- Kontroll av Form 4- och analytikerdata efter en veckas körningar; ARK:s tre gamla fondfiler.
- Dagsserien får dagens punkt först efter kvällens skanning (grafhuvudet visar livekurs med notering).

**Kända begränsningar**
- Live-jobbet var 5:e minut kör GitHub bara 1–2 gånger per dag → intradag kommer i ryck; källa och tid visas alltid.
- Snapshots (avkastning, risk) kräver att appen öppnas regelbundet.
- Backtestet bygger på dagens indexmedlemmar (överlevnadsbias) – förväntan halverad av det skälet.

**Kandidater för 3.0 (inget beslutat)**
- Strategitest hösten 2027 på loggens egna data: gapsäljning 22–25 %, omvänd volatilitet, radar/analytikerrevideringar som avgörare inom topp 20.
- Överlevnadsfri data (Börsdata/Norgate) och lönsamhetsfilter (TESTPLAN § 2 D) – ägarens databeslut.
- Avknoppningar i paritetstabellen.
- Notiser (push) vid månadsfönster, gapfall i innehav, datafel.

---

## 9. Hur ett nytt utvecklingssteg startar

1. Läs `docs/HANDBOK.md`, `SPEC.md`, `STATUS.md`, `TESTPLAN_2.0.md` (i den ordningen).
2. Klona repot med push-behörighet; kontrollera `APPV` i `index.html` och senaste körningar i Actions (`gh run list`).
3. Skriv ner ägarens önskemål som numrerad arbetslista; varje punkt som rör strategin får en beslutsregel i TESTPLAN före test.
4. Arbeta i små versioner: ändra, syntaxkontroll, provrendera vid behov (Playwright mot lokal server fungerar), commit med attribution, push, bumpa `APPV`.
5. Uppdatera STATUS (läge och återstående lista) i slutet av varje arbetspass; SPEC bara när en regel ändras eller en byggnot tillkommer; TESTPLAN när ett test körs.
