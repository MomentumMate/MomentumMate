# MomentumMate 3.0 – testplan

**Version 2026-11-23. Beslutsreglerna nedan är skrivna och committade före körningen och ändras inte efteråt.**
Underlag: samtal 2026-11-23 (reelen "Selling after a 10% drop vs holding, 1965–2026", tech-utförsäljningen i november 2026).
Ägarens beslut 2026-11-23: kör testerna direkt i den ordning Claude bedömer lämplig.

Alla körs på backtestets tioåriga historik (`backtest.py --strat after | pullback | sectorsale`, workflow `test3.yml`).
Referens **A = modell 2.0 på P3-basis** (Clenow 120, köp vid månadsslut, kalender-12-1, T+1, slippage, 20 innehav, 10 Mkr).

| # | Test | Fråga | Typ |
|---|---|---|---|
| T5 | Efterförsäljningsanalys | Är försäljningarna på hållregeln onödiga (sälj nära botten, köps tillbaka)? | Diagnos – ändrar inget |
| T6 | Buffert på SMA200 | Färre onödiga försäljningar utan att tappa skyddet? | Regeltest, körs bara om T5 utlöser |
| T7 | Rekyl i upptrend | Ger förtur åt starka aktier som just fallit tillbaka mer? | Regeltest |
| T8 | Sektor-rea | Lönar det sig att köpa storbolag i en sektor som fallit brett? | Fristående strategi (reafickans priskomponent) |

Ordning: T5 först (avgör T6), T7 och T8 parallellt – de är oberoende.

## T5 Efterförsäljningsanalys
A körs med diagnostik. För varje försäljning på hållregeln (orsak SMA200 respektive 12-1): den sålda aktiens avkastning 1/3/6/12 mån efter
försäljningen (fyllnad T+1) mot ersättarna (köp samma dag eller inom fem handelsdagar) och mot universumets likaviktade snitt;
andel som köps tillbaka inom 3 mån; andel som sker när bredden (andel över SMA200) är under 30 %; antal per år.
**Tolkningsregel:** T6 körs om sålda aktier i median slår ersättaren över 3 **och** 6 mån **och** mer än 25 % köps tillbaka inom 3 mån. Annars stryks T6.

## T6 Buffert på SMA200 (villkorad)
T6-a sälj först under 0,97 × SMA200 · T6-b under 0,95 × SMA200 · T6-c SMA200-brott två månadsskiften i rad. 12-1-villkoret oförändrat.
**Beslutsregel:** TESTPLAN_2.0 § 9 punkt 1–5: Sharpe **och** Calmar högre än A på hela perioden; Sharpe inte lägre i någon halva;
håller med dubbla kostnader och förskjutet kvartal; CAGR faller högst 2 pe; nedgång inte djupare. Vinner flera väljs den mittersta.

## T7 Rekyl i upptrend
Rekyl = 1-månadsavkastning mellan −25 och −10 %, aktien fortfarande rankad (över SMA200, likviditet).
T7-a rekylaktier inom topp 30 sorteras först · T7-b rekyl ger +5 poäng · T7-c rekyl ger +10 poäng.
**Beslutsregel:** som T6.

