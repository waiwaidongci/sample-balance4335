export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
  get reason(): string | undefined {
    return this.detail && typeof this.detail === 'object' && 'reason' in this.detail
      ? (this.detail as { reason?: string }).reason
      : undefined;
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers: { 'Content-Type': 'application/json',
      Authorization: 'Bearer ' + localStorage.getItem('balance_token'), ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: '连接中断，请重试' }));
    if (response.status === 401) {
      localStorage.removeItem('balance_token');
      localStorage.removeItem('balance_user');
    }
    const detail = (data as { detail?: unknown }).detail;
    const message = typeof detail === 'string'
      ? detail
      : (detail && typeof detail === 'object' && 'message' in detail
        ? String((detail as { message?: unknown }).message)
        : '输入内容未通过校验，请检查取值范围');
    throw new ApiError(response.status, message, detail);
  }
  return response.json();
}
export async function download(path: string, filename: string) {
  const r = await fetch('/api' + path, { headers: { Authorization: 'Bearer ' + localStorage.getItem('balance_token') } });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    const detail = (data as { detail?: unknown }).detail;
    const message = typeof detail === 'string'
      ? detail
      : (detail && typeof detail === 'object' && 'message' in detail
        ? String((detail as { message?: unknown }).message)
        : '导出失败，请等待整数化成功后重试');
    throw new ApiError(r.status, message, detail);
  }
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
