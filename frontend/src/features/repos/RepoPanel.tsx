import { useMutation } from '@tanstack/react-query';
import { type SyntheticEvent, useId, useState } from 'react';
import { type Repository, requestIndexing } from '../../api/repos';
import { useRepository } from './useRepository';

interface RepoPanelProps {
  repoId: string | null;
  onIndexing: (repoId: string) => void;
}

/** Add a GitHub repository to index and follow its indexing progress. */
export function RepoPanel({ repoId, onIndexing }: RepoPanelProps) {
  const [url, setUrl] = useState('');
  const [ref, setRef] = useState('');
  const urlId = useId();
  const refId = useId();
  const repository = useRepository(repoId);
  const indexing = useMutation({
    mutationFn: requestIndexing,
    onSuccess: (accepted) => onIndexing(accepted.repo_id),
  });

  const busy = indexing.isPending || repository.data?.status === 'running';

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    const trimmedRef = ref.trim();
    indexing.mutate({ url: url.trim(), ...(trimmedRef ? { ref: trimmedRef } : {}) });
  };

  return (
    <section aria-labelledby={`${urlId}-heading`} className="flex flex-col gap-3 rounded border border-slate-200 p-4 dark:border-slate-700">
      <h2 id={`${urlId}-heading`} className="text-lg font-semibold">
        Repository
      </h2>

      <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex grow flex-col gap-1">
          <label htmlFor={urlId} className="text-sm font-medium">
            GitHub URL
          </label>
          <input
            id={urlId}
            type="url"
            required
            maxLength={300}
            placeholder="https://github.com/owner/name"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            className="rounded border border-slate-300 bg-white p-2 dark:border-slate-600 dark:bg-slate-800"
          />
        </div>
        <div className="flex flex-col gap-1 sm:w-40">
          <label htmlFor={refId} className="text-sm font-medium">
            Branch or tag
          </label>
          <input
            id={refId}
            maxLength={255}
            placeholder="default branch"
            value={ref}
            onChange={(event) => setRef(event.target.value)}
            className="rounded border border-slate-300 bg-white p-2 dark:border-slate-600 dark:bg-slate-800"
          />
        </div>
        <button
          type="submit"
          disabled={busy || url.trim() === ''}
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
        >
          Index
        </button>
      </form>

      {indexing.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {indexing.error.message}
        </p>
      )}
      {repository.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          Could not load the index status: {repository.error.message}
        </p>
      )}
      {repository.data && <RepoStatusView repository={repository.data} />}
    </section>
  );
}

function RepoStatusView({ repository }: { repository: Repository }) {
  const name = repository.url.replace(/^https:\/\/github\.com\//, '');
  const counts = Object.entries(repository.chunk_counts);
  return (
    <div className="flex flex-col gap-2">
      <p role="status" className="text-sm">
        <span className="font-medium">{name}</span>
        {repository.ref && ` @ ${repository.ref}`}
        {repository.commit_sha && <code className="ml-2 text-slate-500">{repository.commit_sha.slice(0, 7)}</code>}
        {' — '}
        {repository.status === 'running' && 'Indexing…'}
        {repository.status === 'done' && 'Indexed'}
        {repository.status === 'failed' && 'Indexing failed'}
      </p>
      {repository.status === 'failed' && repository.error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {repository.error}
        </p>
      )}
      {counts.length > 0 && (
        <table className="w-fit text-sm">
          <caption className="text-left text-slate-500">Chunks per strategy</caption>
          <thead>
            <tr>
              <th scope="col" className="pr-6 text-left font-medium">
                Strategy
              </th>
              <th scope="col" className="text-right font-medium">
                Chunks
              </th>
            </tr>
          </thead>
          <tbody>
            {counts.map(([strategy, count]) => (
              <tr key={strategy}>
                <td className="pr-6">{strategy}</td>
                <td className="text-right tabular-nums">{count.toLocaleString('en-US')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
