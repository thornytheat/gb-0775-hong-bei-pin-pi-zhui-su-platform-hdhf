from datetime import UTC,date,datetime
from time import perf_counter_ns

from app.config import RULE_VERSION
from app.errors import RoastError
from app.modules.proof.labels import issuance
from app.modules.proof.recall import recall
from app.pipeline import compute

from .fingerprint import address, checksum, encode


def seal_run(cabinet, files, previous=None, today=None):
    start=perf_counter_ns()
    reports=compute(files)
    key,hashes=address(files)
    digest=checksum(encode(reports))
    if previous is not None and (previous['id'],previous['result_sha256'])!=(key,digest):
        raise RoastError('REPLAY_CHANGED','复算摘要与原卷联不同',409)
    manifest=dict(id=key,rules=RULE_VERSION,files=hashes,result_sha256=digest,
                  created_at=datetime.now(UTC).isoformat(timespec='seconds'),elapsed_ms=(perf_counter_ns()-start)//1_000_000)
    inserted=cabinet.write(dict(manifest=manifest,files=files,reports=reports))
    stored=cabinet.read(key)
    if stored['manifest']['result_sha256']!=digest or checksum(encode(stored['reports']))!=digest or stored['files']!=files:
        raise RoastError('REPLAY_CHANGED','存量归档未通过一致性核验',409)
    if reports['balance']['balanced']:
        fresh,marks=issuance(recall(reports),cabinet.read_labels(),key,today or date.today(),manifest['created_at'])
        if fresh or marks:
            cabinet.merge_labels(fresh,marks)
    return dict(id=key,manifest=stored['manifest'],reused=not inserted,balance=reports['balance'])
