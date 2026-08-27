# Rootly — Roadmap de implementare

> Document de planificare generat pe baza analizei README.md și a stării actuale a repo-ului (27 august 2026).
> Scop: transformarea documentației existente (faza de design) într-un MVP funcțional.

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

- [ ] Creează structura de directoare:
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
- [ ] Generează datele mock conform secțiunii 5.3:
  - 5–10 componente CMDB cu dependențe realiste
  - 50–100 log entries pe mai multe servicii
  - 2–3 scenarii de alertă cu cauze diferite (ex: DB connection pool exhaustion, deployment cu bug, resource exhaustion)
  - 5–10 incidente istorice pentru RAG
- [ ] Validează schemele cu Pydantic (corespondență exactă cu JSON-urile din README §5.2).

**Rezultat:** date mock complete și validate, gata de consumat de tool-uri.

---

### Faza 2 — Tool-urile (fără agent încă)
**Obiectiv:** funcții deterministe, testabile independent, înainte de a le conecta la un LLM.

- [ ] `cmdb_lookup(component: str) -> CMDBComponent` — lookup + traversare dependențe.
- [ ] `log_search(service: str, time_window: tuple, level: str | None) -> list[LogEntry]` — filtrare structurată.
- [ ] `similar_incidents_search(query: str) -> list[Incident]` — poate fi întâi un simplu keyword/tag match (placeholder), înlocuit ulterior cu ChromaDB în Faza 5.
- [ ] Teste unitare pentru fiecare tool, pe datele mock.
- [ ] Definește schema de function-calling (tool schema JSON) pentru fiecare, compatibilă cu API-ul de LLM ales.

**Rezultat:** 3 tool-uri complet funcționale și testate, independent de orice LLM.

---

### Faza 3 — Orchestratorul ReAct (miezul sistemului)
**Obiectiv:** bucla Thought → Action → Observation descrisă în README §6.

- [ ] Alege un LLM cu tool-use/function-calling (Claude sau alt model — vezi secțiunea de stack mai jos).
- [ ] Implementează bucla de bază: prompt inițial cu alerta → LLM decide tool-ul → execută tool → adaugă observația în context → repetă.
- [ ] Implementează criteriile de oprire din README §6 (componenta principală verificată, cel puțin o dependență investigată, dovezi din log găsite, pas maxim atins).
- [ ] Păstrează un **step history** explicit (listă de {thought, action, action_input, observation}) — necesar atât pentru trace-ul afișat în UI cât și pentru KPI-uri.
- [ ] Sintetizează output-ul final: pachetul de diagnostic (summary, componentă afectată, severitate, dependențe critice, dovezi log, ipoteză root-cause, nivel de încredere, recomandare de escaladare).
- [ ] Rulează pe cele 2–3 scenarii mock și verifică manual dacă diagnosticul e plauzibil.

**Rezultat:** poți rula `python run_scenario.py ALRT-001` din CLI și primești un pachet de diagnostic JSON + trace-ul de raționament în stdout. Acesta e primul demo real.

---

### Faza 4 — Interfață minimală (UI/CLI)
**Obiectiv:** README §1.3 cere un mod de a declanșa un scenariu și a inspecta trace-ul + diagnosticul.

- [ ] Varianta rapidă: CLI cu output formatat (Rich/tabelar) — suficient pentru demo intern.
- [ ] Varianta demo-ready: UI web simplu (Streamlit e cel mai rapid pentru acest tip de proiect) cu:
  - dropdown de selecție a scenariului de alertă;
  - panou cu trace-ul live (Thought/Action/Observation, pas cu pas);
  - panou cu pachetul final de diagnostic (formatat + export JSON/Markdown).
- [ ] Export al pachetului de diagnostic ca fișier `.json` și `.md`.

**Rezultat:** oricine poate rula proiectul și vedea end-to-end fluxul, nu doar tu din cod.

---

### Faza 5 — RAG pentru incidente istorice (opțional, dar valoros pt. diferențiere)
**Obiectiv:** înlocuiește placeholder-ul din Faza 2 cu retrieval semantic real, conform README §5.5.