## T8 Sektor-rea (fristående)
Signal vid månadsskifte: sektorns medianaktie (minst 15 likvida aktier) ≥ 20/25/30 % under 52-veckorshögsta medan universumets median är högst 10 % under.
Köp de 5 som fallit mest bland sektorns 15 mest omsatta (20-dagars omsättning i kronor, känd den dagen); lika vikt; fyllnad T+1; 0,2 % kostnad per sida;
håll 6 eller 12 mån; variant med stopp −25 %. Ingen ny signal i samma sektor förrän innehavet avslutats.
Jämförs över exakt samma fönster med universumets och sektorns likaviktade snitt och med modell 2.0.
**Tolkningsregel:** blir aktuell som regelstyrd reaficka (utanför 2.0, egen kapitalandel) bara om mittvarianten (25 %, 12 mån, ingen stopp) slår
universumet i minst två tredjedelar av signalerna **och** dess snitt slår modell 2.0 över samma fönster.
Har mittvarianten färre än 8 avslutade signaler är utfallet "för få signaler"; då redovisas en relativ variant (sektorns median minst x pe sämre
än universumets, inget krav på universumet) som **indikation** med samma regel – inte som beslut.
**Känd snedvridning:** universumet är dagens indexmedlemmar. Överlevnadsbias gynnar just rea-köp (bolag som föll och aldrig kom tillbaka saknas).
Ett positivt T8-utfall är därför en övre gräns. Analytikerfiltret (riktkurs/prognos inte sänkt) kan inte backtestas – historik saknas;
data samlas från 2026-11-23 för hela universumet (`analysts.yml`, `pt30`) och prövas 2027.

## Inte nu
Marknadsutgång efter fall eller bredd (förkastad, T2 2026-11-18). Gapsäljning, omvänd volatilitet, radar/analytikerrevideringar som avgörare: 2027 på loggens data.

## T9 och T10 – volatila rusare mot jämna trender (ägarbeslut 2026-11-23, regler skrivna före körning)

Bakgrund: topplistan domineras av aktier som rusat (MRNA: Clenow 4 460, volatilitet 266 %) medan stabila uppgångar (MA, V: Clenow 58–66,
volatilitet ~22 %) aldrig når topp 20. 52 aktier delar nästan maxpoängen; ordningen mellan dem avgörs i dag av rått Clenow-mått = brantast först.
Ägarens fråga: är jämnare trender en styrka? Blankningsgrad kan inte testas – historik saknas (visas som information).

| Test | Varianter | Mekanism |
|---|---|---|
| **T9 Volatilitetstak vid köp** | årsvolatilitet (60 d) över 80 / 100 / 120 % får inte köpas | `vol_cap` – påverkar bara köp, inte hållregeln |
| **T10 Jämna trender först** | a) avgörare R² (120 d) – jämnast först; b) avgörare lägst volatilitet; c) tak 100 i stället för 150 + R² som avgörare (fler delar toppen, jämnast väljs) | `tie_mode`, `mom_cap` |

