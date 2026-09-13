# Rootly — Roadmap de implementare

> Document de planificare generat pe baza analizei README.md și a stării actuale a repo-ului (27 august 2026).
> Scop: transformarea documentației existente (faza de design) într-un MVP funcțional.
>
> **Actualizare (13 septembrie 2026):** Fazele 1–6 sunt implementate — vezi bifele de mai jos și secțiunea
> "MVP Status" din README.md pentru starea curentă. Stack-ul de LLM a fost `Mistral` (via `langchain-mistralai`)
> în loc de Claude, pentru că echipa are acces la o cheie gratuită Mistral, nu la una Anthropic — vezi nota din
> §3 mai jos. Restul planului (structură, tool-uri, guardrail, RAG, UI) a rămas neschimbat față de recomandare.

---

## 0. Unde suntem acum

Repo-ul conține în acest moment **doar documentație și diagrame** — niciun cod sursă:

```text
Rootly/
├── README.md
└── diagrame_imagini/
    ├── architecture.png
    ├── react-reasoning-loop.png
    └── (imagini ChatGPT suplimentare)
```

Ce e deja bine definit în README și poate fi folosit direct ca specificație:
- Scopul MVP-ului și ce e explicit **exclus** (remediere automată, integrare cu sisteme reale, multi-tenant) — foarte util, ține scope-ul mic și fezabil.
- Schema de date (Alert, CMDB Component, Log Entry, Historical Incident) — suficient de detaliată pentru a genera mock data direct.
- Bucla ReAct (Thought → Action → Observation) și cele 3 tool-uri (`cmdb_lookup`, `log_search`, `similar_incidents_search`).
- KPI-uri de succes (Time-to-Diagnosis, Correctness Rate).

Ce lipsește complet: cod, structură de proiect, alegere de stack tehnic, date mock efective, teste, UI.

Notă administrativă: `git status` arată că imaginile vechi (`architecture.png`, `react-reasoning-loop.png` etc., referențiate direct din README) au fost mutate în `diagrame_imagini/` dar README încă le linkuiește din rădăcină (`./architecture.png`). Dacă struc­tura nouă e cea dorită, README trebuie actualizat (fie mutăm imaginile înapoi, fie corectăm path-urile) — altfel imaginile nu se mai văd pe GitHub.

---

## 1. Direcție generală recomandată

Trece de la "documentație" la "cod care rulează" cât mai repede, chiar dacă e minimal — un demo funcțional (fie și doar în CLI, cu un singur scenariu hardcodat) validează arhitectura mult mai bine decât mai multă documentație. Ordinea de mai jos e gândită ca fiecare fază să producă ceva demonstrabil.

---

## 2. Faze de implementare

### Faza 1 — Schelet de proiect + date mock (fundația)
**Obiectiv:** structură de repo pentru cod + toate datele mock din secțiunea 5 a README-ului, ca fișiere statice.

- [x] Creează structura de directoare:
  ```text
  Rootly/
  ├── src/
  │   ├── tools/          # cmdb_lookup, log_search, similar_incidents_search
  │   ├── agent/           # orchestratorul ReAct
  │   └── models/          # scheme Pydantic pentru Alert, CMDB, LogEntry, Incident
  ├── data/
  │   ├── alerts.json
  │   ├── cmdb.json
  │   ├── logs.json
  │   └── incidents.json
  ├── tests/
  ├── docs/diagrams/       # mută imaginile aici, actualizează README
  ├── requirements.txt / pyproject.toml
  └── .env.example
  ```
- [x] Generează datele mock conform secțiunii 5.3 — varianta finală a mers dincolo de minimul din README:
  5 scenarii de alertă (nu 2–3), 10 componente CMDB, 124 log entries, 10 incidente istorice (câte 2 per scenariu:
  match direct + parțial). Detalii complete în [`MOCK_DATA_README.md`](./MOCK_DATA_README.md).
- [x] Validează schemele cu Pydantic (corespondență exactă cu JSON-urile din README §5.2) — `src/models/schemas.py`.

**Rezultat:** date mock complete și validate, gata de consumat de tool-uri.

---

### Faza 2 — Tool-urile (fără agent încă)
**Obiectiv:** funcții deterministe, testabile independent, înainte de a le conecta la un LLM.

- [x] `cmdb_lookup(component: str) -> CMDBComponent` — lookup + traversare dependențe (`src/tools/cmdb_lookup.py`).
- [x] `log_search(service: str, time_window: tuple, level: str | None) -> list[LogEntry]` — filtrare structurată (`src/tools/log_search.py`).
- [x] `similar_incidents_search(query: str) -> list[Incident]` — implementat direct cu ChromaDB (Faza 5 s-a făcut aici, nu separat), cu fallback automat pe BM25 dacă ChromaDB/modelul de embeddings nu se pot încărca (`src/tools/incident_search.py`).
- [x] Teste unitare pentru fiecare tool, pe datele mock (`tests/test_tools.py`).
- [x] Definește schema de function-calling (tool schema JSON) pentru fiecare — schema comună e definită o dată per tool și convertită la formatul specific modelului în `src/agent/react_loop.py::to_openai_tool`.

