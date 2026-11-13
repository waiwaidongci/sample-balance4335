"""整数化交付：把获批的小数权重受控取整为整数人数。

口径（与逐列手调不同，保证全局边际）：
- 每个维度的每个类别，其整数权重合计必须精确等于整数目标；
- 全部整数权重合计精确等于目标总人数；
- 每条权重优先只在原权重的上下整数 [floor, ceil] 内取值（放松参数 k=0），
  页面可放宽到 [floor-k, ceil+k] 重新求解。

求解是多维整数可行性问题，用整数域上的边界一致性传播 + 分裂回溯完成，
不引入外部求解器。无解原因分三类显式区分；超时单独抛出，绝不伪装成无解。
"""
import math
import time

EPS = 1e-7
# 演示规模（单批最多 5000 行）下的求解预算；超时拒绝交付而非给出半成品
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_NODES = 200_000
# “结构无整数解”判定时采用的最大放松范围；再宽也只改变单条权重、改变不了结构
STRUCTURAL_MAX_K = 8


class IntegerizationError(Exception):
    """整数化失败基类，reason 供前端区分提示。"""

    def __init__(self, reason, message, **extra):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.extra = extra


class TargetNonInteger(IntegerizationError):
    def __init__(self, non_integer):
        super().__init__(
            'target_non_integer',
            '存在非整数目标人数，无法在整数世界对齐边际，请先把目标边际登记为整数。',
            non_integer=non_integer,
        )
        self.non_integer = non_integer


class RangeInsufficient(IntegerizationError):
    def __init__(self, message, blocking=None):
        super().__init__('range_insufficient', message, blocking=blocking or [])
        self.blocking = blocking or []


class StructuralInfeasible(IntegerizationError):
    def __init__(self, message):
        super().__init__('structural_infeasible', message)


class SolverTimeout(IntegerizationError):
    def __init__(self, seconds):
        super().__init__(
            'timeout',
            f'整数化求解超过 {seconds:g} 秒仍未判定，已拒绝交付；'
            '请缩小放松范围或联系负责人，小数权重导出仍可正常使用。',
        )
        self.seconds = seconds


def _is_integer(value):
    return math.isfinite(value) and abs(value - round(value)) <= EPS


def _check_integer_targets(targets):
    """目标必须本身是整数（校准阶段已保证各维总量一致，这里只做整数校验）。"""
    non_integer = []
    for t in targets:
        if not _is_integer(t['population']):
            non_integer.append({
                'dimension': t['dimension'],
                'category': t['category'],
                'population': t['population'],
            })
    if non_integer:
        raise TargetNonInteger(non_integer)


def _feasible(domains, constraints):
    """整数域上的边界一致性传播 + 分裂回溯，判定类别合计可行性。

    对约束 Σx = req 做精确边界收紧：
      x_i ∈ [max(lo_i, req−Σ_{j≠i}hi_j), min(hi_i, req−Σ_{j≠i}lo_j)]
    全部变量为二值（域宽 ≤1，即 floor/ceil 取整）时走专门的快速回溯；
    否则走通用整数域分裂回溯。迭代式、trail 精确回滚，避免深递归与整表复制。
    成功返回取值列表；判定不可行返回 None；预算用尽抛 SolverTimeout。
    """
    if all(hi - lo <= 1 for lo, hi in domains):
        return _binary_feasible(domains, constraints)
    return _general_feasible(domains, constraints)


