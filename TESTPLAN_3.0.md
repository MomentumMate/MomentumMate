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

## Utfall
*(fylls i efter körning)*
