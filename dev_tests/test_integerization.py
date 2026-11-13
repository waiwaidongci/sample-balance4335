"""整数化服务的离线单元测试（只用标准库）。运行：PYTHONPATH=/workspace/backend python3 -I 本文件。"""
import time

from src.services.integerization_service import (  # noqa: E402
    integerize,
    TargetNonInteger,
    RangeInsufficient,
    StructuralInfeasible,
    SolverTimeout,
)

DIMS = ('zone', 'age_band', 'mode')


def unit(code, zone, age, mode, w):
    return {'code': code, 'zone': zone, 'age_band': age, 'mode': mode,
            'trip_minutes': 20, 'base_weight': 1.0, 'weight': w}


def make_targets(totals_by_dim):
    out = []
    for dim, cats in totals_by_dim.items():
        for cat, pop in cats.items():
            out.append({'dimension': dim, 'category': cat, 'population': pop})
    return out


def check(name, cond):
    if not cond:
        raise AssertionError('FAILED: ' + name)
    print('ok -', name)


# 1) 基础受控取整：4 个样本，zone/age 两类别 50/50、mode 60/40，总目标 100
# 可行整数解 A=30 B=20 C=20 D=30，且都落在各自 floor/ceil 内
assignments = [
    unit('A', 'z1', 'a1', 'm1', 30.4),
    unit('B', 'z1', 'a2', 'm2', 20.6),
    unit('C', 'z2', 'a1', 'm2', 19.7),
    unit('D', 'z2', 'a2', 'm1', 29.3),
]
targets = make_targets({
    'zone': {'z1': 50, 'z2': 50},
    'age_band': {'a1': 50, 'a2': 50},
    'mode': {'m1': 60, 'm2': 40},
})
res = integerize(assignments, targets, relax=0)
check('k=0 总人数精确等于目标', res['integer_total'] == 100 and res['total_matches'])
check('k=0 全部类别对齐', res['all_categories_match'])
check('k=0 每条限于 floor/ceil', all(c['within_floor_ceil'] for c in res['weight_changes']))
for r in res['reconciliations']:
    check(f"对账 {r['dimension']}/{r['category']} 残差为0", r['integer_residual'] == 0)
check('整数权重为整数', all(isinstance(v, int) for v in res['integer_weights']))


# 2) 非整数目标 -> target_non_integer
bad_targets = make_targets({
    'zone': {'z1': 50.5, 'z2': 49.5},
    'age_band': {'a1': 50, 'a2': 50},
    'mode': {'m1': 50, 'm2': 50},
})
try:
    integerize(assignments, bad_targets, relax=0)
    raise AssertionError('FAILED: 应拒绝非整数目标')
except TargetNonInteger as e:
    check('非整数目标被识别', e.reason == 'target_non_integer' and len(e.non_integer) == 2)


# 3) 取整范围不足 -> range_insufficient，放松后可解
# 两个样本同属 z1，小数 20.4/20.6；z1 目标 43（floor 合 40、ceil 合 42，k=0 够不到）
assignments2 = [
    unit('A', 'z1', 'a1', 'm1', 20.4),
    unit('B', 'z1', 'a2', 'm2', 20.6),
    unit('C', 'z2', 'a1', 'm2', 30.0),
    unit('D', 'z2', 'a2', 'm1', 29.0),
]
targets2 = make_targets({
    'zone': {'z1': 43, 'z2': 57},
    'age_band': {'a1': 50, 'a2': 50},
    'mode': {'m1': 49, 'm2': 51},
})
try:
    integerize(assignments2, targets2, relax=0)
    raise AssertionError('FAILED: k=0 应无可行整数解')
except RangeInsufficient as e:
    check('范围不足被识别', e.reason == 'range_insufficient')
    check('范围不足给出阻塞类别', any(b['category'] == 'z1' for b in e.blocking))
