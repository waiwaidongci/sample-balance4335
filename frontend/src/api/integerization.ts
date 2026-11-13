import { request, download } from './client';
import type { IntegerDelivery } from '../types/domain';

export const integerizeRun = (runId: number, relax: number) =>
  request<IntegerDelivery>('/runs/integerize', {
    method: 'POST',
    body: JSON.stringify({ run_id: runId, relax }),
  });

export const getIntegerDeliveries = (runId: number) =>
  request<IntegerDelivery[]>(`/runs/${runId}/integer-deliveries`);

export const exportIntegerRun = (runId: number, relax: number) =>
  download(`/runs/${runId}/export-integer?relax=${relax}`,
    `yy-weights-integer-${runId}-k${relax}.csv`);
