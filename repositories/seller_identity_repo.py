"""
Repository for Seller Identities and Seller Graph operations (KZ Price Hunter 2.0).
Handles digital footprint lookups, review queues, and seller merges.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from domain.seller_identity import IdentityType, SellerIdentity, utc_now_iso
from repositories.base import BaseRepository

logger = logging.getLogger("kz_price_hunter.repositories.seller_identity")


class SellerIdentityRepository(BaseRepository):
    """Encapsulates database operations for Seller Identities."""

    def add_identity(self, identity: SellerIdentity) -> SellerIdentity:
        """Adds or updates a digital footprint identity for a seller."""
        sql = """
        INSERT INTO seller_identities (id, seller_id, identity_type, identity_value, confidence, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(identity_type, identity_value, seller_id) DO UPDATE SET
            confidence = MAX(seller_identities.confidence, excluded.confidence),
            source = COALESCE(excluded.source, seller_identities.source)
        """
        self.execute(
            sql,
            (
                identity.id,
                identity.seller_id,
                identity.identity_type,
                identity.identity_value,
                identity.confidence,
                identity.source,
                identity.created_at,
            ),
        )
        return identity

    def add_identity_values(
        self,
        seller_id: str,
        identity_type: str,
        identity_value: str,
        confidence: float = 1.0,
        source: Optional[str] = None,
    ) -> SellerIdentity:
        """Helper to create and insert SellerIdentity from raw values."""
        hash_id = hashlib.sha256(f"{seller_id}:{identity_type}:{identity_value}".encode("utf-8")).hexdigest()[:16]
        ident_id = f"ident_{hash_id}"
        ident = SellerIdentity(
            id=ident_id,
            seller_id=seller_id,
            identity_type=identity_type,
            identity_value=identity_value,
            confidence=confidence,
            source=source,
            created_at=utc_now_iso(),
        )
        return self.add_identity(ident)

    def get_identities_by_seller(self, seller_id: str) -> List[SellerIdentity]:
        """Returns all identity records for a given seller."""
        sql = "SELECT * FROM seller_identities WHERE seller_id = ? ORDER BY identity_type, identity_value"
        rows = self.fetchall(sql, (seller_id,))
        return [SellerIdentity.from_row(r) for r in rows]

    def find_sellers_by_identity(self, identity_type: str, identity_value: str) -> List[str]:
        """Finds all seller_ids matching the given identity key/value pair."""
        sql = """
        SELECT DISTINCT s.id
        FROM sellers s
        JOIN seller_identities si ON s.id = si.seller_id
        WHERE si.identity_type = ? AND si.identity_value = ? AND s.is_active = 1
        """
        rows = self.fetchall(sql, (identity_type, identity_value))
        return [r["id"] for r in rows]

    def add_to_review_queue(
        self,
        candidate_seller_id: str,
        matched_seller_id: str,
        reason: str,
        confidence: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Enqueues an ambiguous match for administrative or AI review."""
        now = utc_now_iso()
        queue_id = f"srq_{hashlib.sha256(f'{candidate_seller_id}:{matched_seller_id}:{now}'.encode()).hexdigest()[:12]}"
        sql = """
        INSERT INTO seller_review_queue (
            id, candidate_seller_id, matched_seller_id, reason, confidence, details_json, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """
        self.execute(
            sql,
            (
                queue_id,
                candidate_seller_id,
                matched_seller_id,
                reason,
                confidence,
                json.dumps(details or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        return queue_id

    def get_pending_reviews(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns pending review queue items."""
        sql = "SELECT * FROM seller_review_queue WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT ?"
        rows = self.fetchall(sql, (limit,))
        return [dict(r) for r in rows]

    def merge_sellers(self, source_seller_id: str, target_seller_id: str) -> bool:
        """
        Atomically merges source_seller_id into target_seller_id:
        1. Reparents channels from source to target (handling unique constraints).
        2. Reparents offers to target_seller_id.
        3. Copies identity footprints to target_seller_id.
        4. Deactivates source_seller_id (is_active = 0).
        """
        if source_seller_id == target_seller_id:
            return False

        src = self.fetchone("SELECT * FROM sellers WHERE id = ?", (source_seller_id,))
        tgt = self.fetchone("SELECT * FROM sellers WHERE id = ?", (target_seller_id,))
        if not src or not tgt:
            logger.warning("Cannot merge sellers: %s or %s not found", source_seller_id, target_seller_id)
            return False

        now = utc_now_iso()

        # Слияние обязано быть неделимым: наполовину слитый граф хуже неслитого, потому что
        # продавец остаётся активным, а часть его каналов уже уехала. SAVEPOINT работает и внутри
        # чужой транзакции, поэтому метод можно звать из любого места.
        self.execute("SAVEPOINT merge_sellers")
        try:
            self._merge_sellers_body(src, tgt, source_seller_id, target_seller_id, now)
        except Exception:
            self.execute("ROLLBACK TO SAVEPOINT merge_sellers")
            self.execute("RELEASE SAVEPOINT merge_sellers")
            logger.exception("Слияние продавцов %s -> %s отменено", source_seller_id, target_seller_id)
            raise
        self.execute("RELEASE SAVEPOINT merge_sellers")
        return True

    def _absorb_offer(self, loser_id: str, winner_id: str) -> None:
        """Переносит историю цен проигравшего предложения победителю и удаляет дубликат.

        История дороже самой строки предложения: она копится месяцами и при слиянии теряться
        не должна. Совпадающие моменты наблюдения отбрасываются (у победителя они уже есть).
        """
        self.execute(
            """INSERT OR IGNORE INTO offer_price_history (offer_id, price, old_price, observed_at)
               SELECT ?, price, old_price, observed_at FROM offer_price_history WHERE offer_id = ?""",
            (winner_id, loser_id),
        )
        self.execute("DELETE FROM offer_price_history WHERE offer_id = ?", (loser_id,))
        self.execute("DELETE FROM offers WHERE id = ?", (loser_id,))

    def _move_offers_to_channel(self, source_channel_id: str, target_channel_id: str,
                                target_seller_id: str, now: str) -> None:
        """Переносит предложения в существующий канал цели, разрешая совпадения по SKU.

        Один и тот же товар у обоих продавцов — типичная причина слияния, а не исключение.
        Уникальный индекс (channel_id, external_sku) не даёт перенести такой оффер «в лоб»,
        поэтому побеждает более свежее наблюдение, а история проигравшего переходит победителю.
        """
        for offer in self.fetchall("SELECT * FROM offers WHERE channel_id = ?", (source_channel_id,)):
            twin = self.fetchone(
                "SELECT * FROM offers WHERE channel_id = ? AND external_sku = ?",
                (target_channel_id, offer["external_sku"]),
            )
            if not twin:
                self.execute(
                    "UPDATE offers SET channel_id = ?, seller_id = ?, updated_at = ? WHERE id = ?",
                    (target_channel_id, target_seller_id, now, offer["id"]),
                )
                continue

            if (offer["observed_at"] or "") > (twin["observed_at"] or ""):
                # Наблюдение источника свежее: переносим его значения в выжившую строку
                self.execute(
                    """UPDATE offers SET price = ?, old_price = ?, url = ?, availability = ?,
                           condition = ?, city = ?, observed_at = ?, is_active = ?, updated_at = ?
                       WHERE id = ?""",
                    (offer["price"], offer["old_price"], offer["url"], offer["availability"],
                     offer["condition"], offer["city"], offer["observed_at"], offer["is_active"],
                     now, twin["id"]),
                )
            self._absorb_offer(offer["id"], twin["id"])

    def _merge_sellers_body(self, src, tgt, source_seller_id: str, target_seller_id: str,
                            now: str) -> None:

        # 1. Reparent channels
        # For each channel of source seller:
        src_channels = self.fetchall("SELECT * FROM channels WHERE seller_id = ?", (source_seller_id,))
        for sc in src_channels:
            ctype = sc["channel_type"]
            ext_store = sc["external_store_id"]
            # Check if target already has this exact channel
            tc = self.fetchone(
                """
                SELECT id FROM channels
                WHERE seller_id = ? AND channel_type = ? AND COALESCE(external_store_id, '') = COALESCE(?, '')
                """,
                (target_seller_id, ctype, ext_store),
            )
            if tc:
                # Merge offers into existing target channel and remove source channel
                self._move_offers_to_channel(sc["id"], tc["id"], target_seller_id, now)
                self.execute("DELETE FROM channels WHERE id = ?", (sc["id"],))
            else:
                # No conflict: simply reparent channel to target_seller_id
                self.execute("UPDATE channels SET seller_id = ? WHERE id = ?", (target_seller_id, sc["id"]))

        # 2. Reparent remaining offers directly by seller_id
        self.execute(
            "UPDATE offers SET seller_id = ?, updated_at = ? WHERE seller_id = ?",
            (target_seller_id, now, source_seller_id),
        )

        # 3. Copy/migrate identity footprints
        identities = self.fetchall("SELECT * FROM seller_identities WHERE seller_id = ?", (source_seller_id,))
        for ident in identities:
            new_id = f"ident_{hashlib.sha256(f'{target_seller_id}:{ident['identity_type']}:{ident['identity_value']}'.encode()).hexdigest()[:16]}"
            self.execute(
                """
                INSERT INTO seller_identities (id, seller_id, identity_type, identity_value, confidence, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_type, identity_value, seller_id) DO NOTHING
                """,
                (
                    new_id,
                    target_seller_id,
                    ident["identity_type"],
                    ident["identity_value"],
                    ident["confidence"],
                    ident["source"],
                    ident["created_at"],
                ),
            )
        self.execute("DELETE FROM seller_identities WHERE seller_id = ?", (source_seller_id,))

        # 4. Update metadata of target seller with merge history
        tgt_meta = {}
        if tgt["metadata_json"]:
            try:
                tgt_meta = json.loads(tgt["metadata_json"])
            except Exception:
                tgt_meta = {}
        merged_list = tgt_meta.get("merged_from", [])
        if source_seller_id not in merged_list:
            merged_list.append(source_seller_id)
        tgt_meta["merged_from"] = merged_list

        self.execute(
            "UPDATE sellers SET metadata_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(tgt_meta, ensure_ascii=False), now, target_seller_id),
        )

        # 5. Deactivate source seller
        self.execute(
            "UPDATE sellers SET is_active = 0, updated_at = ? WHERE id = ?",
            (now, source_seller_id),
        )
