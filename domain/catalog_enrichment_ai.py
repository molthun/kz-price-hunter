"""
Catalog AI Enrichment Service (KZPH-G5-06).
Enriches canonical products with verified search attributes (RAM, storage, screen size,
color, CPU, load capacity, volume, etc.) using deterministic extraction and Local AI (Ollama).

Adheres to:
1. Deterministic rules first (fast, hermetic, zero cost).
2. Local AI fallback (qwen3-vl:8b-instruct) for unstructured descriptions.
3. Strict GPU/gaming safety (honors local_ai.mode and local_ai.gpu_state).
4. Validation & normalization against ATTRIBUTE_LABELS and normalize_attribute_value.
5. Dual persistence: writes to product_search_attributes (for facets) and
   canonical_products.attributes_json (for product card view).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from domain.attribute_indexing import (
    ATTRIBUTE_LABELS,
    extract_title_attributes,
    normalize_attribute_value,
)
import local_ai

logger = logging.getLogger("catalog_enrichment_ai")

# Default Ollama endpoint & model
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.28.25:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b-instruct")

DDL_ATTRIBUTES = """
CREATE TABLE IF NOT EXISTS product_search_attributes (
    product_id TEXT NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence TEXT NOT NULL,
    source_title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(product_id, key)
);
CREATE INDEX IF NOT EXISTS idx_product_search_attr ON product_search_attributes(key, value, product_id);
"""

# Key attribute schemas for focused extraction
FOCUSED_KEYS = [
    ("storage", "Встроенная память (напр. 128 ГБ, 256 ГБ, 512 ГБ, 1 ТБ)"),
    ("ram", "Оперативная память (напр. 8 ГБ, 16 ГБ, 32 ГБ)"),
    ("screen_size", "Диагональ экрана (напр. 6.1\", 15.6\", 27\", 55\")"),
    ("resolution", "Разрешение экрана (напр. 1920x1080, 2560x1440, 3840x2160)"),
    ("refresh_rate", "Частота обновления (напр. 60 Гц, 120 Гц, 144 Гц)"),
    ("color", "Цвет корпуса (напр. Черный, Белый, Серый, Синий, Серебристый)"),
    ("cpu", "Процессор (напр. Apple M2, Core i5-12400, Ryzen 7 7840HS)"),
    ("gpu", "Видеокарта (напр. RTX 4060, RX 7800 XT)"),
    ("vram", "Видеопамять (напр. 8 ГБ, 12 ГБ, 16 ГБ)"),
    ("battery", "Емкость аккумулятора (напр. 5000 мА·ч)"),
    ("weight", "Вес (напр. 1.2 кг, 190 г)"),
    ("total_volume", "Объем холодильника (напр. 340 л)"),
    ("refrigeration_defrost", "Разморозка (No Frost или Капельная)"),
    ("load_capacity", "Загрузка стиральной машины (напр. 7 кг)"),
    ("spin_speed", "Скорость отжима (напр. 1200 об/мин)"),
    ("energy_class", "Класс энергопотребления (A, A+, A++, A+++)"),
    ("cleaning_type", "Тип уборки пылесоса (Сухая, Влажная, Сухая и влажная)"),
    ("power", "Мощность (напр. 2000 Вт)"),
    ("type", "Тип устройства (напр. Робот-пылесос, Вертикальный)"),
    ("diaper_type", "Тип подгузников (Подгузники или Подгузники-трусики)"),
    ("diaper_size", "Размер подгузников (1, 2, 3, 4, 5, 6)"),
    ("pieces_count", "Количество в упаковке (напр. 52 шт.)"),
    ("artist", "Исполнитель/группа музыкального издания"),
    ("format", "Формат издания (LP, 2LP, CD)"),
]


def can_use_local_ai() -> Tuple[bool, str]:
    """Проверяет, разрешено ли использовать локальный ИИ (GPU) прямо сейчас."""
    m = local_ai.mode()
    if not m.get("on", True):
        return False, f"Локальный ИИ выключен: {m.get('label', '')}"

    g = local_ai.gpu_state()
    if g.get("busy"):
        return False, f"GPU занят: {g.get('reason', '')}"

    return True, "OK"


def extract_attributes_from_rules(title: str, description: str = "") -> Dict[str, str]:
    """Детерминированное извлечение характеристик из названия и текста описания."""
    attrs = extract_title_attributes(title or "")

    desc = (description or "").strip()
    if not desc:
        return attrs

    desc_lower = desc.casefold()

    # Если в названии не было цвета, ищем в описании
    if "color" not in attrs:
        from domain.attribute_indexing import COLOR_PATTERNS
        for pattern_str, color_name in COLOR_PATTERNS:
            clean_pat = re.sub(r'^\(\?[a-zA-Z]+\)', '', pattern_str)
            m_col = re.search(r'(?:цвет|color)[\s:]+' + clean_pat, desc, re.IGNORECASE)
            if not m_col:
                m_col = re.search(pattern_str, desc)
            if m_col:
                attrs["color"] = color_name
                break

    # Память и ОЗУ из описания, если не найдено в названии
    if "ram" not in attrs:
        m_ram = re.search(r'(?i)(?:оперативная\s+память|озу|ram)[\s:]+(\d+)\s*(?:gb|гб)\b', desc)
        if m_ram:
            norm_ram = normalize_attribute_value("ram", m_ram.group(1))
            if norm_ram:
                attrs["ram"] = norm_ram

    if "storage" not in attrs:
        m_st = re.search(r'(?i)(?:встроенная\s+память|накопитель|память|rom|ssd)[\s:]+(\d+)\s*(?:gb|гб|tb|тб)\b', desc)
        if m_st:
            norm_st = normalize_attribute_value("storage", m_st.group(0))
            if norm_st:
                attrs["storage"] = norm_st

    # Диагональ экрана
    if "screen_size" not in attrs:
        m_diag = re.search(r'(?i)(?:диагональ(?:\s+экрана)?|экран)[\s:]+(\d+(?:[.,]\d+)?)\s*(?:дюйм|"|\'\')?', desc)
        if m_diag:
            norm_ss = normalize_attribute_value("screen_size", m_diag.group(1))
            if norm_ss:
                attrs["screen_size"] = norm_ss

    # Холодильники: объем и No Frost
    if "total_volume" not in attrs and ("объем" in desc_lower or "объём" in desc_lower):
        m_vol = re.search(r'(?:общий\s+объ[её]м\w*|объ[её]м\w*|вместимост\w*)[\s:]+(\d+(?:[.,]\d+)?)\s*(?:л|литр)?', desc, re.IGNORECASE)
        if m_vol:
            norm_vol = normalize_attribute_value("total_volume", m_vol.group(1))
            if norm_vol:
                attrs["total_volume"] = norm_vol

    if "refrigeration_defrost" not in attrs:
        if "no frost" in desc_lower or "total no frost" in desc_lower or "но фрост" in desc_lower:
            attrs["refrigeration_defrost"] = "No Frost"
        elif "капельн" in desc_lower:
            attrs["refrigeration_defrost"] = "Капельная"

    # Стиральные машины: загрузка и отжим
    if "load_capacity" not in attrs:
        m_lc = re.search(r'(?i)(?:загрузка|вместимость)[\s:]+(\d+(?:[.,]\d+)?)\s*(?:кг)?', desc)
        if m_lc:
            norm_lc = normalize_attribute_value("load_capacity", m_lc.group(1))
            if norm_lc:
                attrs["load_capacity"] = norm_lc

    if "spin_speed" not in attrs:
        m_sp = re.search(r'(?i)(?:скорость\s+отжима|отжим)[\s:]+(\d{3,4})\s*(?:об/?мин|об)?', desc)
        if m_sp:
            norm_sp = normalize_attribute_value("spin_speed", m_sp.group(1))
            if norm_sp:
                attrs["spin_speed"] = norm_sp

    # Пылесосы: мощность и тип уборки
    if "power" not in attrs:
        m_pw = re.search(r'(?i)(?:потребляемая\s+мощность|мощность(?:\s+всасывания)?)[\s:]+(\d{3,4})\s*(?:вт|w)?', desc)
        if m_pw:
            norm_pw = normalize_attribute_value("power", m_pw.group(1))
            if norm_pw:
                attrs["power"] = norm_pw

    return attrs


def build_ai_prompt(title: str, description: str = "", category: str = "") -> str:
    """Формирует строгий промпт для локальной модели извлечения фактов."""
    schema_lines = "\n".join(f"- {k}: {desc}" for k, desc in FOCUSED_KEYS)
    clean_desc = (description or "")[:1200].strip()

    return f"""Ты — аналитик каталога маркетплейса в Казахстане. Твоя задача — извлечь из текста товара ТОЛЬКО подтвержденные характеристики.
