"""Изолированный живой обход мультигородских источников (только чтение магазинов, временная DATA_DIR).
Печатает по строке JSON на категорию и итог по источникам. Рабочие БД не используются."""
import json, sys, time, threading
from collections import Counter
import config, source_failure
from scrapers.base import CityControl
from scrapers.kaspi import KaspiScraper
from scrapers.magnum import MagnumScraper
from scrapers.technodom import TechnodomScraper
from scrapers.sulpak import SulpakScraper
from scrapers.mechta import MechtaScraper
from scrapers.twelve_months import TwelveMonthsScraper
SOURCES = {
    "kaspi": (KaspiScraper, config.KASPI_CATEGORIES),
    "magnum": (MagnumScraper, config.MAGNUM_CATEGORIES),
    "technodom": (TechnodomScraper, config.TECHNODOM_CATEGORIES),
    "sulpak": (SulpakScraper, config.SULPAK_CATEGORIES),
    "mechta": (MechtaScraper, config.MECHTA_CATEGORIES),
    "twelve_months": (TwelveMonthsScraper, config.TWELVE_MONTHS_CATEGORIES),
}
lock = threading.Lock(); summary = {}
def emit(obj):
    with lock:
        print(json.dumps(obj, ensure_ascii=False), flush=True)
def run(key):
    cls, cats = SOURCES[key]
    t0 = time.monotonic(); agg = {"categories": len(cats), "complete": 0, "errors": 0, "codes": Counter(), "by_city": Counter()}
    for cat in cats:
        s = cls(); t = time.monotonic()
        try:
            res = s._scrape_sync(cat["name"], cat["url"], cat.get("max_pages"))
            info = source_failure.result_failure(res)
            row = {"shop": key, "category": cat["name"], "cities": list(s.cities), "sec": round(time.monotonic() - t, 1),
                   "items_by_city": dict(Counter(x.get("city") for x in res)), "complete": bool(res.complete),
                   "limited": bool(getattr(res, "limited", False)), "unparsed": int(getattr(res, "unparsed", 0) or 0),
                   "error": (res.error or "")[:200] or None, "code": (info or {}).get("error_code")}
        except Exception as e:
            row = {"shop": key, "category": cat["name"], "sec": round(time.monotonic() - t, 1), "exception": type(e).__name__}
        finally:
            try: s.close()
            except Exception: pass
        emit(row)
        agg["complete"] += 1 if row.get("complete") else 0
        agg["errors"] += 1 if (row.get("error") or row.get("exception")) else 0
        if row.get("code"): agg["codes"][row["code"]] += 1
        for c, n in (row.get("items_by_city") or {}).items(): agg["by_city"][c] += n
    agg["sec"] = round(time.monotonic() - t0, 1)
    agg["codes"] = dict(agg["codes"]); agg["by_city"] = dict(agg["by_city"])
    summary[key] = agg
keys = sys.argv[1:] or list(SOURCES)
threads = [threading.Thread(target=run, args=(k,)) for k in keys]
T = time.monotonic()
for th in threads: th.start()
for th in threads: th.join()
controls = {f"{k[0]}|{k[1]}<-{k[2]}": {"passed": v[1], "reason": v[2]} for k, v in CityControl._cache.items()}
emit({"SUMMARY": summary, "city_control": controls, "total_sec": round(time.monotonic() - T, 1)})
