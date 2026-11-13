/** One server-sent event: its name and its raw data, with multi-line data joined by "\n". */
export interface RawEvent {
  event: string;
  data: string;
}

/**
 * Parse a `text/event-stream` body into events, following the WHATWG event stream rules
 * that matter here: `event` and `data` fields, comments, and CRLF, CR or LF line endings.
 */
export async function* parseEventStream(body: ReadableStream<Uint8Array>): AsyncGenerator<RawEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let event = '';
  let data: string[] = [];

  try {
    for (let finished = false; !finished; ) {
      const chunk = await reader.read();
      finished = chunk.done;
      buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });

      // Mid-stream, a trailing "\r" may be the first half of "\r\n", so it waits for more.
      const lines = buffer.split(finished ? /\r\n|\r|\n/ : /\r\n|\r(?!$)|\n/);
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        if (line === '') {
          if (data.length > 0) yield { event: event || 'message', data: data.join('\n') };
          event = '';
          data = [];
          continue;
        }
        if (line.startsWith(':')) continue;

        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        let fieldValue = colon === -1 ? '' : line.slice(colon + 1);
        if (fieldValue.startsWith(' ')) fieldValue = fieldValue.slice(1);

        if (field === 'event') event = fieldValue;
        else if (field === 'data') data.push(fieldValue);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