def _binary_feasible(domains, constraints):
    """二值域（floor/ceil）快速可行性：最大余数贪心 + 增量计数回溯。

    每个自由变量只能取下界或上界（差 1），类别约束化为“恰需选 need 个上界”。
    先做一次确定性最大余数贪心，命中即返回；否则用增量维护
    ones/unknown 的回溯，单次分支与剪枝均为 O(该变量涉及约束数)。
    """
    deadline = time.perf_counter() + DEFAULT_TIMEOUT_SECONDS
    n = len(domains)
    base = [lo for lo, _ in domains]
    free_idx = [i for i, (lo, hi) in enumerate(domains) if lo != hi]
    free_set = set(free_idx)
    # 每条约束：成员、需求、当前已选上界数 ones、未定数 unknown
    c_members, c_need, c_ones, c_unknown = [], [], [], []
    # 变量 -> 它出现的约束位置
    var_cons = {i: [] for i in free_idx}
    for ci, (members, req) in enumerate(constraints):
        need = req - sum(base[i] for i in members)
        fm = [i for i in members if i in free_set]
        if need < 0 or need > len(fm):
            return None
        c_members.append(fm)
        c_need.append(need)
        c_ones.append(0)
        c_unknown.append(len(fm))
        for i in fm:
            var_cons[i].append(ci)

    def build(assigned):
        return [base[i] + (1 if assigned.get(i) == 1 else 0) for i in range(n)]

    # 贪心：每条约束按“取上整倾向”（域宽，二值时恒为 1）与索引稳定排序分配 need 个上界
    chosen = set()
    for fm, need in zip(c_members, c_need):
        order = sorted(fm, key=lambda i: (-(domains[i][1] - base[i]), i))
        chosen.update(order[:need])
    ok = True
    for fm, need in zip(c_members, c_need):
        if sum(1 for i in fm if i in chosen) != need:
            ok = False
            break
    if ok:
        return build({i: 1 for i in chosen})

    # 回溯：state -1/0/1；增量更新各约束计数
    state = {i: -1 for i in free_idx}

    def apply(i, val):
        state[i] = val
        for ci in var_cons[i]:
            c_unknown[ci] -= 1
            if val == 1:
                c_ones[ci] += 1

    def revoke(i):
        val = state[i]
        for ci in var_cons[i]:
            c_unknown[ci] += 1
            if val == 1:
                c_ones[ci] -= 1
        state[i] = -1

    def partial_ok(i):
        for ci in var_cons[i]:
            if c_ones[ci] > c_need[ci] or c_ones[ci] + c_unknown[ci] < c_need[ci]:
                return False
        return True

    def pick():
        best = -1
        best_key = None
        for ci, fm in enumerate(c_members):
            slack = c_need[ci] - c_ones[ci]
            for i in fm:
                if state[i] == -1:
                    key = (slack, i)
                    if best_key is None or key < best_key:
                        best_key, best = key, i
        return best

    nodes = 0
    first = pick()
    stack = [(first, 0)] if first != -1 else []
    while stack:
        nodes += 1
        if nodes > MAX_NODES or ((nodes & 255) == 0 and time.perf_counter() > deadline):
            raise SolverTimeout(DEFAULT_TIMEOUT_SECONDS)
        i, branch = stack[-1]
        if branch >= 2:
            revoke(i)
            stack.pop()
            continue
        stack[-1] = (i, branch + 1)
        apply(i, 1 if branch == 0 else 0)
        if not partial_ok(i):
            revoke(i)
            continue
        nxt = pick()
        if nxt == -1:
            return build(state)
        stack.append((nxt, 0))
    return None


