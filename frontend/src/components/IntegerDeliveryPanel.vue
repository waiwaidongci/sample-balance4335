<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import type {
  IntegerDelivery, IntegerDetail, IntegerFailure,
  IntegerReconciliation, WeightChange,
} from '../types/domain';
import { integerizeRun, getIntegerDeliveries, exportIntegerRun } from '../api/integerization';
import { ApiError } from '../api/client';
import { useTask } from '../hooks/useTask';
import { number } from '../utils/format';
import { dimensionLabels } from '../constants/enums';
import { canEdit } from '../stores/auth';

const props = defineProps<{ runId: number; approved: boolean }>();
const relax = ref(0);
const deliveries = ref<IntegerDelivery[]>([]);
const lastFailure = ref<IntegerFailure | null>(null);
const showChanges = ref(false);
const { error, busy, execute } = useTask();

const solved = computed<IntegerDelivery | null>(
  () => deliveries.value.find((d) => d.status === 'solved' && d.relax === relax.value) ?? null,
);
const detail = computed<IntegerDetail | null>(() =>
  solved.value ? (solved.value.detail as IntegerDetail) : null);
const archivedFailure = computed<IntegerFailure | null>(() => {
  const d = deliveries.value.find((x) => x.relax === relax.value && x.status === 'failed');
  return d ? (d.detail as IntegerFailure) : null;
});
const displayFailure = computed<IntegerFailure | null>(
  () => lastFailure.value ?? archivedFailure.value,
);
const solvedRelaxOptions = computed<number[]>(
  () => [...new Set(deliveries.value.filter((d) => d.status === 'solved').map((d) => d.relax))]
    .sort((a, b) => a - b),
);

const failureReasonText: Record<string, string> = {
  target_non_integer: '目标含非整数人数',
  range_insufficient: '取整范围不足',
  structural_infeasible: '样本结构无整数解',
  timeout: '求解超时，已拒绝交付',
};

async function refresh() {
  deliveries.value = await getIntegerDeliveries(props.runId);
}
onMounted(() => execute(refresh));

async function solve() {
  lastFailure.value = null;
  await execute(async () => {
    try {
      await integerizeRun(props.runId, relax.value);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.reason) {
        lastFailure.value = e.detail as IntegerFailure;
      }
      throw e; // 交给 useTask 统一展示 message
    }
  });
}

async function downloadCsv() {
  if (solved.value) await execute(() => exportIntegerRun(props.runId, solved.value!.relax));
}

function rowClass(r: IntegerReconciliation) {
  return r.matches_target ? 'ok-row' : 'bad-row';
}
function changeClass(c: WeightChange) {
  return c.within_floor_ceil ? 'ok-row' : 'warn-row';
}
</script>

