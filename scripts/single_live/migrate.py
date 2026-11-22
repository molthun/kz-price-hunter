"""Перенос в БД стенда того, чего у неё нет: ранняя ценовая история прода и пользователи.
Источник открывается только на чтение. Цель — копия БД стенда (или, в окне, сама БД при остановленном стенде).
Всё в одной транзакции: при любой ошибке цель не меняется.
Запуск: python3 migrate.py SOURCE_PROD.db TARGET.db [--dry-run]"""
import sqlite3, sys, json, time
src, dst = sys.argv[1], sys.argv[2]
dry = "--dry-run" in sys.argv
t0 = time.time()
c = sqlite3.connect(dst, isolation_level=None)
c.execute("PRAGMA busy_timeout=30000")
c.execute(f"ATTACH \"file:{src}?mode=ro\" AS p")
rep = {}
def cols(db, t): return [r[1] for r in c.execute(f"PRAGMA {db}.table_info({t})")]
c.execute("BEGIN IMMEDIATE")
try:
    rep["before"] = {t: c.execute(f"select count(*) from main.{t}").fetchone()[0] for t in ("offer_price_history", "price_observations", "users")}
    # 1. История предложений: только записи прода раньше первого наблюдения стенда; привязки — текущие у стенда
    c.execute("""CREATE TEMP TABLE first_seen AS
                 SELECT o.id AS offer_id, (SELECT MIN(observed_at) FROM main.offer_price_history h WHERE h.offer_id = o.id) AS first_at
                 FROM main.offers o WHERE o.id IN (SELECT id FROM p.offers)""")
    c.execute("CREATE INDEX temp.fs_i ON first_seen(offer_id)")
    cur = c.execute("""INSERT OR IGNORE INTO main.offer_price_history
          (offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city)
        SELECT h.offer_id, h.price, h.old_price, h.observed_at, o.product_id, o.seller_id, o.channel_id,
               COALESCE(h.condition, o.condition), COALESCE(h.city, o.city)
        FROM p.offer_price_history h
        JOIN first_seen f ON f.offer_id = h.offer_id
        JOIN main.offers o ON o.id = h.offer_id
        WHERE f.first_at IS NULL OR h.observed_at < f.first_at""")
    rep["offer_price_history_added"] = cur.rowcount
    # 2. История товаров (price_observations, ключ — legacy products.id): та же граница
    c.execute("""CREATE TEMP TABLE first_obs AS
                 SELECT pr.id AS product_id, (SELECT MIN(observed_at) FROM main.price_observations po WHERE po.product_id = pr.id) AS first_at
                 FROM main.products pr WHERE pr.id IN (SELECT id FROM p.products)""")
    c.execute("CREATE INDEX temp.fo_i ON first_obs(product_id)")
    cur = c.execute("""INSERT INTO main.price_observations (product_id, price, old_price_on_site, observed_at)
        SELECT po.product_id, po.price, po.old_price_on_site, po.observed_at
        FROM p.price_observations po JOIN first_obs f ON f.product_id = po.product_id
        WHERE f.first_at IS NULL OR po.observed_at < f.first_at""")
    rep["price_observations_added"] = cur.rowcount
    # 3. Сводные цены общих товаров учитывают раннюю историю прода
    cur = c.execute("""UPDATE main.products SET
          min_price = MIN(min_price, (SELECT pp.min_price FROM p.products pp WHERE pp.id = main.products.id)),
          max_price = MAX(max_price, (SELECT pp.max_price FROM p.products pp WHERE pp.id = main.products.id)),
          first_seen_price = CASE WHEN (SELECT pp.created_at FROM p.products pp WHERE pp.id = main.products.id) < created_at
                                  THEN (SELECT pp.first_seen_price FROM p.products pp WHERE pp.id = main.products.id) ELSE first_seen_price END,
          created_at = MIN(created_at, (SELECT pp.created_at FROM p.products pp WHERE pp.id = main.products.id))
        WHERE id IN (SELECT id FROM p.products)
          AND ((SELECT pp.min_price FROM p.products pp WHERE pp.id = main.products.id) < min_price
            OR (SELECT pp.max_price FROM p.products pp WHERE pp.id = main.products.id) > max_price
            OR (SELECT pp.created_at FROM p.products pp WHERE pp.id = main.products.id) < created_at)""")
    rep["products_aggregates_updated"] = cur.rowcount
    # 4. Пользователи: отсутствующие — целиком; у общих заполняются только пустые настройки
    uc = [x for x in cols("main", "users") if x in cols("p", "users")]
    ql = ", ".join(uc)
    cur = c.execute(f"INSERT OR IGNORE INTO main.users ({ql}) SELECT {ql} FROM p.users WHERE id NOT IN (SELECT id FROM main.users)")
    rep["users_added"] = cur.rowcount
    filled = {}
    for uid, ps, ss in c.execute("SELECT p.users.id, p.users.settings, main.users.settings FROM p.users JOIN main.users ON main.users.id = p.users.id").fetchall():
        try: pj, sj = json.loads(ps or "{}"), json.loads(ss or "{}")
        except Exception: continue
        keys = [k for k, v in pj.items() if v not in (None, "", [], {}) and sj.get(k) in (None, "", [], {})]
        if keys:
            for k in keys: sj[k] = pj[k]
            c.execute("UPDATE main.users SET settings = ? WHERE id = ?", (json.dumps(sj, ensure_ascii=False), uid))
            filled[f"user#{len(filled)+1}"] = sorted(keys)   # только имена ключей, без ID и значений
    rep["users_settings_filled"] = filled
    rep["after"] = {t: c.execute(f"select count(*) from main.{t}").fetchone()[0] for t in ("offer_price_history", "price_observations", "users")}
    c.execute("ROLLBACK" if dry else "COMMIT")
except Exception:
    c.execute("ROLLBACK"); raise
rep["dry_run"] = dry
rep["quick_check"] = c.execute("PRAGMA main.quick_check").fetchone()[0]
rep["secs"] = round(time.time() - t0)
print(json.dumps(rep, ensure_ascii=False, indent=1))