**Rezultat:** 3 tool-uri complet funcționale și testate, independent de orice LLM.

---

### Faza 3 — Orchestratorul ReAct (miezul sistemului)
**Obiectiv:** bucla Thought → Action → Observation descrisă în README §6.

- [x] Alege un LLM cu tool-use/function-calling — **Mistral** (`mistral-large-latest` implicit) via `langchain-mistralai`, nu Claude; vezi nota din §3.
- [x] Implementează bucla de bază: prompt inițial cu alerta → LLM decide tool-ul → execută tool → adaugă observația în context → repetă (`src/agent/react_loop.py`).
- [x] Implementează criteriile de oprire din README §6, prin promptul de sistem (`src/agent/system_prompt.py`) plus un guardrail programatic: pachetul final e acceptat doar dacă referă componente CMDB și incidente reale și citează dovezi din loguri — altfel e respins înapoi la model ca eroare de tool, cu motivul exact.
- [x] Păstrează un **step history** explicit (thought/action/observation, cu durată și erori) — expus prin callback-ul `on_event`, consumat atât de CLI cât și de UI.
- [x] Sintetizează output-ul final: pachetul de diagnostic e livrat printr-un tool call `submit_diagnosis` cu schemă fixă (nu text liber de parsat), validat apoi cu `DiagnosisPackage`.
- [x] Rulează pe toate cele 5 scenarii mock (nu doar 2–3) — `python evaluate.py` automatizează asta și scrie `EVAL_RESULTS.md`.

**Rezultat:** `python run_cli.py ALRT-001` din CLI dă un pachet de diagnostic JSON + trace-ul de raționament live. Primul demo real.

---

### Faza 4 — Interfață minimală (UI/CLI)
**Obiectiv:** README §1.3 cere un mod de a declanșa un scenariu și a inspecta trace-ul + diagnosticul.

- [x] Varianta rapidă: CLI cu output formatat Rich (`run_cli.py`) — trace live + tabel de diagnostic.
- [x] Varianta demo-ready: UI Streamlit (`src/ui/streamlit_app.py`) cu dropdown de scenariu, trace live pas cu pas și panou de diagnostic final.
- [x] Export al pachetului de diagnostic ca `.json` și `.md`, plus trace-ul complet ca `.json` (`--export` în CLI, butoane de download în Streamlit).

**Rezultat:** oricine poate rula proiectul și vedea end-to-end fluxul, nu doar tu din cod.

---

### Faza 5 — RAG pentru incidente istorice (opțional, dar valoros pt. diferențiere)
**Obiectiv:** înlocuiește placeholder-ul din Faza 2 cu retrieval semantic real, conform README §5.5.

- [x] Integrează ChromaDB local (persistent, sub `.chroma/`, rebuild automat când `incidents.json` se schimbă).
- [x] Embedding pentru descrierile + root cause + resolution + tags din `incidents.json`, cu `all-MiniLM-L6-v2`.
- [x] La rulare, agentul construiește query-ul din ce a aflat deja și cere top-k incidente similare prin tool-ul `similar_incidents_search`.
- [x] Rezultatele (root cause, resolution, similaritate) ajung direct în contextul agentului ca observație de tool.
- [x] Bonus față de plan: fallback automat pe BM25 (keyword ranking) dacă ChromaDB sau modelul de embeddings nu pot fi încărcate — RAG-ul nu e un single point of failure pentru demo.

**Rezultat:** agentul poate spune "acest incident seamănă cu INC-2025-114, rezolvat prin creșterea connection pool-ului" — un diferențiator puternic față de un simplu lookup CMDB+logs. Testat: pentru fiecare din cele 5 scenarii, incidentul cu match direct iese pe primul loc (`tests/test_tools.py::test_incident_search_ranks_direct_match_first_for_each_scenario`).

---

### Faza 6 — KPI-uri și evaluare
**Obiectiv:** README §7 — măsurarea Time-to-Diagnosis și Correctness Rate.

- [x] Instrumentează orchestratorul să măsoare timpul de la intake până la pachetul final (`elapsed_seconds` pe `DiagnosisResult`, afișat în CLI/UI).
- [x] Creează un checklist de evaluare pentru fiecare scenariu — `evaluate.py` verifică automat componenta afectată, dependențele, dovezile din loguri și incidentul istoric potrivit față de un ground truth fix per scenariu; root-cause-ul rămâne o bifă manuală.
- [x] Rulează toate scenariile, calculează rata de succes, documentează rezultatele — `python evaluate.py` scrie `EVAL_RESULTS.md` cu un tabel per scenariu.
- [ ] (Opțional, netăcut) Compară cu un timp estimat de investigare manuală, ca baseline — nu e implementat, ar fi un rând în plus în `EVAL_RESULTS.md`.

