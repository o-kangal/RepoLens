import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../../src/App';
import { POLL_INTERVAL_MS } from '../../src/features/repos/useRepository';
import { jsonResponse, routeFetch } from '../chat/stream';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test('the chat opens for a repository once its indexing is done', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let status = 'running';
  vi.stubGlobal(
    'fetch',
    routeFetch({
      '/api/models': () => jsonResponse({ models: ['claude-opus-5-5'], default: 'claude-opus-5-5' }),
      '/api/repos': () => jsonResponse({ repo_id: 'repo-1', status: 'running' }, 202),
      '/api/repos/repo-1': () =>
        jsonResponse({
          id: 'repo-1',
          url: 'https://github.com/acme/widgets',
          ref: null,
          commit_sha: 'abcdef1',
          status,
          error: null,
          chunk_counts: { naive: 10 },
        }),
    }).fetchMock,
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <App />
    </QueryClientProvider>,
  );
  const question = screen.getByLabelText('Question');
  expect(question.hasAttribute('disabled')).toBe(true);

  fireEvent.change(screen.getByLabelText('GitHub URL'), { target: { value: 'https://github.com/acme/widgets' } });
  fireEvent.click(screen.getByRole('button', { name: 'Index' }));
  await screen.findByText(/Indexing…/);
  expect(screen.getByLabelText('Question').hasAttribute('disabled')).toBe(true);

  status = 'done';
  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));

  await screen.findByText(/Indexed/);
  expect(screen.getByLabelText('Question').hasAttribute('disabled')).toBe(false);
  expect(screen.getByText('Ask a question about the repository.')).toBeDefined();
});
