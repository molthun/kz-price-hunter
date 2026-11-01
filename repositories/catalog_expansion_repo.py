"""Demand-driven category proposals and evidenced attributes. No user identifiers."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone


DDL = """
CREATE TABLE IF NOT EXISTS catalog_expansions (
 id TEXT PRIMARY KEY, query TEXT NOT NULL, city TEXT NOT NULL,
 plan_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
 demand INTEGER NOT NULL DEFAULT 1, attempts INTEGER NOT NULL DEFAULT 0,
 found INTEGER NOT NULL DEFAULT 0, scan_offset INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, checked_at TEXT,
 scan_total INTEGER NOT NULL DEFAULT 0, scan_text TEXT, failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS catalog_expansion_matches (
 expansion_id TEXT NOT NULL REFERENCES catalog_expansions(id) ON DELETE CASCADE,
 product_id TEXT NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
 PRIMARY KEY(expansion_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_catalog_expansion_status ON catalog_expansions(status, updated_at);
CREATE TABLE IF NOT EXISTS product_search_attributes (
 product_id TEXT NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
 key TEXT NOT NULL, value TEXT NOT NULL, evidence TEXT NOT NULL,
 source_title TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(product_id, key)
);
CREATE INDEX IF NOT EXISTS idx_product_search_attr ON product_search_attributes(key, value, product_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def ensure_progress_columns(conn):
    """Существующая таблица получает поля прогресса. Незавершённая проверка без них считала
    подходящим всё, что подошло к разделу, поэтому начинается заново."""
    known = {row[1] for row in conn.execute('PRAGMA table_info(catalog_expansions)')}
    if 'scan_total' in known:
        return
    conn.execute('ALTER TABLE catalog_expansions ADD COLUMN scan_total INTEGER NOT NULL DEFAULT 0')
    conn.execute('ALTER TABLE catalog_expansions ADD COLUMN scan_text TEXT')
    conn.execute('ALTER TABLE catalog_expansions ADD COLUMN failures INTEGER NOT NULL DEFAULT 0')
    conn.execute("UPDATE catalog_expansions SET scan_offset=0, found=0 WHERE status IN ('approved','checking')")
    conn.commit()


class CatalogExpansionRepository:
    def __init__(self, conn):
        self.conn = conn

    def propose(self, query, city, plan):
        import search_analytics as sa
        clean = sa.storable_text(query)
        if not clean:
            return None
        city = sa.canonical_city(city)
        ident = hashlib.sha256((clean + '\n' + city).encode()).hexdigest()[:24]
        stamp = now()
        self.conn.execute(
            """INSERT INTO catalog_expansions(id,query,city,plan_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            demand=demand+1, updated_at=excluded.updated_at,
            plan_json=CASE WHEN status='proposed' THEN excluded.plan_json ELSE plan_json END""",
            (ident, clean, city, json.dumps(plan, ensure_ascii=False), stamp, stamp))
        self.conn.commit()
        return self.get(ident)

    def get(self, ident):
        cur = self.conn.execute('SELECT * FROM catalog_expansions WHERE id=?', (ident,))
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(zip([c[0] for c in cur.description], row))
        result['plan'] = json.loads(result.pop('plan_json'))
        return result

    def list(self):
        return [self.get(r[0]) for r in self.conn.execute(
            'SELECT id FROM catalog_expansions ORDER BY updated_at DESC LIMIT 100').fetchall()]

    def review(self, ident, approve):
        row = self.get(ident)
        if not row:
            raise ValueError('Заявка не найдена')
        if row['status'] == 'checking':
            raise ValueError('Проверка уже идёт; дождитесь результата')
        # После сбоя проверка продолжается с места остановки, а не с начала.
        resume = approve and row['status'] == 'failed' and row['scan_offset'] > 0
        if approve:
            from domain.category import Category
            from repositories.category_repo import CategoryRepository
            repo = CategoryRepository(self.conn)
            plan = row['plan']
            cat = repo.get_by_id(plan['category_id'])
            if cat is None:
                cat = Category(id=plan['category_id'], name=plan['category_name'],
                               parent_id=plan.get('parent_id'), icon='📦')
            # Existing taxonomy identity/aliases are never overwritten by a model.
            cat.attributes_schema = {**cat.attributes_schema, **{
                f['key']: {'label': f['label'], 'type': f['type']}
                for f in plan.get('fields', [])}}
            repo.save(cat)
            from repositories.search_repo import SearchRepository
            SearchRepository._cat_cache = {}
            status = 'approved'
        else:
            status = 'rejected'
        if resume:
            self.conn.execute("UPDATE catalog_expansions SET status='approved',failures=0,message=?,updated_at=? WHERE id=?",
                              ('Проверка продолжается', now(), ident))
            self.conn.commit()
            return self.get(ident)
        self.conn.execute('DELETE FROM catalog_expansion_matches WHERE expansion_id=?', (ident,))
        self.conn.execute('UPDATE catalog_expansions SET status=?,attempts=0,found=0,scan_offset=0,scan_total=0,scan_text=NULL,failures=0,message=?,updated_at=? WHERE id=?',
                          (status, 'Запланирована проверка подключённых магазинов' if approve else 'Отклонено', now(), ident))
        self.conn.commit()
        return self.get(ident)

    def claim(self):
        # A crashed worker is retried after its bounded lease, not left RUNNING forever.
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.conn.execute("UPDATE catalog_expansions SET status='approved' WHERE status='checking' AND updated_at<?", (cutoff,))
        cur = self.conn.execute("""UPDATE catalog_expansions SET status='checking', attempts=attempts+1,updated_at=?
            WHERE id=(SELECT id FROM catalog_expansions WHERE status='approved'
            ORDER BY updated_at LIMIT 1) AND status='approved' RETURNING id""", (now(),))
        row = cur.fetchone()
        self.conn.commit()
        return self.get(row[0]) if row else None

    def finish(self, ident, status, found, message, scan_offset=0, failures=0):
        """Промежуточная порция (approved) не считается завершённой проверкой."""
        stamp = now()
        self.conn.execute("UPDATE catalog_expansions SET status=?,found=?,scan_offset=?,message=?,failures=?,updated_at=?,"
                          "checked_at=CASE WHEN ?='approved' THEN checked_at ELSE ? END WHERE id=?",
                          (status, found, scan_offset, message, failures, stamp, status, stamp, ident))
        self.conn.commit()

    def set_scope(self, ident, scan_text, scan_total):
        self.conn.execute('UPDATE catalog_expansions SET scan_text=?,scan_total=? WHERE id=?', (scan_text, scan_total, ident))
        self.conn.commit()

    def add_match(self, ident, product_id):
        self.conn.execute('INSERT OR IGNORE INTO catalog_expansion_matches VALUES(?,?)', (ident, product_id))
        self.conn.commit()

    def verified_products(self, query, category_id=None):
        """Exact confirmations plus the saved short scan name in the same category.

        A broad name can reuse confirmed narrower matches, without dropping city/price/attribute
        filters in SearchRepository. Numeric model queries and arbitrary category neighbours do not alias.
        """
        import search_analytics as sa
        clean = sa.storable_text(query)
        if not clean:
            return []
        short_name = bool(category_id and len(clean.split()) <= 2 and not re.search(r'\d', clean))
        rows = self.conn.execute(
            """SELECT DISTINCT m.product_id, e.query FROM catalog_expansion_matches m
               JOIN catalog_expansions e ON e.id=m.expansion_id
               WHERE (e.query=? OR (? AND e.scan_text=? AND json_extract(e.plan_json,'$.category_id')=?))
                 AND e.status!='rejected'""", (clean, short_name, clean, category_id)).fetchall()
        # A store alternative can be over-broad or name a compatible device. Reuse only a literal,
        # positive shortening of the original request, never "tripod for total station" -> "total station".
        relations = {'не', 'нет', 'без', 'кроме', 'для', 'вместо', 'not', 'without', 'for'}
        words = set(clean.split())
        return list(dict.fromkeys(row[0] for row in rows if row[1] == clean or
            (words.issubset(set(row[1].split())) and not relations.intersection(row[1].split()))))

    def prune(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        self.conn.execute('DELETE FROM catalog_expansions WHERE updated_at<?', (cutoff,))
        self.conn.execute('''DELETE FROM catalog_expansions WHERE id IN
            (SELECT id FROM catalog_expansions ORDER BY updated_at DESC LIMIT -1 OFFSET 5000)
            AND status!='checking' ''')
        self.conn.execute('DELETE FROM product_search_attributes WHERE product_id NOT IN (SELECT id FROM canonical_products)')
        self.conn.execute('''DELETE FROM catalog_expansion_matches WHERE product_id NOT IN (SELECT id FROM canonical_products)
            OR expansion_id NOT IN (SELECT id FROM catalog_expansions)''')
        self.conn.commit()

    def classify(self, product_id, category_id, confidence):
        """Only reviewed extensions of an unknown/general category; never a cross-branch move."""
        from repositories.category_repo import CategoryRepository
        mapper = CategoryRepository(self.conn).get_mapper()
        target = mapper.get_by_id(category_id)
        row = self.conn.execute('SELECT category FROM canonical_products WHERE id=?', (product_id,)).fetchone()
        if not target or not row or not math.isfinite(confidence) or confidence < 0.95:
            return False
        current = mapper.map_category(row[0])
        if current and current.id == target.id:
            return True
        ancestors = {b['id'] for b in mapper.get_breadcrumbs(target.id)}
        if current and current.id not in ancestors:
            return False
        if self.conn.execute('SELECT 1 FROM category_ai_verdicts WHERE product_id=? AND applied=1', (product_id,)).fetchone():
            return False
        self.conn.execute('''INSERT INTO category_ai_verdicts VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(product_id) DO UPDATE SET old_category=excluded.old_category,
            new_category=excluded.new_category,confidence=excluded.confidence,model=excluded.model,
            applied=excluded.applied,decided_at=excluded.decided_at''',
            (product_id, row[0], target.name, confidence, 'local_search_reviewed', 1, now()))
        self.conn.execute('UPDATE canonical_products SET category=?,updated_at=? WHERE id=?',
                          (target.name, now(), product_id))
        self.conn.commit()
        from repositories.search_repo import SearchRepository
        SearchRepository._cat_cache = {}
        return True

    def save_attributes(self, product_id, title, values):
        """Only facts with a verbatim evidence span in the actual product title survive."""
        source_title = title
        self.conn.execute('DELETE FROM product_search_attributes WHERE product_id=? AND source_title!=?',
                          (product_id, source_title))
        for key, fact in values.items():
            # A fresh extraction can explicitly make a previously guessed field unknown.
            self.conn.execute('DELETE FROM product_search_attributes WHERE product_id=? AND key=?',
                              (product_id, key))
            if not isinstance(fact, dict):
                continue
            evidence = str(fact.get('evidence') or '').strip()
            value = fact.get('value')
            if not evidence or evidence.casefold() not in title.casefold() or not isinstance(value, (str, int, float, bool)):
                continue
            if isinstance(value, bool):
                value = 'Да' if value else 'Нет'
            value = str(value).strip()[:80]
            if key == 'season':
                # A marketing model name (Arctic, Ice...) is not explicit season evidence.
                text = evidence.casefold()
                expected = ('Всесезонные' if re.search(r'всесезон|all.?season', text) else
                            'Зимние' if re.search(r'зимн|winter', text) else
                            'Летние' if re.search(r'летн|summer', text) else None)
                if value != expected:
                    continue
            if key == 'studs':
                text = evidence.casefold()
                expected = ('Нет' if re.search(r'без\s+шип|нешип|non.?stud', text) else
                            'Да' if re.search(r'с\s+шип|шипован|studded', text) else None)
                if value != expected:
                    continue
            if value:
                self.conn.execute('''INSERT INTO product_search_attributes VALUES(?,?,?,?,?,?)
                    ON CONFLICT(product_id,key) DO UPDATE SET value=excluded.value,evidence=excluded.evidence,
                    source_title=excluded.source_title,updated_at=excluded.updated_at''',
                    (product_id, key, value, evidence[:160], source_title, now()))
        self.conn.commit()
