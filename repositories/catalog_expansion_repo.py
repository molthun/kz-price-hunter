"""Demand-driven category proposals and evidenced attributes. No user identifiers."""
from __future__ import annotations

from contextlib import contextmanager
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
 scan_total INTEGER NOT NULL DEFAULT 0, scan_text TEXT, failures INTEGER NOT NULL DEFAULT 0,
 generation INTEGER NOT NULL DEFAULT 0, source_health TEXT NOT NULL DEFAULT '{}',
 source_queries TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS catalog_expansion_matches (
 expansion_id TEXT NOT NULL REFERENCES catalog_expansions(id) ON DELETE CASCADE,
 product_id TEXT NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
 source_identity TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(expansion_id, product_id)
);
CREATE TABLE IF NOT EXISTS local_search_intents (
 query TEXT PRIMARY KEY, plan_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_expansion_candidates (
 expansion_id TEXT NOT NULL REFERENCES catalog_expansions(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, product_id TEXT NOT NULL,
 PRIMARY KEY(expansion_id, position)
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
    additions = {'scan_total': 'INTEGER NOT NULL DEFAULT 0', 'scan_text': 'TEXT',
                 'failures': 'INTEGER NOT NULL DEFAULT 0', 'generation': 'INTEGER NOT NULL DEFAULT 0',
                 'source_health': "TEXT NOT NULL DEFAULT '{}'", 'source_queries': "TEXT NOT NULL DEFAULT '[]'"}
    for name, definition in additions.items():
        if name not in known:
            conn.execute(f'ALTER TABLE catalog_expansions ADD COLUMN {name} {definition}')
    matches = {r[1] for r in conn.execute('PRAGMA table_info(catalog_expansion_matches)')}
    if 'source_identity' not in matches:
        conn.execute("ALTER TABLE catalog_expansion_matches ADD COLUMN source_identity TEXT NOT NULL DEFAULT ''")
        # Old confirmations have no evidence version. Recheck instead of blessing today's identity.
        conn.execute("UPDATE catalog_expansions SET status='approved',scan_offset=0,scan_total=0,scan_text=NULL,found=0 WHERE status!='rejected' AND id IN (SELECT expansion_id FROM catalog_expansion_matches)")
    if 'generation' not in known or 'scan_total' not in known:
        conn.execute("UPDATE catalog_expansions SET scan_offset=0,scan_total=0,scan_text=NULL,found=0,status='approved' WHERE status IN ('approved','checking')")
    conn.commit()


def product_identity(conn, product_id):
    # Category is deliberately excluded: this very workflow refines it after classification.
    row = conn.execute('SELECT title,canonical_key,brand,model,item_kind FROM canonical_products WHERE id=?',
                       (product_id,)).fetchone()
    return hashlib.sha256(json.dumps(list(row), ensure_ascii=False).encode()).hexdigest() if row else ''



class CatalogExpansionRepository:
    def __init__(self, conn):
        self.conn = conn
        self._atomic = False

    def _commit(self):
        if not self._atomic:
            self.conn.commit()

    @contextmanager
    def lease(self, request):
        """Serialize each worker write with review/claim, checking the attempt generation."""
        self.conn.execute('BEGIN IMMEDIATE')
        self._atomic = True
        try:
            row = self.conn.execute("SELECT 1 FROM catalog_expansions WHERE id=? AND generation=? AND status='checking'",
                                    (request['id'], request['generation'])).fetchone()
            if row:
                self.conn.execute('UPDATE catalog_expansions SET updated_at=? WHERE id=?', (now(),request['id']))
            yield bool(row)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            self._atomic = False

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
            demand=CASE WHEN status='deleted' THEN 1 ELSE demand+1 END, updated_at=excluded.updated_at,
            created_at=CASE WHEN status='deleted' THEN excluded.created_at ELSE created_at END,
            generation=generation+CASE WHEN status='deleted' THEN 1 ELSE 0 END,
            plan_json=CASE WHEN status IN ('proposed','deleted') THEN excluded.plan_json ELSE plan_json END,
            status=CASE WHEN status='deleted' THEN 'proposed' ELSE status END""",
            (ident, clean, city, json.dumps(plan, ensure_ascii=False), stamp, stamp))
        self._commit()
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
            "SELECT id FROM catalog_expansions WHERE status!='deleted' ORDER BY updated_at DESC LIMIT 100").fetchall()]

    def delete(self,ident):
        """Hide/cancel request, retaining the generation fence against in-flight workers."""
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            row=self.conn.execute('SELECT status FROM catalog_expansions WHERE id=?',(ident,)).fetchone()
            if row and row[0]!='deleted':
                self.conn.execute('DELETE FROM catalog_expansion_candidates WHERE expansion_id=?',(ident,))
                self.conn.execute('DELETE FROM catalog_expansion_matches WHERE expansion_id=?',(ident,))
                self.conn.execute("UPDATE catalog_expansions SET status='deleted',generation=generation+1,checked_at=NULL,found=0,scan_offset=0,scan_total=0,scan_text=NULL,failures=0,attempts=0,source_health='{}',source_queries='[]',message='',updated_at=? WHERE id=?",(now(),ident))
            self.conn.commit()
            return bool(row)
        except BaseException:
            self.conn.rollback();raise

    def review(self, ident, approve):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            return self._review(ident, approve)
        except BaseException:
            self.conn.rollback()
            raise

    def _review(self, ident, approve):
        row = self.get(ident)
        if not row or row['status']=='deleted':
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
            self.conn.execute("UPDATE catalog_expansions SET status='approved',generation=generation+1,failures=0,message=?,updated_at=? WHERE id=?",
                              ('Проверка продолжается', now(), ident))
            self._commit()
            return self.get(ident)
        self.conn.execute('DELETE FROM catalog_expansion_candidates WHERE expansion_id=?', (ident,))
        self.conn.execute('DELETE FROM catalog_expansion_matches WHERE expansion_id=?', (ident,))
        self.conn.execute("UPDATE catalog_expansions SET status=?,generation=generation+1,source_health='{}',source_queries='[]',attempts=0,found=0,scan_offset=0,scan_total=0,scan_text=NULL,failures=0,message=?,updated_at=? WHERE id=?",
                          (status, 'Запланирована проверка подключённых магазинов' if approve else 'Отклонено', now(), ident))
        self._commit()
        return self.get(ident)

    def claim(self):
        # A crashed worker is retried after its bounded lease, not left RUNNING forever.
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.conn.execute("UPDATE catalog_expansions SET status='approved' WHERE status='checking' AND updated_at<?", (cutoff,))
        cur = self.conn.execute("""UPDATE catalog_expansions SET status='checking', generation=generation+1, attempts=attempts+1,updated_at=?
            WHERE id=(SELECT id FROM catalog_expansions WHERE status='approved'
            ORDER BY updated_at LIMIT 1) AND status='approved' RETURNING id""", (now(),))
        row = cur.fetchone()
        self._commit()
        return self.get(row[0]) if row else None

    def finish(self, ident, status, found, message, scan_offset=0, failures=0, generation=None):
        """Промежуточная порция (approved) не считается завершённой проверкой."""
        stamp = now()
        self.conn.execute("UPDATE catalog_expansions SET status=?,found=?,scan_offset=?,message=?,failures=?,updated_at=?,"
                          "checked_at=CASE WHEN ?='approved' THEN checked_at ELSE ? END WHERE id=? AND status NOT IN ('rejected','deleted') AND (? IS NULL OR (generation=? AND status='checking'))",
                          (status, found, scan_offset, message, failures, stamp, status, stamp, ident, generation, generation))
        self._commit()

    def set_scope(self, ident, scan_text, scan_total):
        self.conn.execute('UPDATE catalog_expansions SET scan_text=?,scan_total=? WHERE id=?', (scan_text, scan_total, ident))
        self._commit()

    def set_candidates(self, ident, text, ids):
        self.conn.execute('DELETE FROM catalog_expansion_candidates WHERE expansion_id=?', (ident,))
        self.conn.executemany('INSERT INTO catalog_expansion_candidates VALUES(?,?,?)',
                              [(ident, n, pid) for n, pid in enumerate(ids)])
        self.set_scope(ident, text, len(ids))

    def add_match(self, ident, product_id):
        self.conn.execute('INSERT OR REPLACE INTO catalog_expansion_matches(expansion_id,product_id,source_identity) VALUES(?,?,?)',
                          (ident, product_id, product_identity(self.conn, product_id)))
        self._commit()

    def cached_intent(self, query):
        import search_analytics as sa
        row = self.conn.execute('SELECT plan_json FROM local_search_intents WHERE query=?',
                                (sa.storable_text(query),)).fetchone()
        return json.loads(row[0]) if row else None

    def save_intent(self, query, plan):
        import search_analytics as sa
        clean = sa.storable_text(query)
        if clean:
            self.conn.execute('INSERT INTO local_search_intents VALUES(?,?,?) ON CONFLICT(query) DO UPDATE SET plan_json=excluded.plan_json,updated_at=excluded.updated_at',
                              (clean,json.dumps(plan,ensure_ascii=False),now()))
            self.conn.execute('DELETE FROM local_search_intents WHERE query IN (SELECT query FROM local_search_intents ORDER BY updated_at DESC LIMIT -1 OFFSET 5000)')
            cutoff=(datetime.now(timezone.utc)-timedelta(days=90)).isoformat()
            self.conn.execute('DELETE FROM local_search_intents WHERE updated_at<?',(cutoff,))
            self._commit()

    def saved_plan(self, query):
        import search_analytics as sa
        clean = sa.storable_text(query)
        row = self.conn.execute("SELECT plan_json FROM catalog_expansions WHERE query=? AND status!='rejected' ORDER BY updated_at DESC LIMIT 1",
                                (clean,)).fetchone()
        if row:
            return json.loads(row[0])
        if not clean or len(clean.split())>2 or re.search(r'\d',clean):
            return None
        relations = {'не','нет','без','кроме','для','вместо','not','without','for'}
        rows = self.conn.execute("SELECT plan_json,query FROM catalog_expansions WHERE (scan_text=? OR EXISTS(SELECT 1 FROM json_each(source_queries) WHERE value=?)) AND status!='rejected' ORDER BY updated_at DESC",(clean,clean)).fetchall()
        plans = [json.loads(raw) for raw,original in rows if set(clean.split()).issubset(set(original.split()))
                 and not relations.intersection(original.split())]
        return plans[0] if plans and len({p['category_id'] for p in plans})==1 else None

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
            """SELECT DISTINCT m.product_id, e.query, m.source_identity FROM catalog_expansion_matches m
               JOIN catalog_expansions e ON e.id=m.expansion_id
               WHERE (e.query=? OR (? AND (e.scan_text=? OR EXISTS(SELECT 1 FROM json_each(e.source_queries) WHERE value=?)) AND json_extract(e.plan_json,'$.category_id')=?))
                 AND e.status!='rejected'""", (clean, short_name, clean, clean, category_id)).fetchall()
        # A store alternative can be over-broad or name a compatible device. Reuse only a literal,
        # positive shortening of the original request, never "tripod for total station" -> "total station".
        relations = {'не', 'нет', 'без', 'кроме', 'для', 'вместо', 'not', 'without', 'for'}
        words = set(clean.split())
        result = []
        for pid, source_query, identity in rows:
            alias = words.issubset(set(source_query.split())) and not relations.intersection(source_query.split())
            if identity == product_identity(self.conn, pid) and (source_query == clean or alias):
                result.append(pid)
        return list(dict.fromkeys(result))

    def prune(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        self.conn.execute('DELETE FROM catalog_expansions WHERE updated_at<?', (cutoff,))
        self.conn.execute('''DELETE FROM catalog_expansions WHERE id IN
            (SELECT id FROM catalog_expansions ORDER BY updated_at DESC LIMIT -1 OFFSET 5000)
            AND status!='checking' ''')
        self.conn.execute('DELETE FROM product_search_attributes WHERE product_id NOT IN (SELECT id FROM canonical_products)')
        self.conn.execute('''DELETE FROM catalog_expansion_matches WHERE product_id NOT IN (SELECT id FROM canonical_products)
            OR expansion_id NOT IN (SELECT id FROM catalog_expansions)''')
        self._commit()

    def classify(self, product_id, category_id, confidence):
        """Only reviewed extensions of an unknown/general category; never a cross-branch move."""
        from repositories.category_repo import CategoryRepository
        tag=self.conn.execute('SELECT category_id FROM product_category_tags WHERE product_id=?',(product_id,)).fetchone()
        if tag and tag[0]!=category_id:
            return False
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
        self._commit()
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
            from domain.attribute_indexing import normalize_attribute_value
            norm_val = normalize_attribute_value(key, value)
            if not norm_val:
                continue
            value = norm_val
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
        self._commit()
