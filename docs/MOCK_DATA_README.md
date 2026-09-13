# Rootly — Mock Data

## Ce sunt aceste date?

Datele mock simulează un mediu real de producție e-commerce cu 10 microservicii interconectate. Ele alimentează cele 3 tool-uri ale agentului (`cmdb_lookup`, `log_search`, `similar_incidents_search`) și conțin 5 scenarii de incident pre-construite, fiecare cu un drum de investigare complet pe care agentul ReAct îl poate urma.

Scenariile nu sunt inventate — sunt modelate pe **taxonomia StackGen State of Reliability 2026**, un studiu pe 178,000+ incidente reale de la 360+ companii.


---


## Structura fișierelor

```
data/
├── alerts.json        5 alerte (câte una per scenariu)
├── cmdb.json          10 componente IT cu dependențe
├── logs.json          124 log entries (22-24 per scenariu + 10 zgomot)
└── incidents.json     10 incidente istorice (pentru RAG)

src/models/
└── schemas.py         Scheme Pydantic pentru toate entitățile
```


---


## Topologia CMDB — 10 componente

Sistemul simulat este un e-commerce cu microservicii. Dependențele permit investigarea în cascadă pe mai multe nivele:

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
        └── auth-service
              ├── redis-cache
              └── user-db
```

Fiecare componentă are: `id`, `name`, `type`, `owner_team`, `depends_on`, `depended_by`, `environment`, `criticality`, `last_deploy`, `config_version`.

`auth-service` apare ca dependență pentru 3 servicii — este un **single point of failure** intenționat, exploatat de scenariile 3 și 5.


---


## Cele 5 scenarii de incident

Fiecare scenariu mapează pe un root-cause și un failure mode real din taxonomia StackGen.


### Scenariu 1 — DB Connection Pool Exhaustion

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-001` pe `checkout-api`, error_rate_high, severity high |
| Root-cause | `payments-db` rămâne fără conexiuni (pool 20/20) |
| Taxonomie | RC-04 Capacity Exhaustion × FM-13 Resource Exhaustion |
| Drum de investigare | checkout-api → CMDB: depinde de payments-db → loguri: "Max connections reached" + "Connection timeout to payments-db" |
| Incident istoric match | INC-2025-114 (aproape identic, rezolvat cu mărire pool) |

**Ce testează:** traversarea dependențelor CMDB + corelarea logurilor între 2 servicii.


### Scenariu 2 — Deploy cu Memory Leak

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-002` pe `user-service`, latency_high, severity medium |
| Root-cause | Deployment v2.5.0 a introdus un memory leak → OOMKilled |
| Taxonomie | RC-01 Code Defect × FM-09 Deploy-Induced Regression |
| Drum de investigare | user-service → loguri: deploy event la 14:25, memorie crescând 72%→94%, OOMKilled → CMDB: depinde de user-db (care e sănătos) → concluzie: problema e în user-service |
| Incident istoric match | INC-2025-203 (notification-service cu memory leak similar) |

**Ce testează:** agentul trebuie să identifice root-cause-ul în serviciul alertat, nu într-o dependență. Dacă merge pe pistă falsă spre user-db, datele arată că DB-ul răspunde normal (3ms).


### Scenariu 3 — Cascading Failure (Redis → Auth → Tot)

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-003` pe `api-gateway`, error_rate_high, severity critical |
| Root-cause | `redis-cache` crash OOM → `auth-service` cade → cascadă pe 3 nivele |
| Taxonomie | RC-06 Network/DNS × FM-01 Cross-Org Cascade |
| Drum de investigare | api-gateway → loguri: "502 from auth-service" → CMDB auth-service: depinde de redis-cache → loguri redis-cache: "OOM killer invoked" → loguri auth-service: "Redis connection refused" |
| Incident istoric match | INC-2025-156 (auth outage tot din cauza redis, rezolvat cu mărire memorie + fallback DB) |

**Ce testează:** investigare adâncă pe 3 hop-uri prin CMDB. Cel mai complex scenariu — agentul trebuie să ajungă de la api-gateway la redis-cache prin auth-service.


