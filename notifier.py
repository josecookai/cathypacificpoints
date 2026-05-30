"""Telegram notification sender for Cathay Pacific award ticket monitor."""

import logging
from datetime import date

from telegram import Bot
from telegram.error import TelegramError

from config import CX_BOOK_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _weekday_zh(dt_str: str) -> str:
    """Return Chinese weekday string for a YYYY-MM-DD date string."""
    try:
        d = date.fromisoformat(dt_str)
        return _WEEKDAY_ZH[d.weekday()]
    except (ValueError, IndexError):
        return ""


def _format_combo(combo: dict) -> str:
    """Build the formatted notification message for one outbound+inbound combo."""
    ob = combo["outbound"]
    ib = combo["inbound"]

    ob_origin = ob.get("origin", "")
    ob_dest = ob.get("destination", "")
    ib_origin = ib.get("origin", "")
    ib_dest = ib.get("destination", "")

    ob_flight = ob.get("flight_number", "")
    ob_date = ob.get("date", "")
    ob_weekday = _weekday_zh(ob_date)
    ob_depart = ob.get("depart_time", "")
    ob_arrive = ob.get("arrive_time", "")
    ob_arrive_offset = ob.get("arrive_day_offset", 0)
    ob_miles = ob.get("miles", 0)
    ob_tax = ob.get("tax", 0)
    ob_tax_currency = ob.get("tax_currency", "HKD")
    ob_seats = ob.get("seats_available", 0)

    ib_flight = ib.get("flight_number", "")
    ib_date = ib.get("date", "")
    ib_weekday = _weekday_zh(ib_date)
    ib_depart = ib.get("depart_time", "")
    ib_arrive = ib.get("arrive_time", "")
    ib_arrive_offset = ib.get("arrive_day_offset", 0)
    ib_miles = ib.get("miles", 0)
    ib_tax = ib.get("tax", 0)
    ib_tax_currency = ib.get("tax_currency", "HKD")
    ib_seats = ib.get("seats_available", 0)

    total_miles = ob_miles + ib_miles
    total_tax = ob_tax + ib_tax
    stay_days = combo.get("stay_days", "")

    ob_arrive_str = (
        f"{ob_arrive}+{ob_arrive_offset}" if ob_arrive_offset else ob_arrive
    )
    ib_arrive_str = (
        f"{ib_arrive}+{ib_arrive_offset}" if ib_arrive_offset else ib_arrive
    )

    lines = [
        f"✈️ 国泰积分商务舱 | {ob_origin}→{ob_dest} + {ib_origin}→{ib_dest}",
        "",
        f"去程: {ob_flight}  {ob_date} ({ob_weekday})",
        f"  出发: {ob_origin} {ob_depart} → {ob_dest} {ob_arrive_str}",
        f"  里程: {ob_miles:,} miles | 税费: {ob_tax_currency} {ob_tax:,}",
        f"  余座: {ob_seats}",
        "",
        f"回程: {ib_flight}  {ib_date} ({ib_weekday})",
        f"  出发: {ib_origin} {ib_depart} → {ib_dest} {ib_arrive_str}",
        f"  里程: {ib_miles:,} miles | 税费: {ib_tax_currency} {ib_tax:,}",
        f"  余座: {ib_seats}",
        "",
        f"合计里程: {total_miles:,} | 合计税费: {ob_tax_currency} {total_tax:,}",
        f"停留: {stay_days} 天",
        "",
        f"🔗 立即预订: {CX_BOOK_URL}/",
    ]
    return "\n".join(lines)


class TelegramNotifier:
    """Send Telegram notifications for award ticket findings."""

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._bot: Bot | None = Bot(token=token) if token else None

    def is_configured(self) -> bool:
        """Return True if bot token and chat_id are both set."""
        return bool(self._token and self._chat_id)

    async def send_combo(self, combo: dict) -> None:
        """Send a formatted message about one outbound+inbound combo."""
        message = _format_combo(combo)

        if not self.is_configured():
            print(message)
            return

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
            )
            logger.info(
                "Sent combo notification: %s → %s + %s → %s",
                combo["outbound"].get("origin"),
                combo["outbound"].get("destination"),
                combo["inbound"].get("origin"),
                combo["inbound"].get("destination"),
            )
        except TelegramError as exc:
            logger.error("Failed to send Telegram combo message: %s", exc)

    async def send_summary(self, combos: list[dict]) -> None:
        """Send a summary of all combos found in this poll cycle."""
        if not combos:
            return

        count = len(combos)
        lines = [
            f"📊 本轮扫描完成 — 发现 {count} 个可用组合",
            "",
        ]
        for i, combo in enumerate(combos, start=1):
            ob = combo["outbound"]
            ib = combo["inbound"]
            total_miles = ob.get("miles", 0) + ib.get("miles", 0)
            stay_days = combo.get("stay_days", "?")
            lines.append(
                f"{i}. {ob.get('origin')}→{ob.get('destination')} "
                f"{ob.get('date')}  +  "
                f"{ib.get('origin')}→{ib.get('destination')} "
                f"{ib.get('date')}  |  "
                f"{total_miles:,} miles  |  {stay_days} 天"
            )

        message = "\n".join(lines)

        if not self.is_configured():
            print(message)
            return

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
            )
            logger.info("Sent summary notification for %d combos.", count)
        except TelegramError as exc:
            logger.error("Failed to send Telegram summary message: %s", exc)


def build_notifier() -> TelegramNotifier:
    """Construct a TelegramNotifier from environment config."""
    return TelegramNotifier(
        token=TELEGRAM_BOT_TOKEN,
        chat_id=TELEGRAM_CHAT_ID,
    )