**Beslutsregel (samma som TESTPLAN_2.0 § 9):** Sharpe **och** Calmar högre än A på hela perioden; Sharpe inte lägre i någon halva; håller med dubbla kostnader och
förskjutet kvartal; CAGR faller högst 2 pe; nedgång inte djupare. Vinner flera nivåer väljs den mittersta. Rapporteras även: median volatilitet vid köp.
Förväntan i förväg: T9 ger lägre volatilitet och grundare nedgång men troligen lägre CAGR (som omvänd volatilitet T4); T10 oklart – avgörare har tidigare
spelat liten roll (#74), men under 2.0 delar ovanligt många toppen.

### Utfall T9/T10 – körda 2026-11-23 (`test3.yml`, grenarna `test3-volcap`, `test3-steady`)

A i körningarna: **42,8 % / Sharpe 1,23 / Calmar 1,02 / −42,0 %**, median volatilitet vid köp 61 %.

| Variant | CAGR | Sharpe | Calmar | Nedgång | Halvor | Dubbla | Q+1 | Vol vid köp | Utfall |
|---|---|---|---|---|---|---|---|---|---|
| T9-80 | 30,6 | 1,15 | 0,81 | −37,6 | 1,34 / 0,97 | 30,3 / 1,14 / 0,80 | 31,5 / 1,19 / 0,85 | 53 % | faller på allt utom nedgång |
| T9-100 | 36,1 | 1,24 | 0,86 | −41,9 | 1,44 / 1,04 | 34,8 / 1,20 / 0,82 | 32,7 / 1,16 / 0,78 | 57 % | faller: Calmar, halvor, dubbla, Q+1, CAGR −6,6 |
| T9-120 | 38,6 | 1,28 | 0,95 | −40,5 | 1,47 / 1,01 | 36,0 / 1,21 / 0,89 | 36,9 / 1,32 / 0,90 | 60 % | faller: Calmar, halvor, dubbla, Q+1, CAGR −4,1 |
| T10-a jämnast först (R²) | 25,7 | 1,13 | 0,62 | −41,8 | 1,23 / 1,07 | 25,0 / 1,10 / 0,59 | 26,6 / 1,20 / 0,74 | 38 % | faller på allt utom nedgång |
| T10-b lugnast först | 24,3 | 1,22 | 0,69 | −35,4 | 1,24 / 1,04 | 23,1 / 1,16 / 0,63 | 19,8 / 1,00 / 0,53 | 29 % | faller på allt utom nedgång |
| T10-c tak 100 + jämnast | 22,3 | 1,14 | 0,63 | −35,2 | 1,13 / 1,18 | 21,5 / 1,10 / 0,61 | 18,3 / 0,95 / 0,51 | 30 % | faller på allt utom nedgång |

**Slutsats: ingen ändring.** Att välja jämna eller lugna trender framför branta halverar tillväxten (22–26 % mot 43 %) utan bättre Sharpe – nedgången blir bara något grundare.
Ett volatilitetstak kostar 4–12 pe och håller inte i halvorna eller vid förskjutet kvartal. Modellens avkastning kommer från just de branta, volatila trenderna;
de enskilda bakslagen (MRNA, FRO) är priset, och likavikt över 20 innehav är skyddet. Stabilitet hör hemma i kapitalfördelningen (allokeringsvyn), inte i urvalet.

## T11 Rekylzonen (Fibonacci 61,8–78,6 %) som köpläge (ägarbeslut 2026-11-25, regel skriven före körning)

Fråga: har aktier som köps i rekylzonen slagit aktier som köps nära toppen av samma uppgång? Zonen visas i appen som information (SPEC § 12);
testet avgör om den förtjänar mer än så. Händelsestudie, inte strategikörning: modell 2.0 ändras inte oavsett utfall.

**Metod** (`--strat fibzone`, samma universum och kurser som backtestet, 10 år): vid varje månadsslut, för varje aktie, samma definition som skannern
(`fib_zone`): topp = högsta stängning senaste 252 dagarna, botten = lägsta stängning före toppen, uppgång ≥ 30 %. Grupper:
**ZON** = kursen i 61,8–78,6 % rekyl; **TOPP** = kursen högst 5 % under toppen (samma uppgångskrav); **ZON+** och **TOPP+** = samma men kurs över SMA200.
Köp till nästa dags stängning (T+1). Mått: avkastning 1, 3 och 6 månader minus universumets likaviktade snitt samma period (överavkastning, pe);
median, medel, andel som slår universum; för ZON även andel som bryter zonen (stänger under 78,6 %-nivån) inom 3 månader.
Uppdelat på första och andra halvan av perioden.

**Beslutsregel** – zonen räknas som ett köpläge värt en strategikörning (en egen reaficka, ny regel skrivs då) om ALLA gäller:
1. ZON:s median-överavkastning på 6 mån är > 0 och minst **2 pe högre** än TOPP:s,
2. samma riktning (ZON > TOPP) på 3 mån,
3. medelöveravkastningen för ZON på 6 mån är > 0,
4. ZON > TOPP på 6 mån i **båda** halvorna.

Kravet på 2 pe är skärpt eftersom överlevnadsbias gynnar ZON: aktier som föll och sedan försvann ur index finns inte i universumet.
Klarar zonen inte regeln förblir den information; ägaren avgör då om modulen REKYLZONER på startsidan ska bort.

### T11 utfall (Actions `test3.yml` #45604703719, gren `test3-fibzone`, 2026-11-25)

Månadsslut 2016–2026, ca 2 300 aktier. Överavkastning mot universumets likaviktade snitt (pe), köp T+1:

| Grupp | n | 3 mån median | 6 mån median | 6 mån medel | slår univ. 6 mån | bruten inom 3 mån |
|---|---|---|---|---|---|---|
| ZON | 9 525 | −0,87 | **−2,59** | +0,69 | 44,9 % | **69 %** |
| TOPP | 59 320 | −0,98 | **−2,06** | −0,51 | 45,0 % | – |
| ZON+ (över SMA200) | 769 | −0,30 | −2,18 | +2,59 | 45,1 % | 69 % |

Halvor, 6 mån median ZON mot TOPP: första −3,03 mot −2,53; andra −2,31 mot −1,62. Regel: 1) nej (ZON 0,5 pe *sämre* än TOPP),
2) ja (3 mån marginellt bättre), 3) ja (medel svagt positivt – några stora återhämtningar), 4) nej. **Klarar inte regeln.**
Sju av tio aktier i zonen stänger under zonens nedre kant inom tre månader: zonen håller sällan som stöd. Köp nära toppen är inte sämre
än köp i rekylen – samma slutsats som T7 och grunden för momentum. Zonen förblir information (SPEC § 11).

