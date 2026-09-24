"""Выбор модели в режиме «Авто»: берём живую модель из списка провайдера, а не имя, зашитое в код.

Зашитое имя уже подвело: `gemini-2.5-flash` перестал отвечать ключу владельца, а «Авто» продолжал
звать именно его — тысяча запросов подряд с ответом 404. Поэтому здесь нет ни одного конкретного
имени модели: есть правила, по которым из присланного провайдером списка выбирается подходящая.

Что считается подходящей для этого проекта:
- она должна уметь отвечать текстом (картинки, звук, видео, эмбеддинги и прочее отсеиваются);
- предпочитаем дешёвую и быструю (flash / mini / lite / nano): основная нагрузка — массовая
  нормализация названий, где важнее цена и скорость, чем глубина рассуждений;
- среди подходящих берём самую свежую по номеру версии, а при равенстве — детерминированно по имени,
  чтобы выбор был воспроизводим.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Tuple

# Не текстовые задачи и служебные варианты: такие модели не годятся, чем бы ни были хороши
NON_TEXT = ("embedding", "embed", "tts", "image", "vision-only", "audio", "live", "realtime",
            "transcribe", "whisper", "speech", "video", "veo", "imagen", "robotics",
            "computer-use", "moderation", "rerank", "guard")

# Признаки дешёвых и быстрых моделей — на них и рассчитан основной поток задач проекта
CHEAP = ("flash", "mini", "lite", "nano", "small", "turbo")

# Предварительные и экспериментальные версии в «Авто» не берём: они меняются и исчезают
UNSTABLE = ("preview", "exp", "experimental", "alpha", "beta", "latest-test")

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)?)")


def is_text_model(name: str) -> bool:
    lowered = str(name or "").lower()
    if not lowered:
        return False
    return not any(word in lowered for word in NON_TEXT)


def is_stable(name: str) -> bool:
    lowered = str(name or "").lower()
    return not any(word in lowered for word in UNSTABLE)


def is_cheap(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(word in lowered for word in CHEAP)


def version_of(name: str) -> float:
    """Номер версии из имени модели: gemini-3.8-flash → 3.8, gpt-4o-mini → 4. Нет числа — 0."""
    match = _VERSION_RE.search(str(name or ""))
    return float(match.group(1)) if match else 0.0


def _key(name: str) -> Tuple[int, float, str]:
    # Сначала дешёвые, затем по свежести версии, затем по имени — для воспроизводимости
    return (1 if is_cheap(name) else 0, version_of(name), str(name))


def candidates(names: Iterable[str]) -> List[str]:
    """Пригодные модели в порядке предпочтения."""
    usable = [str(n) for n in names if is_text_model(n) and is_stable(n)]
    return sorted(usable, key=_key, reverse=True)


def preferred(names: Sequence[str], fallback: Optional[str] = None) -> Optional[str]:
    """Модель для режима «Авто» или fallback, если выбрать не из чего.

    Если дешёвых моделей нет вовсе, берём самую свежую из оставшихся: лучше работать на дорогой,
    чем не работать совсем — но такой выбор виден в отчёте, а не происходит молча.
    """
    ranked = candidates(names)
    return ranked[0] if ranked else fallback
