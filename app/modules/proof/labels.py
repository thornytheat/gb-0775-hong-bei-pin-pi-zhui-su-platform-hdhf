from datetime import date


def _day(today):
    return today.isoformat() if hasattr(today,'isoformat') else str(today)


def present(packages, labels, today=None):
    day=_day(today or date.today())
    shown={}
    for key,row in packages.items():
        label=labels.get(key)
        expiry=row['best_before']
        if label is not None:
            status='affected' if label.get('affected') else 'issued'
            sign_until=label['sign_until']
        elif expiry is None:
            status,sign_until='blocked_unknown',None
        elif expiry<day:
            status,sign_until='blocked_expired',None
        else:
            status,sign_until='signable',expiry
        shown[key]={**row,'label':label,'status':status,'sign_until':sign_until}
    return shown


def issuance(packages, ledger, run, today, stamp):
    day=_day(today)
    fresh, marks={},{}
    for key,row in packages.items():
        expiry=row['best_before']
        label=ledger.get(key)
        if label is None:
            if expiry is not None and expiry>=day:
                fresh[key]=dict(package=key,sign_until=expiry,run=run,issued_at=stamp,affected=None)
        elif label.get('affected') is None and (expiry is None or expiry<label['sign_until']):
            marks[key]=dict(run=run,evidence_date=expiry,marked_at=stamp)
    return fresh,marks
