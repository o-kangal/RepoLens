import { ApiError, isRecord, toApiError } from './http';

// Mirrors backend/repolens/api/repos.py.

export type RepoStatus = 'running' | 'done' | 'failed';

export interface IndexRequest {
  url: string;
  ref?: string;
}

export interface IndexAccepted {
  repo_id: string;
  status: RepoStatus;
}

export interface Repository {
  id: string;
  url: string;
  ref: string | null;
  commit_sha: string | null;
  status: RepoStatus;
  error: string | null;
  /** Chunk count per chunking strategy (`naive`, `ast`, `markdown`). */
  chunk_counts: Record<string, number>;
}

export async function requestIndexing(request: IndexRequest): Promise<IndexAccepted> {
  const response = await fetch('/api/repos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw await toApiError(response);
  const body: unknown = await response.json();
  if (!isRecord(body) || typeof body['repo_id'] !== 'string' || !isRepoStatus(body['status'])) {
    throw malformed();
  }
  return { repo_id: body['repo_id'], status: body['status'] };
}

export async function fetchRepository(id: string, signal?: AbortSignal): Promise<Repository> {
  const response = await fetch(`/api/repos/${encodeURIComponent(id)}`, signal ? { signal } : {});
  if (!response.ok) throw await toApiError(response);
  const body: unknown = await response.json();
  if (!isRepository(body)) throw malformed();
  return body;
}

function malformed(): ApiError {
  return new ApiError(502, 'The server sent a malformed repository response.');
}

function isRepoStatus(value: unknown): value is RepoStatus {
  return value === 'running' || value === 'done' || value === 'failed';
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isRepository(value: unknown): value is Repository {
  if (!isRecord(value)) return false;
  const counts = value['chunk_counts'];
  return (
    typeof value['id'] === 'string' &&
    typeof value['url'] === 'string' &&
    isNullableString(value['ref']) &&
    isNullableString(value['commit_sha']) &&
    isRepoStatus(value['status']) &&
    isNullableString(value['error']) &&
    isRecord(counts) &&
    Object.values(counts).every((count) => Number.isInteger(count))
  );
}
