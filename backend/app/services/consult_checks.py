"""開單檢查保持純函式，讓日期、成分與施藥歷史都能離線驗證。"""
from datetime import date
from app.services.resistance import MoaCode, parse_moa_codes, shares_moa

# 連續第幾次共用同一機制才值得在案件層級喊一聲。
# 這個門檻跟逐項檢查一致（逐項是「最近兩次＋本次」共用才 warning），
# 差別只在案件層級會把整段序列攤出來，並且算得出連續超過 3 次的情況。
ROTATION_ALERT_STREAK = 3


def prescription_checks(*, today: date, expected_harvest_on: date | None,
                        phi_days: int | None, ingredient_names: list[str],
                        banned_names: set[str], moa_codes: list[str], history: list[dict]) -> list[dict]:
    banned={n.strip().casefold() for n in banned_names}
    hits=sorted({n for n in ingredient_names if n.strip().casefold() in banned})
    results=[{'type':'banned','level':'block' if hits else ('ok' if ingredient_names else 'warning'),
        'message':('成分涉及禁用名單，不可開立：'+ '、'.join(hits)) if hits else (
            '已辨識成分未命中本次禁用名單。' if ingredient_names else '成分未能辨識，請核對來源原文。'),
        'ingredients':hits,'checked_on':today.isoformat()}]
    remaining=(expected_harvest_on-today).days if expected_harvest_on else None
    shortfall=max(phi_days-remaining,0) if phi_days is not None and remaining is not None else None
    if phi_days is None:
        level,message='warning','採收期未載明，請看原文。'
    elif remaining is None:
        level,message='warning','未填預計採收日，無法確認安全採收期間距。'
    elif shortfall:
        level,message='block',f'距預計採收 {remaining} 天，小於安全採收期 {phi_days} 天，尚差 {shortfall} 天，不可開立。'
    else:
        level,message='ok',f'安全採收期 {phi_days} 天 ≤ 距預計採收 {remaining} 天；本檢查以今日施用為基準。'
    results.append({'type':'phi','level':level,'message':message,'phi_days':phi_days,
        'remaining_days':remaining,'shortfall_days':shortfall,'checked_on':today.isoformat()})
    # 不略過缺代碼的中間紀錄，否則會把非連續的兩次誤判成連續。
    recent=sorted([r for r in history if r['applied_on']<=today],
        key=lambda r:(r['applied_on'],str(r.get('created_at') or ''),r['id']),reverse=True)[:2]
    candidate=parse_moa_codes(moa_codes)
    previous=[parse_moa_codes(r.get('moa_codes')) for r in recent]
    shared=shares_moa(candidate, shares_moa(*previous)) if len(previous)==2 else []
    # 輪替僅檢查 FRAC／IRAC；不能把不同分類制度的相同數字視為同組。
    shared=[c for c in shared if c.scheme in ('FRAC','IRAC')]
    groups=sorted(str(c) for c in shared)
    if groups:
        level,message='warning','最近兩次實際施藥連續使用 '+ '、'.join(groups)+'，本次也共用此機制，建議評估更換組別。'
    elif not candidate or len(previous)<2 or not all(previous):
        level,message='warning','施藥歷史不足或作用機制未載明，無法完整檢查連續輪替。'
    else:
        level,message='ok','本次未命中近兩次連續共用的 FRAC／IRAC 機制；仍請核對完整施藥紀錄。'
    results.append({'type':'rotation','level':level,'message':message,'groups':groups,
        'record_ids':[r['id'] for r in recent],'checked_on':today.isoformat()})
    return results


def rotation_streak(candidate: list[MoaCode], previous: list[list[MoaCode]]) -> tuple[int, list[MoaCode]]:
    """從最近一次往回數，連續幾次跟本次共用同一個 FRAC／IRAC 機制。

    回傳 (連續次數含本次, 一路共用到底的機制)。

    交集是**逐次收窄**的：A 次共用 FRAC 3，B 次共用 FRAC 11，兩次不是同一個機制，
    不能算成「連續兩次同機制」。中間有一次缺代碼就停——不知道等於不能宣稱連續。
    """
    shared = [c for c in candidate if c.scheme in ('FRAC', 'IRAC')]
    if not shared:
        return 1, []
    streak = 1
    # 「共用」要真的跟某一次歷史共用過才算。沒有歷史時本次自己的機制不是共用，
    # 否則第一次開這支藥就會被說成「連續使用 FRAC 3」，連帶把預篩排除掉。
    sustained: list[MoaCode] = []
    for codes in previous:
        if not codes:
            break
        narrowed = shares_moa(shared, codes)
        if not narrowed:
            break
        shared = narrowed
        sustained = narrowed
        streak += 1
    return streak, sustained


def rotation_summary(*, today: date, item_codes: list[list[str]], history: list[dict],
                     window: int = 5) -> dict:
    """案件層級的輪替摘要：連續次數、施藥序列、要排除的機制。

    跟 `prescription_checks` 的 rotation 差在**看的是整張處方**：
    同一張處方開了三支藥，只要其中一支延續了前幾次的機制就該提醒，
    逐項提示各自成立卻沒有人把它們加起來看，正是上一輪留下來的缺口。

    `item_codes` 是這張處方每一項的原始機制代碼；history 由新到舊。
    """
    recent = sorted(history, key=lambda r: (r['applied_on'], str(r.get('created_at') or ''), str(r['id'])),
                    reverse=True)[:window]
    # 整張處方的機制聯集：任何一項延續了前次機制都算。
    candidate: list[MoaCode] = []
    for codes in item_codes:
        for code in parse_moa_codes(codes):
            if code not in candidate:
                candidate.append(code)
    previous = [parse_moa_codes(r.get('moa_codes')) for r in recent]
    streak, shared = rotation_streak(candidate, previous)
    groups = sorted(str(c) for c in shared)

    sequence = [{'applied_on': r['applied_on'].isoformat() if hasattr(r['applied_on'], 'isoformat') else r['applied_on'],
                 'product_text': r.get('product_text') or '',
                 'groups': sorted(str(c) for c in parse_moa_codes(r.get('moa_codes'))),
                 'current': False}
                for r in reversed(recent)]
    sequence.append({'applied_on': today.isoformat(), 'product_text': '本次處方',
                     'groups': sorted(str(c) for c in candidate), 'current': True})

    if groups and streak >= ROTATION_ALERT_STREAK:
        level = 'crit'
        message = f'這張處方連續第 {streak} 次使用 {"、".join(groups)}，建議改用不同作用機制。'
    elif not candidate:
        level, message = 'unknown', '本次處方的作用機制未載明，無法檢查連續輪替，請看原文。'
    elif len(previous) < ROTATION_ALERT_STREAK - 1 or not all(previous):
        level, message = 'unknown', '施藥歷史不足或其中有紀錄未載明機制，無法完整檢查連續輪替。'
    else:
        level, message = 'ok', '本次未連續共用同一個 FRAC／IRAC 機制；仍請核對完整施藥紀錄。'

    return {'level': level, 'message': message, 'groups': groups, 'streak': streak,
            'sequence': sequence,
            # 給 RegistrationPicker 預篩用：要換的就是這些機制以外的藥。
            'exclude_groups': groups,
            'checked_on': today.isoformat()}
