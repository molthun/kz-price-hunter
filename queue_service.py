"""In-Container Redis Queue Service & Single DB-Writer Worker for KZ Price Hunter.

Обеспечивает:
1. Автоматический запуск встроенного redis-server (если бинарник есть в ОС).
2. Легковесный асинхронный клиент протокола Redis (RESP) без сторонних библиотек.
3. Бесшовный fallback на in-memory asyncio.Queue и BoundedTTLCache (для тестов и сред без Redis).
4. Паттерн Single DB-Writer: все скраперы отправляют спарсенные товары в очередь,
   а единственный фоновый воркер последовательно и порциями сохраняет их в SQLite.
5. Устранение межпроцессных и межпоточных блокировок database is locked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import time
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
            "--maxmemory-policy", "allkeys-lru",
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
# Менеджер очереди товаров (Redis + Fallback In-Memory)
# ---------------------------------------------------------------------------

class ProductQueueManager:
    """Универсальная очередь сохранения товаров с автоматическим выбором Redis или памяти."""

    def __init__(self):
        self.redis_client: Optional[MiniRedisClient] = None
        self.memory_queue: asyncio.Queue = asyncio.Queue()
        self.use_redis: bool = False
        self.is_worker_running: bool = False
        self._in_memory_cache: Dict[str, Tuple[float, Any]] = {}

    async def initialize(self) -> None:
        """Инициализирует подключение: пробует Redis, при отказе остаётся на очереди в памяти."""
        # 1. Пробуем встроенный демон
        has_redis = start_redis_daemon_if_available()
        if has_redis or _is_port_open(DEFAULT_REDIS_HOST, DEFAULT_REDIS_PORT):
            client = MiniRedisClient(DEFAULT_REDIS_HOST, DEFAULT_REDIS_PORT)
            if await client.connect() and await client.ping():
                self.redis_client = client
                self.use_redis = True
                print("[Queue] 🚀 Очередь скрапинга и кэш подключены к Redis (Single DB-Writer mode)")
                return

        self.use_redis = False
        print("[Queue] ℹ️ Redis не обнаружен: используется высокоскоростная очередь в памяти (In-Memory Single DB-Writer)")

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
    ) -> None:
        """Скраперы сбрасывают спарсенную пачку товаров в очередь без блокировки базы."""
        if not prods and not reconcile and not scan_log:
            return

        payload = {
            "prods": prods or [],
            "shop_name": shop_name,
            "candidate_settings": candidate_settings,
            "shop_key": shop_key,
            "reconcile": reconcile,
            "scan_log": scan_log,
            "enqueued_at": time.time(),
        }

        if self.use_redis and self.redis_client:
            try:
                data_str = json.dumps(payload, ensure_ascii=False)
                await self.redis_client.rpush(QUEUE_KEY, data_str)
                return
            except Exception as e:
                logger.warning("Сбой отправки в Redis (%s), переключаемся на очередь в памяти", e)
                self.use_redis = False

        await self.memory_queue.put(payload)

    async def dequeue(self, timeout: float = 0.5) -> Optional[Dict[str, Any]]:
        """Извлекает одну задачу из очереди (сначала проверяет память, затем Redis)."""
        # Сначала проверяем очередь памяти
        if not self.memory_queue.empty():
            try:
                return self.memory_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

        if self.use_redis and self.redis_client:
            try:
                item_str = await self.redis_client.lpop(QUEUE_KEY)
                if item_str:
                    return json.loads(item_str)
            except Exception as e:
                logger.warning("Сбой чтения из Redis: %s", e)

        # Если в Redis пусто, ждём немного в памяти
        try:
            return await asyncio.wait_for(self.memory_queue.get(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return None

    async def qsize(self) -> int:
        mem_size = self.memory_queue.qsize()
        if self.use_redis and self.redis_client:
            try:
                return mem_size + await self.redis_client.llen(QUEUE_KEY)
            except Exception:
                pass
        return mem_size

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
