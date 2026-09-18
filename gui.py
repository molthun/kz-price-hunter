import os
import socket
import webbrowser
import asyncio
from aiohttp import web
from web.server import create_app

PORT = int(os.getenv("PORT", 8080))
HOST = "0.0.0.0"

def get_local_ip():
    """Определяет реальный IP адрес машины в локальной Wi-Fi/LAN сети."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Подключение к любому публичному адресу без отправки пакета для выявления активного интерфейса
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass

def main():
    app = create_app()
    local_ip = get_local_ip()

    local_url = f"http://localhost:{PORT}"
    network_url = f"http://{local_ip}:{PORT}"

    print("\n" + "=" * 62)
    print("🚀 KZ Price Hunter — Web GUI Dashboard запущен!")
    print(f"💻 На этом Mac:        {local_url}")
    print(f"📱 В локальной сети:    {network_url}")
    print("=" * 62)
    print("ℹ️ Вы можете открыть ссылку с телефона или любого устройства")
    print("   подключенного к той же Wi-Fi сети!\n")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.call_later(0.8, open_browser, local_url)

    web.run_app(app, host=HOST, port=PORT, print=None, loop=loop)

if __name__ == "__main__":
    main()
