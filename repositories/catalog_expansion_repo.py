"""Demand-driven category proposals and evidenced attributes. No user identifiers."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone


DDL = """
CREATE TABLE IF NOT EXISTS catalog_expansions (
 id TEXT PRIMARY KEY, query TEXT NOT NULL, city TEXT NOT NULL,
 plan_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
 demand INTEGER NOT NULL DEFAULT 1, attempts INTEGER NOT NULL DEFAULT 0,
 found INTEGER NOT NULL DEFAULT 0, scan_offset INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, checked_at TEXT
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
        self.conn.execute('UPDATE catalog_expansions SET status=?,attempts=0,found=0,scan_offset=0,message=?,updated_at=? WHERE id=?',
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

    def finish(self, ident, status, found, message, scan_offset=0):
        self.conn.execute('UPDATE catalog_expansions SET status=?,found=?,scan_offset=?,message=?,checked_at=?,updated_at=? WHERE id=?',
                          (status, found, scan_offset, message, now(), now(), ident))
        self.conn.commit()

    def prune(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        self.conn.execute('DELETE FROM catalog_expansions WHERE updated_at<?', (cutoff,))
        self.conn.execute('''DELETE FROM catalog_expansions WHERE id IN
            (SELECT id FROM catalog_expansions ORDER BY updated_at DESC LIMIT -1 OFFSET 5000)
            AND status!='checking' ''')
        self.conn.execute('DELETE FROM product_search_attributes WHERE product_id NOT IN (SELECT id FROM canonical_products)')
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
            if not isinstance(fact, dict):
                continue
            evidence = str(fact.get('evidence') or '').strip()
            value = fact.get('value')
            if not evidence or evidence.casefold() not in title.casefold() or not isinstance(value, (str, int, float, bool)):
                continue
            if isinstance(value, bool):
                value = 'Да' if value else 'Нет'
            value = str(value).strip()[:80]
            if value:
                self.conn.execute('''INSERT INTO product_search_attributes VALUES(?,?,?,?,?,?)
                    ON CONFLICT(product_id,key) DO UPDATE SET value=excluded.value,evidence=excluded.evidence,
                    source_title=excluded.source_title,updated_at=excluded.updated_at''',
                    (product_id, key, value, evidence[:160], source_title, now()))
        self.conn.commit()
