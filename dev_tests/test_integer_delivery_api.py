"""整数化交付的端到端集成测试：SQLite + FastAPI TestClient（不依赖 MySQL/Redis）。

覆盖：成功求解与逐类对账、三类无解原因的区分、超时独立 504、
审批/权限门控、整数与小数两类 CSV 导出、审计留痕与结果归档。
运行：/tmp/bal_venv/bin/python 本文件（venv 已装 fastapi/sqlalchemy/pydantic/httpx）。
"""
import os
import sys

os.environ['DATABASE_URL'] = 'sqlite+pysqlite:///:memory:'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['SEED_DEMO'] = 'false'

sys.path.insert(0, '/workspace/backend')

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import src.config.database as db_config  # noqa: E402
from src.config.database import Base  # noqa: E402
from src.models import (  # noqa: E402
    User, SampleBatch, SampleUnit, MarginTarget, WeightRun,
    WeightReview, IntegerDelivery, Audit,
)
from src.routes import integer_deliveries, runs, reviews, auth  # noqa: E402
from src.services.raking_algorithm import calibrate  # noqa: E402
from src.utils.security import hash_password, token_for  # noqa: E402

# 让应用层与测试共用同一个内存引擎；StaticPool 保证所有连接共享同一内存库
engine = create_engine(
    'sqlite+pysqlite:///:memory:',
    connect_args={'check_same_thread': False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
db_config.engine = engine
db_config.SessionLocal = TestingSession
Base.metadata.create_all(engine)


def override_session():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app = FastAPI()
for module in (auth, integer_deliveries, runs, reviews):
    app.include_router(module.router)
app.dependency_overrides[db_config.session] = override_session
client = TestClient(app)

DIMS = ('zone', 'age_band', 'mode')


def build_result(weighted_units, targets, *, converged=True):
    """构造最小可用的校准结果 JSON（整数化层只依赖 assignments/targets/checksum）。"""
    assignments = [dict(u) for u in weighted_units]
    return {
        'converged': converged,
        'iterations': 1,
        'max_residual': 0.0,
        'target_total': sum(t['population'] for t in targets
                            if t['dimension'] == 'zone'),
        'weight_total': sum(u['weight'] for u in weighted_units),
        'sample_count': len(weighted_units),
        'effective_sample_size': 1.0,
        'design_effect': 1.0,
        'weight_min': min(u['weight'] for u in weighted_units),
        'weight_max': max(u['weight'] for u in weighted_units),
        'weight_cap': 999.0,
        'unweighted_mean': 20.0,
        'weighted_mean': 20.0,
        'diagnostics': [],
        'trace': [],
        'assignments': assignments,
        'targets': targets,
        'checksum': 'testchecksum0000',
        'method_note': '测试用合成结果',
    }


def seed_run(weighted_units, targets, *, approved=True, converged=True,
             use_calibrate=False):
    seed_run._n += 1
    seq = seed_run._n
    db = TestingSession()
    admin = User(email=f'a{seq}@t.test', name='负责人', role='admin',
                 password_hash=hash_password('pw123456'))
    analyst = User(email=f'n{seq}@t.test', name='分析师',
                   role='analyst', password_hash=hash_password('pw123456'))
    viewer = User(email=f'v{seq}@t.test', name='阅读者',
                  role='viewer', password_hash=hash_password('pw123456'))
    db.add_all([admin, analyst, viewer])
    db.flush()
    batch = SampleBatch(code=f'B{seq}', name='测试批', source_file='s.csv')
    db.add(batch)
    db.flush()
    for u in weighted_units:
        db.add(SampleUnit(batch_id=batch.id,
                          **{k: u[k] for k in ('code', 'zone', 'age_band', 'mode',
                                               'trip_minutes', 'base_weight')}))
    for t in targets:
        db.add(MarginTarget(batch_id=batch.id, **t))
    if use_calibrate:
        plain = [{k: u[k] for k in ('code', 'zone', 'age_band', 'mode',
                                    'trip_minutes', 'base_weight')}
                 for u in weighted_units]
        result = calibrate(plain, targets, 0.001, 500, 12)
    else:
        result = build_result(weighted_units, targets, converged=converged)
    run = WeightRun(name='测试运行', batch_id=batch.id, tolerance=0.001,
                    max_iterations=500, cap_ratio=12, status='completed',
                    result=result, created_by=analyst.id)
    if not converged:
        run.result = {**result, 'converged': False}
    db.add(run)
    db.flush()
    if approved:
        db.add(WeightReview(run_id=run.id, decision='approved',
                            note='测试批准口径足够长的说明', reviewer_id=admin.id))
    db.commit()
    ids = {'admin': admin.id, 'analyst': analyst.id, 'viewer': viewer.id,
           'run': run.id}
    db.close()
    return ids


seed_run._n = 0


def auth_header(user_id):
    return {'Authorization': f'Bearer {token_for(type("U", (), {"id": user_id})())}'}


def units4(weights):
    return [
        {'code': 'A', 'zone': 'z1', 'age_band': 'a1', 'mode': 'm1',
         'trip_minutes': 20, 'base_weight': 1.0, 'weight': weights[0]},
        {'code': 'B', 'zone': 'z1', 'age_band': 'a2', 'mode': 'm2',
         'trip_minutes': 22, 'base_weight': 1.0, 'weight': weights[1]},
        {'code': 'C', 'zone': 'z2', 'age_band': 'a1', 'mode': 'm2',
         'trip_minutes': 24, 'base_weight': 1.0, 'weight': weights[2]},
        {'code': 'D', 'zone': 'z2', 'age_band': 'a2', 'mode': 'm1',
         'trip_minutes': 26, 'base_weight': 1.0, 'weight': weights[3]},
    ]


TARGETS_OK = [
    {'dimension': 'zone', 'category': 'z1', 'population': 50},
    {'dimension': 'zone', 'category': 'z2', 'population': 50},
    {'dimension': 'age_band', 'category': 'a1', 'population': 50},
    {'dimension': 'age_band', 'category': 'a2', 'population': 50},
    {'dimension': 'mode', 'category': 'm1', 'population': 60},
    {'dimension': 'mode', 'category': 'm2', 'population': 40},
]

# 1) 成功整数化：精确对齐总人数与各维类别，每条限于 floor/ceil
ids = seed_run(units4([30.4, 20.6, 19.7, 29.3]), TARGETS_OK)
r = client.post('/runs/integerize', json={'run_id': ids['run'], 'relax': 0},
                headers=auth_header(ids['analyst']))
assert r.status_code == 201, r.text
body = r.json()
assert body['status'] == 'solved' and body['failure_reason'] is None
d = body['detail']
assert d['integer_total'] == 100 and d['total_matches']
assert d['all_categories_match']
assert all(x['matches_target'] and x['integer_residual'] == 0
           for x in d['reconciliations'])
assert all(x['within_floor_ceil'] for x in d['weight_changes'])
print('ok - 成功求解：总人数与各维类别精确对齐，且限于上下整数')

# 2) 整数 CSV 导出，且原小数 CSV 仍可用
rc = client.get(f"/runs/{ids['run']}/export-integer?relax=0",
                headers=auth_header(ids['viewer']))
assert rc.status_code == 200 and 'integer_weight' in rc.text
assert 'attachment' in rc.headers['content-disposition']
for line in rc.text.splitlines()[1:]:
    if line.strip():
        int_cell = line.split(',')[7]
        assert float(int_cell) == int(float(int_cell))
rd = client.get(f"/runs/{ids['run']}/export",
                headers=auth_header(ids['viewer']))
assert rd.status_code == 200 and 'calibrated_weight' in rd.text
print('ok - 整数 CSV 可下载且为整数；原小数 CSV 继续可用')

# 3) viewer 无权发起整数化
r = client.post('/runs/integerize', json={'run_id': ids['run'], 'relax': 0},
                headers=auth_header(ids['viewer']))
assert r.status_code == 403
print('ok - 阅读者不能发起整数化（403）')

# 4) 未批准运行不能整数化，也不能导出
ids2 = seed_run(units4([30.4, 20.6, 19.7, 29.3]), TARGETS_OK, approved=False)
r = client.post('/runs/integerize', json={'run_id': ids2['run'], 'relax': 0},
                headers=auth_header(ids2['admin']))
assert r.status_code == 409
re = client.get(f"/runs/{ids2['run']}/export-integer",
                headers=auth_header(ids2['admin']))
assert re.status_code == 409
print('ok - 未批准运行被拒绝整数化与导出（409）')

# 5) 非整数目标 -> target_non_integer（422）
t_nonint = [dict(x, population=x['population'] + 0.5) for x in TARGETS_OK]
ids3 = seed_run(units4([30.4, 20.6, 19.7, 29.3]), t_nonint)
r = client.post('/runs/integerize', json={'run_id': ids3['run'], 'relax': 0},
                headers=auth_header(ids3['admin']))
assert r.status_code == 422
assert r.json()['detail']['reason'] == 'target_non_integer'
assert len(r.json()['detail']['non_integer']) >= 1
print('ok - 非整数目标被识别（422 target_non_integer）')

# 6) 取整范围不足 -> range_insufficient（422），放松后可解
units_r = units4([20.4, 20.6, 30.0, 29.0])
targets_range = [
    {'dimension': 'zone', 'category': 'z1', 'population': 43},
    {'dimension': 'zone', 'category': 'z2', 'population': 57},
    {'dimension': 'age_band', 'category': 'a1', 'population': 50},
    {'dimension': 'age_band', 'category': 'a2', 'population': 50},
    {'dimension': 'mode', 'category': 'm1', 'population': 49},
    {'dimension': 'mode', 'category': 'm2', 'population': 51},
]
ids4 = seed_run(units_r, targets_range)
r = client.post('/runs/integerize', json={'run_id': ids4['run'], 'relax': 0},
                headers=auth_header(ids4['admin']))
assert r.status_code == 422
assert r.json()['detail']['reason'] == 'range_insufficient'
assert r.json()['detail']['blocking']
r2 = client.post('/runs/integerize', json={'run_id': ids4['run'], 'relax': 2},
                 headers=auth_header(ids4['admin']))
assert r2.status_code == 201 and r2.json()['status'] == 'solved'
assert any(not x['within_floor_ceil'] for x in r2.json()['detail']['weight_changes'])
print('ok - 范围不足（422 range_insufficient）与放松后重解成功')

# 7) 样本结构无整数解 -> structural_infeasible（422，与范围不足区分）
targets_struct = [
    dict(x, population=60) if x['category'] == 'z1'
    else dict(x, population=40) if x['category'] == 'z2' else x
    for x in targets_range
]
ids5 = seed_run(units_r, targets_struct)
r = client.post('/runs/integerize', json={'run_id': ids5['run'], 'relax': 0},
                headers=auth_header(ids5['admin']))
assert r.status_code == 422
assert r.json()['detail']['reason'] == 'structural_infeasible'
print('ok - 结构无整数解（422 structural_infeasible），与范围不足分开')

# 8) 超时独立为 504，且不产生 solved 归档、小数导出仍可用
import src.services.integerization_service as svc  # noqa: E402
svc.MAX_NODES = 0  # 强制首个搜索节点即超预算，确定性触发 SolverTimeout
try:
    r = client.post('/runs/integerize', json={'run_id': ids['run'], 'relax': 0},
                    headers=auth_header(ids['admin']))
    assert r.status_code == 504, r.text
    assert r.json()['detail']['reason'] == 'timeout'
finally:
    svc.MAX_NODES = 200_000
db = TestingSession()
# 超时在该 (run, relax=0) 上只留下 failed/timeout 记录，不产出可交付整数解
timeout_row = db.scalars(select(IntegerDelivery).where(
    IntegerDelivery.run_id == ids['run'],
    IntegerDelivery.status == 'failed',
    IntegerDelivery.failure_reason == 'timeout')).first()
assert timeout_row is not None
assert not db.scalars(select(IntegerDelivery).where(
    IntegerDelivery.run_id == ids['run'],
    IntegerDelivery.relax == 0,
    IntegerDelivery.status == 'solved')).first()
# 另一放松范围的成功解不受影响
assert r2.json()['status'] == 'solved'
other_solved = db.scalars(select(IntegerDelivery).where(
    IntegerDelivery.run_id == ids4['run'],
    IntegerDelivery.status == 'solved')).first()
assert other_solved is not None
db.close()
# 小数权重导出不受整数化超时影响，继续可用
rd2 = client.get(f"/runs/{ids['run']}/export",
                 headers=auth_header(ids['viewer']))
assert rd2.status_code == 200
print('ok - 超时独立 504、留痕但不交付，小数权重导出仍可用')

# 9) 审计留痕包含成功与失败两类动作
db = TestingSession()
actions = {a.action for a in db.scalars(select(Audit)).all()}
assert 'integer_solved' in actions
assert 'integer_failed' in actions
db.close()
print('ok - 成功与失败的整数化操作均写入审计')

# 10) 列表接口返回该运行的归档（含成功与失败），字段完整
lst = client.get(f"/runs/{ids['run']}/integer-deliveries",
                 headers=auth_header(ids['viewer']))
assert lst.status_code == 200
items = lst.json()
assert items and all({'run_id', 'relax', 'status', 'failure_reason',
                      'detail', 'created_at'} <= set(x) for x in items)
assert any(x['status'] == 'failed' and x['failure_reason'] == 'timeout'
           for x in items)
# 另一运行的成功归档也可列出
lst2 = client.get(f"/runs/{ids4['run']}/integer-deliveries",
                  headers=auth_header(ids4['viewer']))
assert any(x['relax'] == 2 and x['status'] == 'solved' for x in lst2.json())
print('ok - 整数化归档列表可查，成功/失败记录与字段完整')

print('\n端到端集成测试全部通过。')