**Rezultat:** dovadă cuantificabilă că agentul funcționează — utilă pentru orice prezentare/demo către stakeholderi. **Rulați `python evaluate.py` cu o cheie Mistral validă înainte de prezentare** ca `EVAL_RESULTS.md` să reflecte comportamentul curent al agentului.

---

### Faza 7 — Polish & pregătire pentru extindere
- [x] README actualizat cu instrucțiuni reale de instalare/rulare — secțiunea "MVP Status" din README.md.
- [ ] `CONTRIBUTING.md` minimal dacă lucrați în echipă — nefăcut, opțional pentru un MVP de prezentare.
- [ ] CI simplu (GitHub Actions) care rulează testele tool-urilor la fiecare push — nefăcut; `python -m pytest` local acoperă asta pentru acum.
- [ ] Discuție despre pașii din README §9 care rămân pentru "viitor îndepărtat": arhitectură multi-agent, integrare cu sisteme reale (Datadog/ServiceNow/Splunk) — nu le începe înainte ca MVP-ul de mai sus să fie stabil.

---

## 3. Stack tehnologic recomandat

| Componentă | Recomandare inițială | Ce s-a folosit efectiv |
|---|---|---|
| Limbaj | Python 3.11+ | Python, ✓ neschimbat |
| LLM / tool-use | Claude (Anthropic API, tool use nativ) | **Mistral** (`mistral-large-latest`), via `langchain-mistralai` — echipa avea acces la o cheie gratuită Mistral (planul „Experiment”, console.mistral.ai), nu la una Anthropic |
| Validare date | Pydantic v2 | Pydantic v2, ✓ neschimbat |
| Vector store (Faza 5) | ChromaDB (local, zero infra) | ChromaDB, ✓ neschimbat, cu fallback BM25 |
| UI | Streamlit | Streamlit, ✓ neschimbat |
| Teste | pytest | pytest, ✓ neschimbat |
| Orchestrare buclă ReAct | implementare proprie (simplă, ~100 linii) inițial | Buclă proprie în continuare (`src/agent/react_loop.py`) — `langchain-core`/`langchain-mistralai` sunt folosite doar ca binding către modelul Mistral (mesaje + tool calling), nu ca framework de orchestrare tip LangGraph |

Recomandarea de a nu introduce un framework de orchestrare (LangGraph etc.) de la început a rămas valabilă: bucla ReAct e în continuare scrisă manual, ceea ce o face mai ușor de depanat și de explicat într-o prezentare. `langchain-mistralai` a fost necesar doar pentru că e calea documentată de a vorbi cu API-ul Mistral cu tool calling din Python — nu aduce cu el o buclă de agent.

---

## 4. Prioritizare — ce faci primul dacă ai timp limitat

Dacă vrei un singur demo end-to-end cât mai repede, calea critică minimă este:

1. Faza 1 (date mock) — jumătate de zi
2. Faza 2 (tool-uri) — jumătate de zi
3. Faza 3 (orchestrator ReAct, CLI-only) — 1–2 zile
4. Faza 4 varianta CLI — câteva ore

Cu asta ai deja un MVP demonstrabil. RAG (Faza 5), UI web (Faza 4 varianta Streamlit) și KPI-uri (Faza 6) sunt îmbunătățiri ulterioare, nu blocante pentru primul demo.

---

## 5. Acțiune imediată recomandată (rezolvată)

~~Înainte de orice altceva: rezolvă inconsistența dintre `diagrame_imagini/` (folder nou, netracked) și path-urile din README...~~
Rezolvat: diagramele sunt acum în `docs/diagrams/`, iar toate documentele de planificare (acest fișier inclus) sunt în `docs/`, cu doar `README.md` rămas la rădăcina repo-ului.

## 6. Ce mai rămâne, pentru o prezentare solidă

Cu Fazele 1–6 gata, ce contează acum pentru o prezentare bună:

- [ ] **Rulați efectiv agentul** cu o cheie Mistral validă pe toate cele 5 scenarii (`python evaluate.py`) și verificați manual `EVAL_RESULTS.md` — până acum a fost testat doar cu un model simulat în teste, nu cu Mistral real.
- [ ] Dacă un scenariu iese greșit, ajustați `src/agent/system_prompt.py` (nu tool-urile sau datele, care sunt deja validate) și rerulați.
- [ ] Pregătiți 1–2 rulări demonstrate live (`run_cli.py` sau Streamlit) pentru prezentare, plus `EVAL_RESULTS.md` ca "dovadă" pentru audiență.
- [ ] Opțional: un slide/paragraf care explică explicit alegerea Mistral în locul Claude — e o decizie justificată (acces la cheie gratuită), nu un compromis de calitate.