## T12 Riktkurser: uppsida och revideringar (ägarbeslut 2026-11-25, regler skrivna före hämtning och körning)

Fråga: förtjänar Börsdeskens **Price target** (störst uppsida mot konsensus) och **revideringarna** (höjda/sänkta riktkurser 30 d) mer än att vara information?
Händelsestudie; modell 2.0 ändras inte oavsett utfall.

**Data** (`pt_hist.py`, workflow `pt_hist.yml`): Yahoos historik över analyshusens graderingar med riktkurs (datum, analyshus, ny och tidigare riktkurs) för
hela universumet, plus aktiesplittar. Riktkurser före en split delas med splitfaktorn så att de jämförs med splitjusterade kurser.
Utdelningsjustering görs inte (kursen bakåt blir något lägre → uppsidan något för hög, lika för alla).

**Konsensus vid varje månadsslut** (bara det som var känt då): median av varje analyshus senaste riktkurs de senaste 365 dagarna; minst 3 analyshus
(Price target-listan kräver 5 – redovisas som undergrupp). Uppsida = konsensus / stängning − 1; kvot utanför 0,25–4 utesluts (som appen).
**Revidering 30 d**: antal analyshus som höjt minus sänkt sin riktkurs (ny mot tidigare) senaste 30 dagarna.
**Grupper**: uppsidetiondelar U1 (lägst) … U10 (högst); **REV+** = minst 2 höjningar och ingen sänkning; **REV−** = minst 2 sänkningar och ingen höjning;
REV+ över SMA200. Köp T+1. Mått: avkastning 3, 6, 12 mån minus likaviktat snitt av alla aktier med konsensus samma månad (överavkastning, pe);
median, medel, andel som slår snittet. Halvor = före/efter mittdatum för månaderna med tillräcklig täckning.

**Täckningsgrind**: bara månader med minst 200 aktier med konsensus räknas. Färre än 36 sådana månader → utfallet redovisas som **indikation**, inte beslut.

**Beslutsregel A – uppsidan (Price target-listan)**: listan förtjänar mer än information om U10:s median-överavkastning på 6 mån är > +2 pe,
U10 > U1 på 6 och 12 mån, och U10 > 0 på 6 mån i båda halvorna. Annars förblir den information; är U10 klart negativ (< −2 pe på 6 mån i båda halvorna)
får listan en varningstext.
**Beslutsregel B – revideringar**: REV+ median-överavkastning > +1 pe på både 3 och 6 mån, REV+ > REV− på 3 och 6 mån, och REV+ > 0 på 6 mån i båda halvorna.
Klarar B regeln blir revideringar kandidat till strategitestet 2027 (avgörare inom topp 20, egen regel skrivs då); annars stryks det testet.
**Känd snedvridning**: överlevnadsbias (dagens indexmedlemmar) gynnar hög uppsida (fallna bolag som försvann saknas); Yahoos täckning av analyshus är ofullständig.

