"""
Live Stand Verification for:
1. Header profile button removed on mobile screens (< 900px) when bottom nav appears.
2. Bottom nav has user avatar photo instead of generic icon.
3. Center highlighted prominent Search button (FAB).
4. Symmetrical 5-tab mobile navigation: Каталог, Скидки, [Поиск FAB], Избранное, Профиль.
Stand: https://shdev.molthun.ru
"""
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent
BASE_URL = "https://shdev.molthun.ru"

async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = {
        "base_url": BASE_URL,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checks": {},
        "screenshots": [],
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        # -------------------------------------------------------------
        # 1. MOBILE iPHONE 13 (390 x 844) - DARK THEME
        # -------------------------------------------------------------
        ctx_mobile = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            ignore_https_errors=True,
            color_scheme="dark",
        )
        # Add owner session cookie on stand
        await ctx_mobile.add_cookies([{
            "name": "kzph_session",
            "value": "8RRNk7OPKK5on-CHZnBbiQ8xIPxu_CucMdKNv1bBI-o",
            "domain": "shdev.molthun.ru",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        }])
        page = await ctx_mobile.new_page()

        resp = await page.goto(f"{BASE_URL}/", wait_until="networkidle")
        results["checks"]["home_http_status"] = resp.status if resp else None

        # A. Check Mobile Header: profile hidden, city selector spacious
        header_check = await page.evaluate("""() => {
            const userBtn = document.querySelector('.header-user-btn');
            const authArea = document.querySelector('#headerAuthArea');
            const citySel = document.querySelector('.city-selector');
            const logo = document.querySelector('.site-logo');
            const headerInner = document.querySelector('.header-inner');

            const btnDisplay = userBtn ? window.getComputedStyle(userBtn).display : 'none';
            const authAreaDisplay = authArea ? window.getComputedStyle(authArea).display : 'none';

            return {
                userBtnPresent: !!userBtn,
                userBtnDisplay: btnDisplay,
                headerAuthAreaDisplay: authAreaDisplay,
                headerProfileHidden: btnDisplay === 'none' && authAreaDisplay === 'none',
                citySelWidth: citySel ? citySel.getBoundingClientRect().width : null,
                headerInnerScrollWidth: headerInner ? headerInner.scrollWidth : null,
                headerInnerClientWidth: headerInner ? headerInner.clientWidth : null,
                headerOverflows: headerInner ? headerInner.scrollWidth > headerInner.clientWidth : false,
                hasHorizontalScroll: document.documentElement.scrollWidth > window.innerWidth || document.body.scrollWidth > window.innerWidth
            };
        }""")
        results["checks"]["mobile_header"] = header_check
        results["checks"]["mobile_header_profile_hidden"] = header_check["headerProfileHidden"]
        results["checks"]["mobile_header_no_horizontal_scroll"] = not header_check["hasHorizontalScroll"]

        # B. Check Bottom Navigation: 5 items, center Search FAB, profile avatar photo
        bottom_nav_check = await page.evaluate("""() => {
            const nav = document.querySelector('.mobile-bottom-nav');
            const navDisplay = nav ? window.getComputedStyle(nav).display : 'none';
            const items = nav ? Array.from(nav.querySelectorAll('.bottom-nav-item')).map(el => {
                const label = el.querySelector('.bottom-nav-label') ? el.querySelector('.bottom-nav-label').textContent.trim() : el.textContent.trim();
                const rect = el.getBoundingClientRect();
                return {
                    id: el.id,
                    label: label,
                    isActive: el.classList.contains('active'),
                    centerX: Math.round(rect.left + rect.width / 2),
                    width: Math.round(rect.width)
                };
            }) : [];

            const fab = document.querySelector('.bottom-nav-fab');
            const fabRect = fab ? fab.getBoundingClientRect() : null;
            const searchItem = document.querySelector('#bottomNavSearch');

            const avatarImg = document.querySelector('#bottomNavProfileAvatar');
            const avatarFallback = document.querySelector('#bottomNavProfileFallback');
            const avatarImgDisplay = avatarImg ? window.getComputedStyle(avatarImg).display : 'none';
            const avatarSrc = avatarImg ? avatarImg.src : '';

            return {
                navVisible: navDisplay !== 'none',
                itemsCount: items.length,
                items: items,
                centerItem: items[2] || null,
                fabPresent: !!fab,
                fabMetrics: fabRect ? {
                    width: Math.round(fabRect.width),
                    height: Math.round(fabRect.height),
                    top: Math.round(fabRect.top),
                    centerX: Math.round(fabRect.left + fabRect.width / 2)
                } : null,
                avatarImgVisible: avatarImgDisplay !== 'none',
                avatarSrc: avatarSrc ? avatarSrc.substring(0, 50) + '...' : '',
                avatarFallbackDisplay: avatarFallback ? window.getComputedStyle(avatarFallback).display : 'none'
            };
        }""")
        results["checks"]["bottom_nav"] = bottom_nav_check
        results["checks"]["bottom_nav_has_5_items"] = bottom_nav_check["itemsCount"] == 5
        results["checks"]["bottom_nav_center_is_search"] = bottom_nav_check["centerItem"] and bottom_nav_check["centerItem"]["id"] == "bottomNavSearch"
        results["checks"]["bottom_nav_profile_has_photo"] = bottom_nav_check["avatarImgVisible"]

        # Screenshot 1: iPhone 13 Home Dark (shows clean header + center FAB bottom nav with photo)
        ss1 = str(OUT / "01-iphone13-home-dark.png")
        await page.screenshot(path=ss1)
        results["screenshots"].append("01-iphone13-home-dark.png")

        # C. Test Search FAB click: focuses search input
        await page.click('#bottomNavSearch')
        await page.wait_for_timeout(300)
        active_id = await page.evaluate("() => document.activeElement ? document.activeElement.id : null")
        results["checks"]["search_fab_focuses_input"] = active_id == "homeSearchInput"

        # D. Navigate to Profile via bottom nav
        await page.click('#bottomNavProfile')
        await page.wait_for_timeout(800)

        ss2 = str(OUT / "02-iphone13-profile-dark.png")
        await page.screenshot(path=ss2)
        results["screenshots"].append("02-iphone13-profile-dark.png")

        # -------------------------------------------------------------
        # 2. MOBILE iPHONE 13 - LIGHT THEME
        # -------------------------------------------------------------
        await page.evaluate("() => selectTheme('light')")
        await page.wait_for_timeout(500)
        ss3 = str(OUT / "03-iphone13-profile-light.png")
        await page.screenshot(path=ss3)
        results["screenshots"].append("03-iphone13-profile-light.png")

        await page.click('#bottomNavSearch')
        await page.wait_for_timeout(500)
        ss4 = str(OUT / "04-iphone13-home-light.png")
        await page.screenshot(path=ss4)
        results["screenshots"].append("04-iphone13-home-light.png")

        # -------------------------------------------------------------
        # 3. DESKTOP (1440 x 900) - Verification
        # -------------------------------------------------------------
        ctx_desktop = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
            color_scheme="dark",
        )
        await ctx_desktop.add_cookies([{
            "name": "kzph_session",
            "value": "8RRNk7OPKK5on-CHZnBbiQ8xIPxu_CucMdKNv1bBI-o",
            "domain": "shdev.molthun.ru",
            "path": "/",
            "httpOnly": True,
            "secure": True,
        }])
        page_desk = await ctx_desktop.new_page()
        await page_desk.goto(f"{BASE_URL}/", wait_until="networkidle")

        desk_check = await page_desk.evaluate("""() => {
            const nav = document.querySelector('.mobile-bottom-nav');
            const userBtn = document.querySelector('.header-user-btn');
            const navDisplay = nav ? window.getComputedStyle(nav).display : 'none';
            const userBtnDisplay = userBtn ? window.getComputedStyle(userBtn).display : 'none';

            return {
                bottomNavHiddenOnDesktop: navDisplay === 'none',
                headerProfileVisibleOnDesktop: userBtnDisplay !== 'none'
            };
        }""")
        results["checks"]["desktop_nav"] = desk_check

        ss5 = str(OUT / "05-desktop-home-dark.png")
        await page_desk.screenshot(path=ss5)
        results["screenshots"].append("05-desktop-home-dark.png")

        await browser.close()

    # Save summary report
    rep_path = OUT / "verification_results.json"
    rep_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
