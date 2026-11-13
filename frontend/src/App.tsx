import { useState } from 'react';
import { Chat } from './features/chat/Chat';
import { RepoPanel } from './features/repos/RepoPanel';
import { useRepository } from './features/repos/useRepository';

export function App() {
  const [repoId, setRepoId] = useState<string | null>(null);
  const repository = useRepository(repoId);
  const readyRepoId = repository.data?.status === 'done' ? repository.data.id : null;

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold">RepoLens</h1>
      <RepoPanel repoId={repoId} onIndexing={setRepoId} />
      {/* A new repository starts a new conversation. */}
      <Chat key={readyRepoId} repoId={readyRepoId} />
    </main>
  );
}
