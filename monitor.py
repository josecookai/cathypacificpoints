"""Main polling orchestrator for the Cathay Pacific award ticket monitor."""

import asyncio
import calendar
import logging
import random
from datetime import date, datetime, timedelta, timezone

import config
import db
from notifier import build_notifier
from scraper import search_one

logger = logging.getLogger(__name__)


def _dates_for_month(year: int, month: int) -> list[date]:
    """Return every calendar date in the given year/month."""
    _, last_day = calendar.monthrange(year, month)
    return [date(year, month, day) for day in range(1, last_day + 1)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_combination(outbound: dict, inbound: dict) -> bool:
    """Return True if the outbound/inbound pair forms a valid round-trip.

    Rules:
    - Outbound destination must be in the open-jaw set for the inbound origin.
    - Inbound departure must be MIN_STAY..MAX_STAY days after outbound departure.
    """
    ob_dest = outbound["destination"]
    ib_orig = inbound["origin"]

    valid_origins = config.VALID_OPENJAW_PAIRS.get(ob_dest, [ob_dest])
    if ib_orig not in valid_origins:
        return False

    try:
        ob_date = date.fromisoformat(outbound["departure"][:10])
        ib_date = date.fromisoformat(inbound["departure"][:10])
    except ValueError:
        logger.warning("Could not parse departure dates: %s / %s", outbound["departure"], inbound["departure"])
        return False

    delta = (ib_date - ob_date).days
    return config.MIN_STAY <= delta <= config.MAX_STAY


class AwardMonitor:
    """Polls Cathay Pacific award availability for all configured routes and dates."""

    async def poll_once(self) -> list[dict]:
        """Run one complete scan of all routes/dates.

        Returns a list of newly inserted combo dicts (not previously in DB).
        """
        db.init_db()

        all_dates = _dates_for_month(config.SEARCH_YEAR, config.SEARCH_MONTH)
        month_str = f"{config.SEARCH_YEAR:04d}-{config.SEARCH_MONTH:02d}"
        logger.info(
            "Poll cycle starting — %d dates in %s, %d outbound routes, %d inbound routes",
            len(all_dates),
            month_str,
            len(config.OUTBOUND_ROUTES),
            len(config.INBOUND_ROUTES),
        )

        # Build a shuffled work list of (direction, route, date) tuples so
        # requests are staggered rather than hammering one route sequentially.
        work_items: list[tuple[str, tuple[str, str], date]] = []
        for route in config.OUTBOUND_ROUTES:
            for d in all_dates:
                work_items.append(("outbound", route, d))
        for route in config.INBOUND_ROUTES:
            for d in all_dates:
                work_items.append(("inbound", route, d))
        random.shuffle(work_items)

        # Maps (origin, destination, departure_date_str) -> db flight id
        outbound_flights: dict[tuple[str, str, str], int] = {}
        inbound_flights: dict[tuple[str, str, str], int] = {}

        for direction, (origin, destination), dep_date in work_items:
            dep_str = dep_date.isoformat()
            logger.info("[%s] Searching %s -> %s on %s", direction, origin, destination, dep_str)

            try:
                results: list[dict] = await search_one(origin, destination, dep_str)
            except Exception as exc:
                logger.warning(
                    "Search failed for %s->%s %s: %s",
                    origin, destination, dep_str, exc,
                    exc_info=True,
                )
                results = []

            now = _now_iso()
            for flight in results:
                flight.setdefault("first_seen", now)
                flight["last_seen"] = now
                flight["origin"] = origin
                flight["destination"] = destination

                flight_id = db.upsert_flight(flight)
                key = (origin, destination, flight["departure"][:10])

                if direction == "outbound":
                    outbound_flights[key] = flight_id
                else:
                    inbound_flights[key] = flight_id

            # Polite delay: 1–2 seconds between requests
            await asyncio.sleep(random.uniform(1.0, 2.0))

        # ── Match combos ─────────────────────────────────────────────────────
        logger.info(
            "Matching combos: %d outbound flights × %d inbound flights",
            len(outbound_flights),
            len(inbound_flights),
        )

        # Rebuild full flight dicts for matching (avoid re-querying for every pair)
        all_db_flights = {f["id"]: f for f in db.get_all_flights(month_str)}

        new_combos: list[dict] = []
        for (ob_origin, ob_dest, ob_date_str), ob_id in outbound_flights.items():
            ob_flight = all_db_flights.get(ob_id)
            if ob_flight is None:
                continue
            ob_check = {"destination": ob_dest, "departure": ob_date_str}

            for (ib_origin, ib_dest, ib_date_str), ib_id in inbound_flights.items():
                ib_check = {"origin": ib_origin, "departure": ib_date_str}
                if not _is_valid_combination(ob_check, ib_check):
                    continue

                combo_id = db.upsert_combo(ob_id, ib_id)
                new_combos.append({
                    "combo_id": combo_id,
                    "outbound_id": ob_id,
                    "inbound_id": ib_id,
                })

        # Fetch full combo dicts (with outbound/inbound detail) for new combos
        unnotified = db.get_unnotified_combos()
        new_combo_ids = {c["combo_id"] for c in new_combos}
        full_new_combos = [c for c in unnotified if c["combo_id"] in new_combo_ids]

        # Send notifications
        if full_new_combos:
            notifier = build_notifier()
            for combo in full_new_combos:
                await notifier.send_combo(combo)
                db.mark_combo_notified(combo["combo_id"])
            await notifier.send_summary(full_new_combos)

        logger.info("Poll cycle complete — %d new combos found", len(full_new_combos))
        return full_new_combos

    async def run_forever(self) -> None:
        """Poll every POLL_INTERVAL_MINUTES minutes, logging each cycle."""
        logger.info(
            "Starting monitor loop — interval=%d minutes",
            config.POLL_INTERVAL_MINUTES,
        )
        while True:
            try:
                combos = await self.poll_once()
                logger.info("Cycle finished with %d combos; sleeping %d min", len(combos), config.POLL_INTERVAL_MINUTES)
            except Exception as exc:
                logger.error("Unexpected error in poll cycle: %s", exc, exc_info=True)

            await asyncio.sleep(config.POLL_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    asyncio.run(AwardMonitor().run_forever())
