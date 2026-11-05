# MomentumMate – specifikation av kärnmodellen

**Modell 2.0, 2026-11-05.** Detta dokument är facit. Appens beslutsmotor ska göra exakt detta och ingenting annat.
Allt som kan styra ett köp eller en försäljning står här; allt annat i appen är information.
Varje regel har validerats i `backtest.py` (10 år, ca 2 300 aktier, fyllnad dagen efter signal, courtage, växling, slippage) – datum och körning anges.
Ändringar i § 1–10 görs bara via ett test med beslutsregel skriven i förväg (`TESTPLAN_2.0.md`) och ger en ny version i tabellen nedan.
§ 15–18 styr ingen rad i förslaget.

| Version | Datum | Ändring | Underlag |
|---|---|---|---|
| 1.0 | 2026-11-04 | 12-1 med tak 150, lika poäng → högst 12-1; 20 innehav, kostnadsläge 5 nordiska | `--tie` X4 |
| **2.0** | **2026-11-05** | Momentummåttet är Clenow lutning × R² över 120 dagar (§ 3); 12-1 i kalendermånader (§ 3, § 6); kostnadsläge 7 innehav (§ 4); likviditetsgolv lika i app och backtest (§ 2) | Actions #73, #76, #78, #79 |

Appen visar gällande modellversion i regelarket. Håll/sälj (§ 6), månadsprövning och kvartalslikaviktning (§ 7) är oförändrade sedan 1.0.

## 1. Mål

Högsta möjliga tillväxt över tid med en regelstyrd momentumstrategi, utan diskretionära bedömningar.
Priset är djupa nedgångar (backtest −43 %, räkna med −50 %) och stora svängningar år för år. Risknivån styrs av hur stor del av kapitalet som ligger i strategin (§ 15).

## 2. Universum och data

