# MomentumMate – läge 2026-11-23 (eftermiddag)

Regelverket står i `SPEC.md` (modell 2.0); handboken i `docs/HANDBOK.md` beskriver verktyget, arkitekturen och alla funktioner; testplanen i `TESTPLAN_2.0.md`. Detta dokument säger var saker står. Alla fyra finns i repots rot/`docs/` och i claude.ai-projektet (`claude/`).

## Sammanfattning

Modell 2.0 (Clenow 120 d) är validerad, specificerad och körs i appen sedan 5 nov. 18 nov kördes fyra strategitester (gapsäljning, riskbudget efter bredd, sektortak, omvänd volatilitet) med regel skriven i förväg – ingen klarade regeln, 2.0 står oförändrad (TESTPLAN § 9, SPEC § 11). Utvecklingslistan från 9 och 16 nov är genomförd. 18–20 nov: gränssnittet städat och likriktat (aktiekortens sidhuvud lika överallt, Börsdesken med flikarna Momentum 25 / Daghandel / Alla, radarn med nya flikar Mest ägda och Ackumulering, utfällbara sektorer, inga emojis), 1D-grafen över hela handelsdagen, riktkurser i det utfällda kortet, portföljinnehaven med i live- och analytikerurvalet. 23 nov: handbok skriven som utgångspunkt för 3.0.

**23 nov eftermiddag (3.0, steg 1):** riktkurser för hela universumet (`analysts.yml`, eget jobb: prioriterade dagligen + 600 roterande, fyllnadskörning av alla ~2 300 i dag; varje analyshus riktkursändring sparas → `pt30` = höjda/sänkta och medianändring senaste 30 dagarna, från första dagen). Historiken flyttad till `analysts_hist.json`. Fyra tester körda enligt `TESTPLAN_3.0.md` (regler committade före körning): T5 efterförsäljning visar att försäljningarna på hållregeln inte är onödiga (ersättarna slår de sålda med median 9 pe på 6 mån, 22 pe på 12 mån) → T6 SMA200-buffert stryks; T7 rekyl i upptrend faller; T8 sektor-rea har för få signaler (1 på tio år vid 25 %; vid 20 % slog den 2.0 i 0 av 5). **2.0 oförändrad.**

**23 nov, demoportföljen granskad** (start 14 nov, 1 000 000 kr): −4,8 % t.o.m. 20 nov mot OMXSPI −5,1 % och MSCI World −6,9 % i SEK – i linje med marknaden; innehaven är främst hälsovård/life science, inte tech. Två fel hittade och rättade: (1) översiktskortet visade värdet utan kassa (app c); (2) BLFS rankades nr 9 trots sista kurs 7 okt – köpordern kunde aldrig utföras och ca 50 000 kr låg oinvesterat. Nu får aktier utan ny stängning på 7 dagar poäng 0 i skannern (paritet med backtestet, SPEC § 16/§ 18) och demons order i sådana aktier stryks (app d). 10 aktier berördes i dag. BLKB köptes med 12-1 −20 % och säljs troligen 30 nov – samma beteende som backtestet.

**23 nov, informationstillägg (inget styr förslaget):** rekylzon (Fibonacci), rapportkalender med rapportreaktion, sektorflik i Börsdesken, radarns Stora insiderköp, låg bevakning och uppskattad 13F-inköpskurs, påminnelse inför månadsprövningen. Skannern räknar `fz`, `er`, `r3ly`, `qavg`; riktkursjobbet hämtar rapportdatum. Inget av detta krävde strategitest – det ändrar ingen rad. Rapportdatumen loggas för ett gaptest 2027.

**23 nov, kursfel rättat:** skanningen tog efterhandelns kurs som dagens kurs för USA-aktier när den körde efter stängningen (387 aktier avvek > 0,25 % den 20 nov). Rättat till officiell stängning – viktigt inför prövningen 30 nov, som räknas på kvällens skanning. Grafhuvudet i 1D/1V visade fredagens sista stapel medan övriga intervall visade livekursen (Visa 402,22 mot 406,53) – rättat.

**24 nov:** GitHub körde ingen av datakontrollens sex nattkörningar (inte heller måndagens skanningar). Kontrollen startades manuellt 09:36: gul (warn – 11 tysta, 15 flaggade av 2 293, inga egna innehav), ingen spärr. Appen startar nu kontrollen själv efter 04:20 UTC om den saknas (samma mekanism som skanningen), och den kan startas genom `.github/datacheck_request.txt`.

Kvar är uppföljning: månadsprövningen **30 november** – första under 2.0 och första som loggas. Radar/analytikerrevideringar som avgörare testas tidigast 2027. Överlevnadsbias med Börsdata avvaktar.

## Gällande versioner (repo MomentumMate/MomentumMate)

