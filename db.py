"""SQLite database layer for Cathay Pacific award ticket monitor."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

import config

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    flight_number TEXT,
    departure TEXT NOT NULL,
    arrival TEXT,
    miles INTEGER DEFAULT 0,
    taxes_hkd REAL DEFAULT 0,
    seats INTEGER DEFAULT 1,
    cabin TEXT DEFAULT 'Business',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS combos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outbound_id INTEGER REFERENCES flights(id),
    inbound_id INTEGER REFERENCES flights(id),
    notified INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(outbound_id, inbound_id)
);
"""


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    """Yield a SQLite connection with row_factory set to Row."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not yet exist."""
    with _connect() as conn:
        conn.executescript(DDL)
    logger.info("Database initialised at %s", config.DB_PATH)


def upsert_flight(flight: dict) -> int:
    """Insert or update a flight record and return its id.

    The natural key is (origin, destination, departure).  On conflict the
    mutable fields (arrival, miles, taxes_hkd, seats, last_seen) are updated
    while first_seen is preserved.
    """
    sql_insert = """
        INSERT INTO flights
            (origin, destination, flight_number, departure, arrival,
             miles, taxes_hkd, seats, cabin, first_seen, last_seen)
        VALUES
            (:origin, :destination, :flight_number, :departure, :arrival,
             :miles, :taxes_hkd, :seats, :cabin, :first_seen, :last_seen)
        ON CONFLICT(origin, destination, departure) DO UPDATE SET
            flight_number = excluded.flight_number,
            arrival       = excluded.arrival,
            miles         = excluded.miles,
            taxes_hkd     = excluded.taxes_hkd,
            seats         = excluded.seats,
            last_seen     = excluded.last_seen
    """
    # Ensure required defaults are present so the dict can be passed directly.
    row = {
        "origin": flight["origin"],
        "destination": flight["destination"],
        "flight_number": flight.get("flight_number"),
        "departure": flight["departure"],
        "arrival": flight.get("arrival"),
        "miles": flight.get("miles", 0),
        "taxes_hkd": flight.get("taxes_hkd", 0.0),
        "seats": flight.get("seats", 1),
        "cabin": flight.get("cabin", "Business"),
        "first_seen": flight.get("first_seen", datetime.utcnow().isoformat()),
        "last_seen": flight.get("last_seen", datetime.utcnow().isoformat()),
    }

    with _connect() as conn:
        # SQLite does not support ON CONFLICT with a named unique index on
        # multiple columns unless we define it.  Use INSERT OR REPLACE
        # approach via a manual check instead to preserve first_seen.
        cur = conn.execute(
            "SELECT id, first_seen FROM flights "
            "WHERE origin=:origin AND destination=:destination AND departure=:departure",
            row,
        )
        existing = cur.fetchone()
        if existing:
            flight_id: int = existing["id"]
            conn.execute(
                """UPDATE flights SET
                       flight_number = :flight_number,
                       arrival       = :arrival,
                       miles         = :miles,
                       taxes_hkd     = :taxes_hkd,
                       seats         = :seats,
                       last_seen     = :last_seen
                   WHERE id = :id""",
                {**row, "id": flight_id},
            )
            logger.debug("Updated flight id=%d %s->%s %s", flight_id, row["origin"], row["destination"], row["departure"])
        else:
            cur = conn.execute(
                """INSERT INTO flights
                       (origin, destination, flight_number, departure, arrival,
                        miles, taxes_hkd, seats, cabin, first_seen, last_seen)
                   VALUES
                       (:origin, :destination, :flight_number, :departure, :arrival,
                        :miles, :taxes_hkd, :seats, :cabin, :first_seen, :last_seen)""",
                row,
            )
            flight_id = cur.lastrowid  # type: ignore[assignment]
            logger.debug("Inserted flight id=%d %s->%s %s", flight_id, row["origin"], row["destination"], row["departure"])

    return flight_id


