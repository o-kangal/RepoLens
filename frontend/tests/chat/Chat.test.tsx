import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { Chat } from '../../src/features/chat/Chat';
import { controlledStream, jsonResponse, requestBody, routeFetch, sseFrame } from './stream';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const models = { models: ['claude-opus-5-5', 'gpt-6'], default: 'claude-opus-5-5' };
const source = { label: 'S1', path: 'src/app.py', start_line: 3, end_line: 9 };

function doneData(answer: string, stopReason = 'end') {
  return {
    answer,
    citations: [source],
    invalid_labels: [],
    stop_reason: stopReason,
    model: 'claude-opus-5-5',
    usage: [{ model: 'claude-opus-5-5', input_tokens: 10, output_tokens: 5 }],
  };
}

function renderChat(repoId: string | null = 'repo-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Chat repoId={repoId} />
    </QueryClientProvider>,
  );
}

/** Match a paragraph by its whole text, across the chips inside it. */
function paragraph(text: string) {
  return (_content: string, element: Element | null) => element?.tagName === 'P' && element.textContent === text;
}

function askQuestion(text: string) {
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
}

test('the conversation is a polite live log with an empty state', async () => {
  vi.stubGlobal('fetch', routeFetch({ '/api/models': () => jsonResponse(models) }).fetchMock);
  renderChat();

  const log = screen.getByRole('log', { name: 'Conversation' });
  expect(log.getAttribute('aria-live')).toBe('polite');
  expect(within(log).getByText('Ask a question about the repository.')).toBeDefined();
  expect(await screen.findByRole('combobox', { name: 'Model' })).toBeDefined();
});

