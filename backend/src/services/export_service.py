import csv
import io

from fastapi import HTTPException
from sqlalchemy import select

from src.models import WeightReview
from .run_service import get_run
from .integer_delivery_service import get_solved

COLUMNS = ['code', 'zone', 'age_band', 'mode', 'trip_minutes', 'base_weight']


def _approved(db, run_id):
    run = get_run(db, run_id)
    review = db.scalar(select(WeightReview).where(
        WeightReview.run_id == run_id, WeightReview.decision == 'approved'))
    if not review:
        raise HTTPException(409, '请先由负责人批准权重口径，再导出交付')
    return run


def _safe(vals):
    return ["'" + x if isinstance(x, str) and x.startswith(('=', '+', '-', '@')) else x
            for x in vals]


def export_csv(db, run_id):
    """原小数权重导出，保持既有口径不变。"""
    run = _approved(db, run_id)
    out = io.StringIO()
    out.write('﻿')
    writer = csv.writer(out)
    writer.writerow(COLUMNS + ['calibrated_weight', 'input_sha256'])
    for row in run.result['assignments']:
        vals = _safe([row[k] for k in COLUMNS])
        writer.writerow(vals + [round(row['weight'], 8), run.result['checksum']])
    return out.getvalue()


def export_integer_csv(db, run_id, relax):
    """整数化交付导出：只输出已成功求解并归档的整数人数。"""
    run = _approved(db, run_id)
    delivery = get_solved(db, run_id, relax)
    detail = delivery.detail
    changes = {c['code']: c for c in detail['weight_changes']}
    out = io.StringIO()
    out.write('﻿')
    writer = csv.writer(out)
    writer.writerow(COLUMNS + ['decimal_weight', 'integer_weight',
                               'within_floor_ceil', 'relax', 'input_sha256'])
    for row in run.result['assignments']:
        base = _safe([row[k] for k in COLUMNS])
        ch = changes[row['code']]
        writer.writerow(base + [
            round(ch['decimal_weight'], 8),
            ch['integer_weight'],
            1 if ch['within_floor_ceil'] else 0,
            relax,
            run.result['checksum'],
        ])
    return out.getvalue()
