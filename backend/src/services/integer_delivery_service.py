"""整数化交付的应用服务：校验运行与审批、调用求解、归档结果、区分失败原因。"""
from sqlalchemy import select
from fastapi import HTTPException

from src.models import WeightReview, IntegerDelivery, Audit
from src.utils.serializers import serialize
from .run_service import get_run
from .integerization_service import (
    integerize,
    IntegerizationError,
    TargetNonInteger,
    RangeInsufficient,
    StructuralInfeasible,
    SolverTimeout,
)

# 失败原因 -> HTTP 状态码。无解类与超时类刻意分开。
_STATUS = {
    'target_non_integer': 422,
    'range_insufficient': 422,
    'structural_infeasible': 422,
    'timeout': 504,
}


def _approved_run(db, run_id):
    run = get_run(db, run_id)
    if run.status != 'completed' or not run.result:
        raise HTTPException(409, '校准尚未完成，无法整数化')
    review = db.scalar(select(WeightReview).where(
        WeightReview.run_id == run_id, WeightReview.decision == 'approved'))
    if not review:
        raise HTTPException(409, '请先由负责人批准权重口径，再进行整数化交付')
    return run


def _targets_from_result(result):
    """从校准结果取边际目标：优先显式 targets，否则由诊断明细还原。"""
    if result.get('targets'):
        return result['targets']
    return [{'dimension': d['dimension'], 'category': d['category'],
             'population': d['target']} for d in result['diagnostics']]


def solve_delivery(db, run_id, relax, user):
    """求解并归档；失败按原因映射为不同 HTTP 状态，绝不返回半成品。"""
    run = _approved_run(db, run_id)
    assignments = run.result['assignments']
    targets = _targets_from_result(run.result)
    try:
        result = integerize(assignments, targets, relax=relax)
    except TargetNonInteger as exc:
        return _fail(db, run_id, relax, user, exc, extra={'non_integer': exc.non_integer})
    except RangeInsufficient as exc:
        return _fail(db, run_id, relax, user, exc, extra={'blocking': exc.blocking})
    except StructuralInfeasible as exc:
        return _fail(db, run_id, relax, user, exc)
    except SolverTimeout as exc:
        # 超时不写“失败解”，只留可区分的审计记录后按 504 拒绝交付
        record_delivery(db, run_id, relax, user, 'failed', 'timeout',
                        {'message': exc.message, 'reason': 'timeout',
                         'seconds': exc.seconds})
        db.commit()
        raise HTTPException(_STATUS['timeout'],
                            {'reason': 'timeout', 'message': exc.message,
                             'seconds': exc.seconds})
    except IntegerizationError as exc:
        raise HTTPException(400, exc.message)

    record_delivery(db, run_id, relax, user, 'solved', None, result)
    db.commit()
    return latest_delivery(db, run_id)


def _fail(db, run_id, relax, user, exc, extra=None):
    detail = {'message': exc.message, 'reason': exc.reason}
    if extra:
        detail.update(extra)
    record_delivery(db, run_id, relax, user, 'failed', exc.reason, detail)
    db.commit()
    raise HTTPException(_STATUS[exc.reason], detail)


def record_delivery(db, run_id, relax, user, status, reason, detail):
    """同一 (运行, 放松范围) 覆盖旧记录，保留最新一次求解；同时写审计留痕。"""
    existing = db.scalar(select(IntegerDelivery).where(
        IntegerDelivery.run_id == run_id, IntegerDelivery.relax == relax))
    if existing:
        existing.status = status
        existing.failure_reason = reason
        existing.detail = detail
        existing.created_by = user.id
        row = existing
    else:
        row = IntegerDelivery(run_id=run_id, relax=relax, status=status,
                              failure_reason=reason, detail=detail,
                              created_by=user.id)
        db.add(row)
        db.flush()
    action = 'integer_solved' if status == 'solved' else 'integer_failed'
    db.add(Audit(actor=user.email, action=action,
                 target=f'{run_id}:k{relax}:{reason or "ok"}'))
    return row


def list_deliveries(db, run_id):
    get_run(db, run_id)
    rows = db.scalars(select(IntegerDelivery).where(
        IntegerDelivery.run_id == run_id).order_by(
        IntegerDelivery.relax, IntegerDelivery.id.desc())).all()
    # 每个 relax 只取最新一条
    seen = set()
    out = []
    for row in rows:
        if row.relax in seen:
            continue
        seen.add(row.relax)
        out.append(serialize(row))
    return out


def latest_delivery(db, run_id):
    row = db.scalar(select(IntegerDelivery).where(
        IntegerDelivery.run_id == run_id).order_by(IntegerDelivery.id.desc()))
    if not row:
        raise HTTPException(404, '尚无整数化结果，请先求解')
    return serialize(row)


def get_solved(db, run_id, relax):
    row = db.scalar(select(IntegerDelivery).where(
        IntegerDelivery.run_id == run_id,
        IntegerDelivery.relax == relax).order_by(IntegerDelivery.id.desc()))
    if not row or row.status != 'solved':
        raise HTTPException(404, '该放松范围下没有可交付的整数解，请先在页面成功求解')
    return row
