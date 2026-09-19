"""Temporary loopback-only browser fixture. Never starts scanning or Telegram."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import ipaddress
import os
import socket
import tempfile

async def main():
    with tempfile.TemporaryDirectory() as data_dir:
        os.environ['DATA_DIR']=data_dir
        for key in ('TELEGRAM_BOT_TOKEN','DNS_BOT_TOKEN','OPENAI_API_KEY','GEMINI_API_KEY','ADMIN_TELEGRAM_IDS','PUBLIC_ORIGIN','APP_URL','TRUSTED_PROXIES'):
            os.environ[key]=''
        os.environ['ALLOW_DEV_LOGIN']='1'
        original_connect=socket.socket.connect
        def connect(sock,address):
            if isinstance(address,tuple) and not ipaddress.ip_address(address[0]).is_loopback:
                raise RuntimeError('Browser test blocked external network')
            return original_connect(sock,address)
        socket.socket.connect=connect
        from curl_cffi import requests
        def blocked(*args,**kwargs):raise RuntimeError('Browser test blocked external HTTP')
        requests.Session.request=blocked
        from aiohttp import web
        from web.server import create_app
        app=create_app();app.cleanup_ctx.clear()
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        print(f'BROWSER_TEST_URL=http://127.0.0.1:{port}',flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()

if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