- `index.html` **2026-11-25-b** (25-b: rekylzonens text – procenten är rekylen, zonen bruten under angiven kurs; 25-a: radarlistan slår ihop aktieslag – GOOG/GOOGL m.fl. – till ett bolag; 24-z: säkerhet – innehaven till Actions-variabeln LIVE_WATCH i stället för publika live_watch.json, påminnelse om säkerhetskopia, tokenkontroll; 24-y: Rapport-texten gul; 24-x: topp 3 utan guldrutor; innehavens signalknappar linjerar med tickern; 24-w: Value visar hela listan med Visa fler; 24-v: RISK-modulens pil liten och gul som övriga; 24-t–u: N/5 i daghandeln gul och tryckbar, "Rapport ÅÅMMDD"; 24-s: tryck på signalknapp eller RAPPORT/NYNOTERAD/DAGHANDEL visar förklaringen; 24-r: sidhuvudets signaler enligt ägarens val – knappar STÖD/MOTSTÅND, VOLYMBALANS, NEDTREND, UNDER ATH, BLANKNING; vit text på tickerraden RAPPORT, NYNOTERAD, DAGHANDEL; 24-q: signalen SVAG borttagen; 24-p: VOLYMBALANS grön när köpvolymen dominerar, röd när säljvolymen dominerar; 24-o: rapportknappen vit; 24-n: blankningsknappen "BLANKNING N%" – röd från 10 %, gul 5–10 %; 24-k–m: förklaring av placeringspilen, fliken heter Momentum, signalknapparna utan ikoner och rapportchippet "Rapport ÅÅMMDD"; 24-j: radarlistans förklaring fälls ut med ⓘ i rubriken; 24-i: flaggorna i sidhuvudet – veckovisa lägre toppar heter LÄGRE TOPPAR N V, marknadsstrukturen lägre toppar och bottnar heter NEDTREND, dubbletter visas aldrig; 24-h: Price target – rekommendationen först i raden, fet, grön för köp/starkt köp, gul för behåll, röd för sälj; 24-g: Price targets textrad inne i kortets sidhuvud, i linje med tickern och bruten vid kurskolumnen; 24-f: Price target visar alla aktier med riktkurs, 50 i taget med Visa fler; 24-e: radarn som en sammanvägd lista, Institutionellt stöd 0–100, med Små och medelstora, Säljs mest, Aktivister, Stora insiderköp, Sektorer och Sektorstyrka; 24-d: sammanvägt daghandelsmått och daghandelslogg med utfall; 24-c: appen startar nattens datakontroll om den uteblivit; 24-b: en gemensam ordning för daghandeln – relativ volym – på startsidan, i Börsdesken och på daghandelssidan; flaggor för Polen, Österrike, Irland, Portugal; 24-a: handelsplanen räknar rekylzonen som stöd/motstånd – planen och zonen motsäger inte längre varandra (MRVL); l: Börsdesken Momentum 25 / Value / Price target / Daghandel, Sektorstyrka i Radarn, all poängvisning = 2.0 – momentumstapeln visade 1.0-poängen; k: appen startar skanningen själv när ett schemalagt pass uteblivit; j: aktiekortet – rutan Plats borttagen, Poäng (2.0) på dess plats, Från 52H ny; rekylzonens lager säger till när zonen saknas eller ligger utanför grafen; h–i: varje grafintervall visar sin egen förändring, dagsdatum i UTC; g: grafhuvudet i 1D/1V visar livekursen när den är nyare än staplarna; f: rapportkalender, sektorflik, stora insiderköp, låg bevakning, 13F-inköpskurs, påminnelse inför månadsprövning; e: rekylzon i aktiekort, graf och startsida; d: demoorder i aktier utan ny kurs stryks; c: översiktskortet inkl. kassa; b: analytiker för hela universumet). Före det 2026-11-20-l. Sedan 18 nov: insättningar/uttag (az), allokeringsvy (az), 1D över hela sessionen med svenska klockslag (bd), grafhuvud = sidhuvudets kurs (be, j), gamla poängmodulen bort och flikar per marknad (bc, g), live_watch.json (bc), sidhuvud bara bolagsnamn och radbrutet (bf, bg), radar: hela texten, inga emojis/ikoner, knappordning, tickern → aktiesidan, gul trekant, Mest ägda, Ackumulering, sektorer utfällbara (bh–f), analytiker i kortet (a), kortens högerblock lika (h), daghandelskort som Momentum 25 (i, k, l).
- `scanner.py` – live-urvalet läser `live_watch.json` (appens portföljinnehav). `analysts.py` – samma urval. `backtest.py` – `--strat`. `radar.py`, `datacheck.py`, `parity_test.js` oförändrade sedan 16 nov.
- Workflows (Python 3.13): scan (12 körtider, grind), datacheck (6), radar (4; radar + analytiker), daytrade (från appen), news (15 min), parity, backtest (`strat`), diagnos, lookup.
- Hemligheten `RADAR_CONTACT` finns. Ägarens app har GitHub-token och Finnhub-nyckel.

