"""行情／成本前端離線回歸：只讀本機 Vite；所有 API、外部流量皆攔截。"""
import asyncio
from collections import Counter
import json
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, expect

BASE = 'http://127.0.0.1:5173'
META = dict(status='ok', stale=False, cached=False, complete=True, error=None,
            fetched_at='2026-09-21T03:00:00+00:00', snapshot_id='test-snapshot', source_url='https://data.moa.gov.tw/api.aspx', key_configured=False, notice='離線行情驗收；批發價不是農場收購價')
FARMER = dict(id='fixture', role='farmer', name='測試農友', farm_name='測試農場', location='')

def plan(id, crop):
    return dict(id=id, farmer_id='fixture', crop_key=None, crop_name=crop, field_name=id, variety='', area='',
                planted_on='2026-09-01',end_on='2026-12-01',notes='',archived=False,version=1,template_version=None,sources=[],events=[])

def market():
    row = dict(date='2026-09-20',crop_code='T1',crop='測試品項甲',market_code='M1',market='甲市場',category='N04',average=41.5,upper=50,middle=42,lower=30,volume_kg=100)
    return dict(**META, query='測試甲',requested_crop='測試甲',rows=[row,
        {**row,'date':'2026-09-18','average':None,'volume_kg':None},
        {**row,'crop_code':'T2','crop':'測試品項乙','average':999},
        {**row,'market_code':'M2','market':'乙市場','average':888},
        {**row,'category':'N05','crop':'同碼其他類別','average':777}],
        closed=[dict(date='2026-09-21',market_code='M1',market='甲市場',category='N04')],
        ignored_rows=0,**{'from':'2026-09-15','to':'2026-09-21'})

