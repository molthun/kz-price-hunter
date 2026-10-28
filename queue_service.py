"""Очередь записи товаров и кэш аналитики KZ Price Hunter.

1. Журнал задач записи в SQLite (product_queue_spool): приём, захват с владельцем и сроком,
   подтверждение по токену; переживает kill -9 и рестарт (C01–C05, R01–R06).
2. Встроенный redis-server и лёгкий клиент RESP — только для кэша аналитики, не для задач.
3. Кэш в памяти (TTL), если Redis нет.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("kz_price_hunter.queue")

QUEUE_KEY = "kzph:products:queue"
STATS_CACHE_KEY_PREFIX = "kzph:stats:"
DEFAULT_REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
DEFAULT_REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
DEFAULT_COMMAND_TIMEOUT = float(os.getenv("REDIS_COMMAND_TIMEOUT", "0.2"))


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def start_redis_daemon_if_available(port: int = DEFAULT_REDIS_PORT) -> bool:
    """Запускает redis-server как локальный демон, если он установлен и ещё не запущен."""
    if _is_port_open("127.0.0.1", port):
        return True

    redis_bin = shutil.which("redis-server")
    if not redis_bin:
        return False

    try:
        cmd = [
            redis_bin,
            "--daemonize", "yes",
            "--port", str(port),
            "--bind", "127.0.0.1",
            "--maxmemory", "64mb",
            "--maxmemory-policy", "volatile-lru",  # B03: вытесняются только ключи кэша с TTL, очередь без TTL защищена
            "--save", "",
            "--appendonly", "no",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            for _ in range(10):
                if _is_port_open("127.0.0.1", port):
                    print(f"[Redis] ⚡ Встроенный redis-server успешно запущен на порту {port}")
                    return True
                time.sleep(0.1)
    except Exception as e:
        logger.warning("Не удалось запустить redis-server: %s", e)

    return False


# ---------------------------------------------------------------------------
# Минимальный асинхронный клиент Redis RESP
# ---------------------------------------------------------------------------

class MiniRedisClient:
    """Минимальный клиент RESP 2 для очередей и кэша без внешних библиотек."""

    def __init__(self, host: str = DEFAULT_REDIS_HOST, port: int = DEFAULT_REDIS_PORT):
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=1.0
            )
            return True
        except Exception:
            self._reader = None
            self._writer = None
            return False

    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def close(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _send_command(self, *args: str, timeout: Optional[float] = None) -> Any:
        to = timeout if timeout is not None else DEFAULT_COMMAND_TIMEOUT
        try:
            return await asyncio.wait_for(self._send_command_locked(*args), timeout=to)
        except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError) as e:
            await self.close()
            raise TimeoutError(f"Таймаут команды Redis {args[0] if args else ''} ({to}с)") from e

    async def _send_command_locked(self, *args: str) -> Any:
        async with self._lock:
            if not self.is_connected():
                if not await self.connect():
                    raise ConnectionError("Redis недоступен")

            # RESP Protocol format
            buf = [f"*{len(args)}\r\n".encode("utf-8")]
            for a in args:
                b = str(a).encode("utf-8")
                buf.append(f"${len(b)}\r\n".encode("utf-8"))
                buf.append(b + b"\r\n")

            assert self._writer is not None
            assert self._reader is not None
            try:
                self._writer.writelines(buf)
                await self._writer.drain()
                return await self._read_response()
            except Exception:
                await self.close()
                raise

    async def _read_response(self) -> Any:
        assert self._reader is not None
        line = await self._reader.readline()
        if not line:
            raise ConnectionError("Соединение с Redis разорвано")

        prefix = line[0:1]
        payload = line[1:-2]  # strip prefix and \r\n

        if prefix == b"+":
            return payload.decode("utf-8")
        elif prefix == b":":
            return int(payload)
        elif prefix == b"-":
            raise RuntimeError(f"Redis Error: {payload.decode('utf-8')}")
        elif prefix == b"$":
            length = int(payload)
            if length == -1:
                return None
            data = await self._reader.readexactly(length)
            await self._reader.readexactly(2)  # \r\n
            return data.decode("utf-8")
        elif prefix == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [await self._read_response() for _ in range(count)]
        else:
            raise ValueError(f"Неизвестный префикс RESP: {prefix}")

    async def ping(self, timeout: Optional[float] = None) -> bool:
        try:
            res = await self._send_command("PING", timeout=timeout)
            return res == "PONG"
        except Exception:
            return False

    async def rpush(self, key: str, value: str, timeout: Optional[float] = None) -> int:
        return await self._send_command("RPUSH", key, value, timeout=timeout)

    async def lpop(self, key: str, timeout: Optional[float] = None) -> Optional[str]:
        return await self._send_command("LPOP", key, timeout=timeout)

    async def llen(self, key: str, timeout: Optional[float] = None) -> int:
        try:
            return await self._send_command("LLEN", key, timeout=timeout)
        except Exception:
            return 0

    async def get(self, key: str, timeout: Optional[float] = None) -> Optional[str]:
        return await self._send_command("GET", key, timeout=timeout)

    async def setex(self, key: str, seconds: int, value: str, timeout: Optional[float] = None) -> bool:
        res = await self._send_command("SETEX", key, str(seconds), value, timeout=timeout)
        return res == "OK"

    async def delete(self, key: str, timeout: Optional[float] = None) -> int:
        return await self._send_command("DEL", key, timeout=timeout)


# ---------------------------------------------------------------------------
# Менеджер очереди товаров: журнал в SQLite (C01–C05, R01–R06)
# ---------------------------------------------------------------------------

# Владелец захватов этого процесса. Хост и PID позволяют после рестарта доказать, что прежний
# владелец мёртв (тот же хост, процесса нет или PID наш, а метка другая), и не ждать срока.
QUEUE_OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

# Срок захвата задачи; воркер продлевает его каждую треть срока (совпадает с database.SPOOL_CLAIM_SECONDS)
CLAIM_SECONDS = 60.0

# Все обращения очереди к SQLite идут через свой небольшой пул: ожидание блокировки базы
# занимает поток пула, а не цикл событий, и не отнимает потоки у веб-запросов (R01, C06).
_spool_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="kzph-spool")


async def _spool_db(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_spool_executor, functools.partial(func, *args))


def owner_is_dead(owner: str) -> bool:
    """Погиб ли владелец захвата. Чужой хост — неизвестно, значит жив (ждём срока захвата)."""
    try:
        host, pid_str, _nonce = owner.rsplit(":", 2)
        pid = int(pid_str)
    except (AttributeError, ValueError):
        return True  # строка без владельца или чужого формата — владельца не доказать
    if host != socket.gethostname() or owner == QUEUE_OWNER:
        return False
    if pid == os.getpid():
        return True  # наш PID с другой меткой — прежний экземпляр процесса (рестарт контейнера)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return False


class Admission:
    """Исход постановки задачи. Истинно, только если задача записана в журнал."""
    __slots__ = ("accepted", "reason", "task_id")

    def __init__(self, accepted: bool, reason: str, task_id: Optional[str] = None):
        self.accepted = accepted
        self.reason = reason
        self.task_id = task_id

    def __bool__(self) -> bool:
        return self.accepted

    def __repr__(self) -> str:
        return f"Admission({self.accepted}, {self.reason!r}, {self.task_id!r})"


class ProductQueueManager:
    """Очередь записи товаров поверх журнала SQLite. Redis используется только как кэш аналитики.

    Резервной очереди в памяти нет намеренно (R02): задача без строки в журнале не переживёт
    смерть процесса, поэтому отказ журнала — это явный отказ постановки, а не тихий приём.
    """

    def __init__(self, maxsize: int = 200, total_budget: int = 2000, owner: Optional[str] = None,
                 claim_seconds: Optional[float] = None):
        self.redis_client: Optional[MiniRedisClient] = None
        self.use_redis: bool = False
        self.is_worker_running: bool = False
        self._in_memory_cache: Dict[str, Tuple[float, Any]] = {}
        self._new_item_event: Optional[asyncio.Event] = None
        self.maxsize = maxsize
        self.total_budget = total_budget
        self.owner = owner or QUEUE_OWNER
        # Синглтон создаётся при импорте модуля, поэтому database здесь не импортируется
        self.claim_seconds = claim_seconds or CLAIM_SECONDS

    def _event(self) -> asyncio.Event:
        if self._new_item_event is None:
            self._new_item_event = asyncio.Event()
        return self._new_item_event

    def wake(self) -> None:
        """Будит воркер: в журнале появилась работа (новая задача или повтор отказа)."""
        self._event().set()

    async def initialize(self) -> None:
        """Освобождает захваты погибших владельцев и подключает Redis для кэша аналитики."""
        self._new_item_event = asyncio.Event()
        try:
            from database import spool_recover_orphans
            released = await _spool_db(spool_recover_orphans, owner_is_dead)
            if released > 0:
                print(f"[Queue] 🔄 Освобождено {released} захватов погибших воркеров — задачи будут дописаны")
                self._new_item_event.set()
        except Exception as e:
            logger.error("Восстановление захватов журнала не выполнено: %s", e)

        has_redis = start_redis_daemon_if_available()
        if has_redis or _is_port_open(DEFAULT_REDIS_HOST, DEFAULT_REDIS_PORT):
            client = MiniRedisClient(DEFAULT_REDIS_HOST, DEFAULT_REDIS_PORT)
            if await client.connect() and await client.ping():
                self.redis_client = client
                self.use_redis = True
                print("[Queue] 🚀 Аналитический кэш подключен к Redis")
                return

        self.use_redis = False
        print("[Queue] ℹ️ Кэш работает в памяти (In-Memory Bounded TTLCache)")

    def is_redis_active(self) -> bool:
        return self.use_redis and self.redis_client is not None and self.redis_client.is_connected()

    async def enqueue(
        self,
        prods: List[Dict[str, Any]],
        shop_name: str,
        candidate_settings: Any,
        shop_key: Optional[str] = None,
        reconcile: Optional[Dict[str, Any]] = None,
        scan_log: Optional[Dict[str, Any]] = None,
        lease_owner: Optional[str] = None,
        scan_id: Optional[str] = None,
        wait_seconds: float = 30.0,
    ) -> Admission:
        """Ставит пачку в журнал. При исчерпанном бюджете ждёт место до wait_seconds (C03, R02).

        lease_owner — владелец аренды планировщика: результаты обхода принимаются, только пока
        аренда у него, и проверка идёт в транзакции вставки (R04).
        """
        if not prods and not reconcile and not scan_log:
            return Admission(True, "empty")

        from database import spool_admit
        task_id = f"task_{uuid.uuid4().hex}"
        payload = {
            "prods": prods or [],
            "shop_name": shop_name,
            "candidate_settings": candidate_settings,
            "shop_key": shop_key,
            "reconcile": reconcile,
            "scan_log": scan_log,
            "enqueued_at": time.time(),
        }
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            try:
                status = await _spool_db(spool_admit, task_id, shop_key, shop_name, scan_id, payload,
                                         lease_owner, self.maxsize, self.total_budget)
            except Exception as e:
                logger.error("Журнал очереди недоступен, пачка не принята: %s", e)
                return Admission(False, f"spool_unavailable:{type(e).__name__}")
            if status == "accepted":
                self._event().set()
                return Admission(True, "accepted", task_id)
            if status == "fenced":
                return Admission(False, "fenced")
            if time.monotonic() >= deadline:
                logger.error("Журнал очереди переполнен (%s), пачка не принята", shop_key)
                return Admission(False, "full")
            await asyncio.sleep(0.5)

    async def dequeue(self, timeout: float = 0.5) -> Optional[Dict[str, Any]]:
        """Захватывает следующую задачу журнала; без задач ждёт сигнала или таймаута."""
        from database import spool_claim
        for attempt in range(2):
            try:
                item = await _spool_db(spool_claim, self.owner, self.claim_seconds)
            except Exception as e:
                logger.warning("Захват задачи журнала не выполнен: %s", e)
                await asyncio.sleep(timeout)
                return None
            if item or attempt:
                return item
            event = self._event()
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            event.clear()
        return None

    async def renew(self, item: Dict[str, Any]) -> bool:
        from database import spool_renew
        return await _spool_db(spool_renew, item["task_id"], item["claim_token"], self.claim_seconds)

    async def ack(self, item: Dict[str, Any]) -> bool:
        """Подтверждение вне цикла событий. False — захват уже не наш, задачу допишет владелец."""
        from database import spool_ack
        return await _spool_db(spool_ack, item["task_id"], item["claim_token"])

    async def nack(self, item: Dict[str, Any], error: Optional[str] = None, max_retries: int = 3) -> Optional[str]:
        """Возвращает задачу на повтор или в dead_letter; None — захват уже не наш."""
        from database import spool_nack
        status = await _spool_db(spool_nack, item["task_id"], item["claim_token"], error, max_retries)
        if status == "dead_letter":
            logger.error("Задача %s (%s) превысила лимит повторов и помещена в dead-letter",
                         item.get("task_id"), item.get("shop_name"))
        elif status == "pending":
            logger.warning("Повтор задачи %s (%s), попытка %s", item.get("task_id"), item.get("shop_name"),
                           item.get("attempts"))
        return status

    async def stats(self, shop_key: Optional[str] = None) -> Dict[str, int]:
        """Счётчики журнала; ошибка чтения пробрасывается — вызывающий решает fail-closed (R06)."""
        from database import spool_stats
        return await _spool_db(spool_stats, shop_key)

    async def qsize(self, shop_key: Optional[str] = None) -> int:
        stats = await self.stats(shop_key)
        return stats["pending"] + stats["processing"]

    async def wait_drained(self, task_ids: List[str], timeout: float = 60.0) -> bool:
        """Барьер обхода: True, только когда каждая принятая им задача подтверждена (R06).

        Смотрит на конкретные ID, а не на магазин: старый отказ не портит новый обход, а отказ
        этого обхода не скрыт чужими задачами. Нечитаемый журнал — не успех: ждём и по таймауту False.
        """
        ids = [t for t in task_ids if t]
        if not ids:
            return True
        from database import spool_task_states
        deadline = time.monotonic() + timeout
        while True:
            try:
                states = await _spool_db(spool_task_states, ids)
            except Exception as e:
                logger.warning("wait_drained: журнал не читается: %s", e)
                states = None
            if states is not None:
                if any(st not in ("pending", "processing") for st in states.values()):
                    logger.warning("wait_drained: задачи обхода не записаны: %s",
                                   sorted(set(states.values())))
                    return False
                if not states:
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.1)

    # Кэширование аналитических данных (/api/stats и др.)
    async def get_cached(self, key: str) -> Optional[Any]:
        if self.use_redis and self.redis_client:
            try:
                val = await self.redis_client.get(STATS_CACHE_KEY_PREFIX + key)
                if val:
                    return json.loads(val)
            except Exception:
                pass

        # In-memory fallback cache
        cached = self._in_memory_cache.get(key)
        if cached:
            exp, data = cached
            if time.time() < exp:
                return data
            else:
                self._in_memory_cache.pop(key, None)
        return None

    async def set_cached(self, key: str, data: Any, ttl_seconds: int = 30) -> None:
        if self.use_redis and self.redis_client:
            try:
                val_str = json.dumps(data, ensure_ascii=False)
                await self.redis_client.setex(STATS_CACHE_KEY_PREFIX + key, ttl_seconds, val_str)
                return
            except Exception:
                pass

        self._in_memory_cache[key] = (time.time() + ttl_seconds, data)

    async def clear_cache(self) -> None:
        self._in_memory_cache.clear()
        if self.use_redis and self.redis_client:
            pass


# Глобальный синглтон очереди
product_queue = ProductQueueManager()
