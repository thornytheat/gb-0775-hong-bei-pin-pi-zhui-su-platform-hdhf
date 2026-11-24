from datetime import date

import pytest

from app.main import create_app
from app.modules.proof.labels import present
from app.modules.proof.recall import recall
from app.modules.proof.service import verify_and_export
from app.modules.runs.cabinet import Cabinet
from app.modules.runs.service import seal_run
from app.pipeline import compute

TODAY = date(2026, 11, 24)


def dossier(origin_rows):
    return {
        'origins': 'origin,label,available,unit,best_before\n' + origin_rows,
        'jobs': 'work,label,measured,unit\nW-1,一批,1000,g\nW-2,二批,600,g\n',
        'inputs': ('ticket,work,source_kind,source,quantity,unit\n'
                   'IN-1,W-1,origin,OR-1,600,g\nIN-2,W-1,origin,OR-2,400,g\n'
                   'IN-3,W-2,return,RT-1,300,g\nIN-4,W-2,origin,OR-1,300,g\n'),
        'outputs': ('ticket,work,kind,gross,tare,unit\n'
                    'PK-1,W-1,pack,700,10,g\nRT-1,W-1,return,300,0,g\nPK-2,W-2,pack,600,0,g\n'),
    }


@pytest.fixture
def dated():
    return dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,2026-12-10\n')


@pytest.fixture
def store(tmp_path):
    return Cabinet(tmp_path / 'labels')


def test_return_coupon_carries_expiry(dated):
    jobs = compute(dated)['lineage']['jobs']
    assert jobs['W-1']['best_before'] == '2026-12-10'
    assert jobs['W-2']['best_before'] == '2026-12-10'  # 经 RT-1 返料传入，不只看直接投料


def test_unknown_origin_date_blocks_whole_blend():
    files = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,-\n')
    jobs = compute(files)['lineage']['jobs']
    assert jobs['W-1']['best_before'] is None and jobs['W-2']['best_before'] is None


def test_signable_packages_issued_at_seal(dated, store):
    seal_run(store, dated, today=TODAY)
    labels = store.read_labels()
    assert {key: row['sign_until'] for key, row in labels.items()} == {'PK-1': '2026-12-10', 'PK-2': '2026-12-10'}


def test_expired_and_unknown_never_issued(store):
    expired = dossier('OR-1,甲产地,5000,g,2026-11-01\nOR-2,乙产地,5000,g,2027-01-31\n')
    unknown = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,未知\n')
    seal_run(store, expired, today=TODAY)
    seal_run(store, unknown, today=TODAY)
    assert store.read_labels() == {}


def test_late_dates_rejudge_unsigned_packages(store):
    undated = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,-\n')
    first = seal_run(store, undated, today=TODAY)
    assert store.read_labels() == {}
    completed = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,2026-12-10\n')
    second = seal_run(store, completed, today=TODAY)
    assert second['id'] != first['id']
    assert set(store.read_labels()) == {'PK-1', 'PK-2'}


def test_issued_label_frozen_but_flagged(dated, store):
    seal_run(store, dated, today=TODAY)
    earlier = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,2026-11-01\n')
    late = seal_run(store, earlier, today=TODAY)
    labels = store.read_labels()
    assert len(labels) == 2  # 包装数不新增
    for key in ('PK-1', 'PK-2'):
        assert labels[key]['sign_until'] == '2026-12-10'  # 已发标签不改换
        assert labels[key]['affected'] == {'run': late['id'], 'evidence_date': '2026-11-01',
                                           'marked_at': labels[key]['issued_at']}


def test_rerun_keeps_labels_and_count(dated, store):
    first = seal_run(store, dated, today=TODAY)
    before = store.read_labels()
    again = seal_run(store, dated, previous=store.read(first['id'])['manifest'], today=TODAY)
    assert again['reused'] and store.read_labels() == before


def test_unbalanced_run_issues_nothing(files, store, monkeypatch):
    reports = compute(files)
    reports['balance']['balanced'] = False
    monkeypatch.setattr('app.modules.runs.service.compute', lambda *args: reports)
    seal_run(store, files, today=TODAY)
    assert store.read_labels() == {}


def test_present_statuses(dated):
    packages = recall(compute(dated))
    shown = present(packages, {}, TODAY)
    assert shown['PK-1']['status'] == 'signable' and shown['PK-1']['sign_until'] == '2026-12-10'
    stale = present(packages, {}, date(2026, 12, 11))
    assert stale['PK-1']['status'] == 'blocked_expired' and stale['PK-1']['sign_until'] is None
    labelled = present(packages, {'PK-1': dict(sign_until='2026-12-10', affected=None)}, TODAY)
    assert labelled['PK-1']['status'] == 'issued'


def test_dirty_sample_blocking(files, store):
    key = seal_run(store, files, today=TODAY)['id']
    assert set(store.read_labels()) == {'PK-D'}  # 只有日期齐全且未过期的能发
    proof = verify_and_export(store.read(key), store.read_labels(), TODAY)
    status = {key: row['status'] for key, row in proof['packages'].items()}
    assert status == {'PK-A': 'blocked_expired', 'PK-B': 'blocked_unknown',
                      'PK-C': 'blocked_unknown', 'PK-D': 'issued'}


def test_export_marks_affected_after_late_arrival(tmp_path):
    cabinet_dir = tmp_path / 'api'
    client = create_app(cabinet_dir).test_client()
    shipped = dossier('OR-1,甲产地,5000,g,2099-01-31\nOR-2,乙产地,5000,g,2098-12-10\n')
    client.post('/api/runs', json={'files': shipped})
    corrected = dossier('OR-1,甲产地,5000,g,2099-01-31\nOR-2,乙产地,5000,g,2026-11-01\n')
    second = client.post('/api/runs', json={'files': corrected}).json
    proof = client.get(f"/api/export/{second['id']}").json
    row = proof['packages']['PK-1']
    assert row['status'] == 'affected' and row['sign_until'] == '2098-12-10'
    assert row['label']['affected']['evidence_date'] == '2026-11-01'
    again = client.post(f"/api/runs/{second['id']}/rerun").json
    assert again['reused'] and len(Cabinet(cabinet_dir).read_labels()) == 2


def test_proof_page_shows_blocking(client):
    page = client.get('/ledger/proof?sample=dirty').get_data(as_text=True)
    assert '挡住 · 日期不明' in page and '挡住 · 已过期 2026-11-20' in page


def test_malformed_date_is_unknown_not_rejected():
    files = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-2,乙产地,5000,g,2026-13-99\n')
    report = compute(files)
    assert report['weighins']['tables']['origins']['OR-2']['best_before'] is None
    assert not report['weighins']['rejected']
    assert report['lineage']['jobs']['W-1']['best_before'] is None


def test_conflicting_origin_dates_isolated():
    files = dossier('OR-1,甲产地,5000,g,2027-01-31\nOR-1,甲产地,5000,g,2027-02-02\n'
                    'OR-2,乙产地,5000,g,2026-12-10\n')
    report = compute(files, 'weighins')['weighins']
    assert [row['code'] for row in report['rejected']] == ['TICKET_CONFLICT'] * 2
    assert 'OR-1' not in report['tables']['origins']