## Utfall – körda 2026-11-23 (Actions `test3.yml` #45177208027, grenarna `test3-after`, `test3-pullback`, `test3-sectorsale`)

**Slutsats: ingen ändring av modell 2.0.** T6 stryks enligt T5, T7 faller på beslutsregeln, T8 har för få signaler för att avgöras.

**T5 Efterförsäljning** (A: 44,2 % / Sharpe 1,28 / Calmar 1,05 / −42,0 %; 241 försäljningar på tio år, 232 på SMA200, 9 på 12-1):

| Horisont | Såld aktie (median) | Ersättaren (median) | Såld − ersättare (median) | Andel där sålda slog ersättaren | Såld − universum (median) |
|---|---|---|---|---|---|
| 1 mån | +2,8 % | 0,0 % | +0,6 pe | 51 % | +0,7 pe |
| 3 mån | +9,0 % | +6,5 % | −0,2 pe | 50 % | +4,5 pe |
| 6 mån | +8,4 % | +17,6 % | −8,9 pe | 43 % | +4,4 pe |
| 12 mån | +23,3 % | +37,2 % | −21,7 pe | 38 % | +2,9 pe |

Återköpta inom 3 mån: 1 %. Försäljningar vid bredd under 30 %: 8 %. Per år 12–32 st (flest 2018, 2022, 2026).
Tolkning: de sålda aktierna går inte dåligt – de slår universumet något – men ersättarna går klart bättre, och skillnaden växer med tiden.
Försäljningarna är alltså inte onödiga: de flyttar kapital till starkare aktier. Tolkningsregeln utlöses inte → **T6 stryks**.

**T7 Rekyl i upptrend** (A i samma körning: 42,8 % / 1,23 / 1,02 / −42,0 %):

| Variant | CAGR | Sharpe | Calmar | Nedgång | Halvor | Dubbla | Q+1 | Utfall |
|---|---|---|---|---|---|---|---|---|
| T7-a rekyl inom topp 30 först | 40,3 | 1,30 | 0,99 | −40,6 | 1,64 / 0,88 | 38,6 / 1,26 / 0,94 | 42,3 / 1,33 / 1,03 | faller: Calmar, halva 2, dubbla, Q+1, CAGR −2,5 |
| T7-b rekyl +5 p | 35,4 | 1,22 | 0,89 | −39,9 | 1,55 / 0,92 | 34,4 / 1,19 / 0,86 | 34,3 / 1,24 / 0,85 | faller på allt utom nedgång |
| T7-c rekyl +10 p | 39,5 | 1,33 | 0,99 | −39,8 | 1,62 / 1,25 | 38,4 / 1,29 / 0,98 | 34,2 / 1,23 / 0,88 | faller: Calmar, dubbla, Q+1, CAGR −3,3 |

Rekylförtur ger något högre Sharpe och grundare nedgång men lägre tillväxt och håller inte vid förskjutet kvartal. Förkastad (SPEC § 11).

**T8 Sektor-rea:** universumets median låg högst 10 % under 52-veckorshögsta i 46 % av månaderna. Mittvarianten (25 %, 12 mån) gav **en** signal på tio år
(energi 2020-12-31: rea +51 %, universum +29 %, sektor +65 %, 2.0 +77 %); den relativa varianten också en. **För få signaler – inget utfall.**
Vid 20 % kom 5 signaler (energi 2015, 2019, 2020 ×2; teknik 2026-02): rea-köpet slog universumet i 2 av 5 på 6 mån, **slog 2.0 i 0 av 5** på både 6 och 12 mån
(snitt 6 mån: rea +2,7 %, universum +5,9 %, 2.0 +30,4 %). Stopp −25 % gjorde det sämre. Underlaget är litet och överlevnadsbias gynnar rea-köp,
så T8 talar snarast **emot** en reaficka – men avgör inget. Analytikerfiltret prövas 2027 på data som nu samlas för hela universumet.
