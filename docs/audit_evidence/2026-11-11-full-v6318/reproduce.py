import asyncio
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

from tests.web.test_audit_v6315_fixes import _products
from tests.web.test_audit_v6313_fixes import _seed, UID
import auth, database, notifier
from domain import barcode_resolver as barcode
from web import server

async def main():
    _seed()
    token = database.create_session(UID)
    wanted = 'Активный переходник HDMI на DisplayPort'
    reverse = 'Активный переходник DisplayPort на HDMI'
    _products([('reverse_converter', reverse)])
    data = {'items':[]}
    await server._apply_barcode_matches(data, '4006381333931', {'title':wanted, 'source':'searxng'})
    print('barcode_direction', barcode.is_exact_variant_match(wanted, reverse),
          data.get('barcode_match'), barcode.get_barcode_product_ids('4006381333931'))

    card = server._trusted_share_card_sync('', 'b02_target', 'Астана')
    recorded = []
    def capture(method, payload):
        recorded.append(payload)
        r = Mock(status_code=200)
        r.json.return_value = {'result':{'id':'synthetic'}}
        return r
    with patch.object(notifier,'get_bot_token',return_value='synthetic'), patch.object(auth,'get_bot_token',return_value='synthetic'), patch.object(notifier,'APP_URL','https://audit.invalid'), patch.dict(auth._bot_username_cache, {'token':'synthetic','username':'synthetic_bot','main_web_app':True}), patch.object(notifier,'telegram_api',side_effect=capture):
        notifier.save_prepared_inline_message(UID, card['title'], card['price'], card['shop'], card['url'], card['city'], card['image_url'], card['compare_path'], card['start_param'])
    button = next(button['url'] for row in recorded[0]['result']['reply_markup']['inline_keyboard'] for button in row if 'Сравнить цены' in button.get('text',''))
    param = parse_qs(urlsplit(button).query)['startapp'][0]
    app = web.Application(middlewares=[auth.auth_middleware])
    app.add_routes(server.routes)
    app.router.add_static('/static/', server.BASE_DIR / 'web' / 'static')
    async with TestServer(app) as http:
        origin = str(http.make_url('/')).rstrip('/')
        with patch.object(auth,'MAINTENANCE_DEV_MODE',False), patch.object(server,'MAINTENANCE_DEV_MODE',False):
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(channel='chrome')
                ctx = await browser.new_context(viewport={'width':390,'height':844})
                await ctx.add_cookies([{'name':auth.SESSION_COOKIE,'value':token,'url':origin}])
                page = await ctx.new_page()
                async def local_only(route):
                    if route.request.url.startswith(origin+'/'):
                        await route.continue_()
                    else:
                        await route.abort()
                await page.route('**/*',local_only)
                await page.add_init_script("window.Telegram = {WebApp:{initDataUnsafe:{start_param:"+repr(param)+"}}};")
                await page.goto(origin+'/',wait_until='domcontentloaded')
                await page.wait_for_function("AppState.currentProduct?.product?.id === 'b02_target'",timeout=12000)
                page_data = await page.evaluate("({city:AppState.city,price:AppState.currentProduct.min_price})")
                print('compare_city',card['city'],card['price'],card['compare_path'],param,page_data)
                await page.close()
                page = await ctx.new_page()
                await page.route("**/*",local_only)
                await page.goto(origin+'/offer/b02_astana_gone',wait_until='domcontentloaded')
                await page.wait_for_function("AppState.currentProduct?.selected_offer?.id === 'b02_astana_gone'",timeout=12000)
                await page.evaluate("""() => {
                  TelegramApp.isInside = false;
                  Object.defineProperty(navigator,'share',{configurable:true,value:args=>{window.auditShare=args;return Promise.resolve();}});
                }""")
                await page.locator('#detailShareBtn').click(force=True)
                share = await page.evaluate("({purchasable:AppState.currentProduct.selected_offer.is_purchasable, minPrice:AppState.currentProduct.min_price, display:document.getElementById('detailBestPrice').textContent, text:window.auditShare?.text})")
                print('pwa_unavailable_price', share)
                await browser.close()
asyncio.run(main())