Строгие правила:
1. НЕ выдумывай значения! Если параметра нет в названии или описании, НЕ добавляй его.
2. Используй ТОЛЬКО разрешенные ключи из списка ниже.
3. Верни ТОЛЬКО валидный JSON со словарем {{"ключ": "значение"}}. Без рассуждений и комментариев.

Разрешенные ключи:
{schema_lines}

Товар: {title}
Раздел: {category or 'Общий'}
{f'Описание: {clean_desc}' if clean_desc else ''}

Ответь валидным JSON объектом (например, {{"storage": "256 ГБ", "color": "Черный"}} или {{}} если параметров нет):"""


def extract_attributes_with_local_ai(
    title: str,
    description: str = "",
    category: str = "",
    ollama_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
    timeout: float = 12.0,
) -> Dict[str, str]:
    """Извлекает подтвержденные характеристики через локальный Ollama API (qwen3-vl)."""
    allowed, reason = can_use_local_ai()
    if not allowed:
        logger.debug(f"Skipping Local AI attribute extraction: {reason}")
        return {}

    url = (ollama_url or DEFAULT_OLLAMA_URL).rstrip("/")
    model = ollama_model or DEFAULT_OLLAMA_MODEL
    prompt = build_ai_prompt(title, description=description, category=category)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 150},
    }

    try:
        req = urllib.request.Request(
            f"{url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                text = data.get("response", "").strip()

                # Извлекаем JSON из ответа
                match = re.search(r"\{.*?\}", text, re.DOTALL)
                if not match:
                    return {}

                parsed = json.loads(match.group(0))
                if not isinstance(parsed, dict):
                    return {}

                validated: Dict[str, str] = {}
                for k, raw_v in parsed.items():
                    if not raw_v or k not in ATTRIBUTE_LABELS:
                        continue
                    norm_val = normalize_attribute_value(k, raw_v)
                    if norm_val:
                        validated[k] = norm_val

                return validated
    except Exception as ex:
        logger.warning(f"Local AI attribute extraction failed for '{title[:50]}': {ex}")

    return {}


def enrich_canonical_product(
    conn,
    product_id: str,
    use_ai_if_needed: bool = True,
    force: bool = False,
    ollama_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Обогащает канонический товар характеристиками:
    1. Правила (название + описание)
    2. Если атрибутов мало/нет — локальный AI (Ollama)
    3. Сохраняет в product_search_attributes и canonical_products.attributes_json
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, category, description, attributes_json FROM canonical_products WHERE id = ?",
        (product_id,),
    )
    row = cur.fetchone()
    if not row:
        return {"ok": False, "error": f"Product {product_id} not found"}

    pid, title, cat, desc, attrs_raw = row
    title = title or ""
    cat = cat or ""
    desc = desc or ""

    existing_attrs: Dict[str, Any] = {}
    if attrs_raw:
        try:
            existing_attrs = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
        except Exception:
            existing_attrs = {}

    if existing_attrs and not force:
        return {
            "ok": True,
            "product_id": pid,
            "attributes": existing_attrs,
            "source": "already_enriched",
            "count": len(existing_attrs),
        }

    # 1. Извлечение правилами
    rule_attrs = extract_attributes_from_rules(title, desc)
    final_attrs = dict(rule_attrs)
    source_used = "rule"

    # 2. Если атрибутов не найдено или мало, подключаем Local AI
    if len(final_attrs) < 2 and use_ai_if_needed:
        ai_attrs = extract_attributes_with_local_ai(
            title,
            description=desc,
            category=cat,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
        )
        if ai_attrs:
            # Правила имеют высший приоритет над AI
            for k, v in ai_attrs.items():
                if k not in final_attrs:
                    final_attrs[k] = v
            source_used = "merged_ai" if rule_attrs else "local_ai"

    if not final_attrs:
        return {
            "ok": True,
            "product_id": pid,
            "attributes": {},
            "source": "empty",
            "count": 0,
        }

    now_iso = datetime.now(timezone.utc).isoformat()

    # 3. Сохраняем в product_search_attributes для фасетов поиска
    for k, v in final_attrs.items():
        evidence_str = (
            "Правила каталога (название/описание)"
            if k in rule_attrs
            else "Локальный AI (qwen3-vl по описанию товара)"
        )
        conn.execute(
            """
            INSERT INTO product_search_attributes (product_id, key, value, evidence, source_title, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, key) DO UPDATE SET
                value=excluded.value,
                evidence=excluded.evidence,
                source_title=excluded.source_title,
                updated_at=excluded.updated_at
            """,
            (pid, k, v, evidence_str, title, now_iso),
        )

    # 4. Формируем красивый словарь с русскими названиями для attributes_json карточки
    display_attrs: Dict[str, str] = {}
    for k, v in final_attrs.items():
        label = ATTRIBUTE_LABELS.get(k, k.replace("_", " ").capitalize())
        display_attrs[label] = v

    conn.execute(
        "UPDATE canonical_products SET attributes_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(display_attrs, ensure_ascii=False), now_iso, pid),
    )

    return {
        "ok": True,
        "product_id": pid,
        "attributes": display_attrs,
        "source": source_used,
        "count": len(display_attrs),
    }


def batch_enrich_catalog(
    conn,
    limit: int = 50,
    category: Optional[str] = None,
    use_ai: bool = True,
    pause_between_ai_calls: float = 0.3,
    force: bool = False,
    ollama_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Пакетное обогащение товаров каталога с паузами и контролем нагрузки."""
    if hasattr(conn, "executescript"):
        conn.executescript(DDL_ATTRIBUTES)

    cur = conn.cursor()

    where_clauses = []
    params: List[Any] = []

    if not force:
        where_clauses.append("(attributes_json IS NULL OR attributes_json = '{}' OR attributes_json = '')")

    if category and category != "Все":
        where_clauses.append("category = ?")
        params.append(category)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    sql = f"SELECT id FROM canonical_products {where_sql} ORDER BY id ASC LIMIT ?"
    params.append(limit)

    cur.execute(sql, params)
    rows = cur.fetchall()

    stats = {
        "total_candidates": len(rows),
        "processed": 0,
        "enriched": 0,
        "by_rule": 0,
        "by_ai": 0,
        "empty": 0,
    }

    for (pid,) in rows:
        res = enrich_canonical_product(
            conn,
            pid,
            use_ai_if_needed=use_ai,
            force=force,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
        )
        stats["processed"] += 1
        if res.get("ok") and res.get("count", 0) > 0:
            stats["enriched"] += 1
            src = res.get("source", "")
            if src == "rule":
                stats["by_rule"] += 1
            elif src in ("local_ai", "merged_ai"):
                stats["by_ai"] += 1
                if pause_between_ai_calls > 0:
                    time.sleep(pause_between_ai_calls)
        else:
            stats["empty"] += 1

    if hasattr(conn, "commit"):
        conn.commit()

    return stats
