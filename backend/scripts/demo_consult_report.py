"""產出可分享的暫定版型；只用明確標示的展示資料，不寫入正式案件。"""
from pathlib import Path
from app.services.consult_report import render_report_pdf

ROOT=Path(__file__).resolve().parents[2]


def demo_case():
    return {
        'case_no':'DEMO-田間複合判讀','status':'draft','issued_at':None,
        'farmer_text':'展示委託人（虛構案例）','crop_text':'示範作物','variety':'展示品種',
        'field_text':'展示田區（非實際地址）','area_text':'未填','expected_harvest_on':None,
        'examined_by':['photo'],'diagnosis_basis':['visual'],
        'symptoms':'【僅供功能展示】葉面有散在斑點，葉背另有聚集痕跡。不同部位的現象可能同時存在；需補充全株、葉背近照與健康對照。',
        'diagnosis_text':'【展示判讀】葉斑類症候與刺吸式害蟲候選並存；病原及蟲種尚待確認，另需排除非生物性逆境。',
        'cultural_control':'【展示紀錄】補充田間分布、發生日期與近期施作，保留受影響與健康部位的對照影像；依後續確認結果調整處置。',
        'items':[{
            'product_text':'展示藥劑欄位（非實際農藥）','ingredient_text':'成分待查核','moa_text':'未載明',
            'dilution':None,'dose':None,'frequency':'尚未決定施用方式','phi_days':None,
            'advice':'此列僅展示表單功能，不得據此施藥。','modified_fields':['advice'],
            'checks':[{'type':'phi','level':'warning','message':'展示提醒：未填採收日及安全採收期，需由診療師查核。'},
                {'type':'rotation','level':'warning','message':'展示提醒：尚未取得同田區實際施藥紀錄。'}],
            'source_values':{'advice':'','comparison':{'application_method':'','sources':{},'mrls':[]}},
        }],
    }


if __name__=='__main__':
    data=render_report_pdf(demo_case())
    for target in [ROOT/'output/pdf/diagnosis-report-demo.pdf',ROOT/'frontend/public/demo/diagnosis-report.pdf']:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
        print(target)
