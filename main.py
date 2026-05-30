"""CLI entry point for the Cathay Pacific award ticket monitor."""

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from config import (
    CABIN_CODE,
    INBOUND_ROUTES,
    OUTBOUND_ROUTES,
    POLL_INTERVAL_MINUTES,
    SEARCH_MONTH,
    SEARCH_YEAR,
)

console = Console()

LOG_FILE = "cathay_monitor.log"

_CABIN_LABELS = {
    "C": "商务舱",
    "F": "头等舱",
    "Y": "经济舱",
}

_MONTH_NAMES = {
    1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10", 11: "11", 12: "12",
}


def _setup_logging() -> None:
    """Configure root logger: INFO to console, DEBUG to rotating log file."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    root.addHandler(console_handler)
    root.addHandler(file_handler)


def _build_banner() -> Panel:
    """Build the startup rich Panel banner."""
    outbound_str = "/".join(dst for _, dst in OUTBOUND_ROUTES)
    inbound_str = "/".join(src for src, _ in INBOUND_ROUTES)
    cabin_label = _CABIN_LABELS.get(CABIN_CODE, CABIN_CODE)

    lines = [
        "  国泰航空积分机票监控  v1.0              ",
        f"  搜索月份: {SEARCH_YEAR}年{SEARCH_MONTH}月{'  ' * (14 - len(str(SEARCH_MONTH)))}",
        f"  舱位: {cabin_label}{'  ' * (21 - len(cabin_label))}",
        f"  去程: HKG→{outbound_str}{'  ' * max(0, 19 - len(outbound_str))}",
        f"  回程: {inbound_str}→HKG{'  ' * max(0, 19 - len(inbound_str))}",
        f"  轮询间隔: {POLL_INTERVAL_MINUTES}分钟{'  ' * (18 - len(str(POLL_INTERVAL_MINUTES)))}",
    ]

    content = "\n".join(lines)
    return Panel(content, border_style="cyan", expand=False)


def _print_combos_table(combos: list[dict]) -> None:
    """Render a rich table of combos."""
    table = Table(
        title="可用积分机票组合",
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
    )
    table.add_column("去程航班", style="cyan", no_wrap=True)
    table.add_column("去程日期", style="green", no_wrap=True)
    table.add_column("回程航班", style="cyan", no_wrap=True)
    table.add_column("回程日期", style="green", no_wrap=True)
    table.add_column("合计里程", justify="right", style="yellow")
    table.add_column("停留天数", justify="right", style="white")

    if not combos:
        table.add_row("—", "—", "—", "—", "—", "—")
    else:
        for combo in combos:
            ob = combo["outbound"]
            ib = combo["inbound"]
            total_miles = ob.get("miles", 0) + ib.get("miles", 0)
            ob_label = f"{ob.get('origin', '')}→{ob.get('destination', '')} {ob.get('flight_number', '')}"
            ib_label = f"{ib.get('origin', '')}→{ib.get('destination', '')} {ib.get('flight_number', '')}"
            table.add_row(
                ob_label,
                ob.get("date", ""),
                ib_label,
                ib.get("date", ""),
                f"{total_miles:,}",
                str(combo.get("stay_days", "?")),
            )

    console.print(table)


# ── Command handlers ──────────────────────────────────────────────────────────


async def cmd_run() -> None:
    """Start continuous polling."""
    from monitor import AwardMonitor

    console.print(_build_banner())
    monitor = AwardMonitor()
    await monitor.run_forever()


async def cmd_scan() -> None:
    """Run one scan cycle, print results, then exit."""
    from monitor import AwardMonitor

    console.print("[bold cyan]Running single scan cycle…[/bold cyan]")
    monitor = AwardMonitor()
    combos = await monitor.poll_once()
    _print_combos_table(combos)
    console.print(f"\n[green]扫描完成，共发现 {len(combos)} 个组合。[/green]")


def cmd_list() -> None:
    """Read DB and print all stored combos."""
    import db

    db.init_db()
    combos = db.get_all_combos()
    console.print(f"[bold]数据库路径:[/bold] {config.DB_PATH}")
    _print_combos_table(combos)
    console.print(f"\n共 {len(combos)} 条记录。")


async def cmd_discover() -> None:
    """Run API discovery and print discovered details."""
    from scraper import CathayAwardScraper

    console.print("[bold cyan]Running API discovery…[/bold cyan]")
    scraper = CathayAwardScraper()
    result = await scraper.discover_api()
    console.print_json(data=result)


# ── Entrypoint ────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cathay-monitor",
        description="Cathay Pacific award ticket monitor",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Start continuous polling (default)")
    subparsers.add_parser("scan", help="Run one scan cycle and exit")
    subparsers.add_parser("list", help="List all found combos from DB")
    subparsers.add_parser("discover", help="Run API discovery and print results")

    return parser


def main() -> None:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()

    command = args.command or "run"

    if command == "run":
        asyncio.run(cmd_run())
    elif command == "scan":
        asyncio.run(cmd_scan())
    elif command == "list":
        cmd_list()
    elif command == "discover":
        asyncio.run(cmd_discover())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
