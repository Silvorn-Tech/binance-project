#!/usr/bin/env python3
import argparse

from hermes.reporting.post_mortem_audit import PostMortemAuditor
from hermes.reporting.trade_reporter import TradeReporter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post-mortem audit summary for recent trades."
    )
    parser.add_argument("--bot-id", required=True, help="Bot ID to audit.")
    parser.add_argument("--limit", type=int, default=30, help="Number of SELL trades.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write latest summary to reports/post_mortem.",
    )
    args = parser.parse_args()

    reporter = TradeReporter()
    auditor = PostMortemAuditor(reporter)
    summary = auditor.generate_summary(bot_id=args.bot_id, limit=args.limit)
    print(summary)
    if args.write:
        result = auditor.write_latest_summary(bot_id=args.bot_id, limit=args.limit)
        print("")
        print(f"Wrote: {result.path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