### Scenariu 4 — Config Change (Timeout Greșit)

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-004` pe `order-service`, error_rate_high, severity high |
| Root-cause | Config change a setat `payments_db_timeout_ms` de la 5000 la 50 (prea mic) |
| Taxonomie | RC-02 Config Change × FM-10 Config-Induced Failure |
| Drum de investigare | order-service → loguri: "Config update applied: timeout changed from 5000 to 50" + "Query timeout after 50ms" → loguri payments-db: "Database healthy, avg query time 65ms" → concluzie: DB e ok, timeout-ul e prea mic |
| Incident istoric match | INC-2025-278 (checkout-api cu timeout scăzut la 500ms, aceeași cauză) |

**Ce testează:** agentul trebuie să detecteze un config_change din loguri, nu un defect de dependență. DB-ul e sănătos dar răspunde în 65-85ms — mai mult decât timeout-ul de 50ms.


### Scenariu 5 — TLS Certificate Expired

| Câmp | Valoare |
|---|---|
| Alertă | `ALRT-005` pe `web-frontend`, service_unavailable, severity critical |
| Root-cause | Certificatul TLS al `auth-service` a expirat → toate serviciile dependente cad simultan |
| Taxonomie | RC-07 Auth × FM-23 Hidden Internal Coupling |
| Drum de investigare | web-frontend → loguri: "All authenticated pages returning 502" → CMDB api-gateway: depinde de auth-service → loguri auth-service: "TLS certificate has expired" → loguri user-service + checkout-api: "SSL certificate has expired" |
| Incident istoric match | INC-2025-341 (aceeași cauză exactă, rezolvat cu cert-manager automat) |

**Ce testează:** multiple servicii afectate simultan (hidden coupling). Agentul trebuie să identifice un single point of failure (auth-service) din care emană toate erorile.


---


## Designul logurilor

Fiecare scenariu conține 22-24 log entries, plus 10 entries de zgomot (background noise) distribuite pe toate zilele.

Logurile sunt proiectate cu:

- **Dovezi clare** — mesaje de eroare care indică root-cause-ul (ex: "Max connections reached", "OOMKilled", "TLS certificate has expired")
- **Piste false** — servicii sănătoase care raportează normal (ex: "Database healthy, connections 4/20") pentru a testa dacă agentul nu trage concluzii greșite
- **Zgomot** — INFO-uri de rutină pe servicii neafectate (health checks, backup-uri, notificări) care ar trebui ignorate
- **Event types** — fiecare log are un `event_type` (deploy, config_change, error, metric, health_check, alert) pentru filtrare

Câmpul `trace_id` codifică scenariul: `tr-1aXX` = scenariu 1, `tr-2bXX` = scenariu 2, etc.


---


## Incidentele istorice (pentru RAG)

10 incidente, câte 2 per scenariu:

| ID | Potrivire cu | Tip match |
|---|---|---|
| INC-2025-114 | Scenariu 1 | Direct — checkout-api + payments-db pool exhaustion |
| INC-2025-089 | Scenariu 1 | Partial — order-service + payments-db, cauză similară |
| INC-2025-203 | Scenariu 2 | Direct — memory leak după deployment |
| INC-2025-067 | Scenariu 2 | Partial — deployment cu problemă de performanță |
| INC-2025-156 | Scenariu 3 | Direct — redis crash → auth-service outage |
| INC-2024-301 | Scenariu 3 | Partial — cascadă din dependență externă |
| INC-2025-278 | Scenariu 4 | Direct — timeout greșit din config change |
| INC-2024-445 | Scenariu 4 | Partial — payments-db cu probleme de timing |
| INC-2025-341 | Scenariu 5 | Direct — certificat TLS expirat pe auth-service |
| INC-2024-512 | Scenariu 5 | Partial — auth-service degradat din cauza IAM policy |

Structura asta permite testarea RAG: un similarity search bun ar trebui să returneze match-ul direct pe primul loc și match-ul parțial pe al doilea.


---


## Scheme Pydantic

`src/models/schemas.py` definește:

- `Alert` — alerta care declanșează investigarea
- `CMDBComponent` — componentă IT cu dependențe
- `LogEntry` — eveniment de log cu timestamp, level, message, trace_id, event_type
- `HistoricalIncident` — incident istoric pentru RAG
- `DiagnosisPackage` — output-ul pe care agentul îl produce la final
- Enum-uri: `AlertType`, `Severity`, `LogLevel`, `EventType`, `ComponentType`, `RootCauseCategory`, `FailureMode`

Câmpurile `root_cause_category` și `failure_mode` sunt prezente pe alerte și incidente dar **nu trebuie expuse agentului** — sunt metadate pentru evaluare (a compara ce a diagnosticat agentul vs. cauza reală).


---


## Surse

Scenariile sunt modelate pe date reale din:

- [The Root Causes Behind 178,000 SRE Incidents](https://stackgen.com/blog/sre-root-cause-taxonomy-online-services) — 16 root-cause categories, 7 themes
- [How Online Services Actually Break: Failure Mode Taxonomy](https://stackgen.com/blog/sre-failure-mode-taxonomy) — 30 failure modes, 8 families
- [StackGen State of Reliability 2026 — Full Report](https://stackgen.com/state-of-reliability-2026/report/read) — datasetul complet cu 178K+ incidente
- [SRE: Incident Types (Oleh Ilin)](https://medium.com/@olehilin/sre-incident-types-714ad6f16a5e) — clasificare complementară


---


## Validare

Toate datele au fost validate automat:

- JSON-urile parsează fără erori
- Toate serviciile din `logs.json` și `alerts.json` există în `cmdb.json`
- Toate entry-urile trec prin schemele Pydantic corespunzătoare
- Dependențele CMDB sunt bidirecționale și consistente
