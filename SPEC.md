# MomentumMate – specifikation av kärnmodellen

Version 2026-11-04. Detta dokument är facit. Appens beslutsmotor ska göra exakt detta och ingenting annat.
Allt som kan styra ett köp eller en försäljning står här; allt annat i appen är information.
Varje regel har validerats i `backtest.py` (10 år, ca 2 300 aktier, verkliga kostnader) – datum och resultat anges.

## 1. Mål

Högsta möjliga tillväxt över tid med en regelstyrd momentumstrategi, utan diskretionära bedömningar.
Priset är djupa nedgångar (historiskt ca −40 % som värst) och stora svängningar år för år.

## 2. Universum och data

- Ca 2 300 aktier: Norden (OMXS, OMXC, OSE, OMXH), USA (S&P 500/400/600, Nasdaq-100, Dow, ADR), Europa (STOXX 600 m.fl.).
- Stängningskurser. Signaler räknas på senaste **stängning**, aldrig på kurs under pågående handel.
- Minst 253 handelsdagar historik krävs för rankning (12-1). Aktier med kortare historik är **nynoterade** (§ 9).
- Likviditet: aktier med omsättning under ca 150 000 kr/dag får −20 poäng (skannern); backtestet kräver 3 Mkr.

## 3. Poäng och rankning

Poäng 0–100 per aktie:

- **Momentum (80 %)**: 12-1-avkastning = kurs för 21 handelsdagar sedan / kurs för 252 handelsdagar sedan − 1.
  Skalas linjärt 0–150 % → 0–100 poäng; över 150 % ger 100. *(Tak 150 valt 2026-11-04, test `--tie`, X4.)*
- **Trendkvalitet (20 %)**: kurs > SMA50 (30), kurs > SMA200 (30), SMA50 > SMA200 (20), SMA200 stigande (20).
- **Överhettningsavdrag**: 1 månads uppgång över 25 % ger upp till −15; RSI över 80 ger upp till −10. *(Validerat 2026-10-16.)*
- **Lika poäng avgörs av högre 12-1.** *(2026-11-04, `--tie`: 36 mot 20 % per år; vann 12 av 12 delprov.)*
- Bara aktier **över SMA200** rankas som köpkandidater (trendfilter).

Rankningsplats = ordning efter poäng, hela universumet. Platsen är ett **inträdeskriterium**, inte ett hållkriterium (§ 6).

## 4. Portföljstorlek

- Kapital ≥ 161 000 kr (5 × 32 222): **20 innehav**, alla marknader. Målvikt 5 %.
- Kapital < 161 000 kr (**kostnadsläge**): **5 innehav, bara nordiska aktier**, rankade sinsemellan. Målvikt 20 %.
  Utländska poster som kapitalet inte bär säljs, lägst rankad först. *(Mix-test 2026-10; minimicourtaget 29 kr dominerar under 32 222 kr per affär.)*
- Fler innehav än målet (bara möjligt efter manuella köp): lägst rankad säljs ner till målantalet.

## 5. Köp

- Köp sker när det finns **lediga platser** (innehav < målantal) och prövas vid **månadsskiftet** tillsammans med säljsignalerna.
- Kandidater: de högst rankade i universumet (nordiska i kostnadsläge) som **inte redan ägs** och som ligger i **topp 20** (topp 5 i kostnadsläge).
- Ordning: **ren rankningsordning**. Inga tillägg för RSI, fundamenta, sektor eller befintligt innehav. *(2026-11-04.)*
- Belopp: målvikt × portföljvärde per aktie. Alla lediga platser fylls; kassa placeras alltid om kandidater finns.
- Nytt kapital utan lediga platser (och utan likaviktningsköp): fördelas som påfyllning upp till målvikt på befintliga innehav, lägst vikt först.
- En aktie som sålts i samma förslag köps inte tillbaka i samma förslag.

## 6. Håll och sälj

Ett innehav behålls så länge **båda** gäller:

1. 12-1 > 0
2. Kurs > SMA200

Villkoren prövas **en gång per månad**, på sista handelsdagens stängning. Fönstret för att agera är sista handelsdagen
till och med tredje dagen i nästa månad; appen kan räkna om inom tio dagar om prövningen missats.
Bryts något villkor vid prövningen säljs **hela innehavet**. Mellan prövningarna ger ett brutet villkor bara ⏳ (information).
*(Månadsprövning mot daglig: 21,2 mot 20,3 %, 2026-10-23. Kraschtest 2026-11-02: gapsäljning, stopp och "stå kvar" alla sämre.)*