test('without a repository the question box is disabled', () => {
  vi.stubGlobal('fetch', routeFetch({ '/api/models': () => jsonResponse(models) }).fetchMock);
  renderChat(null);

  expect(screen.getByText('Index a repository to start asking questions.')).toBeDefined();
  expect(screen.getByLabelText('Question').hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button', { name: 'Ask' }).hasAttribute('disabled')).toBe(true);
});

test('streams tokens, then shows the final answer from the done event', async () => {
  const stream = controlledStream();
  const { fetchMock } = routeFetch({ '/api/models': () => jsonResponse(models), '/api/query': () => stream.response });
  vi.stubGlobal('fetch', fetchMock);
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  askQuestion('Where does it start?');
  const log = screen.getByRole('log');
  expect(within(log).getByText('Where does it start?')).toBeDefined();
  expect(screen.getByLabelText('Question')).toHaveProperty('value', '');

  await act(async () => {
    stream.push(sseFrame('sources', { sources: [source] }));
    stream.push(sseFrame('token', { text: 'It starts ' }));
    await Promise.resolve();
  });
  await act(async () => {
    stream.push(sseFrame('token', { text: 'in app.py [S1][S9].' }));
    await Promise.resolve();
  });
  expect(await within(log).findByText('It starts in app.py [S1][S9].')).toBeDefined();
  expect(log.getAttribute('aria-busy')).toBe('true');
  expect(screen.getByRole('button', { name: 'Answering…' }).hasAttribute('disabled')).toBe(true);

  await act(async () => {
    stream.push(sseFrame('done', doneData('It starts in app.py [S1].')));
    stream.close();
    await Promise.resolve();
  });

  const answer = await within(log).findByText(paragraph('It starts in app.py S1.'));
  expect(within(answer).getByText('S1').getAttribute('tabindex')).toBe('0');
  expect(within(log).queryByText('It starts in app.py [S1][S9].')).toBeNull();
  expect(log.getAttribute('aria-busy')).toBe('false');
});

test('an error event after the stream opened is shown as an alert', async () => {
  const stream = controlledStream();
  vi.stubGlobal(
    'fetch',
    routeFetch({ '/api/models': () => jsonResponse(models), '/api/query': () => stream.response }).fetchMock,
  );
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  askQuestion('Why?');
  await act(async () => {
    stream.push(sseFrame('error', { code: 'llm_failed', message: 'The language model request failed.' }));
    stream.close();
    await Promise.resolve();
  });

  const alert = await within(screen.getByRole('log')).findByRole('alert');
  expect(alert.textContent).toBe('The language model request failed.');
  expect(screen.getByRole('button', { name: 'Ask' })).toBeDefined();
});

test('an HTTP error and an unreachable server both become alerts', async () => {
  const replies = [
    () => jsonResponse({ detail: 'no LLM provider is configured' }, 503),
    () => Promise.reject(new TypeError('Failed to fetch')),
  ];
  vi.stubGlobal(
    'fetch',
    routeFetch({ '/api/models': () => jsonResponse(models), '/api/query': () => replies.shift()!() }).fetchMock,
  );
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  askQuestion('First?');
  expect((await screen.findByText('no LLM provider is configured')).getAttribute('role')).toBe('alert');

  askQuestion('Second?');
  expect((await screen.findByText('Could not reach the server.')).getAttribute('role')).toBe('alert');
});

test('a stream that ends without done or error is reported', async () => {
  const stream = controlledStream();
  vi.stubGlobal(
    'fetch',
    routeFetch({ '/api/models': () => jsonResponse(models), '/api/query': () => stream.response }).fetchMock,
  );
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  askQuestion('Why?');
  await act(async () => {
    stream.push(sseFrame('token', { text: 'Half' }));
    stream.close();
    await Promise.resolve();
  });

  expect(await screen.findByText('The answer stream ended unexpectedly.')).toBeDefined();
});

test('a truncated answer says so', async () => {
  vi.stubGlobal(
    'fetch',
    routeFetch({
      '/api/models': () => jsonResponse(models),
      '/api/query': () =>
        new Response(sseFrame('done', doneData('Partial', 'max_tokens')), {
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    }).fetchMock,
  );
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  askQuestion('Long?');

  expect(await screen.findByText('The answer was cut off at the length limit.')).toBeDefined();
});

test('the model selector lists the server models, starts on the default and sends the choice', async () => {
  const { fetchMock, calls } = routeFetch({
    '/api/models': () => jsonResponse(models),
    '/api/query': () =>
      new Response(sseFrame('done', doneData('Done.')), { headers: { 'Content-Type': 'text/event-stream' } }),
  });
  vi.stubGlobal('fetch', fetchMock);
  renderChat();

  const select = await screen.findByRole('combobox', { name: 'Model' });
  expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual(models.models);
  expect(select).toHaveProperty('value', 'claude-opus-5-5');

  fireEvent.change(select, { target: { value: 'gpt-6' } });
  askQuestion('  Which?  ');
  await screen.findByText('Done.');

  expect(requestBody(calls.find((call) => call.path === '/api/query'))).toEqual({
    repo_id: 'repo-1',
    question: 'Which?',
    model: 'gpt-6',
  });
});

test('Enter submits and Shift+Enter does not', async () => {
  const { fetchMock, calls } = routeFetch({
    '/api/models': () => jsonResponse(models),
    '/api/query': () =>
      new Response(sseFrame('done', doneData('Done.')), { headers: { 'Content-Type': 'text/event-stream' } }),
  });
  vi.stubGlobal('fetch', fetchMock);
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  const box = screen.getByLabelText('Question');
  fireEvent.change(box, { target: { value: 'Keys?' } });
  fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
  expect(calls.some((call) => call.path === '/api/query')).toBe(false);

  fireEvent.keyDown(box, { key: 'Enter' });
  expect(await screen.findByText('Done.')).toBeDefined();
});

test('an empty model list blocks asking and a failed model request is shown', async () => {
  vi.stubGlobal('fetch', routeFetch({ '/api/models': () => jsonResponse({ models: [], default: null }) }).fetchMock);
  renderChat();

  expect(await screen.findByText('No model is configured on the server.')).toBeDefined();
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'Anyone?' } });
  expect(screen.getByRole('button', { name: 'Ask' }).hasAttribute('disabled')).toBe(true);
  cleanup();

  vi.stubGlobal('fetch', routeFetch({ '/api/models': () => jsonResponse({ detail: 'boom' }, 500) }).fetchMock);
  renderChat();

  expect((await screen.findByText('Could not load models: boom')).getAttribute('role')).toBe('alert');
});

test('labels the server removed are listed under the answer', async () => {
  vi.stubGlobal(
    'fetch',
    routeFetch({
      '/api/models': () => jsonResponse(models),
      '/api/query': () =>
        new Response(sseFrame('done', { ...doneData('See [S1].'), invalid_labels: ['S7', 'S9'] }), {
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    }).fetchMock,
  );
  renderChat();
  await screen.findByRole('combobox', { name: 'Model' });

  askQuestion('Where?');

  expect(await screen.findByText('Removed citations that matched no retrieved source: S7, S9')).toBeDefined();
});
