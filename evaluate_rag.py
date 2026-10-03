"""
Evaluare RAGAS a componentei RAG (similar_incidents_search).

    python evaluate_rag.py                    # chromadb (default)
    python evaluate_rag.py --backend bm25     # BM25
    python evaluate_rag.py --backend both     # side-by-side

Scoreaza Context Precision si Context Recall pe cele 10 scenarii. Backend-urile sunt
instantiate direct (_ChromaBackend / _BM25Backend) ca sa ocoleasca lru_cache-ul din
src/tools/incident_search._backend, care altfel ar servi un singur backend per proces.
Necesita MISTRAL_API_KEY: judecatorul e acelasi model ca al agentului.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from ragas import EvaluationDataset, SingleTurnSample, evaluate  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall  # noqa: E402
from ragas.run_config import RunConfig  # noqa: E402

from src.agent.graph_nodes import build_llm  # noqa: E402
from src.data_loader import load_incidents  # noqa: E402
from src.tools.incident_search import _BM25Backend, _ChromaBackend  # noqa: E402

OUTPUT = Path(__file__).resolve().parent / "RAG_EVAL_RESULTS.md"

SCENARIOS = {
    "ALRT-001": ("checkout-api connection timeouts, payments-db max connections reached, pool exhaustion", "INC-2025-114"),
    "ALRT-002": ("memory leak after deployment, OOMKilled pod restarts, latency degradation", "INC-2025-203"),
    "ALRT-003": ("redis-cache OOM crash, auth-service outage cascading to api-gateway 502", "INC-2025-156"),
    "ALRT-004": ("config change reduced database query timeout, queries timing out at 50ms", "INC-2025-278"),
    "ALRT-005": ("auth-service TLS certificate expired, SSL handshake failures across all services", "INC-2025-341"),
    "ALRT-006": ("batch job contention, payments-db connection pool held by reconciliation, real-time queries starved", "INC-2025-089"),
    "ALRT-007": ("unindexed query full table scan after deployment, CPU exhaustion 95%", "INC-2025-067"),
    "ALRT-008": ("CDN provider routing anomaly, static asset 504 timeouts, health check passing", "INC-2024-301"),
    "ALRT-009": ("payments-db maintenance failover delayed by replication lag, write queries failing", "INC-2024-445"),
    "ALRT-010": ("IAM policy update removed OAuth scope user:read, service-to-service 403 Forbidden", "INC-2024-512"),
}


def build_samples(backend) -> list[SingleTurnSample]:
    incidents_by_id = {i.id: i for i in load_incidents()}
    samples = []
    for alert_id, (query, ref_id) in SCENARIOS.items():
        results = backend.search(query, top_k=3)
        retrieved_contexts = [
            r["description"] + ". Root cause: " + r["root_cause"] for r in results
        ]
        ref = incidents_by_id[ref_id]
        reference = ref.description + ". Root cause: " + ref.root_cause
        samples.append(
            SingleTurnSample(
                user_input=query,
                response=retrieved_contexts[0] if retrieved_contexts else "",
                retrieved_contexts=retrieved_contexts,
                reference=reference,
            )
        )
    return samples


def run_eval(backend, evaluator_llm, run_config):
    samples = build_samples(backend)
    dataset = EvaluationDataset(samples=samples)
    metrics = [
        LLMContextPrecisionWithReference(llm=evaluator_llm),
        LLMContextRecall(llm=evaluator_llm),
    ]
    return evaluate(dataset=dataset, metrics=metrics, run_config=run_config)


def format_result(result, backend_name: str) -> list[str]:
    df = result.to_pandas()
    lines = [
        f"### Backend: {backend_name}",
        "",
        "| Scenario | Context Precision | Context Recall |",
        "|---|---|---|",
    ]
    for i, alert_id in enumerate(SCENARIOS):
        row = df.iloc[i]
        cp = row.get("llm_context_precision_with_reference", float("nan"))
        cr = row.get("context_recall", float("nan"))
        lines.append(f"| {alert_id} | {cp:.2f} | {cr:.2f} |")
    lines += [""]
    return lines


def main():
    parser = argparse.ArgumentParser(description="RAGAS evaluation of similar_incidents_search")
    parser.add_argument("--backend", choices=["chromadb", "bm25", "both"], default="chromadb")
    args = parser.parse_args()

    evaluator_llm = LangchainLLMWrapper(build_llm("codestral-latest"))
    # Mistral free tier ~1 req/s, latenta reala 2-5s/apel.
    # RAGAS default 16 workers -> furtuna de 429. Serializam.
    run_config = RunConfig(max_workers=1)

    report = [
        "# Rootly — RAG Evaluation (RAGAS)",
        "",
        "**Judge LLM:** codestral-latest (Mistral free tier). Codestral is a code model",
        "used as an NLI judge here — expect noisier scores than a dedicated reasoning model.",
        "",
    ]

    backends = []
    if args.backend in ("chromadb", "both"):
        backends.append(("chromadb", _ChromaBackend()))
    if args.backend in ("bm25", "both"):
        backends.append(("bm25", _BM25Backend()))

    for name, backend in backends:
        print(f"Running RAGAS on {name}...")
        result = run_eval(backend, evaluator_llm, run_config)
        report += format_result(result, name)

    OUTPUT.write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
