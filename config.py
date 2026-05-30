import os
from dotenv import load_dotenv

load_dotenv()

# ── Routes ───────────────────────────────────────────────────────────────────
OUTBOUND_ROUTES = [
    ("HKG", "LAX"),
    ("HKG", "BCN"),
    ("HKG", "MAD"),
    ("HKG", "PVG"),
]

INBOUND_ROUTES = [
    ("LAX", "HKG"),
    ("BCN", "HKG"),
    ("MAD", "HKG"),
    ("PVG", "HKG"),
]

# Open-jaw pairs: any outbound city can pair with any inbound city
# e.g. HKG->MAD outbound + BCN->HKG inbound is valid
VALID_OPENJAW_PAIRS = {
    "LAX": ["LAX"],           # US: LAX only
    "BCN": ["BCN", "MAD"],    # Spain: cross-city open jaw
    "MAD": ["BCN", "MAD"],
    "PVG": ["PVG"],
}

# ── Search parameters ─────────────────────────────────────────────────────────
SEARCH_YEAR  = int(os.getenv("SEARCH_YEAR",  "2026"))
SEARCH_MONTH = int(os.getenv("SEARCH_MONTH", "10"))
CABIN_CODE   = "C"   # Business class IATA code
MIN_STAY     = 3     # days between outbound and inbound
MAX_STAY     = 35

# ── Polling ───────────────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "30"))

# ── Browser ───────────────────────────────────────────────────────────────────
HEADLESS   = os.getenv("HEADLESS", "true").lower() != "false"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── Cathay Pacific URLs ───────────────────────────────────────────────────────
CX_AWARD_URL = "https://www.cathaypacific.com/cx/en_HK/plan-and-book/redeem-asia-miles.html"
CX_BOOK_URL  = "https://book.cathaypacific.com"

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "cathay_awards.db"
