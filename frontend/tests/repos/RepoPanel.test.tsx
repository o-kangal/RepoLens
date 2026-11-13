import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { Repository } from '../../src/api/repos';
import { RepoPanel } from '../../src/features/repos/RepoPanel';
import { POLL_INTERVAL_MS } from '../../src/features/repos/useRepository';
import { jsonResponse, requestBody, routeFetch } from '../chat/stream';

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const STATUS_PATH = '/api/repos/repo-1';

function repository(overrides: Partial<Repository>): Repository {
  return {
    id: 'repo-1',
    url: 'https://github.com/acme/widgets',
    ref: null,
    commit_sha: null,
    status: 'running',
    error: null,
    chunk_counts: {},
    ...overrides,
  };
}

/** Answer status polls from a script; the last entry repeats. */
function statusSequence(...states: Repository[]) {
  let i = 0;
  return () => jsonResponse(states[Math.min(i++, states.length - 1)]);
}

function Harness() {
  const [repoId, setRepoId] = useState<string | null>(null);
  return <RepoPanel repoId={repoId} onIndexing={setRepoId} />;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

function submit(url: string, ref = '') {
  fireEvent.change(screen.getByLabelText('GitHub URL'), { target: { value: url } });
  fireEvent.change(screen.getByLabelText('Branch or tag'), { target: { value: ref } });
  fireEvent.click(screen.getByRole('button', { name: 'Index' }));
}

const polls = (calls: { path: string }[]) => calls.filter((call) => call.path === STATUS_PATH).length;

test('requests indexing with the URL and ref, then polls until indexing is done', async () => {
  const { fetchMock, calls } = routeFetch({
    '/api/repos': () => jsonResponse({ repo_id: 'repo-1', status: 'running' }, 202),
    [STATUS_PATH]: statusSequence(
      repository({}),
      repository({ ref: 'v2', commit_sha: 'abcdef1234567', chunk_counts: { naive: 120 } }),
      repository({ ref: 'v2', commit_sha: 'abcdef1234567', status: 'done', chunk_counts: { naive: 1520, markdown: 37 } }),
    ),
  });
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();

  submit(' https://github.com/acme/widgets ', ' v2 ');

  expect(await screen.findByText(/Indexing…/)).toBeDefined();
  expect(requestBody(calls.find((call) => call.path === '/api/repos'))).toEqual({
    url: 'https://github.com/acme/widgets',
    ref: 'v2',
  });
  expect(screen.getByRole('button', { name: 'Index' }).hasAttribute('disabled')).toBe(true);

  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
  expect(await screen.findByRole('cell', { name: '120' })).toBeDefined();

  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
  const status = await screen.findByRole('status');
  expect(status.textContent).toBe('acme/widgets @ v2abcdef1 — Indexed');
  expect(screen.getByRole('table', { name: 'Chunks per strategy' })).toBeDefined();
  expect(screen.getByRole('row', { name: 'naive 1,520' })).toBeDefined();
  expect(screen.getByRole('row', { name: 'markdown 37' })).toBeDefined();
  expect(polls(calls)).toBe(3);

  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5));
  expect(polls(calls)).toBe(3);
  expect(screen.getByRole('button', { name: 'Index' }).hasAttribute('disabled')).toBe(false);
});

test('a failed index shows its error and stops polling', async () => {
  const { fetchMock, calls } = routeFetch({
    '/api/repos': () => jsonResponse({ repo_id: 'repo-1', status: 'running' }, 202),
    [STATUS_PATH]: statusSequence(repository({}), repository({ status: 'failed', error: 'clone failed' })),
  });
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();

  submit('https://github.com/acme/widgets');
  await screen.findByText(/Indexing…/);
  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));

  expect(await screen.findByText(/Indexing failed/)).toBeDefined();
  expect(screen.getByRole('alert').textContent).toBe('clone failed');

  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5));
  expect(polls(calls)).toBe(2);
});

test('a failed poll is shown and stops polling', async () => {
  let first = true;
  const { fetchMock, calls } = routeFetch({
    '/api/repos': () => jsonResponse({ repo_id: 'repo-1', status: 'running' }, 202),
    [STATUS_PATH]: () => {
      if (!first) return jsonResponse({ detail: 'repository not found' }, 404);
      first = false;
      return jsonResponse(repository({}));
    },
  });
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();

  submit('https://github.com/acme/widgets');
  await screen.findByText(/Indexing…/);
  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));

  expect(await screen.findByText('Could not load the index status: repository not found')).toBeDefined();
  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5));
  expect(polls(calls)).toBe(2);
});

test('a rejected URL shows the server message and starts no polling', async () => {
  const { fetchMock, calls } = routeFetch({
    '/api/repos': () => jsonResponse({ detail: 'only https://github.com/ URLs are accepted' }, 422),
  });
  vi.stubGlobal('fetch', fetchMock);
  renderPanel();

  submit('https://gitlab.com/acme/widgets');

  expect((await screen.findByRole('alert')).textContent).toBe('only https://github.com/ URLs are accepted');
  expect(requestBody(calls[0])).toEqual({ url: 'https://gitlab.com/acme/widgets' });
  expect(polls(calls)).toBe(0);
});
