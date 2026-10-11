"""Shared helpers: HTTP, hashing, DB, number parsing, voltage ranking, validation."""
import hashlib, json, os, re, sqlite3, time, csv
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA, "tariff.db")
SCHEMA = os.path.join(ROOT, "pipeline", "schema.sql")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


class _Resp:
    def __init__(self, content):
        self.content = content


def http_get(url, retries=2, timeout=40):
    """GET via curl (the sandbox's env HTTP_PROXY returns 404 for js.sgcc.com.cn when used
    by Python clients; curl ignores upper-case HTTP_PROXY and connects directly).
    At most `retries` retries – never loops more than twice."""
    import subprocess
    last = None
    for i in range(retries + 1):
        p = subprocess.run(["curl", "-sS", "-L", "-m", str(timeout), "-A", UA["User-Agent"],
                            "-w", "\n__HTTP_CODE__%{http_code}", url], capture_output=True)
        body, _, code = p.stdout.rpartition(b"\n__HTTP_CODE__")
        if p.returncode == 0 and code.strip() == b"200":
            return _Resp(body)
        last = f"curl rc={p.returncode} http={code.decode(errors='ignore')} {p.stderr.decode(errors='ignore')[:200]}"
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed after {retries+1} tries: {url}: {last}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def doc_id(url):
    return hashlib.sha1(url.encode()).hexdigest()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- numbers / text ----------
def norm(s):
    if s is None:
        return ""
    return re.sub(r"\s+", "", str(s)).replace("（", "(").replace("）", ")")


def num(s):
    s = norm(s)
    if s in ("", "-", "—", "/", "－"):
        return None
    m = re.fullmatch(r"-?\d+(\.\d+)?", s)
    return float(s) if m else None


VOLTAGE_RANK = [
    (r"不满1千伏|^1千伏以下|^1千伏及以下", 1),
    (r"^1[-~—–]10(\(20\))?千伏", 2),
    (r"^10(\(20\))?千伏", 2),          # 上海/部分省 write just '10千伏'
    (r"^20千伏", 2),
    (r"^35", 3),
    (r"^(66|110)", 4),
    (r"^220|^330", 5),
]


def voltage_rank(v):
    v = norm(v).replace("－", "-").replace("~", "-")
    for pat, r in VOLTAGE_RANK:
        if re.search(pat, v):
            return r
    return None


# ---------- DB ----------
def connect():
    os.makedirs(DATA, exist_ok=True)
    fresh = not os.path.exists(DB_PATH)
    con = sqlite3.connect(DB_PATH, timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    con.execute("PRAGMA foreign_keys=ON")
    if fresh:
        con.executescript(open(SCHEMA, encoding="utf-8").read())
    return con


def upsert(con, table, row):
    cols = list(row)
    con.execute(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
        [row[c] for c in cols],
    )


# ---------- validation ----------
TOL = 0.0005
TARIFF_FIELDS = ["month", "category", "tariff_type", "voltage", "voltage_rank", "energy_price",
                 "purchase_price", "line_loss", "tdp", "system_op", "gov_fund", "sharp", "peak", "flat", "valley",
                 "deep_valley", "demand_price", "capacity_price"]
PRICE_FIELDS = ["energy_price", "sharp", "peak", "flat", "valley", "deep_valley", "demand_price", "capacity_price"]


def validate_rows(rows):
    """Return list of failure dicts. rows: list of tariff_month dicts."""
    fails = []

    def f(r, check, detail):
        fails.append({"month": r["month"], "category": r["category"], "tariff_type": r["tariff_type"],
                      "voltage": r["voltage"], "check": check, "detail": detail})

    for r in rows:
        seq = [(k, r.get(k)) for k in ("sharp", "peak", "flat", "valley", "deep_valley") if r.get(k) is not None]
        for (ka, a), (kb, b) in zip(seq, seq[1:]):
            ok = a >= b if ka == "sharp" else a > b
            if not ok:
                f(r, "tou_order", f"{ka}={a} !{'>=' if ka=='sharp' else '>'} {kb}={b}")
        parts = [r.get(k) for k in ("purchase_price", "line_loss", "tdp", "system_op", "gov_fund")]
        if None in parts or r.get("energy_price") is None:
            f(r, "components_missing", f"parts={parts} energy={r.get('energy_price')}")
        else:
            s = round(sum(parts), 6)
            if abs(s - r["energy_price"]) > TOL:
                f(r, "components_sum", f"sum={s} energy={r['energy_price']} diff={round(s-r['energy_price'],6)}")
    # voltage monotonic within (month, category, tariff_type)
    groups = {}
    for r in rows:
        groups.setdefault((r["month"], r["category"], r["tariff_type"]), []).append(r)
    for g in groups.values():
        g = sorted(g, key=lambda x: x["voltage_rank"] or 0)
        for lo, hi in zip(g, g[1:]):
            for k in PRICE_FIELDS:
                if lo.get(k) is not None and hi.get(k) is not None and hi[k] > lo[k] + 1e-9:
                    f(hi, "voltage_monotonic", f"{k}: {hi['voltage']}={hi[k]} > {lo['voltage']}={lo[k]}")
    return fails


def write_validation_csv(path, fails):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["month", "category", "tariff_type", "voltage", "check", "detail"])
        w.writeheader()
        w.writerows(fails)
