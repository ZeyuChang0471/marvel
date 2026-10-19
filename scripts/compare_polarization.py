"""A/B: does the provenance discipline actually change what the debate does?

The offline experiment in ``tests/test_debate_polarization.py`` measures the
**architecture** with scripted models — it cannot say whether DeepSeek restates an
unverified rumour. This script runs the same real debate twice on the same nine
analyst reports, with one difference:

* **baseline** — ``MARVEL_ABLATE_PROVENANCE_DISCIPLINE=1``: no provenance digest
  for the judge, no "keep the label" instruction for the debaters, no PM rule.
* **current** — the shipped prompts.

Both arms receive the *same* injected single-source claim (default: the audit's
sentinel), so a difference in repetition/label retention can only come from the
de-polarization work.

⚠️ **This is not a test and it is not free**: two debate chains ≈ 16–20 model
calls. It prints the plan first and refuses to run without ``--yes``.

Usage:
    python scripts/compare_polarization.py \
        --from-recording .marvel_state/recordings/debate-600487-2026-10-16.jsonl \
        --ticker 600487 --date 2026-10-16 --yes
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_CLAIM = "【独家爆料】据三名知情人士透露，公司已被证监会立案调查财务造假，虚增收入约120亿元"
#: The distinctive part used to spot the claim in an output (the tag at the front
#: may legitimately be paraphrased away).
DEFAULT_CORE = "已被证监会立案调查财务造假"

STAGES = (
    "bull", "bear", "research_manager", "trader",
    "aggressive", "conservative", "neutral", "portfolio_manager",
)


def _load_record_module():
    """Import the sibling script (it holds the recording/chain helpers)."""
    path = Path(__file__).resolve().parent / "record_debate.py"
    spec = importlib.util.spec_from_file_location("record_debate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inject_claim(reports: dict, claim: str, label: str) -> dict:
    """Append one single-source claim to the news report (both arms see it)."""
    injected = dict(reports)
    news = str(injected.get("news_report") or "").rstrip()
    injected["news_report"] = (
        news + f"\n\n### [新闻] {claim}（{label}）\n"
    )
    return injected


def stage_texts(records, classify) -> dict:
    """Last output per stage, counting both plain content and tool-call args."""
    texts: dict = {}
    for rec in records:
        stage = classify(rec.get("prompt", "") or "")
        if stage not in STAGES:
            continue
        text = (rec.get("response") or "") + "".join(
            str(call.get("args") or "") for call in (rec.get("tool_calls") or [])
        )
        if text.strip():
            texts[stage] = text
    return texts


def summarise(records, classify, label: str, core: str = DEFAULT_CORE) -> dict:
    """Per stage: did it repeat the claim, and did it keep any provenance label?"""
    texts = stage_texts(records, classify)
    return {
        stage: {
            "repeats_claim": core in text,
            "keeps_label": label in text,
            "chars": len(text),
        }
        for stage, text in texts.items()
    }


def _run_arm(name, config, rd, ticker, trade_date, reports, quality, out_dir,
             max_chars, ablate: bool):
    from marvel.harness.llm_recorder import JsonlLlmRecorder, iter_records

    records_path = out_dir / f"ab-{name}.jsonl"
    if records_path.exists():
        records_path.unlink()
    previous = os.environ.get("MARVEL_ABLATE_PROVENANCE_DISCIPLINE")
    if ablate:
        os.environ["MARVEL_ABLATE_PROVENANCE_DISCIPLINE"] = "1"
    else:
        os.environ.pop("MARVEL_ABLATE_PROVENANCE_DISCIPLINE", None)
    try:
        recorder = JsonlLlmRecorder(records_path, max_chars=max_chars)
        state = rd.run_debate_chain(
            config, ticker, trade_date, reports, quality, recorder=recorder,
        )
    finally:
        if previous is None:
            os.environ.pop("MARVEL_ABLATE_PROVENANCE_DISCIPLINE", None)
        else:
            os.environ["MARVEL_ABLATE_PROVENANCE_DISCIPLINE"] = previous
    return state, records_path, list(iter_records(records_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="去极化 prompt 的 A/B 对照（会计费）")
    parser.add_argument("--from-recording", required=True,
                        help="提供九份分析师报告的完整录制")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--claim", default=DEFAULT_CLAIM)
    parser.add_argument("--core", default=DEFAULT_CORE,
                        help="在输出里检索的假消息核心片段")
    parser.add_argument("--out-dir", default="",
                        help="两个臂的录制输出目录（默认 <状态目录>/recordings）")
    parser.add_argument("--max-chars", type=int, default=20000)
    parser.add_argument("--yes", action="store_true", help="确认这次真实运行会花钱")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    rd = _load_record_module()
    from marvel.agents.utils.evidence import UNVERIFIED_LABEL
    from marvel.agents.utils.rating import parse_rating
    from marvel.dataflows.config import get_config
    from marvel.harness.llm_recorder import JsonlLlmRecorder, iter_records

    records = list(iter_records(args.from_recording))
    if not records:
        print(f"没读到记录：{args.from_recording}")
        return 1
    reports, quality = rd.reports_from_recording(records)
    missing = [f for f in rd._ANALYST_FIELDS.values() if f not in reports]
    if missing:
        print(f"⚠️ 录制缺 {len(missing)} 份报告：{missing}（对照组会带上「未包含」占位）")

    reports = inject_claim(reports, args.claim, UNVERIFIED_LABEL)

    config = dict(get_config())
    config["llm_provider"] = os.environ.get("LLM_PROVIDER") or rd.DEFAULT_PROVIDER
    config["deep_think_llm"] = os.environ.get("DEEP_THINK_LLM") or rd.DEFAULT_DEEP_MODEL
    config["quick_think_llm"] = os.environ.get("QUICK_THINK_LLM") or rd.DEFAULT_QUICK_MODEL

    out_dir = Path(args.out_dir) if args.out_dir else (
        Path(config["data_cache_dir"]).parent / "recordings"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("去极化 A/B 对照（同一批报告 + 同一条单一来源假消息，只差纪律提示词）：")
    print(f"  假消息      : {args.claim}")
    print(f"  报告        : {len(reports)}/9" + (f"（缺 {missing}）" if missing else "（齐全）"))
    print(f"  provider    : {config['llm_provider']} / {config['deep_think_llm']} + {config['quick_think_llm']}")
    print(f"  baseline    : MARVEL_ABLATE_PROVENANCE_DISCIPLINE=1（无摘要、无纪律）")
    print(f"  current     : 现行提示词")
    print(f"  输出目录    : {out_dir}")
    print("  调用规模    : 两臂各 8–10 次，共约 16–20 次")
    print("=" * 78)
    if not args.yes:
        print("确认要花钱后加 --yes 再跑。")
        return 2

    rd._apply_llm_budget_env()

    results = {}
    for name, ablate in (("baseline", True), ("current", False)):
        started = time.time()
        print(f"[{name}] 运行中…")
        state, record_path, arm_records = _run_arm(
            name, config, rd, args.ticker, args.date, reports, quality,
            out_dir, args.max_chars, ablate,
        )
        table = summarise(arm_records, rd.classify_stage, UNVERIFIED_LABEL, args.core)
        rating = parse_rating(state.get("final_trade_decision", ""))
        results[name] = {
            "rating": rating,
            "stages": table,
            "recording": str(record_path),
            "seconds": round(time.time() - started, 1),
        }
        print(f"[{name}] 完成 {results[name]['seconds']}s，评级 {rating}")

    print("=" * 78)
    header = f"{'stage':<20} {'baseline 复述/标注':<22} {'current 复述/标注':<22}"
    print(header)
    print("-" * len(header))
    for stage in STAGES:
        b = results["baseline"]["stages"].get(stage)
        c = results["current"]["stages"].get(stage)
        def fmt(row):
            if not row:
                return "—"
            repeat = "是" if row["repeats_claim"] else "否"
            label = "是" if row["keeps_label"] else "否"
            return f"{repeat}/{label}"
        print(f"{stage:<20} {fmt(b):<22} {fmt(c):<22}")
    print("-" * len(header))
    print(f"{'最终评级':<20} {results['baseline']['rating']:<22} {results['current']['rating']:<22}")
    print("=" * 78)
    print("读法：「复述」= 输出里出现假消息的核心片段；「标注」= 同一输出里保留了")
    print("「单一来源，未经交叉验证」。baseline 不保留标注是预期，current 保留才说明纪律生效。")

    summary_path = out_dir / f"ab-summary-{args.ticker}-{args.date}.json"
    summary_path.write_text(
        json.dumps({"claim": args.claim, "ticker": args.ticker, "date": args.date,
                    "arms": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"结构化摘要：{summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
