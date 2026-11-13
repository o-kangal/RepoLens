import { expect, test } from 'vitest';
import { parseEventStream, type RawEvent } from '../../src/api/sse';
import { streamOf } from './stream';

async function collect(body: ReadableStream<Uint8Array>): Promise<RawEvent[]> {
  const events: RawEvent[] = [];
  for await (const event of parseEventStream(body)) events.push(event);
  return events;
}

test('parses named events split across chunks', async () => {
  const events = await collect(streamOf('event: tok', 'en\ndata: {"text":"a"}\n', '\nevent: done\ndata: {}\n\n'));

  expect(events).toEqual([
    { event: 'token', data: '{"text":"a"}' },
    { event: 'done', data: '{}' },
  ]);
});

test('handles CRLF and CR line endings, also when CRLF is split between chunks', async () => {
  const events = await collect(streamOf('event: a\r\ndata: 1\r', '\n\r\nevent: b\rdata: 2\r\r'));

  expect(events).toEqual([
    { event: 'a', data: '1' },
    { event: 'b', data: '2' },
  ]);
});

test('joins multi-line data, skips comments and defaults the event name', async () => {
  const events = await collect(streamOf(': ping\n\ndata: one\ndata:two\n\n'));

  expect(events).toEqual([{ event: 'message', data: 'one\ntwo' }]);
});

test('decodes a multi-byte character split between chunks', async () => {
  const bytes = new TextEncoder().encode('data: ü\n\n');
  const events = await collect(streamOf(bytes.slice(0, 7), bytes.slice(7)));

  expect(events).toEqual([{ event: 'message', data: 'ü' }]);
});

test('drops an event the stream ends before finishing', async () => {
  expect(await collect(streamOf('event: token\ndata: {}\n'))).toEqual([]);
});
