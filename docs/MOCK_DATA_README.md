# Rootly — Mock Data

## Ce sunt aceste date?

Datele mock simulează un mediu real de producție e-commerce cu 11 componente interconectate. Ele alimentează cele 3 tool-uri ale agentului (`cmdb_lookup`, `log_search`, `similar_incidents_search`) și conțin 10 scenarii de incident pre-construite, fiecare cu un drum de investigare complet pe care agentul ReAct îl poate urma.

Scenariile nu sunt inventate — sunt modelate pe **taxonomia StackGen State of Reliability 2026**, un studiu pe 178,000+ incidente reale de la 360+ companii.


---


## Structura fișierelor

```
data/
├── alerts.json        10 alerte (câte una per scenariu)
├── cmdb.json           11 componente IT cu dependențe
├── logs.json           201 log entries (13-24 per scenariu + 14 zgomot)
└── incidents.json      10 incidente istorice (pentru RAG)

src/models/
└── schemas.py         Scheme Pydantic pentru toate entitățile
```


---


## Topologia CMDB — 11 componente

```
web-frontend
  └── api-gateway
        ├── checkout-api
        │     ├── payments-db
        │     ├── auth-service
        │     └── order-service
        ├── user-service
        │     ├── user-db
        │     └── auth-service
        ├── order-service
        │     ├── payments-db
        │     └── notification-service
        ├── auth-service
        │     ├── redis-cache
        │     └── user-db
        └── cdn-provider   ← nou (scenariul 8)
```

Fiecare componentă are: `id`, `name`, `type`, `owner_team`, `depends_on`, `depended_by`, `environment`, `criticality`, `last_deploy`, `config_version`.

`auth-service` apare ca dependență pentru 3 servicii — este un **single point of failure** intenționat, exploatat de scenariile 3, 5 și 10.

`cdn-provider` (CI-011) e un serviciu extern (vendor CDN), adăugat sub `api-gateway`. Folosește `type: "microservice"` pentru că enum-ul `ComponentType` din `schemas.py` nu are încă o categorie dedicată third-party/extern — dacă vrei distincția reflectată corect, adaugă `ComponentType.THIRD_PARTY_SERVICE` în schemă; datele rămân valide oricum, nu e un blocaj.


---


## Cele 10 scenarii de incident

### Scenariile 1-5 (existente)

Neschimbate — vezi tabelele originale mai jos.

### Scenariile 6-10 (noi)

Fiecare mapează exact pe unul din cele 5 incidente istorice care erau deja în corpus ca "partial match" fără alertă asociată — nu a fost nevoie să extindem `incidents.json`.


#### Scenariu 6 — Batch Job Contention pe Connection Pool

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-006` pe `order-service`, resource_exhaustion, severity medium |
| Root-cause | Job-ul de reconciliere lunară blochează 17-18/20 conexiuni pe `payments-db`, sufocând cererile în timp real |
| Taxonomie | RC-04 Capacity Exhaustion × FM-13 Resource Exhaustion |
| Drum de investigare | order-service → CMDB: depinde de payments-db → loguri payments-db: "batch job BATCH-2026-08" ține 17-18 conexiuni |
| Incident istoric match | INC-2025-089 (era deja în corpus fără alertă) |

**Ce testează:** același perete final (payments-db) ca scenariul 1, dar o cauză complet diferită (batch vs. trafic de vârf) — agentul nu trebuie să pattern-matching-uiască pe "e mereu connection pool", trebuie să citească mecanismul specific din loguri.


#### Scenariu 7 — Query Neindexat, CPU Exhaustion după Deploy

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-007` pe `user-service`, resource_exhaustion, severity medium |
| Root-cause | Deploy v2.6.0 introduce un full table scan pe `/api/users/search`, CPU la 95-96% |
| Taxonomie | RC-01 Code Defect × FM-09 Deploy-Induced Regression |
| Drum de investigare | user-service → loguri: deploy la 09:40, degradare progresivă → CMDB: user-db sănătos (3-4ms) → concluzie: problema e în codul din user-service |
| Incident istoric match | INC-2025-067 |

**Ce testează:** aceeași componentă (`user-service`) și aceeași categorie de bază (deploy regression) ca scenariul 2, dar mecanism diferit (CPU/query vs. memory leak) — agentul trebuie să distingă cele două semnături, nu doar să recunoască "user-service + deploy = memory leak".


#### Scenariu 8 — Degradare CDN (Third-Party)

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-008` pe `api-gateway`, latency_high, severity high |
| Root-cause | `cdn-provider` are o anomalie de rutare în EU-West, 40% din request-urile de assets statice eșuează |
| Taxonomie | RC-08 Third-Party × FM-23 Hidden Internal Coupling |
| Drum de investigare | api-gateway → health check-ul trece (nu testează CDN) → loguri: 504 doar pe rute statice, API-ul de backend răspunde normal → CMDB: depinde de cdn-provider → loguri cdn-provider: anomalie de rutare |
| Incident istoric match | INC-2024-301 |

**Ce testează:** capcana health-check-ului care "trece" în timp ce sistemul real e degradat — health check-ul lui api-gateway nu exercită căile dependente de CDN, deci agentul trebuie să citească dincolo de statusul de sănătate raportat.


#### Scenariu 9 — Maintenance Failover pe payments-db (lanț pe 3 hop-uri)

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-009` pe `web-frontend`, error_rate_high, severity high |
| Root-cause | Failover-ul planificat pe `payments-db` durează 12 min (așteptat: 30s) din cauza replication lag |
| Taxonomie | RC-04 Capacity Exhaustion × FM-13 Resource Exhaustion |
| Drum de investigare | web-frontend → CMDB: depinde de api-gateway → loguri api-gateway: 502 de la checkout-api → CMDB: checkout-api depinde de payments-db → loguri payments-db: failover cu replication lag |
| Incident istoric match | INC-2024-445 |

