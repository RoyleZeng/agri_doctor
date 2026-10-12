"""本機 Vite 的離線 API 替身測試，不讀寫真實農戶、病例或對外傳送資料。

啟動前端後：backend/.venv/bin/python frontend/tests/browser_consult_identify.py
"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'tmp/ui-qa'
OUTPUT.mkdir(parents=True,exist_ok=True)


def case_fixture():
    items=[{'id':str(i),'case_id':'test-case','registration_id':i,'product_text':f'合成測試品項 {i}',
        'ingredient_text':'測試成分','moa_text':'待確認','dilution':'100','dose':'測試用量','frequency':'',
        'phi_days':None,'advice':'','source_values':{},'checks':[{'type':'phi','level':'warning','message':'測試採收期提示'}],
        'sort_order':i,'modified_fields':[],'case_version':3} for i in [1,2]]
    return {'id':'test-case','doctor_id':'doctor','farmer_id':None,'farmer_text':'離線測試委託人','case_no':'TEST-01',
        'plan_id':None,'crop_id':1,'crop_text':'合成測試作物','variety':'','field_text':'測試田區','area_text':'',
        'expected_harvest_on':None,'examined_by':[],'symptoms':'','diagnosis_pest_id':None,'diagnosis_text':'',
        'diagnosis_basis':[],'cultural_control':'','status':'draft','issued_at':None,'version':3,
        'created_at':'2031-01-01','updated_at':'2031-01-01','items':items}


with sync_playwright() as p:
    browser=p.chromium.launch(channel='chromium')
    context=browser.new_context(viewport={'width':1440,'height':1080},accept_downloads=True)
    context.route('https://**/*',lambda r:r.abort())
    case=case_fixture(); calls=[]
    def api(route):
        path=route.request.url.split('/api/v1')[1]
        method=route.request.method; calls.append((method,path))
        if path=='/admin/snapshots': data=[]
        # 第三輪共用外殼先載入 session；這兩支也是離線替身，不能漏到真實 API。
        elif path=='/planning/catalog': data={'version':'test','notice':'','sources':[],'crops':[]}
        elif path=='/planning/links': data=[]
        elif path=='/planning/me': data={'id':'doctor','name':'測試醫師','role':'doctor','farm_name':'','location':''}
        elif path=='/consult/cases/test-case' and method=='GET': data=case
        elif path.startswith('/consult/cases/test-case/items/') and method=='PATCH':
            payload=route.request.post_data_json; iid=path.split('/')[-1]
            item=next(i for i in case['items'] if i['id']==iid)
            item.update({k:v for k,v in payload.items() if k!='version'})
            item['modified_fields']=['dilution','dose','frequency','advice']
            case['version']+=1; item['case_version']=case['version']; data=item
        elif path=='/consult/cases/test-case' and method=='PATCH':
            case.update({k:v for k,v in route.request.post_data_json.items() if k!='version'})
            case['version']+=1; data=case
        elif path=='/consult/cases/test-case/issue':
            case['status']='issued'; case['issued_at']='2031-01-02T00:00:00Z'; case['version']+=1; data=case
        elif path.endswith('/report.pdf'):
            route.fulfill(content_type='application/pdf',body=(ROOT/'output/pdf/diagnosis-report-demo.pdf').read_bytes());return
        else:
            raise AssertionError(f'未預期 API {method} {path}')
        route.fulfill(content_type='application/json',body=json.dumps(data,ensure_ascii=False))
    context.route('**/api/v1/**',api)
    page=context.new_page(); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:5173/identify')
    page.get_by_role('button',name='載入展示案例').click()
    page.get_by_role('button',name='查看展示候選').click()
    expect(page.locator('.candidate')).to_have_count(3)
    page.screenshot(path=str(OUTPUT/'identify-candidates-desktop.png'),full_page=True)
    page.get_by_label('加入展示處方草稿').check()
    page.get_by_role('button',name='進行專業判讀').click()
    page.get_by_role('button',name='保存展示紀錄').click()
    expect(page.locator('.problems')).to_be_visible()
    cards=page.locator('.review-item')
    for i,decision in enumerate(['confirmed','confirmed','needs_lab']):
        cards.nth(i).locator('select').select_option(decision)
        cards.nth(i).locator('textarea').fill(f'離線測試判讀理由 {i}')
    page.get_by_role('button',name='保存展示紀錄').click()
    expect(page.get_by_role('button',name='預覽展示處方簽')).to_be_enabled()
    with page.expect_download() as download:
        page.get_by_role('button',name='匯出回饋 JSON').click()
    payload=json.loads(Path(download.value.path()).read_text())
    assert payload['training_eligible'] is False and payload['training_consent_intent'] is False
    assert sum(c['decision']=='confirmed' for c in payload['proposals'])==2
    assert 'url' not in payload['photos'][0]
    page.get_by_role('button',name='預覽展示處方簽').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.screenshot(path=str(OUTPUT/'identify-prescription-demo.png'),full_page=True)
    page.get_by_role('button',name='關閉',exact=True).click()
    page.get_by_role('button',name='農民視角').click()
    page.get_by_role('button',name='聯繫植物醫生 · 展示').click()
    page.get_by_role('button',name='整理諮詢草稿').click()
    expect(page.get_by_text('草稿已整理。尚未串接')).to_be_visible()
    page.get_by_role('button',name='關閉',exact=True).click()
    page.set_viewport_size({'width':390,'height':844})
    while page.locator('.toast').count(): page.locator('.toast').first.click()
    page.screenshot(path=str(OUTPUT/'identify-farmer-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), '手機頁面不可橫向溢位'
    page.get_by_role('button',name='01').click()
    page.locator('input[type=file]').set_input_files(ROOT/'tmp/pdfs/diagnosis-1.png')
    expect(page.get_by_text('本機照片 · 未上傳')).to_be_visible()
    assert all(method == 'GET' and path in {'/admin/snapshots', '/planning/catalog', '/planning/me', '/planning/links'} for method, path in calls), '辨識展示只能讀外殼共用資料，不得呼叫辨識或寫入服務'

    page.set_viewport_size({'width':1440,'height':1080})
    page.goto('http://127.0.0.1:5173/consult?case=test-case')
    expect(page.locator('.rx-item')).to_have_count(2)
    page.locator('.rx-item').nth(1).locator('textarea').fill('第二項未儲存草稿')
    page.locator('.rx-item').nth(0).locator('input').first.fill('修改稀釋測試值')
    page.locator('.rx-item').nth(0).get_by_role('button',name='儲存變更').click()
    expect(page.locator('.rx-item').nth(0).locator('.modified-tag')).to_have_count(4)
    expect(page.locator('.rx-item').nth(1).locator('textarea')).to_have_value('第二項未儲存草稿')
    expect(page.get_by_role('button',name='開立並鎖定',exact=True)).to_be_disabled()
    page.locator('.rx-item').nth(1).get_by_role('button',name='儲存變更').click()
    page.get_by_label('田間狀況與病徵').fill('診斷書離線測試病徵')
    page.get_by_label('診斷結果（可並列多種病蟲害）').fill('候選甲與候選乙並存')
    page.get_by_role('button',name='儲存診察紀錄').click()
    expect(page.get_by_role('button',name='開立並鎖定',exact=True)).to_be_enabled()
    page.get_by_role('button',name='預覽診斷書',exact=True).click()
    expect(page.get_by_role('dialog',name='診斷書預覽')).to_be_visible()
    page.get_by_role('button',name='關閉預覽').click()
    page.get_by_role('button',name='開立並鎖定',exact=True).click()
    page.get_by_role('button',name='確認開立',exact=True).click()
    expect(page.get_by_text('已開立 · 已鎖定')).to_be_visible()
    expect(page.locator('.rx-item').first.locator('input').first).to_be_disabled()
    expect(page.get_by_label('田間狀況與病徵')).to_be_disabled()
    expect(page.get_by_role('button',name='重印診斷書',exact=True)).to_be_enabled()
    while page.locator('.toast').count(): page.locator('.toast').first.click()
    page.wait_for_load_state('networkidle')
    # PDF viewer 的解析不計入主頁 networkidle，截圖前留出插件繪製時間。
    page.wait_for_timeout(2000)
    page.screenshot(path=str(OUTPUT/'consult-issued-desktop.png'),full_page=True)
    assert sum(method=='GET' and path=='/consult/cases/test-case' for method,path in calls)==1, '保存單項不可重載其他草稿'
    assert not errors,errors
    browser.close()
    print('PASS: 辨識雙角色、多標籤回饋／匯出、照片、手機、處方局部儲存、PDF 預覽與開立鎖定')