async def check(browser, role):
    context = await browser.new_context(viewport={'width':1440,'height':950})
    await context.route('https://**/*',lambda route: route.abort())
    counts = Counter(); errors = []; mode = 'ok'
    slow, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def api(route):
        url=urlparse(route.request.url); path=url.path.removeprefix('/api/v1'); counts[path]+=1
        status=200
        if path == '/planning/me': data = FARMER if role == 'farmer' else {**FARMER,'id':'doctor','role':'doctor'}
        elif path == '/planning/catalog': data=dict(version='test',notice='',sources=[],crops=[])
        elif path == '/planning/links': data=[FARMER,{**FARMER,'id':'second','name':'第二位農友'}] if role == 'doctor' else []
        elif path in ('/planning/farmers/fixture','/planning/farmers/second'):
            data=dict(farmer={**FARMER,'id':path.split('/')[-1]},plans=[plan('a','測試甲'),plan('b','測試乙')],notes=[],region_alerts=[])
        elif path.endswith('/logs'): data=dict(items=[],total=0)
        elif path in ('/admin/snapshots','/weather/counties'): data=[]
        elif path == '/economics/market':
            crop=parse_qs(url.query)['crop'][0]
            if mode=='race' and crop=='測試甲': slow.set(); await release.wait()
            data=market()
            if mode=='empty': data={**data,'rows':[],'closed':[]}
            elif mode=='quota': data={**data,'rows':[],'closed':[],'status':'rate_limited','complete':False,'snapshot_id':None,'fetched_at':None}
            elif mode=='stale': data={**data,'status':'stale','stale':True,'error':'unavailable'}
            elif mode=='race': data={**data,'notice':crop+' 的行情'}
        elif path == '/economics/costs':
            data={**META,'query':'','total_documents':3,'documents':[
                dict(title='測試甲生產成本調查表',year=2026,group='調查表',kind='調查表',url='https://agrcost.afa.gov.tw/test.pdf'),
                dict(title='測試甲填表範例',year=2025,group='雜糧',kind='填表範例',url=None),
                dict(title='其他文件',year=2014,group='蔬菜',kind='成本相關文件',url=None)]}
        else: raise AssertionError(path)
        await route.fulfill(status=status,content_type='application/json',body=json.dumps(data))
        if path=='/economics/market' and mode=='race' and parse_qs(url.query)['crop'][0]=='測試甲': done.set()
    await context.route('**/api/v1/**',api)
    page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    await page.goto(BASE+('/farm' if role=='farmer' else '/farmers/fixture'))
    panel=page.locator('.farm-economics')
    await expect(panel).to_be_visible()
    assert counts['/economics/market']==0  # 收合時不占用官方額度。
    await panel.get_by_role('button',name='市場行情與生產成本',exact=False).click()
    await expect(panel.get_by_label('市場品項')).to_be_visible()
    await panel.get_by_label('市場品項').select_option('N04:T1')
    await panel.get_by_label('交易市場').select_option('M1')
    await expect(panel.locator('.quote b')).to_have_text('41.5')
    await expect(panel.locator('.trend-row')).to_have_count(2)
    await expect(panel.locator('.trend-row').first).to_contain_text('未載明')
    await expect(panel.locator('.econ-alert')).to_contain_text('休市不當成 0 元')
    assert '999' not in await panel.locator('.econ-result').inner_text()
    assert '888' not in await panel.locator('.econ-result').inner_text()
    assert '777' not in await panel.locator('.econ-result').inner_text()
    # 修改搜尋框不觸發請求；需明確送出，避免每個字耗一次 API。
    calls=counts['/economics/market']
    await panel.get_by_label('作物搜尋',exact=True).fill('自行輸入')
    await page.wait_for_timeout(150)
    assert counts['/economics/market']==calls
    await expect(panel.get_by_label('作物搜尋',exact=True)).to_have_value('自行輸入')
    for test_mode in ('quota','empty','stale'):
        mode=test_mode
        await panel.get_by_role('button',name='查詢近 7 天').click()
        if mode=='quota':
            await expect(panel).to_contain_text('官方查詢額度已用完')
            assert '此搜尋詞近 7 天未取得' not in await panel.inner_text()
        elif mode=='empty': await expect(panel).to_contain_text('此搜尋詞近 7 天未取得')
        else:
            await expect(panel).to_contain_text('尚未完成更新，顯示本地舊快照')
            await expect(panel).to_contain_text('本站取得')
    mode='race'
    await panel.get_by_role('button',name='測試甲',exact=True).click();await slow.wait()
    await panel.get_by_role('button',name='測試乙',exact=True).click()
    await expect(panel).to_contain_text('測試乙 的行情')
    release.set();await done.wait();await page.wait_for_timeout(100)
    await expect(panel).to_contain_text('測試乙 的行情')
    await panel.get_by_role('tab',name='生產成本文件').click()
    calls=counts['/economics/market']
    await panel.get_by_role('button',name='測試甲',exact=True).click()
    assert counts['/economics/market']==calls
    await panel.get_by_label('成本文件搜尋').fill('測試甲')
    await expect(panel.locator('.cost-document')).to_have_count(2)
    await panel.get_by_label('文件用途').select_option('填表範例')
    await expect(panel.locator('.cost-document')).to_have_count(1)
    await expect(panel.locator('.cost-document')).to_contain_text('官方連結不完整')
    await expect(panel.locator('.cost-document a')).to_have_count(0)
    await panel.get_by_label('文件用途').select_option('調查表')
    await expect(panel.get_by_role('link',name='開啟官方文件')).to_have_attribute('href','https://agrcost.afa.gov.tw/test.pdf')
    await panel.get_by_label('年度').select_option('2014')
    await expect(panel).to_contain_text('不是「生產成本為零」')
    await panel.get_by_role('button',name='查看全部文件').click()
    await expect(panel.locator('.cost-document')).to_have_count(3)
    await page.set_viewport_size({'width':390,'height':844})
    await page.wait_for_timeout(200)  # 等待既有外殼的響應式過場。
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await panel.get_by_role('tab',name='市場行情',exact=True).click()
    await panel.get_by_label('市場品項').select_option('N04:T1')
    await panel.get_by_label('交易市場').select_option('M1')
    await page.wait_for_timeout(200)  # 等待既有外殼的響應式過場。
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if role=='doctor':
        await page.set_viewport_size({'width':1440,'height':950})
        await page.get_by_label('切換農友').select_option('second')
        await expect(page).to_have_url(BASE+'/farmers/second')
        await expect(panel.locator('.econ-toggle')).to_have_attribute('aria-expanded','false')
    assert counts['/economics/costs']==1
    assert not errors,errors
    await context.close()
    print(f'PASS {role}: 品項／市場／類別隔離、休市、缺值、失敗、快照、競速、文件篩選、手機與工作區隔離')

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        for role in ('farmer','doctor'): await check(browser,role)
        await browser.close()
if __name__=='__main__':asyncio.run(main())