res2 = integerize(assignments2, targets2, relax=2)
check('放松 k=2 后可解且对齐', res2['all_categories_match'] and res2['integer_total'] == 100)
check('放松后有权重超出 floor/ceil 被标记',
      any(not c['within_floor_ceil'] for c in res2['weight_changes']))


# 4) 结构无整数解 -> structural_infeasible（放松再多也无解）
# z1 只有 2 个样本，每条约 [19,21]（小数 20.4/20.6，k 最大 8 -> [12,29]），
# 令 z1 目标 60：单条最多 29，两条最多 58，结构上够不到
targets3 = make_targets({
    'zone': {'z1': 60, 'z2': 40},
    'age_band': {'a1': 50, 'a2': 50},
    'mode': {'m1': 49, 'm2': 51},
})
try:
    integerize(assignments2, targets3, relax=0)
    raise AssertionError('FAILED: 应判定结构无整数解')
except StructuralInfeasible as e:
    check('结构无整数解被识别', e.reason == 'structural_infeasible')


# 5) 已全为整数的权重：直接返回，零改动
assignments4 = [unit(c, z, a, m, 25.0) for c, z, a, m in [
    ('A', 'z1', 'a1', 'm1'), ('B', 'z1', 'a2', 'm2'),
    ('C', 'z2', 'a1', 'm2'), ('D', 'z2', 'a2', 'm1')]]
targets4 = make_targets({
    'zone': {'z1': 50, 'z2': 50},
    'age_band': {'a1': 50, 'a2': 50},
    'mode': {'m1': 50, 'm2': 50},
})
res4 = integerize(assignments4, targets4, relax=0)
check('整数权重零改动', res4['moved_count'] == 0 and res4['integer_total'] == 100)


# 6) 较大规模（模拟 800 行）性能与正确性
import random  # noqa: E402
random.seed(42)
n = 800
big = []
for i in range(n):
    big.append(unit(
        f'S{i:04d}',
        f'z{random.randint(1,3)}', f'a{random.randint(1,3)}', f'm{random.randint(1,3)}',
        random.uniform(5, 20),
    ))
# 用当前类别合计四舍五入作为整数目标，保证结构上大致可行
t5 = {}
for dim in DIMS:
    groups = {}
    for row in big:
        groups[row[dim]] = groups.get(row[dim], 0.0) + row['weight']
    vals = {cat: round(v) for cat, v in groups.items()}
    # 统一各维总量到第一维
    t5[dim] = vals
base_total = sum(t5[DIMS[0]].values())
for dim in DIMS[1:]:
    diff = base_total - sum(t5[dim].values())
    first_cat = sorted(t5[dim])[0]
    t5[dim][first_cat] += diff
targets5 = make_targets(t5)
start = time.perf_counter()
res5 = integerize(big, targets5, relax=0)
elapsed = time.perf_counter() - start
check('800 行 k=0 求解在 5 秒内', elapsed < 5.0)
check('800 行全部类别对齐', res5['all_categories_match'])
check('800 行总人数对齐', res5['integer_total'] == base_total)
check('800 行全部限于 floor/ceil',
      all(c['within_floor_ceil'] for c in res5['weight_changes']))
print(f'   (800 行耗时 {elapsed*1000:.0f} ms, 改动 {res5["moved_count"]} 条)')


# 7) 超时被单独抛出，而不是误报无解
import sys  # noqa: E402
svc = sys.modules['src.services.integerization_service']
svc.DEFAULT_TIMEOUT_SECONDS = 0.0001
svc.MAX_NODES = 10**9
try:
    integerize(big, targets5, relax=1)
    raise AssertionError('FAILED: 应超时拒绝')
except SolverTimeout as e:
    check('超时与无解分开', e.reason == 'timeout')
finally:
    svc.DEFAULT_TIMEOUT_SECONDS = 5.0
    svc.MAX_NODES = 300_000

print('\n全部整数化服务测试通过。')
