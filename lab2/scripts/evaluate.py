import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.search import SearchEngine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--eval", type=Path, default=Path("data/eval.json"))
    parser.add_argument("--ranking-mode", choices=["normal", "reverse"], default="normal")
    parser.add_argument("--min-mrr", type=float, default=0.85)
    parser.add_argument("--min-recall-at-3", type=float, default=0.90)
    args = parser.parse_args()

    engine = SearchEngine(args.artifact_dir, args.ranking_mode)
    cases = json.loads(args.eval.read_text())
    reciprocal_ranks = []
    recalls = []
    details = []
    for case in cases:
        ids = [item["id"] for item in engine.search(case["query"], 3)]
        ranks = [ids.index(doc_id) + 1 for doc_id in case["relevant"] if doc_id in ids]
        reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
        recalls.append(len(ranks) / len(case["relevant"]))
        details.append({"query": case["query"], "top3": ids, "relevant": case["relevant"]})

    mrr = sum(reciprocal_ranks) / len(cases)
    recall_at_3 = sum(recalls) / len(cases)
    report = {
        "ranking_mode": args.ranking_mode,
        "mrr": round(mrr, 4),
        "recall_at_3": round(recall_at_3, 4),
        "cases": details,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if mrr < args.min_mrr or recall_at_3 < args.min_recall_at_3:
        print("quality gate failed", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
