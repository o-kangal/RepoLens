import { ApiError, isRecord, toApiError } from './http';
import { parseEventStream } from './sse';

// Mirrors backend/repolens/api/query.py.

export type StopReason = 'end' | 'max_tokens' | 'refusal' | 'other';
export type QueryErrorCode =
  | 'repository_not_found'
  | 'repository_not_ready'
  | 'embedding_model_mismatch'
  | 'embedding_failed'
  | 'llm_failed'
  | 'internal';

export interface QueryRequest {
  repo_id: string;
  question: string;
  model?: string;
}

export interface ModelsResponse {
  models: string[];
  default: string | null;
}

export interface Source {
  label: string;
  path: string;
  start_line: number;
  end_line: number;
}

export interface Usage {
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface DoneData {
  answer: string;
  citations: Source[];
  invalid_labels: string[];
  stop_reason: StopReason;
  model: string;
  usage: Usage[];
}

export type QueryEvent =
  | { type: 'sources'; sources: Source[] }
  | { type: 'token'; text: string }
  | { type: 'done'; data: DoneData }
  | { type: 'error'; code: QueryErrorCode; message: string };

const STOP_REASONS: readonly string[] = ['end', 'max_tokens', 'refusal', 'other'] satisfies StopReason[];
const ERROR_CODES: readonly string[] = [
  'repository_not_found',
  'repository_not_ready',
  'embedding_model_mismatch',
  'embedding_failed',
  'llm_failed',
  'internal',
] satisfies QueryErrorCode[];

export async function fetchModels(signal?: AbortSignal): Promise<ModelsResponse> {
  const response = await fetch('/api/models', signal ? { signal } : {});
  if (!response.ok) throw await toApiError(response);
  const body: unknown = await response.json();
  if (!isModelsResponse(body)) throw malformed('models');
  return body;
}

/**
 * Ask a question and yield its events as they arrive. Request problems (bad model, invalid
 * body) throw an `ApiError`; failures after the stream opened arrive as an `error` event.
 */
export async function* streamQuery(request: QueryRequest, signal?: AbortSignal): AsyncGenerator<QueryEvent> {
  const response = await fetch('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(request),
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) throw await toApiError(response);
  if (!response.body) throw malformed('query');

  for await (const raw of parseEventStream(response.body)) {
    const event = toQueryEvent(raw.event, parseJson(raw.data, raw.event));
    if (event) yield event;
  }
}

/** Validate one event's payload; unknown event names are skipped so the protocol can grow. */
function toQueryEvent(name: string, data: unknown): QueryEvent | undefined {
  if (!isRecord(data)) throw malformed(name);
  switch (name) {
    case 'sources': {
      const { sources } = data;
      if (!isArrayOf(sources, isSource)) throw malformed(name);
      return { type: 'sources', sources };
    }
    case 'token': {
      const { text } = data;
      if (typeof text !== 'string') throw malformed(name);
      return { type: 'token', text };
    }
    case 'done':
      if (!isDoneData(data)) throw malformed(name);
      return { type: 'done', data };
    case 'error': {
      const { code, message } = data;
      if (!isErrorCode(code) || typeof message !== 'string') throw malformed(name);
      return { type: 'error', code, message };
    }
    default:
      return undefined;
  }
}

function parseJson(text: string, what: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw malformed(what);
  }
}

function malformed(what: string): ApiError {
  return new ApiError(502, `The server sent a malformed ${what} response.`);
}

function isArrayOf<T>(value: unknown, guard: (item: unknown) => item is T): value is T[] {
  return Array.isArray(value) && value.every(guard);
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

function isInteger(value: unknown): value is number {
  return Number.isInteger(value);
}

function isModelsResponse(value: unknown): value is ModelsResponse {
  return isRecord(value) && isArrayOf(value['models'], isString) && (value['default'] === null || isString(value['default']));
}

function isSource(value: unknown): value is Source {
  return (
    isRecord(value) &&
    isString(value['label']) &&
    isString(value['path']) &&
    isInteger(value['start_line']) &&
    isInteger(value['end_line'])
  );
}

function isUsage(value: unknown): value is Usage {
  return (
    isRecord(value) && isString(value['model']) && isInteger(value['input_tokens']) && isInteger(value['output_tokens'])
  );
}

function isStopReason(value: unknown): value is StopReason {
  return isString(value) && STOP_REASONS.includes(value);
}

function isErrorCode(value: unknown): value is QueryErrorCode {
  return isString(value) && ERROR_CODES.includes(value);
}

function isDoneData(value: Record<string, unknown>): value is Record<string, unknown> & DoneData {
  return (
    isString(value['answer']) &&
    isArrayOf(value['citations'], isSource) &&
    isArrayOf(value['invalid_labels'], isString) &&
    isStopReason(value['stop_reason']) &&
    isString(value['model']) &&
    isArrayOf(value['usage'], isUsage)
  );
}
