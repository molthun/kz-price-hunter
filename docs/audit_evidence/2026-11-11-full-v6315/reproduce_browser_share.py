import asyncio
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

from tests.web.test_audit_v6313_fixes import _seed, UID
import auth, database, notifier
from web import server

async def main():
    _seed()
    token = database.create_session(UID)
    app = web.Application(middlewares=[auth.auth_middleware])
    app.add_routes(server.routes)
    app.router.add_static('/static/', server.BASE_DIR / 'web' / 'static')
    async with TestServer(app) as http:
        origin = str(http.make_url('/')).rstrip('/')
        with patch.object(auth, 'expected_origin', return_value=origin), patch.object(auth, 'MAINTENANCE_DEV_MODE', False), patch.object(server, 'MAINTENANCE_DEV_MODE', False), patch.object(notifier, 'save_prepared_inline_message', return_value='synthetic') as save:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(channel='chrome')
                ctx = await browser.new_context(viewport={'width':390,'height':844})
                await ctx.add_cookies([{'name':auth.SESSION_COOKIE,'value':token,'url':origin}])
                page = await ctx.new_page()
                page.on("pageerror", lambda err: print("page_error", err))
                page.on("response", lambda res: print("http", res.status, res.url.split(origin)[-1]) if "/api/" in res.url else None)
                async def allow_local(route):
                    if route.request.url.startswith(origin+'/'):
                        await route.continue_()
                    else:
                        await route.abort()
                await page.route('**/*',allow_local)
                await page.goto(origin+'/offer/b02_astana_2%40astana',wait_until='domcontentloaded')
                await page.wait_for_function("AppState.currentProduct?.selected_offer?.id === 'b02_astana_2@astana'",timeout=10000)
                current = await page.evaluate("({city:AppState.city,offer:AppState.currentProduct.selected_offer.city,price:AppState.currentProduct.selected_offer.price})")
                await page.evaluate("""() => {
                    TelegramApp.isInside = true;
                    TelegramApp.tg = {initDataUnsafe:{user:{id:1}},shareMessage:()=>{}};
                }""")
                async with page.expect_response(lambda res: res.url.endswith('/api/telegram/prepared-message'),timeout=10000) as event:
                    await page.locator('#detailShareBtn').click(force=True)
                response = await event.value
                body = response.request.post_data_json
                args = save.call_args.args
                print('visible',current,'payload',body['city'],body['offer_id'],'share',response.status,args[2],args[4],args[5])
                await browser.close()
asyncio.run(main())