<template>
  <section class="integer-panel">
    <div class="section-heading">
      <h2>整数化交付</h2>
      <span>把小数权重受控取整为整数人数，保住各维目标与总人数</span>
    </div>

    <p v-if="!approved" class="method-note">
      权重口径获批后才能整数化与导出；当前运行尚未批准。
    </p>

    <template v-else>
      <div v-if="canEdit" class="integer-controls">
        <label class="relax-field">
          放松范围 k（每条权重限 [floor−k, ceil+k]）
          <input v-model.number="relax" type="number" min="0" max="20" step="1" :disabled="busy" />
        </label>
        <div class="form-actions">
          <button class="primary" :disabled="busy" @click="solve">
            {{ relax === 0 ? '按上下整数求解' : `放宽到 k=${relax} 重新求解` }}
          </button>
          <button v-if="detail" :disabled="busy" @click="downloadCsv">下载整数权重 CSV ↓</button>
        </div>
      </div>
      <div v-else class="integer-controls">
        <label v-if="solvedRelaxOptions.length" class="relax-field">
          已成功的放松范围
          <select v-model.number="relax">
            <option v-for="k in solvedRelaxOptions" :key="k" :value="k">k = {{ k }}</option>
          </select>
        </label>
        <div class="form-actions">
          <button v-if="detail" :disabled="busy" @click="downloadCsv">下载整数权重 CSV ↓</button>
        </div>
        <p v-if="!solvedRelaxOptions.length" class="method-note">阅读者仅可下载已成功求解的整数权重；整数化由负责人或分析师发起。</p>
      </div>
      <p class="source-note">
        k=0 时每条整数权重只能取原权重的 floor 或 ceil；无解时可调大 k 放宽单条范围，
        但各维类别合计与总人数仍必须精确等于整数目标。
      </p>

      <div v-if="displayFailure" class="error" role="alert">
        <strong>{{ failureReasonText[displayFailure.reason] || '整数化失败' }}：</strong>
        {{ displayFailure.message }}
        <ul v-if="displayFailure.reason === 'target_non_integer' && displayFailure.non_integer?.length" class="fail-list">
          <li v-for="t in displayFailure.non_integer" :key="t.dimension + t.category">
            {{ dimensionLabels[t.dimension] }} / {{ t.category }}：{{ number(t.population, 3) }}
          </li>
        </ul>
        <ul v-else-if="displayFailure.reason === 'range_insufficient' && displayFailure.blocking?.length" class="fail-list">
          <li v-for="b in displayFailure.blocking" :key="b.dimension + b.category">
            {{ dimensionLabels[b.dimension] }} / {{ b.category }}
            ：目标 {{ number(b.target, 0) }}，当前可达合计
            {{ number(b.min_total, 0) }}–{{ number(b.max_total, 0) }}
          </li>
        </ul>
        <p v-if="displayFailure.reason === 'timeout'" class="fail-hint">
          超时与无解不同：本次未产出任何整数解，小数权重导出仍可正常使用。
        </p>
        <p v-else-if="displayFailure.reason === 'range_insufficient'" class="fail-hint">
          样本结构本身可解，请调大 k 后重新求解。
        </p>
        <p v-else-if="displayFailure.reason === 'structural_infeasible'" class="fail-hint">
          这不是取整范围问题，调大 k 无效；请调整样本结构或整数目标后重新校准。
        </p>
      </div>

      <template v-if="detail">
        <div class="integer-summary">
          <div>
            <small>目标总人数 / 整数总人数</small>
            <b>{{ number(detail.total_target, 0) }} / {{ number(detail.integer_total, 0) }}</b>
            <p :class="detail.total_matches ? 'ok-text' : 'bad-text'">
              {{ detail.total_matches ? '总人数精确一致' : '总人数不一致（不应发生）' }}
            </p>
          </div>
          <div>
            <small>类别对账</small>
            <b>{{ detail.all_categories_match ? '全部对齐' : '存在偏差' }}</b>
            <p :class="detail.all_categories_match ? 'ok-text' : 'bad-text'">
              残差均为 0 才允许整数交付
            </p>
          </div>
          <div>
            <small>权重改动 / 限于 floor·ceil</small>
            <b>{{ detail.moved_count }} / {{ detail.within_natural_count }}</b>
            <p>共 {{ detail.sample_count }} 条，放松 k={{ detail.relax }}</p>
          </div>
        </div>

        <div class="section-heading">
          <h2>逐类对账</h2>
          <span>整数合计必须精确等于整数目标</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>维度 / 类别</th><th>样本数</th><th>整数目标</th>
                <th>小数权重合计</th><th>整数权重合计</th><th>整数残差</th><th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in detail.reconciliations" :key="r.dimension + r.category" :class="rowClass(r)">
                <td><small>{{ dimensionLabels[r.dimension] }}</small><strong>{{ r.category }}</strong></td>
                <td>{{ r.sample_count }}</td>
                <td>{{ number(r.target, 0) }}</td>
                <td>{{ number(r.decimal_weighted, 3) }}</td>
                <td>{{ number(r.integer_weighted, 0) }}</td>
                <td>{{ number(r.integer_residual, 0) }}</td>
                <td>{{ r.matches_target ? '✓ 对齐' : '✗ 偏差' }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="section-heading">
          <h2>权重改动</h2>
          <button type="button" @click="showChanges = !showChanges">
            {{ showChanges ? '收起明细' : '展开逐条改动' }}
          </button>
        </div>
        <div v-if="showChanges" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>样本编号</th><th>小数权重</th><th>floor</th><th>ceil</th>
                <th>整数权重</th><th>改动量</th><th>是否限于上下整数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in detail.weight_changes" :key="c.code" :class="changeClass(c)">
                <td><strong>{{ c.code }}</strong></td>
                <td>{{ number(c.decimal_weight, 4) }}</td>
                <td>{{ c.floor }}</td>
                <td>{{ c.ceil }}</td>
                <td>{{ c.integer_weight }}</td>
                <td>{{ number(c.delta, 4) }}</td>
                <td>{{ c.within_floor_ceil ? '是' : '否（已放宽）' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </section>
</template>