Rankningsplats, RSI, vinst sedan köp, innehavstid, sektor – **påverkar inte** håll/sälj.
*(Rotation 2026-10-22, trötta innehav 2026-11-04: innehav som legat 1–2 år gav +116 %, över 2 år +525 %; att tvinga ut dem kostade.)*

## 7. Likaviktning vid kvartalsskiftet

Vid sista handelsdagen i **mars, juni, september, december** (fönster 45 dagar):

- Innehav som väger **över 1,25 × målvikt** säljs ner till målvikt (MINSKA).
- Innehav som väger **under 0,75 × målvikt** köps upp till målvikt (ÖKA), om de klarar hållregeln och inte är nynoterade.
- Likaviktningsköp går före nya köp när kassan inte räcker till allt.
*(Likaviktning mot ingen: +2 procentenheter, 2026-11-04. Bara ner / bara topp 20 / viktning efter styrka: ingen robust skillnad.)*

Det finns **inget tak per aktie mellan kvartalen**. En vinnare får växa till nästa kvartalsskifte.

## 8. Kostnadsläge – detaljer

- Den enda storleksregeln är gränsen 161 000 kr (§ 4). Över den köps utländska poster även om de är små –
  mix-testet accepterade 29 kr courtage på 8 000-kronorsposter. Minsta rad i ett förslag är 1 500 kr.
- Portföljstorleken som styr målantal och kostnadsläge räknas **inklusive nytt kapital** som ska placeras.
- I kostnadsläge rankas bara nordiska aktier; alla utländska innehav säljs.

## 9. Nynoterade innehav

Innehav med < 253 handelsdagar kan inte prövas (saknar 12-1 och/eller SMA200). De får BEHÅLL med texten
"inget underlag", fylls **inte** på vid likaviktning och får aldrig KÖP/ÖKA. Minskning över 1,25 × målvikt gäller.
*(2026-11-04. Backtestet kan aldrig äga en sådan aktie.)*

## 10. Tidsregler för handel

- Räkna om förslaget **en gång** på månadsskiftets stängning. Handla hela listan samma dag: Norden på förmiddagen, USA efter 15:30.
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
| Volatilitetsjusterat momentum | 2026-10-16 | 10,6 mot 15,3 % |
| 6-1, 3-1, blandat, viktat momentum | 2026-10 | 12-1 bäst |
| Volymbekräftelse i poängen | 2026-10-16 | 13,7 mot 16,0 % |
| RSI/MACD/SMA50 som köpfilter | 2026-10-23 | borttagna |
| Fundamenta (Graham, Magic Formula) | 2026-10-16 | felimplementerade, framåtblick |
| Sektortak / regimfilter (undvik teknik) | aldrig validerat | borttaget 2026-11-04 |
| Vikt efter styrka (topp får mer) | 2026-11-04 | ej robust, djupare nedgång |
| Jämnhet / fall inom fönstret (kvalitet) | 2026-11-04 | 25–30 mot 36 % |
| Vinstmål (ta hem vid +35 %) | 2026-10 | sämre |
| Tak per aktie mellan kvartal | aldrig validerat | borttaget 2026-11-04 |

## 12. Information som visas men inte styr

RSI, MACD, stöd/motstånd, volym, OBV, ATH-avstånd, fundamenta, blankning, nyheter, sektor, daghandelslistan,
rankningsplats för befintliga innehav, ⏳ mellan prövningarna. Allt detta får visas; inget av det får ändra en rad i förslaget.

## 13. Paritetstest

`parity_test.js` kör appens förslagsmotor och en oberoende referens skriven från detta dokument på samma data och
samma portföljer (verkliga + syntetiska: månadsfönster, kvartal, kostnadsläge, fler än målet, nynoterade, tom portfölj)
och jämför rad för rad. Testet ska köras och vara grönt före varje uppladdning av index.html.

## 14. Förväntan

Backtest 2015–2026: 36 % per år, Sharpe 1,2, största nedgång −40 %. Med överlevnadsbias och verklig friktion:
räkna med 20–25 % per år, enskilda år mellan −30 och +80 %. Riktningen (nya rankningen slår gamla) är robust; siffran är det inte.