**Ce testează:** cel mai adânc lanț din tot corpusul — alerta e la 3 hop-uri distanță de origine (web-frontend → api-gateway → checkout-api → payments-db). Există și o cale alternativă validă prin `order-service` (care de asemenea depinde de payments-db); ambele căi sunt acceptabile, dar agentul trebuie să investigheze *o cale completă*, nu doar componenta finală. Acesta e scenariul construit special pentru guardrail-ul tranzitiv (vezi mai jos).


#### Scenariu 10 — IAM/OAuth Scope Greșit pe auth-service

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-010` pe `user-service`, error_rate_high, severity high |
| Root-cause | O actualizare de politică IAM elimină scope-ul `user:read` de pe token-urile service-to-service |
| Taxonomie | RC-07 Auth × FM-10 Config-Induced Failure |
| Drum de investigare | user-service → loguri: 403 Forbidden, "missing scope: user:read" → CMDB: depinde de auth-service → loguri auth-service: policy update + scope rejection → loguri auth-service: certificatul TLS e valid (elimină ipoteza scenariului 5) → loguri redis-cache: sănătos (elimină ipoteza scenariului 3) |
| Incident istoric match | INC-2024-512 |

**Ce testează:** `auth-service` mai apare ca origine și în scenariile 3 (Redis) și 5 (TLS expirat) — acest scenariu verifică explicit, prin loguri de rule-out, că agentul nu presupune automat "auth-service pică = deja am mai văzut asta" și diagnostichează corect un al treilea mecanism diferit (permisiuni, nu disponibilitate).


---


## Guardrail-ul tranzitiv (gap identificat și corectat)

Guardrail-ul original (`_unexamined_blamed_dependencies` din `graph_nodes.py`) verifică doar dacă *componenta finală trimisă* își blamează în loguri o dependență neinvestigată. Asta lasă o portiță: un agent poate ghici direct o componentă-frunză aflată la 3+ hop-uri distanță (ex. `payments-db` pornind de la `web-frontend`) și, dacă acea frunză n-are dependențe proprii, guardrail-ul trece fără să fi verificat vreodată nodurile intermediare.

Scenariul 9 e construit special ca să expună asta. Fix-ul propus (nu inclus în acest set de date, doar în cod): verifică dacă `affected_component` e accesibil de la `alert.service` prin graful de `depends_on`, folosind *doar* noduri deja investigate (plus capetele). Dacă nu există nicio cale complet investigată, submit-ul e respins — indiferent ce spun propriile loguri ale componentei finale.

Testat cu succes (vezi conversația / `validate_data.py`) că:
- toate cele 5 scenarii vechi trec neschimbate;
- un shortcut pe scenariul 9 (sare peste `api-gateway`) e respins corect;
- ambele căi valide prin scenariul 9 (via `checkout-api` sau via `order-service`) sunt acceptate.


---


## Designul logurilor

Fiecare scenariu conține 12-24 log entries, plus 14 entries de zgomot (background noise, incl. 4 noi pentru perioada scenariilor 6-10).

Logurile sunt proiectate cu:

- **Dovezi clare** — mesaje de eroare care indică root-cause-ul
- **Piste false** — servicii sănătoase care raportează normal, inclusiv rule-out-uri explicite pentru capcanele din scenariile anterioare (ex. scenariul 10 verifică explicit TLS și Redis, ca să nu fie confundat cu scenariile 5 și 3)
- **Zgomot** — INFO-uri de rutină pe servicii neafectate
- **Event types** — fiecare log are un `event_type` (deploy, config_change, error, metric, health_check, alert)

Câmpul `trace_id` codifică scenariul: `tr-1a` = scenariu 1 ... `tr-10j` = scenariu 10, `tr-bg` = zgomot de fundal.


---


## Incidentele istorice (pentru RAG)

Tot 10, neschimbate — dar acum toate au un match direct printr-o alertă, nu doar 5:

| ID | Potrivire cu | Tip match |
|---|---|---|
| INC-2025-114 | Scenariu 1 | Direct |
| INC-2025-089 | Scenariu 6 | Direct (era partial pentru scenariul 1) |
| INC-2025-203 | Scenariu 2 | Direct |
| INC-2025-067 | Scenariu 7 | Direct (era partial pentru scenariul 2) |
| INC-2025-156 | Scenariu 3 | Direct |
| INC-2024-301 | Scenariu 8 | Direct (era partial pentru scenariul 3) |
| INC-2025-278 | Scenariu 4 | Direct |
| INC-2024-445 | Scenariu 9 | Direct (era partial pentru scenariul 4) |
| INC-2025-341 | Scenariu 5 | Direct |
| INC-2024-512 | Scenariu 10 | Direct (era partial pentru scenariul 5) |

Fiecare incident e acum "direct" pentru exact un scenariu — testul RAG (Chroma vs. BM25) devine mai relevant pentru că fiecare query trebuie să discrimineze între 10 documente, nu 2.


---


## Scheme Pydantic

Neschimbate — vezi `src/models/schemas.py`.


---


## Surse

Neschimbate.


---


## Validare

Toate datele au fost validate automat (script separat, `validate_data.py`):

- JSON-urile parsează fără erori
- Toate serviciile din `logs.json` și `alerts.json` există în `cmdb.json`
- Toate enum-urile (level, event_type, alert_type, severity, component type, criticality, root_cause_category, failure_mode) sunt valori valide
- Dependențele CMDB sunt bidirecționale și consistente
- ID-urile și trace_id-urile sunt unice