- Ca 2 300 aktier: Norden (OMXS, OMXC, OSE, OMXH), USA (S&P 500/400/600, Nasdaq-100, Dow, ADR), Europa (STOXX 600 m.fl.).
- Stängningskurser. Signaler räknas på senaste **stängning**, aldrig på kurs under pågående handel. Tidigaste handel är nästa handelsdag.
- Minst tolv kalendermånaders historik krävs för rankning. Aktier med kortare historik är **nynoterade** (§ 9).
- **Likviditet (2.0):** aktier med 20-dagars dagsomsättning under **3 miljoner i handelsvalutan** får poäng 0 och rankas inte. Samma golv i skanner
  (`LIGHT_MIN_DV`) och backtest (`minturnover`). Golvet är därför olika hårt per marknad (3 M USD ≈ 30 Mkr, 3 Mkr i Stockholm, 3 M pence i London);
  ett golv i kronor testades 2026-11-05 (#72, 20–250 Mkr) och gav lägre CAGR och Sharpe – förkastat. Hållregeln påverkas inte av golvet.
  Inga andra likviditetsavdrag finns (det relativa avdraget mot börsens median togs bort 2026-11-05; det hade aldrig validerats).

## 3. Poäng och rankning

Poäng 0–100 per aktie:

- **Momentum (80 %) – 2.0:** Clenow-mått = årstaktad lutning i en linjär regression av ln(stängning) mot tid över de senaste **120 handelsdagarna**,
  gånger regressionens R². Uttryckt i procent: (e^(lutning × 252) − 1) × 100 × R². Skalas linjärt 0–150 → 0–100 poäng; över 150 ger 100; negativt ger 0.
  *(#76 2026-11-05: fönster 90–180 slog 12-1 i Calmar och Sharpe; 90/120/150 höll vid dubbla kostnader och förskjutet kvartal; #79: 10 av 12 delprov.
  120 = mitten av det vinnande intervallet. 1.0 använde 12-1 med samma tak.)*
- **Trendkvalitet (20 %)**: kurs > SMA50 (30), kurs > SMA200 (30), SMA50 > SMA200 (20), SMA200 i dag > SMA200 i går (20).
- **Överhettningsavdrag**: (1-månadsuppgång − 25)/25 × 15 om uppgången överstiger 25 %, plus (RSI14 − 80)/20 × 10 om RSI överstiger 80; summan högst 25. *(Validerat 2026-10-16; formeln harmoniserad 2026-11-05.)*
- **Lika poäng avgörs av högre Clenow-mått.** *(Avgörare-varianter testade #74: måttet spelar roll, inte avgöraren.)*
- Bara aktier **över SMA200** rankas som köpkandidater (trendfilter).
- **12-1 (används i § 6 och som information):** stängning senaste på eller före samma datum **en kalendermånad** tillbaka, dividerad med stängning
  på eller före samma datum **tolv kalendermånader** tillbaka, minus 1. *(Paritetstest P2 2026-11-05; samma i app och backtest.)*

Rankningsplats = ordning efter poäng, hela universumet. Platsen är ett **inträdeskriterium**, inte ett hållkriterium (§ 6).

## 4. Portföljstorlek

- Kapital ≥ 161 000 kr (5 × 32 222): **20 innehav**, alla marknader. Målvikt 5 %.
- Kapital < 161 000 kr (**kostnadsläge**): **7 innehav, bara nordiska aktier**, rankade sinsemellan. Målvikt 1/7 ≈ 14,3 %.
  Utländska poster som kapitalet inte bär säljs, lägst rankad först.
  *(Gränsen 161 000: mix-test 2026-10, minimicourtaget 29 kr på utländska poster. Antalet 7: #78 2026-11-05 – 5/6/7/8/10 innehav vid 60/100/150 tkr;
  alla > 5 slog 5 i Calmar och Sharpe, 7 bäst under 2.0; kapitalnivån saknade betydelse eftersom .ST kostar 1 kr i courtage.)*
- Fler innehav än målet (bara möjligt efter manuella köp): lägst rankad säljs ner till målantalet.

## 5. Köp

- Köp sker när det finns **lediga platser** (innehav < målantal) och prövas vid **månadsskiftet** tillsammans med säljsignalerna.
- Kandidater: de högst rankade i universumet (nordiska i kostnadsläge) som **inte redan ägs** och som ligger i **topp 20** (topp 7 i kostnadsläge).
- Ordning: **ren rankningsordning**. Inga tillägg för RSI, fundamenta, sektor eller befintligt innehav. *(2026-11-04.)*
- Belopp: målvikt × portföljvärde per aktie. Alla lediga platser fylls; kassa placeras alltid om kandidater finns.
- Nytt kapital utan lediga platser (och utan likaviktningsköp): fördelas som påfyllning upp till målvikt på befintliga innehav, lägst vikt först.
- En aktie som sålts i samma förslag köps inte tillbaka i samma förslag.
- Backtestet köper på samma dag som säljprövningen (månadens sista handelsdag), fyllt nästa handelsdag. *(Paritetstest P1/P3 2026-11-05; tidigare köpte backtestet var 21:a handelsdag.)*

## 6. Håll och sälj

Ett innehav behålls så länge **båda** gäller:

1. 12-1 > 0 (definition i § 3)
2. Kurs > SMA200

Villkoren prövas **en gång per månad**, på sista handelsdagens stängning. Fönstret för att agera är dagen efter sista handelsdagen
till och med tredje dagen i nästa månad; appen kan räkna om inom tio dagar om prövningen missats.
Bryts något villkor vid prövningen säljs **hela innehavet**. Mellan prövningarna ger ett brutet villkor bara ⏳ (information).
*(Månadsprövning mot daglig: 21,2 mot 20,3 %, 2026-10-23. Kraschtest 2026-11-02: gapsäljning, stopp och "stå kvar" alla sämre.)*

Hållregeln läser 12-1, inte Clenow-måttet – det är så 2.0 validerades. Rankningsplats, RSI, vinst sedan köp, innehavstid, sektor – **påverkar inte** håll/sälj.
*(Rotation 2026-10-22, trötta innehav 2026-11-04: innehav som legat 1–2 år gav +116 %, över 2 år +525 %; att tvinga ut dem kostade.)*

## 7. Likaviktning vid kvartalsskiftet

Vid sista handelsdagen i **mars, juni, september, december** (appen får agera inom 45 dagar):

- Innehav som väger **över 1,25 × målvikt** säljs ner till målvikt (MINSKA).
- Innehav som väger **under 0,75 × målvikt** köps upp till målvikt (ÖKA), om de klarar hållregeln och inte är nynoterade.
- Likaviktningsköp går före nya köp när kassan inte räcker till allt.
*(Likaviktning mot ingen: +2 procentenheter, 2026-11-04. Bara ner / bara topp 20 / viktning efter styrka: ingen robust skillnad.)*

Det finns **inget tak per aktie mellan kvartalen**. En vinnare får växa till nästa kvartalsskifte.

## 8. Kostnadsläge – detaljer

- Den enda storleksregeln är gränsen 161 000 kr (§ 4). Över den köps utländska poster även om de är små. Minsta rad i ett förslag är 1 500 kr.
- Portföljstorleken som styr målantal och kostnadsläge räknas **inklusive nytt kapital** som ska placeras.
- I kostnadsläge rankas bara nordiska aktier; alla utländska innehav säljs.
- Backtest kostnadsläge 2.0 (7 innehav, 100 tkr, #78): 28,9 % per år, Sharpe 1,18, största nedgång −47 %. Räkna med hälften.

## 9. Nynoterade innehav

Innehav med mindre än tolv kalendermånaders historik kan inte prövas (saknar 12-1 och/eller SMA200). De får BEHÅLL med texten
"inget underlag", fylls **inte** på vid likaviktning och får aldrig KÖP/ÖKA. Minskning över 1,25 × målvikt gäller.
*(2026-11-04. Backtestet kan aldrig äga en sådan aktie.)*

## 10. Tidsregler för handel

- Räkna om förslaget **en gång** på månadsskiftets stängning. Handla hela listan nästa handelsdag: Norden på förmiddagen, USA efter 15:30.
- Räkna inte om flera gånger samma dag och agera inte på intradagskurser – rankningsgränser flyttar sig dagligen och ger brus.
- Mellan prövningarna: titta hur ofta du vill, agera inte.

## 11. Detta ingår INTE (testat och förkastat)

| Regel | Testat | Resultat |
|---|---|---|
| Stopp −15 % (med/utan 5-dagarsfilter) | 2026-10 | 18,3 mot 22,6 % |
| Band 10/30 (sälj utanför topp 30) | 2026-10 | sämre, 98 köp/år |
| Daglig prövning med bekräftelse | 2026-10-23 | 20,3 mot 21,2 % |
| Gapsäljning efter dagsfall 15–25 % | 2026-11-02 | sämre i alla nivåer |
| Stå kvar genom krasch (utan SMA200) | 2026-11-02 | sämre |
| Inträdesfilter kurs > SMA200 | 2026-11-03 | identiskt (ingår redan) |
| Rotation: sälj sämst rankade 5–25 % | 2026-10-22 | ingen vinst som höll |
| Trötta innehav: tvinga ut efter 6–18 mån | 2026-11-04 | sämre |
| Volatilitetsjusterat momentum som poäng | 2026-10-16 | 10,6 mot 15,3 % |
| 6-1, 3-1, blandat, viktat momentum | 2026-10 | 12-1 bäst |
| Volymbekräftelse i poängen | 2026-10-16 | 13,7 mot 16,0 % |
| RSI/MACD/SMA50 som köpfilter | 2026-10-23 | borttagna |
| Fundamenta (Graham, Magic Formula) | 2026-10-16 | felimplementerade, framåtblick |
| Sektortak / regimfilter (undvik teknik) | aldrig validerat | borttaget 2026-11-04 |
| Vikt efter styrka (topp får mer) | 2026-11-04 | ej robust, djupare nedgång |
| Jämnhet (andel positiva dagar) / fall inom fönstret | 2026-11-04, #74 | sämre |
| Vinstmål (ta hem vid +35 %) | 2026-10 | sämre |
| Tak per aktie mellan kvartal | aldrig validerat | borttaget 2026-11-04 |
| Likviditetsgolv i kronor 20–250 Mkr | #72 2026-11-05 | lägre CAGR och Sharpe |
| Clenow, 12-1/volatilitet eller jämnhet som **avgörare** (12-1 som poäng) | #74 2026-11-05 | faller |
| Clenow-fönster 60 eller 250 dagar | #76 2026-11-05 | faller; 90–180 vinner |
| Clenows gapfilter (ingen kandidat med dagsrörelse > 15 %) | #76 2026-11-05 | sämre |
| Relativt likviditetsavdrag mot börsens median (fanns bara i appen) | aldrig validerat | borttaget 2026-11-05 |

## 12. Information som visas men inte styr

RSI, MACD, stöd/motstånd, volym, OBV, ATH-avstånd, fundamenta, blankning, nyheter, sektor, daghandelslistan,
rankningsplats för befintliga innehav, ⏳ mellan prövningarna, 1.0-rankningen (visas bredvid 2.0 under övergången),
omsättning relativt börsens median. Allt detta får visas; inget av det får ändra en rad i förslaget.

## 13. Paritetstest

`parity_test.js` kör appens förslagsmotor och en oberoende referens skriven från detta dokument på samma data och
samma portföljer (verkliga + syntetiska: månadsfönster, kvartal, kostnadsläge, fler än målet, nynoterade, tom portfölj)
och jämför rad för rad. Körs i Actions (`parity.yml`) vid varje ändring av index.html, parity_test.js, SPEC.md eller data.json,
på skanningens data och på data med 2.0-fält. Röd körning = appen avviker från specifikationen.

## 14. Förväntan

Backtest 2015–2026 med fyllnad dagen efter, slippage, köp vid månadsslut (#76/#79): **2.0: 42,8 % per år, Sharpe 1,20, Calmar 1,00, största nedgång −43 %.**
1.0 på samma villkor: 34,1 %, Sharpe 1,11, −44 %. Likaviktat snitt av universumet: 19,1 %.
Siffrorna bygger på dagens indexmedlemmar (överlevnadsbias) och tio års in-sample-tester. Körning-till-körning-bruset är ±3–4 procentenheter.

**Överlevnadsbias, mätt nedre gräns (#81, 2026-11-05, `--survive`):** när dagens S&P 500-medlemmar får vara kandidater först från sitt inträdesdatum
(189 av 503 tillagda sedan 2015) faller 2.0 från 44,0 till **36,4 %** (Sharpe 1,29 → 1,11, Calmar 1,05 → 0,89); bara USA 36,6 → 29,6 %; bara S&P 500 31,7 → **17,5 %**.
1.0 faller 35,5 → 31,3 %. Korrigeringen gäller bara en femtedel av universumet och fångar inte bolag som fallit ur index eller gått under;
den verkliga biasen är större. 2.0 slår 1.0 även efter korrigering (36,4 mot 31,3 %, Calmar 0,89 mot 0,73).
**Räkna därför med hälften av backtestet: 20–25 % per år, enskilda år mellan −30 och +100 %, största nedgång omkring −50 %.** Kostnadsläge: se § 8.

## 15. Riskanalys (information)

Appen visar, utan att något av det ändrar en rad i förslaget: exponering per marknad (Norden/USA/Europa), per valuta och per sektor;
vikt per innehav; portföljens realiserade volatilitet (60 dagar); största nedgång från tidigare topp; antal innehav i samma sektor.
Varning visas vid hög koncentration (t.ex. fler än 8 av 20 i en sektor). Riskprofil hanteras utanför modellen: användaren bestämmer
hur stor del av kapitalet som ligger i strategin och lägger resten i indexfond/räntor.

## 16. Datakvalitet och exekvering (information och spärr)

- **Daglig datakontroll** (`datacheck.yml`, före skanningen): per aktie senaste stängning (högst två handelsdagar gammal i aktiens egen kalender),
  orimlig kurs (dagsrörelse > +100 %/−50 %, nollvolym fem dagar, stillastående tio dagar, dubbletter, EODHD mot Yahoo > 20 %), borta ur lista sedan
  i går (med orsak), indexkällor minst 90 % av gårdagens antal. Resultat i `datacheck.json` och `datacheck.md`.
  Jobbet blir rött vid mer än 2 % tysta aktier, en hel marknad saknad eller ett eget innehav flaggat.
- **Spärr i appen:** om kontrollen är röd eller äldre än ett dygn visas "FÖRSLAG EJ VALIDERAT – DATAPROBLEM" i stället för ett förslag.
  Spärren är det enda i § 15–18 som hindrar ett förslag; den ändrar aldrig en rads innehåll.
- **Felaktiga kurser lagas, kastas inte:** en dagsrörelse över +300 %/under −80 % som är tillbaka inom fem handelsdagar ersätts med föregående stängning,
  i både backtest och skanner. Bara aktier där felet består tas bort. *(Före 2026-11-05 kastade backtestet 20 aktier helt, bland dem tolv nordiska
  med korrupta EODHD-rader 2019-06-10 och 2019-08-05, medan skannern behöll dem.)*
- Avnoterade innehav: backtestet säljer till sista kända kurs; appen visar innehavet med varning tills ägaren registrerar försäljningen.
- Signal räknas på stängningen; tidigaste handel är nästa handelsdag.

## 17. Portföljuppföljning (information)

Transaktionslogg per rad: datum, aktie, marknad, beslut, modellversion, poäng och rankning vid beslutet, orsak (§ 5/6/7), föreslaget belopp,
genomfört belopp och pris, courtage. Loggen ger: modellportfölj mot verklig portfölj, avkastning efter kostnader, avvikelser
(ej genomförda rader, registrerade med orsak), jämförelse mot OMXSPI och ett globalt index i SEK. Loggen är det enda out-of-sample-underlaget.

## 18. Paritet backtest ↔ app (lästa ur koden 2026-11-05)

| Punkt | Backtest | Skanner/app |
|---|---|---|
| Fyllnad | Nästa handelsdags stängning (`t1`) | Handlas dagen efter signalen |
| Köptillfälle | Månadens sista handelsdag (`refill_days="M"`) | Månadsslut (§ 5) |
| Säljprövning | Sista handelsdagen i månaden, stängning | Samma |
| Likaviktning | Sista handelsdagen i kvartalet, band ±25 % | Samma; appen får agera inom 45 dagar |
| 12-1 | Kalendermånader (`m121_cal`) | Kalendermånader (`cd.asof`) |
| Clenow | `clenow_panel`, 120 rader | `np.polyfit` på senaste 120 stängningar |
| SMA200 stigande | SMA200 i dag > i går | Samma |
| Överhettning | (r1m − 25)/25 × 15 + (RSI − 80)/20 × 10, tak 25 | Samma sedan 2026-11-05 |
| Likviditetsgolv | 3 M i handelsvalutan → ingen poäng | Samma sedan 2026-11-05 (`liqok`) |
| Poänggolv | Inget golv, ingen övre gräns | 0–100 (påverkar inte ordningen i toppen) |
| Lika Clenow-värde | Kolumnordning (alfabetisk ticker) | Listordning – **ska fastställas** |
| Felkurser | Lagar dagen (`repair_glitches`), kastar bara om felet består | Lagar dagen (`repair_closes`) – samma regel sedan 2026-11-05 |
| Avnotering | Säljs till sista kända kurs | Varning (§ 16) |
| Avknoppning/split | Yahoo `auto_adjust`; EODHD justerad stängning | Samma datakällor – ska verifieras |

Kvar att harmonisera: lika Clenow-värde, avknoppningar.
