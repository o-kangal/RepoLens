import { afterEach, expect, test, vi } from 'vitest';
import { ApiError } from '../../src/api/http';
import { fetchModels, type QueryEvent, streamQuery } from '../../src/api/query';
import { jsonResponse, requestBody, routeFetch, sseFrame } from './stream';

afterEach(() => {
  vi.unstubAllGlobals();
});

const source = { label: 'S1', path: 'src/app.py', start_line: 3, end_line: 9 };
const done = {
  answer: 'It starts in app.py [S1].',
  citations: [source],
  invalid_labels: [],
  stop_reason: 'end',
  model: 'claude-opus-5-5',
  usage: [{ model: 'claude-opus-5-5', input_tokens: 10, output_tokens: 5 }],
};

function sseResponse(text: string): Response {
  return new Response(text, { headers: { 'Content-Type': 'text/event-stream' } });
}

async function collect(stream: AsyncGenerator<QueryEvent>): Promise<QueryEvent[]> {
  const events: QueryEvent[] = [];
  for await (const event of stream) events.push(event);
  return events;
}

test('streamQuery posts the request and yields typed events, skipping unknown ones', async () => {
  const { fetchMock, calls } = routeFetch({
    '/api/query': () =>
      sseResponse(
        sseFrame('sources', { sources: [source] }) +
          sseFrame('token', { text: 'It ' }) +
          sseFrame('progress', { step: 1 }) +
          sseFrame('done', done),
      ),
  });
  vi.stubGlobal('fetch', fetchMock);

  const events = await collect(streamQuery({ repo_id: 'r1', question: 'Where?', model: 'm' }));

  expect(events).toEqual([
    { type: 'sources', sources: [source] },
    { type: 'token', text: 'It ' },
    { type: 'done', data: done },
  ]);
  expect(calls[0]?.init?.method).toBe('POST');
  expect(requestBody(calls[0])).toEqual({ repo_id: 'r1', question: 'Where?', model: 'm' });
});

test('streamQuery yields an error event with its code', async () => {
  vi.stubGlobal(
    'fetch',
    routeFetch({
      '/api/query': () => sseResponse(sseFrame('error', { code: 'repository_not_ready', message: 'not indexed' })),
    }).fetchMock,
  );

  expect(await collect(streamQuery({ repo_id: 'r1', question: 'q' }))).toEqual([
    { type: 'error', code: 'repository_not_ready', message: 'not indexed' },
  ]);
});

test('streamQuery turns an HTTP error into an ApiError with the server detail', async () => {
  vi.stubGlobal(
    'fetch',
    routeFetch({ '/api/query': () => jsonResponse({ detail: "model 'x' is not allowed" }, 422) }).fetchMock,
  );

  const failure = collect(streamQuery({ repo_id: 'r1', question: 'q', model: 'x' }));

  await expect(failure).rejects.toBeInstanceOf(ApiError);
  await expect(failure).rejects.toMatchObject({ status: 422, message: "model 'x' is not allowed" });
});

test('streamQuery joins validation error messages', async () => {
  vi.stubGlobal(
    'fetch',
    routeFetch({
      '/api/query': () => jsonResponse({ detail: [{ msg: 'bad repo_id' }, { msg: 'question too long' }] }, 422),
    }).fetchMock,
  );

  await expect(collect(streamQuery({ repo_id: 'r1', question: 'q' }))).rejects.toThrow(
    'bad repo_id; question too long',
  );
});

test('streamQuery rejects a malformed event payload', async () => {
  vi.stubGlobal(
    'fetch',
    routeFetch({ '/api/query': () => sseResponse(sseFrame('done', { ...done, stop_reason: 'tired' })) }).fetchMock,
  );

  await expect(collect(streamQuery({ repo_id: 'r1', question: 'q' }))).rejects.toThrow('malformed done');
});

test('streamQuery rejects event data that is not JSON', async () => {
  vi.stubGlobal('fetch', routeFetch({ '/api/query': () => sseResponse('event: token\ndata: {oops\n\n') }).fetchMock);

  await expect(collect(streamQuery({ repo_id: 'r1', question: 'q' }))).rejects.toThrow('malformed token');
});

test('fetchModels validates the response', async () => {
  vi.stubGlobal('fetch', routeFetch({ '/api/models': () => jsonResponse({ models: ['a', 'b'], default: 'a' }) }).fetchMock);
  await expect(fetchModels()).resolves.toEqual({ models: ['a', 'b'], default: 'a' });

  vi.stubGlobal('fetch', routeFetch({ '/api/models': () => jsonResponse({ models: 'a' }) }).fetchMock);
  await expect(fetchModels()).rejects.toThrow('malformed models');
});
