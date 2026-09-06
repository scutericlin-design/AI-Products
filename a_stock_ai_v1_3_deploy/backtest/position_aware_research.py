"""Run the fixed position-aware Hybrid Alpha research comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from backtest.annual_strategy_research import DEFAULT_CANDIDATES, run_annual_research


CANDIDATE_KEYS = {
    "hybrid_alpha_baseline",
    "hybrid_alpha_disciplined_hybrid",
    "hybrid_alpha_disciplined_trend",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare fixed position-aware Hybrid Alpha candidates.")
    parser.add_argument("--start-year", type=int, default=2018)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--universe-limit", type=int, default=120)
    parser.add_argument("--universe-profile", default="blended")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    if args.end_year < args.start_year:
        parser.error("end-year must not be earlier than start-year")
    candidates = tuple(item for item in DEFAULT_CANDIDATES if item.key in CANDIDATE_KEYS)
    if len(candidates) != len(CANDIDATE_KEYS):
        raise RuntimeError("position-aware candidates are not registered")
    result = run_annual_research(
        list(range(args.start_year, args.end_year + 1)),
        universe_profile=args.universe_profile,
        universe_limit=max(args.universe_limit, 20),
        candidates=candidates,
    )
    output_path = Path(args.output).resolve() if args.output else settings.storage_dir / "position_aware_research.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_path": str(output_path), "candidate_summary": result["candidate_summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
