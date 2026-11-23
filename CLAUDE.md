# MomentumMate – startinstruktion för Claude

Läs i denna ordning innan något annat görs i repot:

1. `docs/HANDBOK.md` – vad verktyget är, arkitektur, strategin i sammandrag, alla funktioner i appen, beslut, arbetssätt, öppet läge.
2. `SPEC.md` – regelverket (modell 2.0). Facit. Varje regel som kan påverka köp/sälj står här med körning och datum; allt annat är information.
3. `STATUS.md` – läget just nu och återstående lista.
4. `TESTPLAN_2.0.md` – prövade och planerade tester med beslutsregler.

Samma dokument finns i claude.ai-projektet *MomentumMate* (mappen `claude/`). Repot är sanningen för kod, projektet för beslut – håll dem lika (skriv till båda).

Regler som alltid gäller:
- Ändra aldrig en regel i SPEC § 1–10 utan ett test med beslutsregel skriven i förväg (TESTPLAN).
- Appen är `index.html` (en fil). Bumpa `APPV` vid varje publicerad ändring; syntaxkontrollera (`node -e` över script-blocken) före push.
- Commit-meddelanden på svenska; `git pull --rebase` före push.
- Ägarens e-post skickas aldrig till externa tjänster; hemligheter läggs av ägaren i GitHub.
- Språk: svenska.
