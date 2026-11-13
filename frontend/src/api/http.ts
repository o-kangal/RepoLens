/** A failed API request, with a message fit to show the user. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Build an `ApiError` from a non-OK response. FastAPI puts a string in `detail` for
 * `HTTPException`s and a list of `{ msg }` objects for validation errors.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  return new ApiError(response.status, detailMessage(body) ?? `Request failed with status ${response.status}.`);
}

function detailMessage(body: unknown): string | undefined {
  if (!isRecord(body)) return undefined;
  const { detail } = body;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item: unknown) =>
      isRecord(item) && typeof item['msg'] === 'string' ? [item['msg']] : [],
    );
    if (messages.length > 0) return messages.join('; ');
  }
  return undefined;
}
