# MomentumMate 2.0 – testplan

Version 2026-11-05. Beslutad i samtal 2026-11-05: 2.0 är en **kandidatmodell** som testas mot 1.0 (SPEC.md 2026-11-04).
1.0 är fryst. Inga ändringar i beslutsmotorn görs utan att ett steg i denna plan är klart och resultatet står i STATUS.md.
**Utfall:** steg 1–3 avgjorda 2026-11-05 (2.0 = Clenow 120 d vann, SPEC § 3). § 9: fyra strategitester körda 2026-11-18 – ingen klarade regeln, 2.0 oförändrad.

## 0. Bakgrund

Observation: köplistan domineras av små, okända, volatila bolag. Orsak: avgöraren "högre 12-1" bland alla aktier
som slagit i taket 150 % gör topp 20 av 2 300 till de tjugo aktier som stigit mest senaste året. Storbolag når
sällan 150 %; universumet är till över två tredjedelar små- och medelstora bolag. Det är också där överlevnadsbias,
slippage och kraschrisk är störst. Hoppet 20 → 36 % per år (2026-11-04) kan till stor del vara småbolagseffekt + bias.

Mål med 2.0: starkt momentum i bolag som går att äga och handla – större, likvidare, lönsamma – utan att ändra
håll-/säljreglerna, månadsprövningen eller kvartalslikaviktningen (validerade, oberoende av urvalet).

## 1. Steg 0 – säkra referensen (görs före allt annat)

| # | Åtgärd i `backtest.py` | Varför |
|---|---|---|
| 0a | Kontrollera fyllnadstidpunkt. Om köp/sälj fylls på signaldagens stängning: inför `--t1` (fyll på nästa handelsdags stängning, eller öppning om data finns). | Signalen finns först efter stängning; dagens app handlar T+1. |
| 0b | Inför `--slip X` = slippage per sida i procent, proportionell mot post/20-dagarsomsättning (t.ex. 0,1 % + 0,5 % × andel av ADV, tak 2 %). | "Verkliga kostnader" = courtage; impact saknas för småbolag. |
| 0c | Rapportera **omsättningshastighet**, **medel-ADV i innehaven**, **median börsvärde i innehaven** och **total friktion** i varje körning. | Jämförbarhet mellan varianter. |
| 0d | Kör referensen A = 1.0 med 0a–0c. Detta är nya baslinjen. | Alla jämförelser nedan görs mot A, inte mot 36 %. |

Samtidigt (ingen kod): kostnadslägets egen största nedgång tas fram (5 innehav, Norden). Står inte i SPEC i dag.

## 2. Testmatris

| Variant | Ändring mot föregående | Flagga |
|---|---|---|
| **A** | 1.0 + T+1 + slippage (referens) | `--t1 --slip` |
| **B** | A på storbolagsuniversum: likviditetsgolv höjt till ≥ 20 Mkr/dag (alternativt börsvärde ≥ 10 mdkr); prova även 50 Mkr | `--minadv 20e6` |
| **C** | B med jämnt momentum som avgörare i stället för rå 12-1: Clenow-lutning × R² (90 d) eller 12-1 / 60-dagarsvolatilitet | `--smooth clenow` / `--smooth vol` |
| **D** | C med lönsamhetsfilter: positivt rörelseresultat senaste fyra rapporterade kvartalen (rapportdatum, inte periodslut). Norden först (Börsdata), USA när data finns. | `--quality ebit` |

Ordning: B körs först (minst ändring, störst informationsvärde). C och D bara om B inte faller igenom.
Varje variant körs dessutom med `--cost2` (dubbla kostnader) och `--shift` (förskjutet månadsskifte/kvartal).

## 3. Beslutsregel (skriven före körning)

2.0-kandidaten (B, C eller D) **vinner över A** om samtliga gäller:

1. Calmar (CAGR / största nedgång) högre än A.
2. Sharpe inte lägre än A.
3. CAGR efter slippage faller högst **5 procentenheter** mot A. *(Accepterat 2026-11-05: 2.0 får förlora i rå CAGR.)*
4. Håller vid `--cost2` och `--shift`.
5. Största nedgång inte djupare än A.
6. Medel-ADV i innehaven minst 5× A:s (implementerbarhet).

Förväntan i förväg (så att vi inte flyttar målet efteråt): B–D ger 12–20 % per år med nedgång 30–40 %,
mot A:s sannolika 15–25 % med ~50 %. Om B ger lägre CAGR men klarar 1, 2, 4–6 är det ett **godkänt** utfall.

## 4. Överlevnadsbias – parallellt spår

| Nivå | Åtgärd | Kostnad |
|---|---|---|
| Billig | `--entry-date`: en aktie får vara kandidat först från det datum den togs in i sitt index. S&P 500-ändringar finns publikt (Wikipedia-historik). | 0 kr, en dags arbete |
| Billig | Jämför A på bara S&P 500 + OMXS large cap mot hela universumet. Faller kanten ihop i storbolag ökar misstanken. | 0 kr |
| Bättre | Survivorship-fri USA-data med historiska indexmedlemmar (Norgate e.d.), Börsdata för Norden inkl. avnoterade. Kör även USA 2000–2014 (dot-com, 2008–09). | några tusen kr/år |

