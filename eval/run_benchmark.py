#!/usr/bin/env python3
"""CLI entry point for the MCG swarm benchmark.

    python eval/run_benchmark.py --adapter oracle      # reference (should be ~100%)
    python eval/run_benchmark.py --adapter noisy       # demo: scorer discriminates
    python eval/run_benchmark.py --adapter swarm       # your system (wire it first)
    python eval/run_benchmark.py --build               # (re)generate workbooks first
    python eval/run_benchmark.py --adapter oracle --workbooks sales_regional.xlsx

Outputs (under eval/results/):
    scorecard_<adapter>.json   machine-readable metrics + per-sample results
    report_<adapter>.html      self-contained dashboard
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.adapters.swarm_adapter import get_adapter  # noqa: E402
from eval.harness.report import render_console, render_html  # noqa: E402
from eval.harness.runner import (  # noqa: E402
    DEFAULT_LABELS, DEFAULT_WORKBOOKS, load_labels, run_benchmark, to_payload,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="oracle",
                    help="oracle | noisy | swarm (default: oracle)")
    ap.add_argument("--build", action="store_true",
                    help="regenerate workbooks + labels before running")
    ap.add_argument("--labels-dir", default=str(DEFAULT_LABELS))
    ap.add_argument("--workbooks-dir", default=str(DEFAULT_WORKBOOKS))
    ap.add_argument("--workbooks", nargs="*", default=None,
                    help="filter to specific workbook filenames")
    ap.add_argument("--out-dir", default=str(RESULTS))
    args = ap.parse_args()

    if args.build:
        from eval.generator.build import main as build_main
        build_main()

    labels = load_labels(Path(args.labels_dir))
    if args.workbooks:
        wanted = set(args.workbooks)
        labels = [l for l in labels if l.workbook in wanted]
    if not labels:
        print("No labels found. Run with --build first.", file=sys.stderr)
        return 1

    adapter = get_adapter(args.adapter)
    try:
        result = run_benchmark(adapter, labels, Path(args.workbooks_dir))
    except NotImplementedError as e:
        print(f"\nAdapter '{adapter.name}' is not wired up yet: {e}\n"
              f"Implement eval/adapters/swarm_adapter.py, or run "
              f"`--adapter oracle` / `--adapter noisy`.", file=sys.stderr)
        return 2
    payload = to_payload(result)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"scorecard_{adapter.name}.json").write_text(json.dumps(payload, indent=2))
    (out / f"report_{adapter.name}.html").write_text(render_html(payload))

    print(render_console(payload))
    print(f"\nWrote {out / ('scorecard_' + adapter.name + '.json')}")
    print(f"Wrote {out / ('report_' + adapter.name + '.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
