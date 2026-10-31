"""
Live Stand Verification for:
1. Mobile Header Redesign: spacious city selector, user name pill next to avatar photo, no clipping, balanced layout.
2. User Management in /profile: #profileAdminUsersCard for admins to moderate/confirm users in 1-tap.
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
        # 1. MOBILE iPHONE 13 (390 x 844) - Home & Header
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

        # Check mobile header elements: city selector & user name pill
        header_metrics = await page.evaluate("""() => {
            const btn = document.querySelector('.header-user-btn');
            const nameSpan = document.querySelector('.header-user-btn .header-user-name');
            const citySel = document.querySelector('.city-selector');
            const selectEl = document.querySelector('#headerCitySelect');
            const headerInner = document.querySelector('.header-inner');

            return {
                btnPresent: !!btn,
                btnWidth: btn ? btn.getBoundingClientRect().width : null,
                btnHeight: btn ? btn.getBoundingClientRect().height : null,
                nameSpanDisplay: nameSpan ? window.getComputedStyle(nameSpan).display : 'none',
                nameSpanText: nameSpan ? nameSpan.textContent.trim() : '',
                citySelWidth: citySel ? citySel.getBoundingClientRect().width : null,
                citySelectValue: selectEl ? selectEl.value : null,
                headerInnerScrollWidth: headerInner ? headerInner.scrollWidth : null,
                headerInnerClientWidth: headerInner ? headerInner.clientWidth : null,
                headerOverflows: headerInner ? headerInner.scrollWidth > headerInner.clientWidth : false,
                hasHorizontalScroll: document.documentElement.scrollWidth > window.innerWidth || document.body.scrollWidth > window.innerWidth
            };
        }""")
        results["checks"]["mobile_header_metrics"] = header_metrics
        results["checks"]["header_user_name_visible"] = header_metrics["nameSpanDisplay"] != "none"
        results["checks"]["header_user_name_text"] = header_metrics["nameSpanText"]
        results["checks"]["no_header_overflow"] = not header_metrics["headerOverflows"]
        results["checks"]["no_page_horizontal_scroll"] = not header_metrics["hasHorizontalScroll"]

        # Screenshot 1: iPhone 13 Header on Home
        ss1 = str(OUT / "01-iphone13-header-home.png")
        await page.screenshot(path=ss1)
        results["screenshots"].append("01-iphone13-header-home.png")

        # -------------------------------------------------------------
        # 2. MOBILE iPHONE 13 - Profile & Admin User Moderation Card
        # -------------------------------------------------------------
        await page.goto(f"{BASE_URL}/profile", wait_until="networkidle")
        await page.wait_for_timeout(1000)

        profile_admin_metrics = await page.evaluate("""() => {
            const card = document.querySelector('#profileAdminUsersCard');
            const list = document.querySelector('#adminUsersListContainer');
            const badge = document.querySelector('#adminUsersCountBadge');
            const cardDisplay = card ? window.getComputedStyle(card).display : 'none';
            const buttons = list ? Array.from(list.querySelectorAll('button')).map(b => b.textContent.trim()) : [];
            const userRows = list ? Array.from(list.children).map(c => c.textContent.replace(/\\s+/g, ' ').trim()) : [];

            return {
                cardPresent: !!card,
                cardDisplay: cardDisplay,
                badgeText: badge ? badge.textContent.trim() : '',
                usersCount: list ? list.children.length : 0,
                userRows: userRows,
                actionButtons: buttons,
                hasHorizontalScroll: document.documentElement.scrollWidth > window.innerWidth || document.body.scrollWidth > window.innerWidth
            };
        }""")
        results["checks"]["profile_admin_metrics"] = profile_admin_metrics
        results["checks"]["admin_card_visible"] = profile_admin_metrics["cardDisplay"] != "none"

        # Screenshot 2: iPhone 13 Profile with Admin Users Card
        ss2 = str(OUT / "02-iphone13-profile-admin-users.png")
        await page.screenshot(path=ss2)
        results["screenshots"].append("02-iphone13-profile-admin-users.png")

        # -------------------------------------------------------------
        # 3. DESKTOP (1440 x 900) - Header & Profile Verification
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
        ss3 = str(OUT / "03-desktop-home-dark.png")
        await page_desk.screenshot(path=ss3)
        results["screenshots"].append("03-desktop-home-dark.png")

        await page_desk.goto(f"{BASE_URL}/profile", wait_until="networkidle")
        await page_desk.wait_for_timeout(1000)
        ss4 = str(OUT / "04-desktop-profile-admin-users.png")
        await page_desk.screenshot(path=ss4)
        results["screenshots"].append("04-desktop-profile-admin-users.png")

        await browser.close()

    # Save summary report
    rep_path = OUT / "results.json"
    rep_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
