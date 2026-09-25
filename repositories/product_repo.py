"""
Canonical Product Repository for KZ Price Hunter 2.0.
"""

from __future__ import annotations

import hashlib
import json
from typing import List, Optional

from domain.models import CanonicalProduct, utc_now_iso
from repositories.base import BaseRepository


class ProductRepository(BaseRepository):
    """
    CRUD and lookup operations for canonical goods and models.
    """

    def save_or_update(self, product: CanonicalProduct) -> CanonicalProduct:
        sql = """
        INSERT INTO canonical_products (
            id, canonical_key, title, category, brand, model,
            description, image_url, attributes_json, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            canonical_key=excluded.canonical_key,
            title=excluded.title,
            category=excluded.category,
            brand=COALESCE(excluded.brand, canonical_products.brand),
            model=COALESCE(excluded.model, canonical_products.model),
            description=COALESCE(excluded.description, canonical_products.description),
            image_url=COALESCE(excluded.image_url, canonical_products.image_url),
            attributes_json=excluded.attributes_json,
            updated_at=excluded.updated_at
        """
        now = utc_now_iso()
        params = (
            product.id,
            product.canonical_key,
            product.title,
            product.category,
            product.brand,
            product.model,
            product.description,
            product.image_url,
            json.dumps(product.attributes, ensure_ascii=False),
            product.created_at or now,
            now,
        )
        self.execute(sql, params)
        product.updated_at = now
        return product

    def get_by_id(self, product_id: str) -> Optional[CanonicalProduct]:
        row = self.fetchone("SELECT * FROM canonical_products WHERE id = ?", (product_id,))
        return CanonicalProduct.from_row(row) if row else None

    def get_by_canonical_key(self, canonical_key: str) -> Optional[CanonicalProduct]:
        row = self.fetchone(
            "SELECT * FROM canonical_products WHERE canonical_key = ?",
            (canonical_key,),
        )
        return CanonicalProduct.from_row(row) if row else None

    def get_or_create_canonical(
        self,
        canonical_key: str,
        title: str,
        category: str,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        description: Optional[str] = None,
        image_url: Optional[str] = None,
    ) -> CanonicalProduct:
        existing = self.get_by_canonical_key(canonical_key)
        if existing:
            # Enrich missing fields if present
            changed = False
            if description and not existing.description:
                existing.description = description
                changed = True
            if image_url and not existing.image_url:
                existing.image_url = image_url
                changed = True
            if brand and not existing.brand:
                existing.brand = brand
                changed = True
            if model and not existing.model:
                existing.model = model
                changed = True
            if changed:
                self.save_or_update(existing)
            return existing

        # Stable deterministic product ID from canonical_key
        hash_id = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:16]
        product_id = f"prod_{hash_id}"

        product = CanonicalProduct(
            id=product_id,
            canonical_key=canonical_key,
            title=title,
            category=category,
            brand=brand,
            model=model,
            description=description,
            image_url=image_url,
        )
        return self.save_or_update(product)

    def list_products(
        self, category: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> List[CanonicalProduct]:
        sql = "SELECT * FROM canonical_products"
        params: list = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.fetchall(sql, params)
        return [CanonicalProduct.from_row(r) for r in rows]
