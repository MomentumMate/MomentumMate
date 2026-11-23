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
