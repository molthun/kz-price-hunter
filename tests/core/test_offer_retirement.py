"""G01 (аудит 6.4.5): снятие и возврат товара доходят до V2-предложения, поиска и наблюдений.

Все сценарии идут рабочим путём записи: enqueue → захват → _execute_queue_item (порции, сверка
источника, производные эффекты) → ACK. Поиск — настоящий SearchCore по той же базе.
"""

from tests import test_support  # noqa: F401
import os
import time

import database
from domain.search_core import SearchCore
from repositories import SearchRepository
from tests.core.test_queue_contract import QueueContractBase, WATCH_USER, prod


class OfferRetirementTest(QueueContractBase):
    def setUp(self):
        super().setUp()
        self.tag = os.urandom(3).hex()  # своя строка товаров: база общая для всего прогона
        self.title = f"Retire {self.tag} Laptop"
        self.shop = f"retire-{self.tag}"
        database.upsert_telegram_user({"id": WATCH_USER, "first_name": "Retire"})
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_events")
            conn.execute("DELETE FROM watch_state")
            conn.execute("DELETE FROM watches")
            conn.commit()

    def pid(self, name):
        return f"{self.tag}-{name}"

    def item(self, name, price=100):
        return prod(self.pid(name), price, self.title)

    async def scan(self, names, url="https://example.invalid/cat", complete=True, fail=False):
        """Один обход категории: пачка товаров и сверка источника; fail — задача уходит в отказ."""
        qm = self.qm()
        admission = await qm.enqueue([self.item(n) for n in names], "Contract", {}, shop_key=self.shop,
                                     reconcile={"key": self.shop, "url": url, "complete": complete})
        self.assertTrue(admission)
        item = await qm.dequeue(timeout=0.05)
        self.assertEqual(item["task_id"], admission.task_id)
        if fail:
            self.assertEqual(await qm.nack(item, error="synthetic", max_retries=0), "dead_letter")
        else:
            self.assertTrue(await self.run_item(qm, item))
        time.sleep(0.01)  # следующий обход принят строго позже
        return admission.task_id

    def state(self, name):
        pid = self.pid(name)
        with database.get_connection() as conn:
            product = conn.execute("SELECT is_active FROM products WHERE id = ?", (pid,)).fetchone()[0]
            offer = conn.execute("SELECT is_active FROM offers WHERE id = ?", (f"off_{pid}",)).fetchone()[0]
        offers = database.watched_offers([pid])
        with database.get_connection() as conn:
            found = SearchCore(SearchRepository(conn)).search(self.title, limit=50)
        card = next((r for r in found.items if any(o.id == f"off_{pid}" for o in r.offers)), None)
        purchasable = bool(card and any(o.is_purchasable for o in card.offers if o.id == f"off_{pid}"))
        return {"product": product, "offer": offer, "watch_available": offers[0]["is_available"],
                "search_purchasable": purchasable}

    def assert_available(self, name, available):
        flag = 1 if available else 0
        self.assertEqual(self.state(name), {"product": flag, "offer": flag, "watch_available": flag,
                                            "search_purchasable": bool(available)})

    def watch(self, name, condition, **extra):
        return database.create_watch(WATCH_USER, {"kind": "product", "target": self.pid(name),
                                                  "condition": condition, "quiet_from": "", "quiet_to": "",
                                                  "cooldown_hours": 0, **extra})

    def events(self):
        return database.watch_events(WATCH_USER)

    async def test_full_scan_without_item_retires_it_everywhere_and_return_restores(self):
        await self.scan(["kept", "gone"])
        self.assert_available("gone", True)
        self.watch("gone", "back_in_stock")
        self.watch("gone", "target_price", threshold=1000)
        target_events = len(self.events())
        await self.scan(["kept"])
        self.assert_available("gone", False)
        self.assert_available("kept", True)
        self.assertEqual(len(self.events()), target_events, "снятый товар дал уведомление")
        await self.scan(["kept", "gone"])
        self.assert_available("gone", True)
        reasons = [e["reason"] for e in self.events()]
        self.assertIn("товар снова в продаже", reasons)

    async def test_item_confirmed_by_another_active_source_stays(self):
        await self.scan(["shared"], url="https://example.invalid/cat-a")
        await self.scan(["shared"], url="https://example.invalid/cat-b")
        await self.scan(["other"], url="https://example.invalid/cat-a")  # в категории A товара больше нет
        self.assert_available("shared", True)

    async def test_limited_and_failed_scans_do_not_retire(self):
        await self.scan(["kept", "maybe"])
        await self.scan(["kept"], complete=False)
        self.assert_available("maybe", True)
        await self.scan(["kept"], fail=True)
        self.assert_available("maybe", True)

    async def test_old_retry_after_newer_full_scan_does_not_retire(self):
        await self.scan(["kept", "late"])
        old = await self.scan(["kept"], fail=True)          # давний полный обход без товара — отказ
        await self.scan(["kept", "late"])                   # свежий полный обход товар подтвердил
        self.assertEqual(database.spool_resolve_failures("retry", [old]), 1)
        qm = self.qm()
        item = await qm.dequeue(timeout=0.05)
        self.assertEqual(item["task_id"], old)
        self.assertTrue(await self.run_item(qm, item))
        self.assert_available("late", True)

    async def test_independent_seller_channel_is_not_retired(self):
        await self.scan(["kept", "gone"])
        with database.get_connection() as conn:
            base = dict(conn.execute("SELECT * FROM offers WHERE id = ?", (f"off_{self.pid('gone')}",)).fetchone())
            social = dict(base, id=f"social_{self.tag}", external_sku=f"social-{self.tag}")
            cols = ",".join(social)
            conn.execute(f"INSERT INTO offers ({cols}) VALUES ({','.join('?' * len(social))})", list(social.values()))
            conn.commit()
        await self.scan(["kept"])
        with database.get_connection() as conn:
            row = conn.execute("SELECT is_active FROM offers WHERE id = ?", (f"social_{self.tag}",)).fetchone()
        self.assertEqual(row[0], 1, "снятие legacy-товара задело независимый канал продавца")

    async def test_startup_repairs_offers_retired_before_the_fix(self):
        await self.scan(["kept", "drift"])
        with database.get_connection() as conn:  # состояние баз до 6.4.6: снят только в products
            conn.execute("UPDATE products SET is_active = 0 WHERE id = ?", (self.pid("drift"),))
            conn.commit()
        self.assertEqual(self.state("drift")["offer"], 1)
        self.assertGreaterEqual(database.sync_retired_offers(), 1)
        self.assert_available("drift", False)
        self.assert_available("kept", True)
        self.assertEqual(database.sync_retired_offers(), 0, "починка не идемпотентна")
