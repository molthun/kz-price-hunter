"""
Seller Repository for KZ Price Hunter 2.0.
"""

from __future__ import annotations

import json
from typing import List, Optional

from domain.models import Seller, utc_now_iso
from repositories.base import BaseRepository


class SellerRepository(BaseRepository):
    """
    CRUD and lookup operations for merchants/sellers.
    """

    def save_or_update(self, seller: Seller) -> Seller:
        sql = """
        INSERT INTO sellers (
            id, slug, name, legal_name, bin, domain, phone, rating,
            is_active, metadata_json, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            slug=excluded.slug,
            name=excluded.name,
            legal_name=COALESCE(excluded.legal_name, sellers.legal_name),
            bin=COALESCE(excluded.bin, sellers.bin),
            domain=COALESCE(excluded.domain, sellers.domain),
            phone=COALESCE(excluded.phone, sellers.phone),
            rating=COALESCE(excluded.rating, sellers.rating),
            is_active=excluded.is_active,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """
        now = utc_now_iso()
        params = (
            seller.id,
            seller.slug,
            seller.name,
            seller.legal_name,
            seller.bin,
            seller.domain,
            seller.phone,
            seller.rating,
            1 if seller.is_active else 0,
            json.dumps(seller.metadata, ensure_ascii=False),
            seller.created_at or now,
            now,
        )
        self.execute(sql, params)
        seller.updated_at = now
        return seller

    def set_identity_fields(self, seller: Seller) -> None:
        """Записывает реквизиты как есть. save_or_update пустое значение не стирает, а редактору это нужно."""
        self.execute(
            "UPDATE sellers SET legal_name = ?, bin = ?, domain = ?, phone = ? WHERE id = ?",
            (seller.legal_name, seller.bin, seller.domain, seller.phone, seller.id),
        )

    def get_by_id(self, seller_id: str) -> Optional[Seller]:
        row = self.fetchone("SELECT * FROM sellers WHERE id = ?", (seller_id,))
        return Seller.from_row(row) if row else None

    def get_by_slug(self, slug: str) -> Optional[Seller]:
        row = self.fetchone("SELECT * FROM sellers WHERE slug = ?", (slug,))
        return Seller.from_row(row) if row else None

    def get_or_create(
        self,
        slug: str,
        name: str,
        domain: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Seller:
        existing = self.get_by_slug(slug)
        if existing:
            return existing
        seller_id = f"seller_{slug}"
        seller = Seller(
            id=seller_id,
            slug=slug,
            name=name,
            domain=domain,
            phone=phone,
            is_active=True,
        )
        return self.save_or_update(seller)

    def list_sellers(self, active_only: bool = True) -> List[Seller]:
        sql = "SELECT * FROM sellers"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name ASC"
        rows = self.fetchall(sql)
        return [Seller.from_row(r) for r in rows]

    def count(self, active_only: bool = True) -> int:
        """Returns the total number of sellers."""
        sql = "SELECT COUNT(*) as cnt FROM sellers"
        if active_only:
            sql += " WHERE is_active = 1"
        row = self.fetchone(sql)
        return int(row["cnt"]) if row else 0

    def get_by_id_or_slug(self, identifier: str) -> Optional[Seller]:
        """
        Lookup seller by ID or by slug.
        """
        if not identifier:
            return None
        cleaned = identifier.strip()
        seller = self.get_by_id(cleaned)
        if seller:
            return seller
        return self.get_by_slug(cleaned)

    def list_sellers_summary(
        self,
        city: Optional[str] = None,
        search: Optional[str] = None,
        channel_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict]:
        """
        Lists sellers along with their channel summary and active offer counts.
        """
        sql = """
        SELECT
            s.id,
            s.slug,
            s.name,
            s.legal_name,
            s.domain,
            s.rating,
            s.is_active,
            COUNT(DISTINCT o.id) as active_offers_count,
            COUNT(DISTINCT c.id) as channels_count
        FROM sellers s
        LEFT JOIN channels c ON s.id = c.seller_id AND c.is_active = 1
        LEFT JOIN offers o ON s.id = o.seller_id AND o.is_active = 1
        WHERE s.is_active = 1
        """
        params: list = []
        if search:
            sql += " AND (s.name LIKE ? OR s.slug LIKE ? OR s.domain LIKE ?)"
            s_param = f"%{search.strip()}%"
            params.extend([s_param, s_param, s_param])
        if channel_type:
            sql += " AND c.channel_type = ?"
            params.append(channel_type.strip().lower())
        if city and city != "Казахстан":
            sql += " AND (o.city = ? OR o.city = 'Казахстан')"
            params.append(city)

        sql += """
        GROUP BY s.id
        ORDER BY active_offers_count DESC, s.name ASC
        LIMIT ? OFFSET ?
        """
        params.extend([max(1, min(limit, 100)), max(0, offset)])
        rows = self.fetchall(sql, params)
        return [dict(r) for r in rows]

    def count_sellers_summary(
        self,
        city: Optional[str] = None,
        search: Optional[str] = None,
        channel_type: Optional[str] = None,
    ) -> int:
        sql = """
        SELECT COUNT(DISTINCT s.id) as count
        FROM sellers s
        LEFT JOIN channels c ON s.id = c.seller_id AND c.is_active = 1
        LEFT JOIN offers o ON s.id = o.seller_id AND o.is_active = 1
        WHERE s.is_active = 1
        """
        params: list = []
        if search:
            sql += " AND (s.name LIKE ? OR s.slug LIKE ? OR s.domain LIKE ?)"
            s_param = f"%{search.strip()}%"
            params.extend([s_param, s_param, s_param])
        if channel_type:
            sql += " AND c.channel_type = ?"
            params.append(channel_type.strip().lower())
        if city and city != "Казахстан":
            sql += " AND (o.city = ? OR o.city = 'Казахстан')"
            params.append(city)
        row = self.fetchone(sql, params)
        return row["count"] if row else 0

    def list_admin_sellers(
        self,
        search: Optional[str] = None,
        city: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict]:
        """
        Полный список продавцов для администратора (включая неактивных) со статистикой каналов и предложений.
        """
        sql = """
        SELECT
            s.id,
            s.slug,
            s.name,
            s.legal_name,
            s.bin,
            s.domain,
            s.phone,
            s.rating,
            s.is_active,
            s.metadata_json,
            s.created_at,
            s.updated_at,
            COUNT(DISTINCT c.id) as channels_count,
            COUNT(DISTINCT o.id) as offers_count
        FROM sellers s
        LEFT JOIN channels c ON s.id = c.seller_id AND c.is_active = 1
        LEFT JOIN offers o ON s.id = o.seller_id AND o.is_active = 1
        WHERE 1=1
        """
        params: list = []
        if is_active is not None:
            sql += " AND s.is_active = ?"
            params.append(1 if is_active else 0)
        if search:
            sql += " AND (s.name LIKE ? OR s.slug LIKE ? OR s.domain LIKE ? OR s.bin LIKE ? OR s.phone LIKE ?)"
            s_param = f"%{search.strip()}%"
            params.extend([s_param, s_param, s_param, s_param, s_param])
        if city and city != "Все" and city != "Казахстан":
            sql += " AND (json_extract(s.metadata_json, '$.city') = ? OR o.city = ?)"
            params.extend([city, city])

        sql += """
        GROUP BY s.id
        ORDER BY s.updated_at DESC, s.name ASC
        LIMIT ? OFFSET ?
        """
        params.extend([max(1, min(limit, 200)), max(0, offset)])
        rows = self.fetchall(sql, params)
        res = []
        for r in rows:
            d = dict(r)
            meta = {}
            if d.get("metadata_json"):
                try:
                    meta = json.loads(d["metadata_json"])
                except Exception:
                    meta = {}
            d["metadata"] = meta
            d["city"] = meta.get("city") or "Алматы"
            res.append(d)
        return res

    def count_admin_sellers(
        self,
        search: Optional[str] = None,
        city: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> int:
        sql = """
        SELECT COUNT(DISTINCT s.id) as count
        FROM sellers s
        LEFT JOIN offers o ON s.id = o.seller_id AND o.is_active = 1
        WHERE 1=1
        """
        params: list = []
        if is_active is not None:
            sql += " AND s.is_active = ?"
            params.append(1 if is_active else 0)
        if search:
            sql += " AND (s.name LIKE ? OR s.slug LIKE ? OR s.domain LIKE ? OR s.bin LIKE ? OR s.phone LIKE ?)"
            s_param = f"%{search.strip()}%"
            params.extend([s_param, s_param, s_param, s_param, s_param])
        if city and city != "Все" and city != "Казахстан":
            sql += " AND (json_extract(s.metadata_json, '$.city') = ? OR o.city = ?)"
            params.extend([city, city])
        row = self.fetchone(sql, params)
        return int(row["count"]) if row else 0