def _general_feasible(domains, constraints):
    """通用整数域（放松后域宽可能 >1）的边界一致性传播 + 分裂回溯。"""
    deadline = time.perf_counter() + DEFAULT_TIMEOUT_SECONDS
    nodes = 0
    dom = [tuple(d) for d in domains]
    cons = [(list(members), req) for members, req in constraints]
    trail = []  # (变量索引, 旧域)

    def propagate():
        changed = True
        while changed:
            changed = False
            for members, req in cons:
                lo_sum = sum(dom[i][0] for i in members)
                hi_sum = sum(dom[i][1] for i in members)
                if lo_sum > req or hi_sum < req:
                    return False
                for i in members:
                    lo_i, hi_i = dom[i]
                    new_lo = max(lo_i, req - (hi_sum - hi_i))
                    new_hi = min(hi_i, req - (lo_sum - lo_i))
                    if new_lo > new_hi:
                        return False
                    if (new_lo, new_hi) != (lo_i, hi_i):
                        trail.append((i, (lo_i, hi_i)))
                        dom[i] = (new_lo, new_hi)
                        lo_sum += new_lo - lo_i
                        hi_sum += new_hi - hi_i
                        changed = True
        return True

    def select_var():
        """选域最窄的自由变量（最快固定），索引最小打破平局，保持确定性。"""
        best = -1
        best_key = None
        for i, (lo, hi) in enumerate(dom):
            if lo == hi:
                continue
            key = (hi - lo, i)
            if best_key is None or key < best_key:
                best_key, best = key, i
        return best

    def undo(mark):
        while len(trail) > mark:
            idx, old = trail.pop()
            dom[idx] = old

    if not propagate():
        return None
    if all(lo == hi for lo, hi in dom):
        return [lo for lo, _ in dom]

    # 每个决策点记录进入该点前的 trail 长度
    stack = []
    while True:
        nodes += 1
        if nodes > MAX_NODES or ((nodes & 255) == 0 and time.perf_counter() > deadline):
            raise SolverTimeout(DEFAULT_TIMEOUT_SECONDS)
        i = select_var()
        if i == -1:
            return [lo for lo, _ in dom]
        lo, hi = dom[i]
        mid = lo + (hi - lo) // 2
        progressed = False
        # 先试含下界的一半，保持确定性
        for nlo, nhi in ((lo, mid), (mid + 1, hi)):
            mark = len(trail)
            trail.append((i, dom[i]))
            dom[i] = (nlo, nhi)
            if propagate():
                progressed = True
                stack.append(mark)
                break
            undo(mark)
        if not progressed:
            if not stack:
                return None
            undo(stack.pop())


def integerize(assignments, targets, relax=0, dims=('zone', 'age_band', 'mode')):
    """把校准后的小数权重整数化。

    返回 dict：integer_weights / reconciliations / weight_changes / summary 等。
    失败抛 IntegerizationError 子类，三类无解原因互斥且可区分。
    """
    if not isinstance(relax, int) or relax < 0 or relax > 20:
        raise IntegerizationError('bad_param', '放松范围必须是 0 到 20 之间的整数。')
    _check_integer_targets(targets)

    target_map = {}
    for t in targets:
        target_map.setdefault(t['dimension'], {})[t['category']] = int(round(t['population']))
    total_target = sum(target_map[dims[0]].values())

    # 每条权重的整数域 [floor-k, ceil+k]，且不为负
    domains, floors, ceils = [], [], []
    for row in assignments:
        w = float(row['weight'])
        if not math.isfinite(w) or w < 0:
            raise IntegerizationError('bad_weight', '存在非有限或负权重，无法整数化。')
        f = math.floor(w + EPS)
        c = math.ceil(w - EPS)
        if c < f:  # 数值防护
            c = f
        floors.append(f)
        ceils.append(c)
        domains.append((max(0, f - relax), c + relax))

    # 组装类别合计约束与分组
    constraint_list = []
    groups_by_dim = {}
    for dim in dims:
        groups = {}
        for i, row in enumerate(assignments):
            groups.setdefault(row[dim], []).append(i)
        groups_by_dim[dim] = groups
        for cat, members in groups.items():
            if cat not in target_map[dim]:
                raise RangeInsufficient(
                    f'类别 {dim}/{cat} 在目标边际中缺失，无法对齐整数合计。',
                    blocking=[{'dimension': dim, 'category': cat, 'reason': 'missing_target'}],
                )
            constraint_list.append((members, target_map[dim][cat]))

    solution = _feasible(domains, constraint_list)

    if solution is None:
        # 当前放松范围无解。用宽范围再判一次，区分“范围不足”与“样本结构无整数解”。
        wide_domains = [(max(0, f - STRUCTURAL_MAX_K), c + STRUCTURAL_MAX_K)
                        for f, c in zip(floors, ceils)]
        wide_solution = _feasible(wide_domains, constraint_list)
        if wide_solution is None:
            raise StructuralInfeasible(
                '即使放宽每条权重的取整范围，样本在各维度类别上的分布仍凑不出整数目标：'
                '这是样本结构（交叉类别支持）与整数边际不兼容，而非取整范围问题。'
                '请调整样本结构或整数目标后重新校准。'
            )
        blocking = _range_blocking(domains, target_map, groups_by_dim, dims)
        raise RangeInsufficient(
            f'放松范围 k={relax} 内没有整数解，但样本结构本身支持整数解；'
            '请在页面上调大放松范围（每条权重限 [floor−k, ceil+k]）后重新求解。',
            blocking=blocking,
        )

    return _build_result(assignments, solution, floors, ceils, target_map,
                         groups_by_dim, dims, total_target, relax)


