"""整数化压力与边界测试：5000 行上限、不可行快速判定、确定性、零放松常见情形。"""
import random
import time

from src.services.integerization_service import (
    integerize, RangeInsufficient, StructuralInfeasible,
    TargetNonInteger, _feasible,
)

DIMS = ('zone', 'age_band', 'mode')


def unit(code, z, a, m, w):
    return {'code': code, 'zone': z, 'age_band': a, 'mode': m,
            'trip_minutes': 15, 'base_weight': 1.0, 'weight': w}


def targets_from(rows, dims, jitter=0):
    out = []
    base_total = None
    for dim in dims:
        groups = {}
        for r in rows:
            groups[r[dim]] = groups.get(r[dim], 0.0) + r['weight']
        vals = {cat: round(v) for cat, v in groups.items()}
        if base_total is None:
            base_total = sum(vals.values())
        else:  # 强制各维总量一致
            diff = base_total - sum(vals.values())
            k0 = sorted(vals)[0]
            vals[k0] += diff
        for cat, pop in vals.items():
            out.append({'dimension': dim, 'category': cat,
                        'population': pop + (jitter if cat == sorted(vals)[0] else 0)})
    return out


# 1) 5000 行上限性能
random.seed(7)
rows = []
for i in range(5000):
    rows.append(unit(f'U{i:05d}', f'z{random.randint(1,3)}',
                     f'a{random.randint(1,3)}', f'm{random.randint(1,3)}',
                     random.uniform(8, 25)))
t5k = targets_from(rows, DIMS)
start = time.perf_counter()
res = integerize(rows, t5k, relax=0)
dt = time.perf_counter() - start
assert dt < 5.0, f'5000 行超时 {dt}'
assert res['all_categories_match'] and res['total_matches']
assert all(c['within_floor_ceil'] for c in res['weight_changes'])
print(f'ok - 5000 行 k=0 {dt*1000:.0f}ms，改动 {res["moved_count"]} 条，全部对齐')

# 2) 确定性：同样输入两次结果一致
res2 = integerize(rows, t5k, relax=0)
assert res2['integer_weights'] == res['integer_weights']
print('ok - 求解结果确定性（两次一致）')

# 3) k=0 是“受控逐条取整”：每个整数权重都在 floor/ceil
assert all(c['floor'] <= c['integer_weight'] <= c['ceil']
           for c in res['weight_changes'])
print('ok - k=0 每条权重严格限于原值上下整数')

# 4) 不可行（范围不足）应快速失败而不是跑满超时
small = [unit('A', 'z1', 'a1', 'm1', 20.4), unit('B', 'z1', 'a2', 'm2', 20.6),
         unit('C', 'z2', 'a1', 'm2', 30.0), unit('D', 'z2', 'a2', 'm1', 29.0)]
t_bad = [
    {'dimension': 'zone', 'category': 'z1', 'population': 43},
    {'dimension': 'zone', 'category': 'z2', 'population': 57},
    {'dimension': 'age_band', 'category': 'a1', 'population': 50},
    {'dimension': 'age_band', 'category': 'a2', 'population': 50},
    {'dimension': 'mode', 'category': 'm1', 'population': 49},
    {'dimension': 'mode', 'category': 'm2', 'population': 51},
]
start = time.perf_counter()
try:
    integerize(small, t_bad, relax=0)
    raise AssertionError('应范围不足')
except RangeInsufficient:
    print(f'ok - 范围不足快速判定 {(time.perf_counter()-start)*1000:.1f}ms')

# 5) 结构无解快速判定
t_struct = [dict(x, population=60 if x['category'] == 'z1' else
                 (40 if x['category'] == 'z2' else x['population'])) for x in t_bad]
start = time.perf_counter()
try:
    integerize(small, t_struct, relax=0)
    raise AssertionError('应结构无解')
except StructuralInfeasible:
    print(f'ok - 结构无解快速判定 {(time.perf_counter()-start)*1000:.1f}ms')

# 6) 边界：relax 参数校验
for bad in (-1, 21, 1.5, '0'):
    try:
        integerize(small, t_bad, relax=bad)
        raise AssertionError(f'应拒绝 relax={bad}')
    except Exception as e:  # noqa: BLE001
        assert getattr(e, 'reason', '') == 'bad_param'
print('ok - 放松范围参数边界校验')

# 7) 边界：空目标 / 非整数目标
try:
    integerize(small, [], relax=0)
    raise AssertionError('应拒绝空目标')
except (TargetNonInteger, KeyError):
    pass
try:
    integerize(small, [dict(x, population=x['population'] + 0.3) for x in t_bad], 0)
    raise AssertionError('应拒绝非整数目标')
except TargetNonInteger:
    print('ok - 非整数目标与空目标被拒绝')

# 8) 对账与改动明细字段完整
r = integerize(small, [  # 可解 k=2 的目标
    {'dimension': 'zone', 'category': 'z1', 'population': 43},
    {'dimension': 'zone', 'category': 'z2', 'population': 57},
    {'dimension': 'age_band', 'category': 'a1', 'population': 50},
    {'dimension': 'age_band', 'category': 'a2', 'population': 50},
    {'dimension': 'mode', 'category': 'm1', 'population': 49},
    {'dimension': 'mode', 'category': 'm2', 'population': 51},
], relax=2)
assert len(r['reconciliations']) == 6
assert len(r['weight_changes']) == 4
for item in r['weight_changes']:
    assert set(item) == {'code', 'decimal_weight', 'floor', 'ceil',
                         'integer_weight', 'delta', 'within_floor_ceil'}
for item in r['reconciliations']:
    assert set(item) == {'dimension', 'category', 'sample_count', 'target',
                         'decimal_weighted', 'integer_weighted',
                         'integer_residual', 'matches_target'}
print('ok - 逐类对账与权重改动明细字段完整')

# 9) 传播单元：明显不可行的约束立刻判否
assert _feasible([(0, 1), (0, 1)], [([0, 1], 5)]) is None
assert _feasible([(2, 4), (2, 4)], [([0, 1], 5)]) is not None
print('ok - 边界传播单元正确')

# 10) 结构无解在任意放松范围下都不能被误判成范围不足
for k in (0, 5, 8, 20):
    try:
        integerize(small, t_struct, relax=k)
        raise AssertionError(f'k={k} 应判结构无解')
    except StructuralInfeasible:
        pass
print('ok - 结构无解在 k=0..20 均稳定识别，不误报范围不足')

print('\n压力与边界测试全部通过。')
