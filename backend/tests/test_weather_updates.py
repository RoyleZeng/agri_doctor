from copy import deepcopy
from datetime import datetime, timedelta
import httpx
import pytest
from pydantic import SecretStr
from app.services import weather
from app.services.source_updates import date_from_notice, semantic_changes

NOW = datetime(2026, 9, 8, 12, tzinfo=weather.TAIPEI)


def forecast(pop='70', low='25', high='34', county='臺南市'):
    return {'success':'true','records':{'location':[{'locationName':county,'weatherElement':[
        {'elementName':k,'time':[{'startTime':'2026-09-08 12:00:00','endTime':'2026-09-09 00:00:00','parameter':{'parameterName':v}}]}
        for k,v in [('Wx','多雲短暫陣雨'),('PoP',pop),('MinT',low),('MaxT',high)]
    ]}]}}


def test_weather_thresholds_use_valid_forecast_evidence():
    result=weather.parse_forecast(forecast(), '臺南市', NOW)
    assert [a['key'] for a in result['advice']] == ['rain','heat']
    assert result['periods'][0]['start_at'].endswith('+08:00')
    assert '發生機率' in result['advice'][0]['basis']


@pytest.mark.parametrize('bad', [None,'-99','101','nan','Infinity','未知'])
def test_missing_pop_does_not_become_zero_or_rain_alert(bad):
    result=weather.parse_forecast(forecast(pop=bad, high='29'), '臺南市', NOW)
    assert result['periods'][0]['rain_probability'] is None
    assert result['advice']==[]


def test_expired_forecast_produces_no_care_cues():
    result=weather.parse_forecast(forecast(), '臺南市', NOW+timedelta(days=2))
    assert result['status']=='expired' and result['advice']==[] and result['periods']==[]


@pytest.mark.parametrize('mutate', [lambda b:b.update(success='false'), lambda b:b['records']['location'][0].update(locationName='高雄市'), lambda b:b.update(records={})])
def test_invalid_or_wrong_county_fails_closed(mutate):
    body=forecast();mutate(body)
    with pytest.raises(ValueError): weather.parse_forecast(body,'臺南市',NOW)


def test_no_key_returns_no_mock_weather(monkeypatch):
    monkeypatch.setattr(weather.settings,'cwa_api_key',SecretStr(''))
    result=weather.get_forecast('臺南市',now=NOW)
    assert result['status']=='not_configured' and result['periods']==[]


def test_header_auth_cache_failure_and_expiry(monkeypatch):
    weather._CACHE.clear()
    monkeypatch.setattr(weather.settings,'cwa_api_key',SecretStr('test-cwa-secret'))
    requests=[]
    def handle(request):
        requests.append(request)
        assert request.headers['Authorization']=='test-cwa-secret'
        assert 'test-cwa-secret' not in str(request.url)
        return httpx.Response(200,json=forecast())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        r=weather.get_forecast('臺南市',now=NOW,client=client)
        c=weather.get_forecast('臺南市',now=NOW+timedelta(minutes=1),client=client)
        assert r['status']=='ok' and c['cached'] and len(requests)==1
        assert 'test-cwa-secret' not in str(r)
        expired=weather.get_forecast('臺南市',now=NOW+timedelta(days=1),client=client)
        assert expired['status']=='expired' and not expired['advice']
    weather._CACHE.clear()
    def fail(request): return httpx.Response(401,text='test-cwa-secret')
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        r=weather.get_forecast('臺南市',now=NOW,client=client)
        assert r['status']=='unavailable' and not r['advice'] and 'test-cwa-secret' not in str(r)


def record(kind, payload, url='https://consumer.fda.gov.tw/'):
    return {'kind':kind,'payload':payload,'url':url}


def test_mrl_diff_ignores_sequence_and_page_but_records_value():
    old=record('fda_mrl', {'seq':'1','international_name':'example','common_name':'測試','crop_category':'米類','limit_text':'0.2'})
    same=deepcopy(old);same['payload']['seq']='12';same['url']+='?p=2'
    assert semantic_changes('fda',[old],[same])==[]
    same['payload']['limit_text']='0.5'
    changes=semantic_changes('fda',[old],[same])
    assert len(changes)==1 and changes[0]['kind']=='fda_mrl_changed'
    assert changes[0]['before']['limit_text']=='0.2' and changes[0]['after']['limit_text']=='0.5'


def test_ban_diff_retains_distinct_manufacture_and_use_dates():
    ban=record('fda_banned', {'name':'測試成分','english_name':'test','banned_manufacture_date':'115年1月1日','banned_sale_date':'116年1月1日'})
    c=semantic_changes('fda',[],[ban])[0]
    assert c['effective_date']=='116年1月1日'
    removed=semantic_changes('fda',[ban],[])[0]
    assert '人工核對' in removed['title']


@pytest.mark.parametrize('value,expected', [('115年9月8日','2026-09-08'),('民國116.1.1','2027-01-01'),('2026-09-08','2026-09-08'),('分階段：115.1.1及116.1.1',None),('即日起',None),('115.2.30',None)])
def test_ban_dates_do_not_guess_ambiguous_phases(value,expected):
    parsed=date_from_notice(value)
    assert (parsed.isoformat() if parsed else None)==expected


def test_duplicate_mrl_conflicting_values_block_diff():
    a=record('fda_mrl', {'international_name':'test','crop_category':'米','limit_text':'1'})
    b=deepcopy(a);b['payload']['limit_text']='2'
    with pytest.raises(ValueError): semantic_changes('fda',[],[a,b])


def test_same_revision_different_pdf_hash_is_detected():
    a=record('kdais_document',{'title':'水稻摘要表','revision_date':'115.1.1','sha256':'abc'})
    b=deepcopy(a);b['payload']['sha256']='def'
    assert semantic_changes('kdais',[a],[b])[0]['kind']=='kdais_document_changed'
