// Test helpers: a controllable SSE response and a fetch mock that routes by path.

const encoder = new TextEncoder();

export function sseFrame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A streaming response the test feeds chunk by chunk. */
export function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }),
    push(text: string) {
      controller.enqueue(encoder.encode(text));
    },
    close() {
      controller.close();
    },
  };
}

export function streamOf(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

export interface Call {
  path: string;
  init: RequestInit | undefined;
}

/** A fetch replacement that answers each path with the given handler and records calls. */
export function routeFetch(routes: Record<string, (init: RequestInit | undefined) => Response | Promise<Response>>) {
  const calls: Call[] = [];
  const fetchMock = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url;
    calls.push({ path, init });
    const handler = routes[path];
    if (!handler) return Promise.reject(new TypeError(`unexpected fetch ${path}`));
    return Promise.resolve(handler(init));
  };
  return { fetchMock, calls };
}

export function requestBody(call: Call | undefined): unknown {
  const body = call?.init?.body;
  return typeof body === 'string' ? JSON.parse(body) : undefined;
}