- [ ] Integrează ChromaDB local.
- [ ] Embedding pentru descrierile + tags din `incidents.json` (folosind un model de embeddings ieftin).
- [ ] La rulare, construiește query din alertă + context deja adunat, caută top-k incidente similare.
- [ ] Injectează rezultatele ca și context suplimentar pentru ipoteza de root-cause.

**Rezultat:** agentul poate spune "acest incident seamănă cu INC-2025-114, rezolvat prin creșterea connection pool-ului" — un diferențiator puternic față de un simplu lookup CMDB+logs.

---

### Faza 6 — KPI-uri și evaluare
**Obiectiv:** README §7 — măsurarea Time-to-Diagnosis și Correctness Rate.

- [ ] Instrumentează orchestratorul să măsoare timpul de la intake până la pachetul final.
- [ ] Creează un checklist de evaluare manuală (componentă corectă / dependență corectă / root-cause plauzibil) pentru fiecare scenariu mock.
- [ ] Rulează toate scenariile, calculează rata de succes, documentează rezultatele într-un `EVAL_RESULTS.md`.
- [ ] (Opțional) Compară cu un timp estimat de investigare manuală, ca baseline.

**Rezultat:** dovadă cuantificabilă că agentul funcționează — utilă pentru orice prezentare/demo către stakeholderi.

---

### Faza 7 — Polish & pregătire pentru extindere
- [ ] README actualizat cu instrucțiuni reale de instalare/rulare (`pip install`, `python run.py`, etc.) — înlocuiește secțiunea "MVP Status" care spune încă "documentation and design phase".
- [ ] `CONTRIBUTING.md` minimal dacă lucrați în echipă.
- [ ] CI simplu (GitHub Actions) care rulează testele tool-urilor la fiecare push.
- [ ] Discuție despre pașii din README §9 care rămân pentru "viitor îndepărtat": arhitectură multi-agent, integrare cu sisteme reale (Datadog/ServiceNow/Splunk) — nu le începe înainte ca MVP-ul de mai sus să fie stabil.

---

## 3. Stack tehnologic recomandat

| Componentă | Recomandare | Alternativă |
|---|---|---|
| Limbaj | Python 3.11+ | — |
| LLM / tool-use | Claude (Anthropic API, tool use nativ) | OpenAI function calling |
| Validare date | Pydantic v2 | dataclasses |
| Vector store (Faza 5) | ChromaDB (local, zero infra) | FAISS |
| UI | Streamlit | simplu CLI + Rich, sau FastAPI + React dacă vreți ceva mai serios |
| Teste | pytest | — |
| Orchestrare buclă ReAct | implementare proprie (simplă, ~100 linii) inițial | LangGraph, dacă bucla devine complexă |

Recomandarea generală: **nu introduce LangChain/LangGraph de la început**. Bucla ReAct descrisă în README e suficient de simplă încât o implementare proprie e mai ușor de depanat și de explicat într-un context educațional/demo. Framework-uri de orchestrare pot fi adăugate mai târziu dacă apare nevoia de multi-agent (Faza 9 din README).

---

## 4. Prioritizare — ce faci primul dacă ai timp limitat

Dacă vrei un singur demo end-to-end cât mai repede, calea critică minimă este:

1. Faza 1 (date mock) — jumătate de zi
2. Faza 2 (tool-uri) — jumătate de zi
3. Faza 3 (orchestrator ReAct, CLI-only) — 1–2 zile
4. Faza 4 varianta CLI — câteva ore

Cu asta ai deja un MVP demonstrabil. RAG (Faza 5), UI web (Faza 4 varianta Streamlit) și KPI-uri (Faza 6) sunt îmbunătățiri ulterioare, nu blocante pentru primul demo.

---

## 5. Acțiune imediată recomandată

Înainte de orice altceva: rezolvă inconsistența dintre `diagrame_imagini/` (folder nou, netracked) și path-urile din README (`./architecture.png`, `./react-reasoning-loop.png`) — în starea actuală, imaginile din README sunt sparte pe GitHub. Alege una:
- mută imaginile înapoi în rădăcină și dă `git rm` la `diagrame_imagini/` vechi, sau
- actualizează README să pointeze spre `./diagrame_imagini/...` (sau spre `docs/diagrams/` conform structurii propuse în §10 din README).
