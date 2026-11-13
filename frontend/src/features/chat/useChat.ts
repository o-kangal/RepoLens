import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/http';
import { type DoneData, type QueryRequest, streamQuery } from '../../api/query';

export type Turn =
  | { id: number; question: string; status: 'streaming'; text: string }
  | { id: number; question: string; status: 'done'; result: DoneData }
  | { id: number; question: string; status: 'error'; error: string };

export interface Ask {
  repoId: string;
  question: string;
  model: string | null;
}

/** Holds the conversation and streams one answer at a time into it. */
export function useChat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const controller = useRef<AbortController | null>(null);
  const nextId = useRef(0);

  useEffect(() => () => controller.current?.abort(), []);

  const ask = useCallback(async ({ repoId, question, model }: Ask) => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;

    const id = nextId.current++;
    const update = (turn: Turn) => setTurns((all) => all.map((t) => (t.id === id ? turn : t)));
    let text = '';
    setTurns((all) => [...all, { id, question, status: 'streaming', text }]);

    const request: QueryRequest = { repo_id: repoId, question, ...(model ? { model } : {}) };
    try {
      for await (const event of streamQuery(request, abort.signal)) {
        switch (event.type) {
          case 'token':
            text += event.text;
            update({ id, question, status: 'streaming', text });
            break;
          case 'done':
            // The final answer replaces the streamed text: the citation parser may have
            // dropped labels that were already streamed.
            update({ id, question, status: 'done', result: event.data });
            return;
          case 'error':
            update({ id, question, status: 'error', error: event.message });
            return;
          case 'sources':
            break;
        }
      }
      update({ id, question, status: 'error', error: 'The answer stream ended unexpectedly.' });
    } catch (error) {
      if (abort.signal.aborted) return;
      update({ id, question, status: 'error', error: errorMessage(error) });
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }, []);

  const streaming = turns.some((turn) => turn.status === 'streaming');
  return { turns, streaming, ask };
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return 'Could not reach the server.';
}
