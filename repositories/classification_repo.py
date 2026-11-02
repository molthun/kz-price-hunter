"""Versioned category tags, bounded durable review queue and decision history.
Titles, identities, offers and prices are never modified by this repository.
"""
from __future__ import annotations
import hashlib
from datetime import datetime,timezone
import json
import time

SIGNATURE = "json_array(p.title,p.canonical_key,p.brand,p.model,p.item_kind,coalesce((SELECT e.facts_hash FROM product_source_evidence e WHERE e.product_id=p.id),'[]'))"
DDL = """
CREATE TABLE IF NOT EXISTS product_category_tags (
 product_id TEXT PRIMARY KEY REFERENCES canonical_products(id) ON DELETE CASCADE,
 category_id TEXT NOT NULL REFERENCES categories(id), source TEXT NOT NULL,
 locked INTEGER NOT NULL DEFAULT 0, source_signature TEXT NOT NULL,
 model TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_category_tags_source ON product_category_tags(source);
CREATE INDEX IF NOT EXISTS idx_category_tags_locked ON product_category_tags(locked);
CREATE TABLE IF NOT EXISTS classification_jobs (
 product_id TEXT PRIMARY KEY REFERENCES canonical_products(id) ON DELETE CASCADE,
 source_signature TEXT NOT NULL, policy_version TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'queued', generation INTEGER NOT NULL DEFAULT 0,
 priority INTEGER NOT NULL DEFAULT 50, forced INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
 suggestion_json TEXT, message TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classification_ready ON classification_jobs(status,next_attempt,priority);
CREATE TABLE IF NOT EXISTS classification_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 product_id TEXT NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
 source_hash TEXT NOT NULL, event TEXT NOT NULL, old_category TEXT, category_id TEXT,
 model TEXT NOT NULL DEFAULT '', confidence REAL, reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_classification_history ON classification_history(product_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_classification_history_date ON classification_history(created_at);
CREATE TABLE IF NOT EXISTS classification_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
-- Compatibility writers may not overwrite an authoritative tag or its projection.
CREATE TRIGGER IF NOT EXISTS classification_category_guard BEFORE UPDATE OF category ON canonical_products
WHEN EXISTS(SELECT 1 FROM product_category_tags t JOIN categories c ON c.id=t.category_id
 WHERE t.product_id=OLD.id AND coalesce(NEW.category,'')!=c.name)
BEGIN SELECT RAISE(IGNORE); END;
CREATE TRIGGER IF NOT EXISTS classification_verdict_insert_guard BEFORE INSERT ON category_ai_verdicts
WHEN NEW.model!='classification_tag' AND EXISTS(SELECT 1 FROM product_category_tags WHERE product_id=NEW.product_id)
BEGIN SELECT RAISE(IGNORE); END;
CREATE TRIGGER IF NOT EXISTS classification_verdict_update_guard BEFORE UPDATE ON category_ai_verdicts
WHEN NEW.model!='classification_tag' AND EXISTS(SELECT 1 FROM product_category_tags WHERE product_id=NEW.product_id)
BEGIN SELECT RAISE(IGNORE); END;
"""


class Conflict(ValueError):
    pass


def source_hash(signature):
    return hashlib.sha256(signature.encode()).hexdigest()