def _range_blocking(domains, target_map, groups_by_dim, dims):
    """指出当前范围下紧张的类别，便于页面解释。

    先列直接越界（合计区间够不到目标）的类别；若无单一类别越界，
    说明是跨维联合冲突，则返回可放宽空间最小的若干类别作为定位线索。
    """
    scored = []
    for dim in dims:
        for cat, members in groups_by_dim[dim].items():
            lo = sum(domains[i][0] for i in members)
            hi = sum(domains[i][1] for i in members)
            target = target_map[dim][cat]
            headroom = min(target - lo, hi - target)
            scored.append({
                'dimension': dim, 'category': cat, 'target': target,
                'min_total': lo, 'max_total': hi,
                'violated': lo > target or hi < target,
                'headroom': headroom,
            })
    violated = [x for x in scored if x['violated']]
    if violated:
        return [{k: v for k, v in x.items() if k != 'violated'} for x in violated]
    tight = sorted(scored, key=lambda x: (x['headroom'], x['dimension'],
                                          x['category']))[:5]
    return [{k: v for k, v in x.items() if k != 'violated'} for x in tight]


def _build_result(assignments, solution, floors, ceils, target_map,
                  groups_by_dim, dims, total_target, relax):
    integer_weights = [int(v) for v in solution]
    integer_total = sum(integer_weights)

    reconciliations = []
    for dim in dims:
        for cat in sorted(groups_by_dim[dim]):
            members = groups_by_dim[dim][cat]
            weighted = sum(assignments[i]['weight'] for i in members)
            integer_sum = sum(integer_weights[i] for i in members)
            target = target_map[dim][cat]
            reconciliations.append({
                'dimension': dim,
                'category': cat,
                'sample_count': len(members),
                'target': target,
                'decimal_weighted': weighted,
                'integer_weighted': integer_sum,
                'integer_residual': integer_sum - target,
                'matches_target': integer_sum == target,
            })

    weight_changes = []
    moved = 0
    for src, iv, f, c in zip(assignments, integer_weights, floors, ceils):
        delta = iv - src['weight']
        if abs(delta) > EPS:
            moved += 1
        weight_changes.append({
            'code': src['code'],
            'decimal_weight': src['weight'],
            'floor': f,
            'ceil': c,
            'integer_weight': iv,
            'delta': delta,
            'within_floor_ceil': f <= iv <= c,
        })

    return {
        'relax': relax,
        'total_target': int(total_target),
        'decimal_total': sum(r['weight'] for r in assignments),
        'integer_total': integer_total,
        'total_matches': integer_total == int(total_target),
        'all_categories_match': all(r['matches_target'] for r in reconciliations),
        'moved_count': moved,
        'sample_count': len(assignments),
        'within_natural_count': sum(1 for c in weight_changes if c['within_floor_ceil']),
        'reconciliations': reconciliations,
        'weight_changes': weight_changes,
        'integer_weights': integer_weights,
    }
