import { Chat } from './features/chat/Chat';

export function App() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold">RepoLens</h1>
      {/* No repository can be selected until the repository panel exists. */}
      <Chat repoId={null} />
    </main>
  );
}
