import sys
import time
import re
import threading
from collections import deque
from datetime import datetime
from typing import List, Dict, Any, Optional

class LogEntry:
    def __init__(self, log_id: int, message: str, level: str, source: str, timestamp: Optional[str] = None):
        self.id = log_id
        self.message = message
        self.level = level
        self.source = source
        self.timestamp = timestamp or datetime.now().strftime("%H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message
        }

class LogBuffer:
    def __init__(self, maxlen: int = 1500):
        self._buffer: deque = deque(maxlen=maxlen)
        self._counter: int = 0
        self._lock = threading.Lock()

    def add(self, message: str, level: Optional[str] = None, source: Optional[str] = None) -> LogEntry:
        clean_msg = message.rstrip("\r\n")
        if not clean_msg.strip():
            return None

        # Определение источника из квадратных скобок в начале сообщения (например, [AutoScan] или [Kaspi])
        extracted_source = "System"
        bracket_match = re.match(r"^\[([A-Za-z0-9а-яА-Я_\-\.\s]+)\]\s*(.*)$", clean_msg)
        display_msg = clean_msg
        if bracket_match:
            extracted_source = bracket_match.group(1).strip()
            display_msg = bracket_match.group(2).strip()

        final_source = source or extracted_source

        # Определение уровня лога
        final_level = level
        if not final_level:
            lower = clean_msg.lower()
            if any(w in lower for w in ["error", "ошибка", "exception", "failed", "traceback", "critical"]):
                final_level = "ERROR"
            elif any(w in lower for w in ["warn", "warning", "предупреждение", "slow", "retry"]):
                final_level = "WARN"
            elif any(w in lower for w in ["✅", "успешно", "загружено", "сохранено", "выгружено", "готово", "created"]):
                final_level = "SUCCESS"
            elif any(w in lower for w in ["сканирован", "категори", "опрос", "step", "прогресс", "обновление", "autoscan", "запуск"]):
                final_level = "SCAN"
            else:
                final_level = "INFO"

        with self._lock:
            self._counter += 1
            entry = LogEntry(self._counter, display_msg, final_level, final_source)
            self._buffer.append(entry)
            return entry

    def get_logs(self, since_id: int = 0, limit: int = 200, level: Optional[str] = None, query: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            entries = list(self._buffer)

        # Фильтр по ID для инкрементальной передачи в реальном времени
        if since_id > 0:
            entries = [e for e in entries if e.id > since_id]

        if level and level.upper() != "ALL":
            entries = [e for e in entries if e.level.upper() == level.upper()]

        if query:
            q = query.lower()
            entries = [e for e in entries if q in e.message.lower() or q in e.source.lower()]

        if limit > 0 and len(entries) > limit:
            entries = entries[-limit:]

        return [e.to_dict() for e in entries]

    def clear(self):
        with self._lock:
            self._buffer.clear()

    def export_text(self) -> str:
        with self._lock:
            entries = list(self._buffer)
        lines = []
        for e in entries:
            lines.append(f"[{e.timestamp}] [{e.level:7}] [{e.source}] {e.message}")
        return "\n".join(lines)


# Глобальный буфер логов
log_buffer = LogBuffer(maxlen=2000)

class StreamInterceptor:
    """Перехватчик стандартного потока вывода (stdout/stderr) для дублирования в веб-буфер."""
    def __init__(self, original_stream, default_level: str = "INFO"):
        self.original_stream = original_stream
        self.default_level = default_level
        self._line_buffer = ""

    def write(self, text: str):
        # Всегда передаем вывод в оригинальный терминал
        try:
            self.original_stream.write(text)
            self.original_stream.flush()
        except Exception:
            pass

        # Накопление строк и отправка законченных сообщений в кольцевой буфер
        self._line_buffer += text
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            line = line.strip("\r")
            if line:
                log_buffer.add(line, level=self.default_level if self.default_level == "ERROR" else None)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

_interceptor_installed = False

def install_log_interceptor():
    """Активирует перехват stdout и stderr без блокировки консоли."""
    global _interceptor_installed
    if _interceptor_installed:
        return
    sys.stdout = StreamInterceptor(sys.stdout, default_level="INFO")
    sys.stderr = StreamInterceptor(sys.stderr, default_level="ERROR")
    _interceptor_installed = True
    print("[System] 📡 Модуль онлайн-логирования активирован.")
