"""第三輪區域狀態機的離線瀏覽器回歸：只用本機 Vite，API／外部圖磚皆攔截。

backend/.venv/bin/python frontend/tests/browser_region_states.py
"""
import asyncio
import json
from collections import Counter
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, expect

BASE = 'http://127.0.0.1:5173'
MISSING = '您的農場地區尚未填寫鄉鎮，填寫後就能看到所在鄉鎮的彙整。'
KPI = {'count': 4, 'metric': None, 'area_ha': None, 'sample_insufficient': False}


def summary(count=4):
    return dict(county='高雄市', available=True, reason=None, kpi={**KPI, 'count': count},
                top_crops=[], alerts=[], legend=[0, 2, 5])


async def check(browser, initial):
    context = await browser.new_context(viewport={'width': 1440, 'height': 950})
    await context.route('https://**/*', lambda route: route.abort())
    mode = initial
    user = dict(id='fixture', role='farmer' if initial.startswith('missing') else 'doctor', name='離線測試', farm_name='離線測試農場', location='')
    calls = Counter()
    slow_started, release_slow, slow_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    errors = []

    async def api(route):
        nonlocal mode
        url = urlparse(route.request.url)
        path = url.path.removeprefix('/api/v1')
        calls[path] += 1
        status = 200
        if path == '/admin/snapshots': data = []
        elif path == '/planning/catalog': data = dict(version='test', notice='', sources=[], crops=[])
        elif path == '/planning/me':
            data = user
        elif path == '/planning/links': data = []
        elif path == '/planning/farmers/fixture': data = dict(farmer=user, plans=[], notes=[], region_alerts=[])
        elif path == '/planning/me/location':
            assert route.request.method == 'PATCH'
            user['location'] = route.request.post_data_json['location']
            mode = 'ready'
            data = user
        elif path == '/region/summary':
            if mode == 'missing200': data = {**summary(), 'available': False, 'reason': 'district_missing', 'message': MISSING}
            elif mode == 'missing422': status, data = 422, {'detail': MISSING}
            elif mode == 'unsupported': data = {**summary(), 'available': False, 'reason': 'county_unsupported', 'message': '尚未提供這個縣市的區域地圖資料'}
            elif mode == 'error': status, data = 500, {'detail': '離線測試暫時失敗'}
            elif mode == 'invalid': status, data = 422, {'detail': '日期格式不正確'}
            elif mode == 'race' and parse_qs(url.query).get('source') == ['consult']:
                slow_started.set()
                await release_slow.wait()
                data = summary(91)
            else: data = summary(7 if mode == 'race' else 4)
        elif path == '/region/districts':
            data = dict(county='高雄市', available=True, districts=[{**KPI, 'district': '測試甲區', 'alert': False}])
        elif path == '/region/geo':
            data = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {'district': '測試甲區'},
                    'geometry': {'type': 'Polygon', 'coordinates': [[[120.3, 22.7], [120.4, 22.7], [120.4, 22.8], [120.3, 22.8], [120.3, 22.7]]]}}]}
        elif path.startswith('/region/districts/'):
            data = dict(district='測試甲區', items=[], alerts=[], linked_farms=[])
        else: raise AssertionError(path)
        await route.fulfill(status=status, content_type='application/json', body=json.dumps(data))
        if path == '/region/summary' and mode == 'race' and parse_qs(url.query).get('source') == ['consult']:
            slow_done.set()

    await context.route('**/api/v1/**', api)
    page = await context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    await page.goto(BASE + '/region')
    await expect(page.locator('.leaflet-container')).to_be_visible()
    await page.evaluate("window.savedMapNode = document.querySelector('.map')")
    if initial.startswith('missing') or initial == 'unsupported':
        await expect(page.locator('.ov-card')).to_be_visible()
        if initial.startswith('missing'):
            await expect(page.get_by_role('link', name='到農場設定填寫鄉鎮')).to_be_visible()
        else:
            await expect(page.get_by_role('link', name='到農場設定填寫鄉鎮')).to_have_count(0)
        for label in ('診斷案件', '巡田通報', '主動調查', '全部'):
            await page.get_by_role('button', name=label, exact=True).click()
        assert calls['/region/summary'] == 1
        assert calls['/region/districts'] == calls['/region/geo'] == 0
        assert await page.evaluate("window.savedMapNode === document.querySelector('.map')")
        if initial.startswith('missing'):
            await page.get_by_role('link', name='到農場設定填寫鄉鎮').click()
            await expect(page.get_by_role('dialog')).to_be_visible()
            await page.get_by_label('縣市與鄉鎮市區', exact=True).fill('高雄市大社區')
            await page.get_by_role('button', name='儲存所在地', exact=True).click()
            await expect(page).to_have_url(BASE + '/region')
            await expect(page.locator('.mapbox .ov')).to_have_count(0)
            assert calls['/planning/me/location'] == 1
            assert calls['/planning/me'] == calls['/planning/catalog'] == 1
            await page.evaluate("window.savedMapNode = document.querySelector('.map')")
    else:
        await expect(page.get_by_role('button', name='重試', exact=True)).to_be_visible()
        await expect(page.get_by_role('link', name='到農場設定填寫鄉鎮')).to_have_count(0)
        mode = 'ready'
        await page.get_by_role('button', name='重試', exact=True).click()
        await expect(page.locator('.mapbox .ov')).to_have_count(0)
        await page.locator('.leaflet-control-zoom-in').click()
        await page.wait_for_timeout(350)  # Leaflet 縮放動畫結束後才比較 transform。
        path_node = await page.locator('.leaflet-interactive').element_handle()
        path_before = await path_node.get_attribute('d')
        mode = 'race'
        await page.get_by_role('button', name='診斷案件', exact=True).click()
        await slow_started.wait()
        await expect(page.locator('.kpi .sk')).to_have_count(4)
        await page.get_by_role('button', name='巡田通報', exact=True).click()
        await expect(page.locator('.mapbox .ov')).to_have_count(0)
        await expect(page.locator('.kpi .v').first).to_have_text('7件')
        release_slow.set()
        await slow_done.wait()
        await page.wait_for_timeout(100)
        await expect(page.locator('.kpi .v').first).to_have_text('7件')
        assert await path_node.evaluate('(node) => node.isConnected')
        assert await path_node.get_attribute('d') == path_before
        assert calls['/region/geo'] == 1
    assert await page.evaluate("window.savedMapNode === document.querySelector('.map')")
    assert not errors, errors
    await context.close()
    print(f'PASS: {initial} — DOM 穩定、空狀態不重抓／錯誤可重試、篩選競速保護')


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for state in ('missing200', 'missing422', 'unsupported', 'error', 'invalid'):
            await check(browser, state)
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
