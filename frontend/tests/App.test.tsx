import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { App } from '../src/App';

test('renders the app shell', () => {
  render(<App />);

  expect(screen.getByRole('main')).toBeDefined();
  expect(screen.getByRole('heading', { level: 1, name: 'RepoLens' })).toBeDefined();
});
