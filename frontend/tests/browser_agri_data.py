"""本地農業資料庫 UI 離線驗收；API 皆攔截，不使用官方額度或真實農民資料。"""
import asyncio,json
from urllib.parse import urlparse,parse_qs
from playwright.async_api import async_playwright,expect
BASE='http://127.0.0.1:5173'
CATALOG=[
 ('ImportantAgriculturalPestDiagnosticsType','重要農業害蟲診斷圖鑑','病蟲害',False),
 ('PesticideDataQueryType','農藥許可證與撤銷資料','施肥用藥',True),
 ('AutoWeatherStationType','自動氣象站觀測','農業氣象',False),
]
def meta(endpoint,name,group,exact):
    return dict(endpoint=endpoint,name=name,group=group,available=True,status='stale',row_count=1,snapshot_id='offline-test',
      fetched_at='2026-09-21T03:00:00Z',last_attempt_at='2026-09-21T04:00:00Z',last_error='key_required',source_url='https://data.moa.gov.tw/api.aspx',hours=24,notice='歷史資料供判讀參考；開藥仍須三源比對',exact_search=exact)

async def check(browser,role):
    context=await browser.new_context(viewport={'width':1440,'height':950})
    await context.route('https://**/*',lambda route:route.abort())
    mode='stale';errors=[]
    async def api(route):
        nonlocal mode
        u=urlparse(route.request.url);path=u.path.removeprefix('/api/v1');params=parse_qs(u.query)
        if path=='/planning/me':data=dict(id='test',name='離線使用者',role=role,farm_name='試驗農場',location='')
        elif path=='/planning/catalog':data=dict(version='test',notice='',sources=[],crops=[])
        elif path in ['/planning/links','/admin/snapshots']:data=[]
        elif path=='/agri-data/datasets':data=[meta(*d) for d in CATALOG]
        elif path.startswith('/agri-data/'):
            name=path.split('/')[2];source=meta(*next(d for d in CATALOG if d[0]==name))
            values=dict(Crop_Name='試驗作物',ReceiptDate='2011-01-01',Other='<script>window.bad=true</script>',Image='https://evil.test/photo.jpg')
            observation=None
            if name=='AutoWeatherStationType':values=dict(TIME='2011/01/01 10:00',TEMP=None,RAIN=0,CITY='試驗縣市');observation='outdated'
            if name=='PesticideDataQueryType':values=dict(ChineseName='試驗藥劑',RevocationType='已撤銷',ExpireDate='100-01-01')
            data=dict(source=source,fields={'TEMP':'溫度','RAIN':'雨量','Other':'其他'},items=[dict(id=0,title='試驗來源紀錄',values=values,observation_state=observation)],total=1,page=1,page_size=20,query=params.get('q',[''])[0])
            if mode=='missing':source.update(available=False,status='key_required',row_count=0,snapshot_id=None,fetched_at=None);data.update(items=[],total=0)
            if mode=='empty':data.update(items=[],total=0)
        else:raise AssertionError(path)
        await route.fulfill(content_type='application/json',body=json.dumps(data))
    await context.route('**/api/v1/**',api)
    page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    await page.goto(BASE+'/agri-data')
    await expect(page.locator('.agri-status')).to_contain_text('顯示本地舊快照')
    await expect(page.locator('.agri-record')).to_contain_text('2011-01-01')
    await page.get_by_text('查看完整來源欄位',exact=True).click()
    await expect(page.locator('.agri-record')).to_contain_text('<script>window.bad=true</script>')
    assert await page.evaluate('window.bad') is None
    await expect(page.locator('.agri-record a')).to_have_count(0)
    await page.get_by_label('資料集',exact=True).select_option('PesticideDataQueryType')
    await expect(page.get_by_label('完整藥名、商品名或代碼')).to_be_visible()
    await expect(page.locator('.agri-record')).to_contain_text('已撤銷')
    mode='empty'
    await page.get_by_label('完整藥名、商品名或代碼').fill('試驗')
    await page.get_by_role('button',name='查本地資料',exact=True).click()
    await expect(page.locator('.agri-message')).to_contain_text('這不等於查無核准用藥')
    mode='missing'
    await page.get_by_role('tab',name='農業氣象',exact=True).click()
    await expect(page.locator('.agri-status')).to_contain_text('完整分頁需要會員金鑰')
    await expect(page.locator('.agri-record')).to_have_count(0)
    mode='stale'
    await page.get_by_role('button',name='查本地資料',exact=True).click()
    await expect(page.locator('.observation')).to_contain_text('觀測已過期')
    await expect(page.locator('.agri-preview')).to_contain_text('溫度：未載明')
    await expect(page.locator('.agri-preview')).to_contain_text('雨量：0')
    await page.set_viewport_size({'width':390,'height':844});await page.wait_for_timeout(200)
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors,errors
    await context.close();print('PASS',role,'本地快照、未收錄／查無、撤銷、過期氣象、外部內容與手機')

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch()
        for role in ['farmer','doctor','agency']:await check(b,role)
        await b.close()
if __name__=='__main__':asyncio.run(main())