def upsert_combo(outbound_id: int, inbound_id: int) -> int:
    """Insert a combo if it does not exist and return its id."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id FROM combos WHERE outbound_id=? AND inbound_id=?",
            (outbound_id, inbound_id),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]

        cur = conn.execute(
            "INSERT INTO combos (outbound_id, inbound_id, notified, created_at) "
            "VALUES (?, ?, 0, ?)",
            (outbound_id, inbound_id, now),
        )
        combo_id: int = cur.lastrowid  # type: ignore[assignment]
        logger.debug("Inserted combo id=%d out=%d in=%d", combo_id, outbound_id, inbound_id)
    return combo_id


def get_unnotified_combos() -> list[dict]:
    """Return combos where notified=0, joined with full flight details."""
    sql = """
        SELECT
            c.id          AS combo_id,
            c.outbound_id,
            c.inbound_id,
            c.created_at  AS combo_created_at,

            ob.origin          AS ob_origin,
            ob.destination     AS ob_destination,
            ob.flight_number   AS ob_flight_number,
            ob.departure       AS ob_departure,
            ob.arrival         AS ob_arrival,
            ob.miles           AS ob_miles,
            ob.taxes_hkd       AS ob_taxes_hkd,
            ob.seats           AS ob_seats,
            ob.cabin           AS ob_cabin,

            ib.origin          AS ib_origin,
            ib.destination     AS ib_destination,
            ib.flight_number   AS ib_flight_number,
            ib.departure       AS ib_departure,
            ib.arrival         AS ib_arrival,
            ib.miles           AS ib_miles,
            ib.taxes_hkd       AS ib_taxes_hkd,
            ib.seats           AS ib_seats,
            ib.cabin           AS ib_cabin
        FROM combos c
        JOIN flights ob ON ob.id = c.outbound_id
        JOIN flights ib ON ib.id = c.inbound_id
        WHERE c.notified = 0
        ORDER BY c.id
    """
    with _connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def mark_combo_notified(combo_id: int) -> None:
    """Set notified=1 for the given combo."""
    with _connect() as conn:
        conn.execute("UPDATE combos SET notified=1 WHERE id=?", (combo_id,))
    logger.debug("Marked combo id=%d as notified", combo_id)


def get_all_flights(month: str) -> list[dict]:
    """Return all flights whose departure starts with the given YYYY-MM prefix."""
    prefix = f"{month}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flights WHERE departure LIKE ? ORDER BY departure",
            (prefix,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_combos() -> list[dict]:
    """Return all combos (notified or not) with full outbound/inbound details."""
    sql = """
        SELECT
            c.id          AS combo_id,
            c.outbound_id,
            c.inbound_id,
            c.notified,
            c.created_at  AS combo_created_at,

            ob.origin          AS ob_origin,
            ob.destination     AS ob_destination,
            ob.flight_number   AS ob_flight_number,
            ob.departure       AS ob_departure,
            ob.arrival         AS ob_arrival,
            ob.miles           AS ob_miles,
            ob.taxes_hkd       AS ob_taxes_hkd,
            ob.seats           AS ob_seats,

            ib.origin          AS ib_origin,
            ib.destination     AS ib_destination,
            ib.flight_number   AS ib_flight_number,
            ib.departure       AS ib_departure,
            ib.arrival         AS ib_arrival,
            ib.miles           AS ib_miles,
            ib.taxes_hkd       AS ib_taxes_hkd,
            ib.seats           AS ib_seats
        FROM combos c
        JOIN flights ob ON c.outbound_id = ob.id
        JOIN flights ib ON c.inbound_id  = ib.id
        ORDER BY c.id
    """
    with _connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_combo(dict(r)) for r in rows]


def _row_to_combo(row: dict) -> dict:
    """Convert a flat JOIN row into the nested combo dict used by notifier/main."""
    def _split_dt(iso: str) -> tuple[str, str]:
        """Split ISO datetime into date string and time string."""
        if not iso:
            return "", ""
        parts = iso.replace("T", " ").split(" ")
        return parts[0], parts[1][:5] if len(parts) > 1 else ""

    ob_date, ob_time = _split_dt(row.get("ob_departure", ""))
    ib_date, ib_time = _split_dt(row.get("ib_departure", ""))
    ob_arr_date, ob_arr_time = _split_dt(row.get("ob_arrival", ""))
    ib_arr_date, ib_arr_time = _split_dt(row.get("ib_arrival", ""))

    def _day_offset(dep_date: str, arr_date: str) -> int:
        if not dep_date or not arr_date:
            return 0
        try:
            from datetime import date as _date
            d1 = _date.fromisoformat(dep_date)
            d2 = _date.fromisoformat(arr_date)
            return max(0, (d2 - d1).days)
        except ValueError:
            return 0

    stay_days = ""
    if ob_date and ib_date:
        try:
            from datetime import date as _date
            stay_days = str((_date.fromisoformat(ib_date) - _date.fromisoformat(ob_date)).days)
        except ValueError:
            pass

    return {
        "combo_id": row.get("combo_id"),
        "stay_days": stay_days,
        "outbound": {
            "origin": row.get("ob_origin", ""),
            "destination": row.get("ob_destination", ""),
            "flight_number": row.get("ob_flight_number", ""),
            "date": ob_date,
            "depart_time": ob_time,
            "arrive_time": ob_arr_time,
            "arrive_day_offset": _day_offset(ob_date, ob_arr_date),
            "miles": row.get("ob_miles", 0),
            "tax": row.get("ob_taxes_hkd", 0),
            "tax_currency": "HKD",
            "seats_available": row.get("ob_seats", 0),
        },
        "inbound": {
            "origin": row.get("ib_origin", ""),
            "destination": row.get("ib_destination", ""),
            "flight_number": row.get("ib_flight_number", ""),
            "date": ib_date,
            "depart_time": ib_time,
            "arrive_time": ib_arr_time,
            "arrive_day_offset": _day_offset(ib_date, ib_arr_date),
            "miles": row.get("ib_miles", 0),
            "tax": row.get("ib_taxes_hkd", 0),
            "tax_currency": "HKD",
            "seats_available": row.get("ib_seats", 0),
        },
    }