## Beslut (ägaren)

- 2026-11-05: modell 2.0, kostnadsläge 7, fullt läge 20, datakontroll som spärr.
- 2026-11-06: inga nya köp mellan prövningarna under utvecklingsfasen; säljrader följs; likaviktning genomförs.
- 2026-11-09: handel pausad till 30 nov; portföljerna töms inte; demoportfölj; radar som information.
- 2026-11-11/16: radarn i Börsdesk-stil; 24 förvaltare, inga banker/privatpersoner, Form 4; riktkurser som information.
- 2026-11-18: strategitester gap/riskbudget/sektortak/omvänd volatilitet – ingen ändring; radar/revideringar som avgörare 2027; överlevnadsbias/Börsdata avvaktar.
- 2026-11-20: kortens högerblock två rader när aktien inte ägs; riktkurser bara på aktiekortet, inte i radarn.

## Resultat – sammanfattning

| Körning | Svar |
|---|---|
| #72 | T+1 −2 pe, slippage −1 pe; likviditetsgolv i kronor faller |
| #73 | P3-basis ny referens: 34,1 % / 1,11 / −44 % |
| #74/#76/#79 | Clenow 90–180 d slår P3; 120 d: 42,8 % / 1,20 / 1,00 / −43 % |
| #78/#83 | 7 innehav i kostnadsläge |
| #80 | 20 innehav i fullt läge |
| #81 | Överlevnadsbias nedre gräns: 44,0 → 36,4 %; bara S&P 500 31,7 → 17,5 |
| #82 | 2.0 faller mindre i snabba nedgångar, mer i utdragna |
| strat 2026-11-18 | A = 43,2 / 1,25 / 1,03 / −42,0. Gap, bredd, sektortak, omvänd volatilitet – alla faller på regeln. Ingen ändring. |
| test3 2026-11-23 (2) | T9 volatilitetstak och T10 jämna trender – alla faller (CAGR 22–39 mot 42,8). Ingen ändring. |
| test3 2026-11-23 | T5: sålda slår ersättaren i 43 % (6 mån), median −8,9 pe; återköp 1 % → T6 stryks. T7 rekyl: 35–40 % mot 42,8, faller. T8 sektor-rea: för få signaler. Ingen ändring. |

**Förväntan (SPEC § 14): 20–25 % per år, nedgång −50 %.**

## Drift

- Skanning 3–4 gånger per dygn; datakontroll grön/warn dagligen; radar och analytiker dagligen (13F-batchen för Q3 kom in t.o.m. 16 nov; nästa mitten av februari – 13D/G, ARK, insyn och Form 4 är dagliga).
- Intradag: live-jobbet hämtar staplar för portföljinnehav + bevakning + topp 20 + kandidater när det startas från appen; GitHub kör schemat 1–2 gånger per dag.

## Kända fel och fynd (öppna)

- Live-jobbet kör sporadiskt på schema; källa och tid visas; startlänk i appen.
- ARK: tre av sex fondfiler uppdateras inte på ark-funds.com.
- Dagsserien i graferna får dagens punkt först efter kvällens skanning (grafhuvudet visar livekursen med notering).
- Parallella backtest-körningar skriver över varandras `backtest.json` – loggarna är facit. `test3.yml` publicerar per jobb på egna grenar.
- Commit 37814e8 fick av misstag med wheel-filer (borttagna i nästa commit, finns kvar i historiken).

## Handel

- Paus för nya köp. **30 november:** "Räkna om hela prövningen på dagens data" på månadens sista stängning, handla hela listan dagen efter; varje rad registreras eller avstås med ✕ + orsak. Nvidia under SMA200 sedan 5 nov → ⏳. Demoportföljens första månadsprövning sker automatiskt.
- Rutin efter affärer: "Innehav till skannern" (`holdings.txt`); registrera insättningar/uttag.

## Återstår

**Uppföljning**
1. 30 nov: första månadsprövningen under 2.0 – förslag, förklaringar, logg, ✕-flödet, demoportföljen.
2. Kontroll av Form 4/analytikerdata efter en veckas körningar; ARK:s fondfiler.

**Utveckling (3.0 – inget beslutat; kandidater i HANDBOK § 8)**
3. Överlevnadsbias vidare och lönsamhetsfilter (Börsdata) – avvaktar.
4. Daghandelsloggen (start 2026-11-24): utvärdera måttet när minst 60 kandidater har utfall (ca två veckor), sedan månadsvis.
5. Strategitest 2027 på loggens egna data: gapsäljning, omvänd volatilitet, radar/revideringar som avgörare, analytikerfiltret i en eventuell reaficka (pt30 samlas nu för hela universumet).
5. Avknoppningar i paritetstabellen; notiser.

## Arbetssätt

Se HANDBOK § 7 och `CLAUDE.md` i repots rot (läsordning och regler för varje ny session).
