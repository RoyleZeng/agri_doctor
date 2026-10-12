"""本機展示驗收：只操作 demoadmin / democontent 範例，官方網路不重抓。
先跑 seed_demo_content.py；每次另加一筆標有走查字樣的事項與日誌，保留驗收證據。
"""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import re
from datetime import datetime
from zoneinfo import ZoneInfo
OUT=Path(__file__).resolve().parents[2] / 'docs/screenshots'; OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 c=b.new_context(viewport={'width':1440,'height':1000},locale='zh-TW')
 page=c.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://localhost:5173/login')
 page.get_by_placeholder('英數帳號或電子信箱').fill('demoadmin')
 page.get_by_placeholder('至少10個字元').fill('demo-walkthrough-2026')
 page.get_by_role('button',name='登入工作區 →').click()
 expect(page).to_have_url(re.compile('/admin/farm-content$'))
 expect(page.get_by_role('heading',name='作物與規劃基準')).to_be_visible()
 page.get_by_role('button',name='收合側邊欄',exact=True).click()
 expect(page.locator('aside.side')).to_have_class(re.compile('mini'))
 page.reload();expect(page.get_by_role('button',name='展開側邊欄',exact=True)).to_be_visible()
 page.get_by_role('button',name='展開側邊欄',exact=True).click()
 assert 'mini' not in page.locator('aside.side').get_attribute('class')
 page.get_by_label('作物範本',exact=False).select_option('demo-observation')
 expect(page.get_by_role('heading',name='內容與教學素材')).to_be_visible()
 page.screenshot(path=str(OUT/'farm-content-admin.png'),full_page=False)
 # 用後台實際儲存、發布示範內容，既有規劃應仍保留原快照。
 old_plan = page.request.get('http://localhost:5173/api/v1/planning/farmers/demoadmin').json()['plans'][0]
 previous = page.get_by_label('建議事項／觀察與防治詳細內容').input_value()
 updated = previous.split('\n【展示校對】')[0] + '\n【展示校對】' + datetime.now().isoformat(timespec='seconds')
 page.get_by_label('建議事項／觀察與防治詳細內容').fill(updated)
 page.get_by_role('button',name='儲存草稿',exact=True).click()
 expect(page.get_by_role('button',name='發布新版…',exact=True)).to_be_enabled()
 page.get_by_role('button',name='發布新版…',exact=True).click()
 page.get_by_role('button',name='確認發布',exact=True).click()
 expect(page.get_by_role('button',name='確認發布',exact=True)).to_have_count(0)
 after = page.request.get('http://localhost:5173/api/v1/planning/farmers/demoadmin').json()['plans'][0]
 assert after == old_plan
 # 已發布後台範本是真的保存：重開頁後仍讀到同一內容。
 page.reload(); page.get_by_label('作物範本',exact=False).select_option('demo-observation')
 expect(page.get_by_label('建議事項／觀察與防治詳細內容')).to_have_value(updated)
 page.goto('http://localhost:5173/farm')
 expect(page.locator('.farm-chart')).to_be_visible()
 assert page.locator('.farm-work-grid').bounding_box()['y'] < page.locator('.farm-chart').bounding_box()['y']
 assert page.locator('.farm-economics').bounding_box()['y'] < page.locator('.farm-chart').bounding_box()['y']
 page.screenshot(path=str(OUT/'farm-overview-top.png'))
 page.locator('.event-bar.stage').first.click()
 dialog=page.get_by_role('dialog'); expect(dialog).to_be_visible()
 expect(dialog.locator('input, textarea, select')).to_have_count(0)
 expect(dialog.locator('img')).to_be_visible()
 expect(dialog.locator('video')).to_be_visible()
 page.screenshot(path=str(OUT/'farm-guidance-detail.png'))
 dialog.get_by_role('button',name='關閉視窗').click()
 page.locator('.event-bar.pest').first.click()
 expect(dialog.locator('input, textarea, select')).to_have_count(0)
 expect(dialog.get_by_role('button',name='記錄巡田觀察',exact=True).last).to_be_visible()
 dialog.get_by_role('button',name='關閉視窗').click()
 # 新增測試事項再由 UI 完成，驗證實際日期與日誌點。
 today=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
 title='走查：完成與日誌連動 ' + datetime.now().strftime('%H%M%S')
 api='http://localhost:5173/api/v1'
 headers={'X-Planning-Request':'1'}
 response=page.request.post(api+'/planning/plans/'+old_plan['id']+'/events',headers=headers,data=dict(version=after['version'],kind='care',title=title,start_on=today,end_on=today,detail='純驗收範例',note='',completed=False))
 assert response.ok, response.text()
 page.reload()
 page.get_by_role('button',name='完成並記錄：'+title,exact=True).click()
 expect(dialog.get_by_label('實際日期',exact=True)).to_have_value(today)
 expect(dialog.get_by_role('button',name='只標完成，不記錄')).to_have_count(0)
 dialog.locator('.wlf-save').click()
 expect(dialog).to_have_count(0)
 expect(page.locator('.log-dot').and_(page.get_by_role('button',name=re.compile(re.escape(title))))).to_have_count(1)
 workspace=page.request.get(api+'/planning/farmers/demoadmin').json()
 saved_event=next(e for plan in workspace['plans'] for e in plan['events'] if e['title']==title)
 assert saved_event['completed'] and saved_event['log_id'] and saved_event['log_done_on']==today
 page.goto('http://localhost:5173/farm/log')
 expect(page.locator('.flog-card').filter(has_text=title)).to_be_visible()
 page.screenshot(path=str(OUT/'farm-completion-log.png'))
 page.goto('http://localhost:5173/farmers/democontent/precision')
 expect(page.locator('.precision')).to_be_visible()
 expect(page.get_by_role('button',name='重新分析',exact=False)).to_be_visible()
 page.goto('http://localhost:5173/farm')
 page.set_viewport_size({'width':390,'height':844})
 page.get_by_role('button',name='開啟導覽選單').click()
 expect(page.locator('aside.side.open')).to_be_visible()
 page.get_by_role('button',name='開啟導覽選單').click()
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
 page.screenshot(path=str(OUT/'farm-sidebar-mobile.png'))
 print('actual browser admin, sidebar persistence/mobile, ordering, read-only guidance/media PASS; errors:',errors)
 assert not errors
 b.close()