class ClassificationRepository:
    def __init__(self, conn):
        self.conn=conn

    def _row(self, sql, params=()):
        cur=self.conn.execute(sql,params);row=cur.fetchone()
        return dict(zip([d[0] for d in cur.description],row)) if row else None

    def source(self, pid):
        return self._row(f'SELECT p.id,p.title,p.category,{SIGNATURE} AS signature FROM canonical_products p WHERE p.id=?',(pid,))

    def touch(self, ids, policy=None):
        """No commit: ingestion and its queue admission share the caller's transaction."""
        if not ids:return
        self.conn.execute(f"""INSERT INTO classification_jobs(product_id,source_signature,policy_version,status,priority,message,updated_at)
          SELECT p.id,{SIGNATURE},?,CASE WHEN t.locked=1 THEN 'review' ELSE 'queued' END,
                 CASE WHEN t.locked=1 THEN 90 WHEN v.model='feed' THEN 10 ELSE 50 END,
                 CASE WHEN t.locked=1 THEN 'Данные изменились после ручного решения' ELSE 'Ожидает проверки' END,?
          FROM canonical_products p LEFT JOIN product_category_tags t ON t.product_id=p.id
          LEFT JOIN category_ai_verdicts v ON v.product_id=p.id
          WHERE p.id IN (SELECT value FROM json_each(?))
          ON CONFLICT(product_id) DO UPDATE SET source_signature=excluded.source_signature,
            policy_version=CASE WHEN ? IS NULL THEN classification_jobs.policy_version ELSE excluded.policy_version END,
            status=excluded.status,generation=classification_jobs.generation+1,
            priority=max(classification_jobs.priority,80),forced=0,failures=0,next_attempt=0,lease_until=0,
            suggestion_json=NULL,message=excluded.message,updated_at=excluded.updated_at
          WHERE classification_jobs.source_signature!=excluded.source_signature OR
            (? IS NOT NULL AND classification_jobs.policy_version!=excluded.policy_version AND
             NOT EXISTS(SELECT 1 FROM product_category_tags t WHERE t.product_id=excluded.product_id AND t.locked=1))""",
          (policy or '',time.time(),json.dumps(list(dict.fromkeys(ids))),policy,policy))

    def discover(self, policy, limit=1000):
        row=self.conn.execute("SELECT value FROM classification_meta WHERE key='cursor'").fetchone()
        cursor=int(row[0]) if row else 0
        rows=self.conn.execute('SELECT rowid,id FROM canonical_products WHERE rowid>? ORDER BY rowid LIMIT ?',(cursor,limit)).fetchall()
        self.touch([r[1] for r in rows],policy)
        self.conn.execute("INSERT INTO classification_meta VALUES('cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(rows[-1][0] if rows else 0),))
        last=self.conn.execute("SELECT value FROM classification_meta WHERE key='pruned'").fetchone()
        if not last or time.time()-float(last[0])>3600:
            self.conn.execute('DELETE FROM classification_history WHERE created_at<?',(time.time()-90*86400,))
            self.conn.execute('DELETE FROM classification_history WHERE id < (SELECT id FROM classification_history ORDER BY id DESC LIMIT 1 OFFSET 199999)')
            self.conn.execute("INSERT INTO classification_meta VALUES('pruned',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(time.time()),))
        return len(rows)

    def claim(self, policy):
        self.conn.execute('BEGIN IMMEDIATE')
        now=time.time()
        self.conn.execute("UPDATE classification_jobs SET status='queued' WHERE status='checking' AND lease_until<?",(now,))
        row=self.conn.execute("""UPDATE classification_jobs SET status='checking',generation=generation+1,
           attempts=attempts+1,policy_version=?,lease_until=?,updated_at=?
           WHERE product_id=(SELECT product_id FROM classification_jobs WHERE status='queued' AND next_attempt<=?
             ORDER BY priority DESC,updated_at,product_id LIMIT 1) AND status='queued' RETURNING product_id""",
           (policy,now+180,now,now)).fetchone()
        result=self.job(row[0]) if row else None
        self.conn.commit()
        return result

    def job(self, pid):
        return self._row('SELECT * FROM classification_jobs WHERE product_id=?',(pid,))

    def _history(self,pid,signature,event,old,category=None,model='',confidence=None,reason=''):
        cur=self.conn.execute('''INSERT INTO classification_history
            (product_id,source_hash,event,old_category,category_id,model,confidence,reason,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',(pid,source_hash(signature) if signature is not None else '',event,old,category,model,confidence,str(reason)[:240],time.time()))
        if cur.lastrowid>200000:
            self.conn.execute('DELETE FROM classification_history WHERE id<=?',(cur.lastrowid-200000,))
        self.conn.execute('DELETE FROM classification_history WHERE created_at<?',(time.time()-90*86400,))

    def _apply(self, product, category_id, source, model, reason, locked):
        row=self.conn.execute('SELECT name FROM categories WHERE id=? AND is_active=1',(category_id,)).fetchone()
        if not row:raise ValueError('Выберите существующий активный раздел')
        if not self.conn.execute('SELECT 1 FROM product_category_tags WHERE product_id=?',(product['id'],)).fetchone():
            legacy=self._row('SELECT * FROM category_ai_verdicts WHERE product_id=?',(product['id'],))
            if legacy:
                previous=self.conn.execute('SELECT id FROM categories WHERE name=? OR id=? LIMIT 1',(legacy['new_category'],legacy['new_category'])).fetchone()
                text='Прежнее назначение: '+str(legacy['new_category'] or 'не определено')+'; '+('применено' if legacy['applied'] else 'не применено')+'; дата: '+str(legacy['decided_at'])+'. Версия товара не сохранялась.'
                self._history(product['id'],None,'legacy',legacy['old_category'],previous[0] if previous else None,legacy['model'],legacy['confidence'],text)
        self.conn.execute('''INSERT INTO product_category_tags VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(product_id) DO UPDATE SET category_id=excluded.category_id,source=excluded.source,
           locked=excluded.locked,source_signature=excluded.source_signature,model=excluded.model,
           reason=excluded.reason,updated_at=excluded.updated_at''',
           (product['id'],category_id,source,int(locked),product['signature'],model,reason[:240],time.time()))
        self.conn.execute("""INSERT INTO category_ai_verdicts VALUES(?,?,?,?,?,1,?)
           ON CONFLICT(product_id) DO UPDATE SET old_category=excluded.old_category,new_category=excluded.new_category,
           confidence=excluded.confidence,model=excluded.model,applied=1,decided_at=excluded.decided_at""",
           (product['id'],product['category'],row[0],None,'classification_tag',datetime.now(timezone.utc).isoformat()))
        self.conn.execute('UPDATE canonical_products SET category=? WHERE id=?',(row[0],product['id']))
        from repositories.search_repo import SearchRepository
        SearchRepository._cat_cache={}

    def finish(self, request, decision, model, paused=False):
        self.conn.execute('BEGIN IMMEDIATE')
        pid=request['product_id'];job=self.job(pid);product=self.source(pid)
        if not job or not product or job['generation']!=request['generation'] or job['status']!='checking':
            return False
        if product['signature']!=request['source_signature']:
            self.touch([pid]);return False
        if decision is None:
            failures=job['failures']+(0 if paused else 1)
            delay=30 if paused else min(3600,30*2**min(failures-1,7))
            message='Проверка на паузе; очередь сохранена.' if paused else 'Помощник не завершил проверку. Повторим автоматически.'
            self.conn.execute("UPDATE classification_jobs SET status='queued',failures=?,next_attempt=?,lease_until=0,message=?,updated_at=? WHERE product_id=?",
                (failures,time.time()+delay,message,time.time(),pid))
            self._history(pid,product['signature'],'paused' if paused else 'retry',product['category'],model=model,reason=message)
            return 'retry'
        locked=self.conn.execute('SELECT locked FROM product_category_tags WHERE product_id=?',(pid,)).fetchone()
        apply=decision['apply'] and not(locked and locked[0])
        if apply:
            self._apply(product,decision['category_id'],'rule' if model=='rule' else 'ai',model,decision['reason'],False)
        state='done' if apply else 'review'
        message=decision['reason'] or 'Нужна проверка администратора'
        if locked and locked[0]:message='Ручная метка сохранена. Сравните её с предложением помощника.'
        self.conn.execute('UPDATE classification_jobs SET status=?,suggestion_json=?,message=?,failures=0,next_attempt=0,lease_until=0,updated_at=? WHERE product_id=?',
                          (state,json.dumps(decision,ensure_ascii=False),message,time.time(),pid))
        self._history(pid,product['signature'],'applied' if apply else 'review',product['category'],decision.get('category_id'),model,decision.get('confidence'),message)
        return 'applied' if apply else 'review'

    def edit_token(self,product):
        tag=self.conn.execute('SELECT category_id,source,locked,source_signature,updated_at FROM product_category_tags WHERE product_id=?',(product['id'],)).fetchone()
        return source_hash(product['signature']+json.dumps(list(tag) if tag else None,ensure_ascii=False))

    def act(self,pid,body,policy):
        if not self.conn.in_transaction:
            self.conn.execute('BEGIN IMMEDIATE')
        product=self.source(pid)
        if not product:raise ValueError('Товар не найден')
        self.touch([pid])
        job=self.job(pid)
        if body.get('edit_token')!=self.edit_token(product) or body.get('source_hash')!=source_hash(product['signature']):
            raise Conflict('Карточка обновилась. Обновите данные и проверьте решение ещё раз.')
        action=body.get('action');now=time.time()
        if action in ('assign','create'):
            category=body.get('category_id')
            if action=='create':
                from domain.category import Category
                from repositories.category_repo import CategoryRepository
                from local_search import clean_plan,catalog_context
                requested=body.get('new_category')
                if not isinstance(requested,dict):raise ValueError('Укажите новый раздел')
                name=str(requested.get('name') or '').strip()
                if not 2<=len(name)<=80:raise ValueError('Название раздела — от 2 до 80 символов')
                parent=requested.get('parent_id')
                category_repo=CategoryRepository(self.conn)
                if not isinstance(parent,str):raise ValueError('Выберите родительский раздел')
                parent_cat=category_repo.get_by_id(parent)
                if not parent_cat or not parent_cat.is_active:raise ValueError('Выберите активный родительский раздел')
                labels=requested.get('fields') or []
                if not isinstance(labels,list) or len(labels)>8 or any(not isinstance(v,str) for v in labels):raise ValueError('Не более восьми фильтров')
                prior=json.loads(job['suggestion_json'] or '{}')
                proposed={f['label']:f['key'] for f in prior.get('fields',[]) if isinstance(f,dict) and 'label' in f and 'key' in f}
                fields=[dict(key=proposed.get(label,'attr_'+hashlib.sha256(str(label).encode()).hexdigest()[:12]),label=label) for label in labels if isinstance(label,str) and label.strip()]
                plan=clean_plan(dict(category_name=name,category_id='new',parent_id=parent,fields=fields,confidence=1,attributes={}),'',catalog_context(self.conn))
                if not plan:raise ValueError('Проверьте название нового раздела')
                existing=category_repo.get_by_id(plan['category_id'])
                cat=existing or Category(id=plan['category_id'],name=plan['category_name'],parent_id=plan['parent_id'],icon='📦')
                cat.attributes_schema={**cat.attributes_schema,**{f['key']:{'label':f['label'],'type':'select'} for f in plan['fields']}}
                category_repo.save(cat);category=cat.id

            if not isinstance(category,str):raise ValueError('Выберите раздел')
            note=str(body.get('note') or 'Метка назначена администратором').strip()[:240]
            self._apply(product,category,'admin','admin',note,True)
            self.conn.execute("UPDATE classification_jobs SET status='done',generation=generation+1,forced=0,failures=0,next_attempt=0,lease_until=0,message=?,updated_at=? WHERE product_id=?",(note,now,pid))
            self._history(pid,product['signature'],'manual',product['category'],category,'admin',reason=note)
        elif action in ('retry','unlock'):
            if action=='retry' and job['forced'] and job['status'] in ('queued','checking') and job['failures']==0 and job['next_attempt']<=now:
                return self.detail(pid)
            if action=='unlock':
                self.conn.execute('UPDATE product_category_tags SET locked=0 WHERE product_id=?',(pid,))
            self.conn.execute("UPDATE classification_jobs SET status='queued',generation=generation+1,policy_version=?,forced=1,priority=100,failures=0,next_attempt=0,lease_until=0,message=?,updated_at=? WHERE product_id=?",
                              (policy,'Запланирована проверка локальным ИИ',now,pid))
            self._history(pid,product['signature'],action,product['category'],model='admin',reason='Запрос администратора')
        else:raise ValueError('Неизвестное действие')
        return self.detail(pid)

    def detail(self,pid):
        product=self.source(pid)
        if not product:raise ValueError('Товар не найден')
        self.touch([pid]);job=self.job(pid)
        product['edit_token']=self.edit_token(product)
        product.update(source_hash=source_hash(product.pop('signature')),job=job)
        product['job'].pop('source_signature',None)
        product['job']['suggestion']=json.loads(product['job'].pop('suggestion_json') or 'null')
        product['tag']=self._row('SELECT t.category_id,t.source,t.locked,t.model,t.reason,t.updated_at,c.name FROM product_category_tags t JOIN categories c ON c.id=t.category_id WHERE product_id=?',(pid,))
        if product['tag']:product['tag']['locked']=bool(product['tag']['locked'])
        cur=self.conn.execute('SELECT event,old_category,category_id,model,confidence,reason,created_at FROM classification_history WHERE product_id=? ORDER BY id DESC LIMIT 20',(pid,))
        product['history']=[dict(zip([d[0] for d in cur.description],r)) for r in cur.fetchall()]
        product['sources']=self.sources(pid)
        from product_evidence import stored
        product['evidence']=stored(self.conn,pid)
        return product

    def sources(self,pid):
        """Original product pages, including archived offers; no copied prose or photos."""
        from urllib.parse import urlsplit
        columns={r[1] for r in self.conn.execute('PRAGMA table_info(products)')}
        legacy={'id','url','shop','category'}<=columns
        extra="p.url,p.shop,p.category" if legacy else "NULL,NULL,NULL"
        join="LEFT JOIN products p ON p.id=substr(o.id,5) AND substr(o.id,1,4)='off_'" if legacy else ""
        rows=self.conn.execute(f"""SELECT o.url,s.name,o.is_active,o.observed_at,{extra}
          FROM offers o LEFT JOIN sellers s ON s.id=o.seller_id {join}
          WHERE o.product_id=? ORDER BY o.is_active DESC,o.observed_at DESC,o.id""",(pid,))
        result=[];seen=set()
        for url,name,active,observed,old_url,shop,section in rows:
            # A legacy address may survive an empty/invalid V2 address.
            for candidate in (url,old_url):
                try:
                    parsed=urlsplit((candidate or '').strip())
                    if parsed.scheme.lower() not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:continue
                    address=parsed._replace(fragment='').geturl()
                    if address not in seen:
                        result.append(dict(name=name or shop or parsed.hostname,url=address,active=bool(active),
                                           section=section or '',observed_at=observed,host=parsed.hostname))
                        seen.add(address)
                    break
                except (TypeError,ValueError):continue
        return result

    def listing(self,state='review',query='',offset=0,limit=25):
        clauses=[];params=[]
        if query:
            clauses.append('(p.title LIKE ? OR p.id=?)');params.extend(['%'+query+'%',query])
        if state=='manual':clauses.append('t.locked=1')
        elif state!='all':clauses.append('j.status=?');params.append(state)
        where=' WHERE '+ ' AND '.join(clauses) if clauses else ''
        base=' FROM canonical_products p LEFT JOIN classification_jobs j ON j.product_id=p.id LEFT JOIN product_category_tags t ON t.product_id=p.id'
        total=self.conn.execute('SELECT count(*)'+base+where,params).fetchone()[0]
        cur=self.conn.execute("SELECT p.id,p.title,p.category,coalesce(j.status,'new') AS status,j.message,t.category_id,t.locked "+base+where+' ORDER BY coalesce(j.priority,0) DESC,coalesce(j.updated_at,0) DESC,p.id LIMIT ? OFFSET ?',params+[limit,offset])
        items=[dict(zip([d[0] for d in cur.description],r)) for r in cur.fetchall()]
        summary={r[0]:r[1] for r in self.conn.execute('SELECT status,count(*) FROM classification_jobs GROUP BY status')}
        summary['total_products']=self.conn.execute('SELECT count(*) FROM canonical_products').fetchone()[0]
        summary['not_scanned']=summary['total_products']-sum(v for k,v in summary.items() if k!='total_products')
        summary['tagged']=self.conn.execute('SELECT count(*) FROM product_category_tags').fetchone()[0]
        summary['ai']=self.conn.execute("SELECT count(*) FROM product_category_tags WHERE source='ai'").fetchone()[0]
        summary['rule']=self.conn.execute("SELECT count(*) FROM product_category_tags WHERE source='rule'").fetchone()[0]
        summary['manual']=self.conn.execute('SELECT count(*) FROM product_category_tags WHERE locked=1').fetchone()[0]
        return dict(items=items,total=total,offset=offset,limit=limit,summary=summary)
