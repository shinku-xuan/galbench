from __future__ import annotations
import argparse
import collections
import json
import sys
from pathlib import Path
from . import __version__
from .api import ModelConfig, DEFAULT_MAX_TOKENS
from .data import load_dataset, dataset_coverage
from .demo import mock_server
from .runner import load_config, load_judge_config, new_run_name, rescore, run

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "dev-v2.0"
SMOKE_DATA = Path(__file__).resolve().parents[1] / "spec" / "v2.0"

def print_summary(summary, output):
    if summary["mode"] == "mock_demo": print("MOCK DEMO: scripted fixtures, not real model scores.")
    for board, group in summary["boards"].items():
        value = group["gal_score"] if group["gal_score"] is not None else f"{group.get('display_score')} PROVISIONAL ({group.get('scored_samples')}/{group['samples']} scored; range {group.get('score_lower_bound')}–{group.get('score_upper_bound')})"
        print(f"{board}: {value}/{summary.get('score_scale', 100)}; {group['counts']}")
        if board == "story": print(f"  SummaryOnly: {group['summary_only_score']}/{summary.get('score_scale', 100)}")
    print(f"Status: {summary['status']}; official eligibility is reported separately.")
    print(f"Report: {(output / 'report.md').resolve()}")

def parser():
    p = argparse.ArgumentParser(description="galbench: Agent Harness evaluations, auditable grading and 100-point boards")
    p.add_argument("--version", action="version", version=__version__)
    commands = p.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web", help="Local browser interface for configuration, tests and results")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--open", action="store_true", help="Open the interface in your default browser")
    validate = commands.add_parser("validate", help="Validate v1 JSONL/assets without calling models")
    validate.add_argument("dataset", nargs="*", type=Path)
    demo = commands.add_parser("demo", help="Local HTTP target and judge fixtures, no external API key")
    demo.add_argument("--output", type=Path)
    demo.add_argument("--repeats", type=int, default=1)
    demo.add_argument("--without-judge", action="store_true")
    execute = commands.add_parser("run", help="Run an official Agent Harness; direct model APIs are not supported")
    execute.add_argument("--config", type=Path, required=True)
    execute.add_argument("--dataset", action="append", type=Path, help="Repeat for multiple JSONL files, or supply a directory")
    execute.add_argument("--output", type=Path)
    execute.add_argument("--board", "--track", action="append", choices=["text", "vision", "story"])
    execute.add_argument("--limit", type=int)
    execute.add_argument("--repeats", type=int)
    execute.add_argument("--concurrency", type=int)
    execute.add_argument("--resume", action="store_true")
    execute.add_argument("--no-judge", action="store_true")
    execute.add_argument("--judge-config", type=Path)
    execute.add_argument("--reviews", action="append", type=Path, default=[])
    score = commands.add_parser("rescore", help="Regrade saved outputs, no target generation")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--dataset", action="append", type=Path)
    score.add_argument("--output", type=Path)
    score.add_argument("--judge-config", type=Path)
    score.add_argument("--reviews", action="append", type=Path, default=[])
    score.add_argument("--offline", action="store_true", help="Reuse recorded judge observations only; never call a model")
    score.add_argument("--concurrency", type=int, default=4, help="Concurrent grading workers")
    return p

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "web":
            from .web import serve
            return serve(port=args.port, open_browser=args.open)
        if args.command == "validate":
            dataset = load_dataset(args.dataset or DEFAULT_DATA)
            print(json.dumps({"questions": len(dataset.questions), "sha256": dataset.sha256,
                "input_hash": dataset.input_hash, "boards": dict(collections.Counter(q.board for q in dataset.questions)),
                "target_calls_per_repeat": sum(len(q.stages) for q in dataset.questions),
                "coverage": dataset_coverage(dataset),
                "leaderboard_eligible_questions": sum(q.private["review"]["eligible_for_leaderboard"] for q in dataset.questions)}, indent=2))
            return 0
        if args.command == "demo":
            output = args.output or Path("runs") / ("demo-v2.0-" + new_run_name())
            dataset = load_dataset(SMOKE_DATA)
            with mock_server(dataset=dataset) as (url, _):
                model = ModelConfig(url, "galbench-mock-target", api_key_env=None, supports_vision=True, timeout_seconds=10)
                judge = None if args.without_judge else ModelConfig(url, "galbench-mock-judge", api_key_env=None, timeout_seconds=10, parameters={"max_tokens":DEFAULT_MAX_TOKENS})
                summary = run(dataset, model, output, judge_config=judge, repeats=args.repeats, mode="mock_demo")
        elif args.command == "run":
            runtime, model, judge = load_config(args.config)
            base = args.config.resolve().parent
            configured = runtime.get("datasets", [runtime.get("dataset", "../data/dev-v2.0")])
            if not isinstance(configured, list) or not all(isinstance(v, str) for v in configured): raise ValueError("datasets must be an array of paths")
            dataset = load_dataset(args.dataset or [base / name for name in configured])
            output = args.output or base / runtime.get("output_dir", "../runs/" + new_run_name())
            if args.no_judge and args.judge_config: raise ValueError("--no-judge conflicts with --judge-config")
            if args.judge_config: judge = load_judge_config(args.judge_config)
            if args.no_judge: judge = None
            summary = run(dataset, model, output, judge_config=judge,
                boards=tuple(dict.fromkeys(args.board or runtime.get("boards", ["text","vision","story"]))),
                repeats=args.repeats if args.repeats is not None else runtime.get("repeats", 1),
                concurrency=args.concurrency if args.concurrency is not None else runtime.get("concurrency", 3),
                limit=args.limit, resume=args.resume, review_files=args.reviews)
        else:
            output = args.output or args.run / ("rescored-" + new_run_name())
            judge = load_judge_config(args.judge_config) if args.judge_config else None
            summary = rescore(args.run, load_dataset(args.dataset or DEFAULT_DATA), output,
                              judge_config=judge, review_files=args.reviews, offline=args.offline, concurrency=args.concurrency)
        print_summary(summary, output)
        return 0 if summary["status"] == "complete" else 1
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(f"galbench: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; completed predictions remain on disk. Use --resume.", file=sys.stderr)
        return 130

if __name__ == "__main__":
    raise SystemExit(main())