Det här spåret avgör hur mycket av 36 % som är verkligt. Det påverkar inte beslutsregeln i § 3 (som jämför
varianter mot varandra på samma data) men avgör § 14 i SPEC. *Ägarbeslut 2026-11-18: "Bättre"-nivån avvaktar.*

## 5. Rapportformat (alla körningar, samma kolumner)

CAGR · största nedgång · Sharpe · Calmar · årlig volatilitet · omsättningshastighet · affärer/år ·
medel-ADV innehav · median börsvärde innehav · total friktion % · resultat per delperiod (12 st) · `--cost2` · `--shift`.

Läggs som ruta i `.github/workflows/backtest.yml`; resultat klistras in i STATUS.md i samma form som tidigare tester.

## 6. Senare tester (först när § 2 är avgjort, högst ett i taget)

- Sektortak: högst 6 av 20 per sektor. *(Aldrig validerat; togs bort 2026-11-04 utan test.)* → **körd 2026-11-18, § 9.**
- Riskbudget på portföljnivå: exponering = min(1, σ_mål / σ_realiserad 60 d), eller exponering = andel av universumet
  över SMA200. Måste visa mervärde utöver modellens inbyggda kassaeffekt (kandidaterna tar slut i breda nedgångar). → **bredd-varianten körd 2026-11-18, § 9.**
- Vikter i omvänd proportion till volatilitet (skiljer sig från "vikt efter styrka", som är förkastat). → **körd 2026-11-18, § 9.**

## 7. Ingår inte (beslutat 2026-11-05)

Multifaktormodell med fundamenta i poängen, korrelationsoptimerare, femmotorsarkitektur. Marknadsläge, bredd,
volatilitet och riskexponering byggs som **information** i appen (SPEC § 12), inte som styrning.

## 8. Öppna definitioner som ska in i SPEC när koden lästs

Dessa avgör paritet mellan backtest och app och ska skrivas ut exakt som koden gör dem:
valuta i 12-1 (lokal eller SEK) · handelsdagsräkning per marknad eller gemensam kalender · definition av "sista handelsdagen"
när marknader stänger olika dagar · tidigaste handelsdag = dagen efter signal (§ 6 säger i dag "sista handelsdagen") ·
likaviktningsfönstret "45 dagar" · SMA200-lutningens period · RSI- och överhettningsavdragens kurvor · likviditetsavdragets
beräkning · golv för poäng · avgörare vid lika 12-1 (ticker i bokstavsordning) · hantering av avknoppning, split, handelsstopp,
saknade kurser.

## 9. Strategitester under 2.0 (ägarbeslut 2026-11-18) – `backtest.py --strat`

Referens **A = modell 2.0 på P3-basis** (Clenow 120, köp vid månadsslut, kalender-12-1, T+1, slippage, 20 innehav, 10 Mkr):
44,0 % / Sharpe 1,29 / nedgång −42,1 % i #82. Alla varianter körs på hela perioden, båda halvorna, med dubbla kostnader och
förskjutet kvartal. Fyra tester, en ändring i taget. Beslutsreglerna nedan är skrivna **före** körningen.

