"""
scraper.py — Cathay Pacific award ticket availability scraper.

Strategies (tried in order):
  A. Intercept XHR/fetch during page navigation, replay endpoint with httpx
  B. Playwright form interaction + response interception
  C. Return empty list with a warning (never crash the caller)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any

import httpx
from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
    async_playwright,
)

import config

logger = logging.getLogger(__name__)

# ── URL fragment keywords that identify API responses we care about ───────────
_API_KEYWORDS = (
    "availability",
    "award",
    "redeem",
    "flight-search",
    "flightsearch",
    "search",
    "offer",
    "fare",
    "itinerary",
)

# ── Known Cathay booking-engine patterns ──────────────────────────────────────
_CX_SEARCH_URL_PATTERN = re.compile(
    r"https://book\.cathaypacific\.com.*"
    r"(availability|award|flight|search|offer|redeem)",
    re.IGNORECASE,
)

# ── Return schema sentinel values ─────────────────────────────────────────────
_EMPTY_FLIGHT: dict[str, Any] = {
    "flight_number": "",
    "origin": "",
    "destination": "",
    "departure": "",
    "arrival": "",
    "miles": 0,
    "taxes_hkd": 0.0,
    "seats": 1,
    "cabin": "Business",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _random_delay(lo: float = 1.0, hi: float = 3.0) -> asyncio.Future[None]:
    """Await-able random delay between *lo* and *hi* seconds."""
    return asyncio.sleep(random.uniform(lo, hi))


def _is_interesting_url(url: str) -> bool:
    """Return True if *url* looks like a flight-availability API call."""
    url_lower = url.lower()
    if "cathaypacific.com" not in url_lower and "book.cathay" not in url_lower:
        return False
    return any(kw in url_lower for kw in _API_KEYWORDS)


def _extract_flights(
    payload: Any,
    origin: str,
    destination: str,
    date: str,
) -> list[dict[str, Any]]:
    """
    Walk a parsed JSON payload and extract Business-class award flights.

    Handles several observed response shapes:
      - payload["flights"]
      - payload["journeys"]
      - payload["data"]["availability"]
      - payload["data"]["flights"]
      - payload["availability"]["flights"]
      - payload["offers"]
    """
    flights: list[dict[str, Any]] = []

    if not isinstance(payload, dict):
        return flights

    # Unwrap common envelope layers
    candidates: list[Any] = []

    def _collect(obj: Any) -> None:
        """Recursively try to collect flight-like arrays."""
        if not isinstance(obj, dict):
            return
        for key in ("flights", "journeys", "availability", "offers", "segments", "legs"):
            val = obj.get(key)
            if isinstance(val, list):
                candidates.append(val)
            elif isinstance(val, dict):
                _collect(val)
        data = obj.get("data")
        if isinstance(data, dict):
            _collect(data)

    _collect(payload)

    # Also accept top-level list
    if isinstance(payload, list):
        candidates.append(payload)

    for candidate_list in candidates:
        for item in candidate_list:
            parsed = _parse_flight_item(item, origin, destination, date)
            if parsed:
                flights.append(parsed)

    # Deduplicate by flight_number + departure
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for f in flights:
        key = (f["flight_number"], f["departure"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


def _parse_flight_item(
    item: Any,
    origin: str,
    destination: str,
    date: str,
) -> dict[str, Any] | None:
    """
    Try to build a normalised flight dict from a raw item dict.
    Returns None if the item doesn't look like a usable Business-class flight.
    """
    if not isinstance(item, dict):
        return None

    # ── Cabin filter ──────────────────────────────────────────────────────────
    cabin_raw = (
        item.get("cabin")
        or item.get("cabinClass")
        or item.get("cabin_class")
        or item.get("class")
        or ""
    )
    cabin_str = str(cabin_raw).upper()

    # Accept C, J, D (Business), or explicit "BUSINESS" label
    is_business = cabin_str in ("C", "J", "D", "BUSINESS", "BUSINESS CLASS") or (
        config.CABIN_CODE.upper() in cabin_str
    )
    # If cabin info is absent we'll still include the flight but mark cabin
    cabin_label = "Business" if is_business or not cabin_str else None
    if cabin_label is None:
        return None  # Explicitly non-business cabin

    # ── Availability filter ───────────────────────────────────────────────────
    seats_raw = (
        item.get("seats")
        or item.get("seatsAvailable")
        or item.get("available_seats")
        or item.get("availability")
        or item.get("availableSeats")
    )
    try:
        seats = int(seats_raw) if seats_raw is not None else 1
    except (ValueError, TypeError):
        seats = 1

    avail_flag = item.get("available") or item.get("isAvailable") or item.get("status")
    if isinstance(avail_flag, bool) and not avail_flag:
        return None
    if isinstance(avail_flag, str) and avail_flag.upper() in ("CLOSED", "UNAVAILABLE", "N"):
        return None

    # ── Flight number ─────────────────────────────────────────────────────────
    flight_number = (
        item.get("flightNumber")
        or item.get("flight_number")
        or item.get("flightNo")
        or item.get("flight")
        or ""
    )
    flight_number = str(flight_number).strip()

    # ── Origin / destination ──────────────────────────────────────────────────
    dep_airport = (
        item.get("origin")
        or item.get("departureAirport")
        or item.get("departure_airport")
        or item.get("from")
        or origin
    )
    arr_airport = (
        item.get("destination")
        or item.get("arrivalAirport")
        or item.get("arrival_airport")
        or item.get("to")
        or destination
    )

    # ── Times ─────────────────────────────────────────────────────────────────
    dep_time = (
        item.get("departureTime")
        or item.get("departure_time")
        or item.get("departure")
        or item.get("departs")
        or f"{date}T00:00:00"
    )
    arr_time = (
        item.get("arrivalTime")
        or item.get("arrival_time")
        or item.get("arrival")
        or item.get("arrives")
        or ""
    )

    # ── Miles / taxes ─────────────────────────────────────────────────────────
    miles_raw = (
        item.get("miles")
        or item.get("awardMiles")
        or item.get("redemptionMiles")
        or item.get("points")
        or 0
    )
    try:
        miles = int(miles_raw)
    except (ValueError, TypeError):
        miles = 0

    taxes_raw = (
        item.get("taxes")
        or item.get("taxesHKD")
        or item.get("taxes_hkd")
        or item.get("surcharges")
        or 0
    )
    try:
        taxes_hkd = float(taxes_raw)
    except (ValueError, TypeError):
        taxes_hkd = 0.0

    return {
        "flight_number": flight_number,
        "origin": str(dep_airport).upper()[:3],
        "destination": str(arr_airport).upper()[:3],
        "departure": str(dep_time),
        "arrival": str(arr_time),
        "miles": miles,
        "taxes_hkd": taxes_hkd,
        "seats": seats,
        "cabin": "Business",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main scraper class
# ─────────────────────────────────────────────────────────────────────────────


class CathayAwardScraper:
    """
    Async context manager that owns a Playwright browser session.

    Usage::

        async with CathayAwardScraper() as scraper:
            results = await scraper.search("HKG", "LAX", "2026-10-15")
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        # Discovered API info cache (url, headers, params)
        self._api_info: dict[str, Any] | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "CathayAwardScraper":
        self._playwright = await async_playwright().start()
        browser = await self._playwright.chromium.launch(headless=config.HEADLESS)
        self._context = await browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-HK",
            timezone_id="Asia/Hong_Kong",
            extra_http_headers={
                "Accept-Language": "en-HK,en;q=0.9",
            },
        )
        logger.info("Browser launched (headless=%s)", config.HEADLESS)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._context:
            await self._context.browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _new_page_task(self) -> asyncio.Task[Page]:
        """Create a new page inside the managed context."""
        assert self._context is not None, "Scraper not entered — use 'async with'"
        return asyncio.ensure_future(self._context.new_page())

    async def _intercept_responses(
        self,
        page: Page,
        origin: str,
        destination: str,
        date: str,
        timeout_ms: int = 30_000,
    ) -> list[dict[str, Any]]:
        """
        Collect responses from the page until we find flight data or timeout.
        Returns a (possibly empty) list of normalised flight dicts.
        """
        captured: list[dict[str, Any]] = []
        captured_api_info: dict[str, Any] | None = None

        async def on_response(response: Response) -> None:
            nonlocal captured_api_info
            url = response.url
            if not _is_interesting_url(url):
                return
            if response.status not in (200, 201, 206):
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "javascript" not in content_type:
                return
            try:
                body = await response.json()
            except Exception:
                try:
                    text = await response.text()
                    body = json.loads(text)
                except Exception:
                    return

            flights = _extract_flights(body, origin, destination, date)
            if flights:
                logger.info(
                    "Captured %d Business-class flights from %s", len(flights), url
                )
                captured.extend(flights)
                # Store api_info for Strategy A replay
                if captured_api_info is None:
                    req: Request = response.request
                    captured_api_info = {
                        "url": url,
                        "method": req.method,
                        "headers": dict(req.headers),
                        "post_data": req.post_data,
                    }
            else:
                logger.debug("Interesting URL but no flights extracted: %s", url)

        page.on("response", on_response)
        return captured, captured_api_info  # type: ignore[return-value]

    async def _build_search_url(self, origin: str, destination: str, date: str) -> str:
        """Build a deep-link URL to the Cathay booking engine award search."""
        # Cathay's booking engine accepts query-string parameters
        # Format observed from manual testing: /ibe/default.aspx or similar
        base = f"{config.CX_BOOK_URL}/ibe/default.aspx"
        params = (
            f"?journeyType=O"
            f"&origin={origin}"
            f"&destination={destination}"
            f"&departDate={date}"
            f"&adults=1&children=0&infants=0"
            f"&cabin=C"
            f"&tripType=OW"
            f"&awardBooking=true"
            f"&lang=en_HK"
        )
        return base + params

    # ── Strategy A: replay discovered API endpoint directly ───────────────────

    async def _strategy_a(
        self,
        origin: str,
        destination: str,
        date: str,
        api_info: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Replay the captured API endpoint with httpx and parse the result."""
        url = api_info.get("url", "")
        method = api_info.get("method", "GET").upper()
        headers = api_info.get("headers", {})
        post_data = api_info.get("post_data")

        # Sanitise headers: remove host/content-length — httpx will set these
        filtered_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "content-length", "content-encoding")
        }

        logger.info("Strategy A: replaying %s %s", method, url)
        try:
            async with httpx.AsyncClient(
                headers=filtered_headers,
                follow_redirects=True,
                timeout=20.0,
            ) as client:
                if method == "POST" and post_data:
                    try:
                        body = json.loads(post_data)
                        resp = await client.post(url, json=body)
                    except (json.JSONDecodeError, ValueError):
                        resp = await client.post(url, content=post_data.encode())
                else:
                    resp = await client.get(url)

                resp.raise_for_status()
                payload = resp.json()
                flights = _extract_flights(payload, origin, destination, date)
                logger.info("Strategy A yielded %d flights", len(flights))
                return flights
        except Exception as exc:
            logger.warning("Strategy A failed: %s", exc)
            return []

    # ── Strategy B: Playwright form interaction ───────────────────────────────

    async def _strategy_b(
        self,
        origin: str,
        destination: str,
        date: str,
    ) -> list[dict[str, Any]]:
        """
        Navigate to the award search page, intercept API responses, and return
        any Business-class flights found.
        """
        assert self._context is not None
        page = await self._context.new_page()

        # Wire up response interception BEFORE navigation
        captured: list[dict[str, Any]] = []
        captured_api_info: list[dict[str, Any]] = []  # mutable wrapper

        async def on_response(response: Response) -> None:
            url = response.url
            if not _is_interesting_url(url):
                return
            if response.status not in (200, 201, 206):
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "javascript" not in content_type:
                return
            try:
                body = await response.json()
            except Exception:
                try:
                    text = await response.text()
                    body = json.loads(text)
                except Exception:
                    return

            flights = _extract_flights(body, origin, destination, date)
            if flights:
                logger.info(
                    "Strategy B captured %d flights from %s", len(flights), url
                )
                captured.extend(flights)
                if not captured_api_info:
                    req: Request = response.request
                    captured_api_info.append(
                        {
                            "url": url,
                            "method": req.method,
                            "headers": dict(req.headers),
                            "post_data": req.post_data,
                        }
                    )

        page.on("response", on_response)

        try:
            search_url = await self._build_search_url(origin, destination, date)
            logger.info("Strategy B: navigating to %s", search_url)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45_000)
            await _random_delay(1.5, 3.0)

            # Also try the main redeem page in case the deep link redirects
            if not captured:
                logger.info(
                    "Strategy B: no data yet, trying main award page %s",
                    config.CX_AWARD_URL,
                )
                await page.goto(
                    config.CX_AWARD_URL, wait_until="domcontentloaded", timeout=45_000
                )
                await _random_delay(1.0, 2.5)

                # Attempt to fill the search form if visible
                await self._fill_search_form(page, origin, destination, date)
                # Wait for network to settle
                await asyncio.sleep(5)

            # Cache discovered API info
            if captured_api_info:
                self._api_info = captured_api_info[0]

        except Exception as exc:
            logger.warning("Strategy B navigation error: %s", exc)
        finally:
            await page.close()

        return captured

    async def _fill_search_form(
        self,
        page: Page,
        origin: str,
        destination: str,
        date: str,
    ) -> None:
        """
        Best-effort: try to locate and submit the flight-search form.
        Swallows all exceptions — callers must not depend on this succeeding.
        """
        try:
            # Common selector patterns used by Cathay / typical booking engines
            origin_selectors = [
                "input[placeholder*='From']",
                "input[name*='origin']",
                "input[id*='origin']",
                "input[id*='from']",
                "[data-testid*='origin'] input",
            ]
            dest_selectors = [
                "input[placeholder*='To']",
                "input[name*='destination']",
                "input[id*='destination']",
                "input[id*='to']",
                "[data-testid*='destination'] input",
            ]
            date_selectors = [
                "input[placeholder*='Date']",
                "input[name*='depart']",
                "input[id*='depart']",
                "[data-testid*='depart'] input",
            ]

            async def try_fill(selectors: list[str], value: str) -> bool:
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        await el.wait_for(state="visible", timeout=3_000)
                        await el.triple_click()
                        await el.type(value, delay=50)
                        await _random_delay(0.3, 0.8)
                        return True
                    except Exception:
                        continue
                return False

            origin_filled = await try_fill(origin_selectors, origin)
            await _random_delay(0.5, 1.5)
            dest_filled = await try_fill(dest_selectors, destination)
            await _random_delay(0.5, 1.5)
            date_filled = await try_fill(date_selectors, date.replace("-", "/"))
            await _random_delay(0.5, 1.0)

            logger.debug(
                "Form fill: origin=%s dest=%s date=%s",
                origin_filled,
                dest_filled,
                date_filled,
            )

            if origin_filled and dest_filled:
                submit_selectors = [
                    "button[type='submit']",
                    "button:has-text('Search')",
                    "[data-testid*='search'] button",
                    ".search-btn",
                    "#searchBtn",
                ]
                for sel in submit_selectors:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(state="visible", timeout=3_000)
                        await btn.click()
                        await _random_delay(1.0, 2.0)
                        break
                    except Exception:
                        continue

        except Exception as exc:
            logger.debug("Form fill encountered error (non-fatal): %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(
        self,
        origin: str,
        destination: str,
        date: str,
    ) -> list[dict[str, Any]]:
        """
        Search for Business-class award flights.

        Parameters
        ----------
        origin:      IATA origin airport code (e.g. "HKG")
        destination: IATA destination airport code (e.g. "LAX")
        date:        Search date in YYYY-MM-DD format

        Returns
        -------
        List of flight dicts conforming to the standard schema.
        """
        origin = origin.upper().strip()
        destination = destination.upper().strip()

        logger.info(
            "Searching %s → %s on %s (cabin=%s)",
            origin,
            destination,
            date,
            config.CABIN_CODE,
        )

        # ── Strategy A: replay previously discovered endpoint ─────────────────
        if self._api_info:
            logger.info("Strategy A: reusing cached API info")
            flights = await self._strategy_a(origin, destination, date, self._api_info)
            if flights:
                return flights

        # ── Strategy B: Playwright navigation + interception ──────────────────
        try:
            flights = await self._strategy_b(origin, destination, date)
            if flights:
                return flights
        except Exception as exc:
            logger.warning("Strategy B raised an unexpected error: %s", exc)

        # ── Strategy A (delayed): if Strategy B discovered the API ─────────────
        if self._api_info:
            logger.info("Strategy A (post-discovery): replaying captured endpoint")
            flights = await self._strategy_a(origin, destination, date, self._api_info)
            if flights:
                return flights

        # ── Strategy C: graceful empty result ─────────────────────────────────
        logger.warning(
            "All strategies exhausted for %s→%s on %s — returning empty list",
            origin,
            destination,
            date,
        )
        return []

    async def discover_api(self) -> dict[str, Any] | None:
        """
        Navigate to the Cathay award search page and capture the first
        interesting API call, returning its metadata for later replay.

        Returns a dict with keys: url, method, headers, post_data
        (or None if nothing was found).
        """
        assert self._context is not None
        page = await self._context.new_page()
        discovery: list[dict[str, Any]] = []

        async def on_response(response: Response) -> None:
            if discovery:
                return  # Already found one
            url = response.url
            if not _is_interesting_url(url):
                return
            if response.status != 200:
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                return
            req: Request = response.request
            info = {
                "url": url,
                "method": req.method,
                "headers": dict(req.headers),
                "post_data": req.post_data,
            }
            discovery.append(info)
            logger.info("discover_api: found candidate endpoint %s", url)

        page.on("response", on_response)

        try:
            logger.info("discover_api: navigating to %s", config.CX_AWARD_URL)
            await page.goto(
                config.CX_AWARD_URL, wait_until="domcontentloaded", timeout=45_000
            )
            await _random_delay(2.0, 4.0)

            # Also probe the booking engine
            if not discovery:
                probe_url = await self._build_search_url("HKG", "LAX", "2026-10-15")
                logger.info("discover_api: probing booking engine %s", probe_url)
                await page.goto(probe_url, wait_until="domcontentloaded", timeout=45_000)
                await _random_delay(2.0, 4.0)

        except Exception as exc:
            logger.warning("discover_api navigation error: %s", exc)
        finally:
            await page.close()

        if discovery:
            self._api_info = discovery[0]
            return discovery[0]

        logger.warning("discover_api: no API endpoint discovered")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Convenience one-off function
# ─────────────────────────────────────────────────────────────────────────────


async def search_one(
    origin: str,
    destination: str,
    date: str,
) -> list[dict[str, Any]]:
    """
    Convenience wrapper: launch a fresh browser, search once, close, return.

    Parameters
    ----------
    origin:      IATA code, e.g. "HKG"
    destination: IATA code, e.g. "LAX"
    date:        YYYY-MM-DD, e.g. "2026-10-15"

    Returns
    -------
    List of normalised Business-class flight dicts (may be empty).
    """
    async with CathayAwardScraper() as scraper:
        return await scraper.search(origin, destination, date)


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from rich.console import Console
    from rich.table import Table

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    console = Console()

    # Accept optional CLI args: origin destination date
    _origin = sys.argv[1] if len(sys.argv) > 1 else "HKG"
    _dest = sys.argv[2] if len(sys.argv) > 2 else "LAX"
    _date = sys.argv[3] if len(sys.argv) > 3 else f"{config.SEARCH_YEAR}-{config.SEARCH_MONTH:02d}-15"

    console.print(f"[bold cyan]Cathay Pacific Award Scraper[/]")
    console.print(f"Route: [yellow]{_origin} → {_dest}[/]  Date: [yellow]{_date}[/]\n")

    results = asyncio.run(search_one(_origin, _dest, _date))

    if not results:
        console.print("[red]No Business-class awards found (or scraper returned empty).[/]")
        sys.exit(0)

    table = Table(title=f"Business Awards: {_origin}→{_dest} on {_date}")
    table.add_column("Flight", style="cyan")
    table.add_column("Departure", style="green")
    table.add_column("Arrival", style="green")
    table.add_column("Miles", justify="right", style="magenta")
    table.add_column("Taxes HKD", justify="right")
    table.add_column("Seats", justify="right", style="yellow")

    for f in results:
        table.add_row(
            f["flight_number"],
            f["departure"],
            f["arrival"],
            str(f["miles"]),
            f"{f['taxes_hkd']:.2f}",
            str(f["seats"]),
        )

    console.print(table)
