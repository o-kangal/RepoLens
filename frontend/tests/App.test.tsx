import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { App } from '../src/App';

test('renders the app shell', () => {
  vi.stubGlobal('fetch', () => new Promise<Response>(() => undefined));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <App />
    </QueryClientProvider>,
  );

  expect(screen.getByRole('main')).toBeDefined();
  expect(screen.getByRole('heading', { level: 1, name: 'RepoLens' })).toBeDefined();
  expect(screen.getByRole('log', { name: 'Conversation' })).toBeDefined();
  vi.unstubAllGlobals();
});