| Test | Varianter | Mekanism i koden |
|---|---|---|
| **T1 Gapsäljning** | gap 22 / 25 / 30 % (sälj dagen efter ett dagsfall ≥ x %) | `gap_sell` (fanns; #82 gav 25 %: 43,8 % / 1,39 / −39,4 %) |
| **T2 Riskbudget efter marknadsläge** | inga nya köp när andelen av universumet över SMA200 < 40 / 30 %; halv exponering (10 innehav) under 40 %; halv exponering under 30 % + full under 50 % | `breadth_cut`, `breadth_expo` (ny) – bredd = andel aktier > SMA200 i universumet dagen före |
| **T3 Sektortak** | högst 6 / 8 av 20 per sektor (sektor från `data.json`; aktier utan sektor räknas som egen) | `sector_cap` (ny i urvalet) |
| **T4 Omvänd volatilitet** | vikt ∝ 1/σ60 normerad till 20 innehav, tak 2× och golv 0,5× likavikt; likaviktning vid kvartal mot samma vikter | `inv_vol` (ny) |

**Beslutsregel (gäller varje test för sig):** varianten går in i SPEC bara om samtliga gäller mot A:
1. Sharpe **och** Calmar högre än A på hela perioden.
2. Sharpe inte lägre än A i **båda** halvorna.
3. Håller (regel 1) med dubbla kostnader och med förskjutet kvartal.
4. CAGR faller högst **2 procentenheter** mot A (gapsäljning: högst 2; riskbudget: högst 4, eftersom syftet är grundare nedgång).
5. Största nedgång inte djupare än A.
Faller en variant på någon punkt förkastas den och förs in i SPEC § 11. Vinner flera nivåer i samma test väljs den mittersta.
Förväntan i förväg: T1 (25 %) troligen godkänd (#82); T2 troligen förkastad – modellens egen kassaeffekt gör samma jobb
(H-varianterna i #82); T3 oklar; T4 troligen sämre CAGR, möjligen bättre Sharpe.

**Inte nu:** radarn och analytikerrevideringar som avgörare inom topp 20 – kräver ett års egen logg (2027).

### Utfall § 9 – körda 2026-11-18 (Actions backtest.yml #44339170065 gap, #44339185565 bredd, #44339199857 sektor, #44339214899 invvol)

Referens A i körningarna: **43,2 % / Sharpe 1,25 / Calmar 1,03 / nedgång −42,0 %**, halvor Sharpe 1,45 / 1,08–1,14, dubbla kostnader 42,2 % / 1,22 / 1,00, Q+1 42,5 % / 1,35 / 1,05.

| Variant | CAGR | Sharpe | Calmar | Nedgång | Halvor | Dubbla | Q+1 | Utfall |
|---|---|---|---|---|---|---|---|---|
| T1-22 gap 22 % | 44,6 | 1,40 | 1,26 | −35,4 | 1,42 / 1,29 | 44,5 / 1,39 / 1,23 | 41,6 / 1,32 / 1,09 | faller: halva 1 (1,42 < 1,45), Q+1 Sharpe (1,32 < 1,35) |
| T1-25 gap 25 % | 46,4 | 1,43 | 1,24 | −37,4 | 1,33 / 1,25 | 45,5 / 1,41 / 1,20 | 42,1 / 1,33 / 1,10 | faller: halva 1 (1,33 < 1,45), Q+1 Sharpe |
| T1-30 gap 30 % | 43,5 | 1,36 | 1,11 | −39,3 | 1,44 / 1,08 | 42,4 / 1,33 / 1,08 | 42,5 / 1,33 / 1,04 | faller: halva 1, Q+1 |
| T2-a inga köp bredd < 40 % | 34,6 | 1,02 | 0,82 | −42,2 | 1,15 / 1,03 | 33,4 / 0,99 | 28,0 / 1,05 | faller på allt |
| T2-b inga köp bredd < 30 % | 39,5 | 1,15 | 0,98 | −40,5 | 1,30 / 1,05 | 38,3 / 1,12 | 39,4 / 1,33 | faller på Sharpe, Calmar, halvor |
| T2-c halv exponering < 40 % | 34,5 | 1,07 | 0,83 | −41,6 | 1,61 / 0,69 | 32,8 / 1,02 | 30,8 / 1,06 | faller på allt utom nedgång |
| T2-d halv < 30 %, full > 50 % | 31,1 | 0,98 | 0,82 | −38,2 | 1,50 / 0,61 | 32,1 / 1,00 | 32,6 / 1,10 | faller på allt utom nedgång |
| T3-6 högst 6 per sektor | 38,4 | 1,30 | 0,97 | −39,7 | 1,32 / 1,28 | 37,2 / 1,26 / 0,94 | 37,2 / 1,30 / 0,95 | faller: Calmar, halva 1, dubbla, Q+1, CAGR −4,9 |
| T3-8 högst 8 per sektor | 44,9 | 1,28 | 1,06 | −42,3 | 1,41 / 1,17 | 43,5 / 1,24 / 1,03 | 42,3 / 1,34 / 1,07 | faller: halva 1 (1,41 < 1,45), Q+1, nedgång −42,3 |
| T4 omvänd volatilitet | 38,3 | 1,38 | 1,12 | −34,2 | 1,48 / 1,21 | 36,9 / 1,33 / 1,06 | 35,8 / 1,36 / 1,03 | faller: Q+1 Calmar (1,03 < 1,05), **CAGR −4,9** (gräns 2) |

**Slutsats 2026-11-18: ingen variant klarar beslutsregeln – 2.0 står oförändrad.** Noteringar för framtida test (inget av detta ändrar modellen):
- Gapsäljning 22–25 % höjer Sharpe/Calmar och ger grundare nedgång på hela perioden (tredje gången, #82 och nu), men vinsten ligger i **andra halvan**
  (1,25–1,29 mot A:s 1,08–1,14) och saknas i första; regeln kräver båda. Faller därför, som i § 11. Kan prövas igen om ett år med loggens egna data.
- Riskbudget efter bredd är klart sämre i alla former – modellens egen kassaeffekt gör jobbet (som förutsagt).
- Sektortak: 8 ≈ A inom bruset, 6 kostar 5 procentenheter. Ingen anledning att införa.
- Omvänd volatilitet ger den grundaste nedgången (−34 %) och bästa Sharpe (1,38) i hela omgången men kostar 5 procentenheter CAGR. Målet (SPEC § 1) är tillväxt,
  och risknivån styrs via andelen kapital i strategin (allokeringsvyn), inte i modellen. Förkastad enligt regeln; om ägaren någon gång vill byta mål till
  riskjusterad avkastning är detta den variant som ska prövas först, med en ny regel skriven i förväg.
