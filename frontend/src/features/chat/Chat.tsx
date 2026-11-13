import { type KeyboardEvent, type SyntheticEvent, useId, useState } from 'react';
import type { DoneData } from '../../api/query';
import { ModelSelect, useModels } from './ModelSelect';
import { type Turn, useChat } from './useChat';

interface ChatProps {
  /** The repository questions go to; `null` until one is selected. */
  repoId: string | null;
}

export function Chat({ repoId }: ChatProps) {
  const { turns, streaming, ask } = useChat();
  const models = useModels();
  const [chosenModel, setChosenModel] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const questionId = useId();

  const model = chosenModel ?? models.data?.default ?? null;
  const noModels = models.data?.models.length === 0;
  const canAsk = repoId !== null && !streaming && !noModels && question.trim() !== '';

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!canAsk) return;
    void ask({ repoId, question: question.trim(), model });
    setQuestion('');
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) submit(event);
  };

  return (
    <section aria-label="Chat" className="flex flex-col gap-4">
      <div className="flex min-h-8 items-center justify-end">
        {models.isPending && <p className="text-sm text-slate-500">Loading models…</p>}
        {models.isError && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            Could not load models: {models.error.message}
          </p>
        )}
        {noModels && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            No model is configured on the server.
          </p>
        )}
        {models.data && model !== null && (
          <ModelSelect models={models.data.models} value={model} disabled={streaming} onChange={setChosenModel} />
        )}
      </div>

      <div
        role="log"
        aria-live="polite"
        aria-busy={streaming}
        aria-label="Conversation"
        className="flex min-h-48 flex-col gap-6 rounded border border-slate-200 p-4 dark:border-slate-700"
      >
        {turns.length === 0 ? (
          <p className="text-slate-500">
            {repoId === null
              ? 'Index a repository to start asking questions.'
              : 'Ask a question about the repository.'}
          </p>
        ) : (
          turns.map((turn) => <TurnView key={turn.id} turn={turn} />)
        )}
      </div>

      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor={questionId} className="text-sm font-medium">
          Question
        </label>
        <textarea
          id={questionId}
          value={question}
          maxLength={2000}
          rows={3}
          disabled={repoId === null}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
          className="rounded border border-slate-300 bg-white p-2 dark:border-slate-600 dark:bg-slate-800"
        />
        <button
          type="submit"
          disabled={!canAsk}
          className="self-end rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
        >
          {streaming ? 'Answering…' : 'Ask'}
        </button>
      </form>
    </section>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <article className="flex flex-col gap-2">
      <p className="self-end rounded bg-slate-100 px-3 py-2 whitespace-pre-wrap dark:bg-slate-800">{turn.question}</p>
      {turn.status === 'streaming' && (
        <div>
          <p className="whitespace-pre-wrap">{turn.text}</p>
          <p className="text-sm text-slate-500">Generating answer…</p>
        </div>
      )}
      {turn.status === 'done' && <Answer result={turn.result} />}
      {turn.status === 'error' && (
        <p role="alert" className="text-red-700 dark:text-red-400">
          {turn.error}
        </p>
      )}
    </article>
  );
}

const STOP_NOTES: Partial<Record<DoneData['stop_reason'], string>> = {
  max_tokens: 'The answer was cut off at the length limit.',
  refusal: 'The model declined to answer.',
};

function Answer({ result }: { result: DoneData }) {
  const note = STOP_NOTES[result.stop_reason];
  return (
    <div className="flex flex-col gap-1">
      <p className="whitespace-pre-wrap">{result.answer}</p>
      {note && <p className="text-sm text-amber-700 dark:text-amber-400">{note}</p>}
    </div>
  );
}
