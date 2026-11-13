import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import type { Source } from '../../src/api/query';
import { CitedText } from '../../src/features/chat/CitedText';

afterEach(cleanup);

const s1: Source = { label: 'S1', path: 'src/app.py', start_line: 3, end_line: 9 };
const s2: Source = { label: 'S2', path: 'README.md', start_line: 1, end_line: 4 };

test('renders each marker as a focusable chip and keeps the surrounding text', () => {
  const { container } = render(<CitedText text="Starts in app [S1], see docs [S1, S2]." citations={[s1, s2]} />);

  expect(container.textContent).toBe('Starts in app S1, see docs S1S2.');
  const chips = screen.getAllByText(/^S\d$/);
  expect(chips.map((chip) => chip.textContent)).toEqual(['S1', 'S1', 'S2']);
  expect(chips.every((chip) => chip.getAttribute('tabindex') === '0')).toBe(true);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

test('hover shows the source location and leaving hides it', () => {
  render(<CitedText text="See [S2]." citations={[s1, s2]} />);
  const chip = screen.getByText('S2');

  fireEvent.mouseEnter(chip);
  expect(screen.getByRole('tooltip').textContent).toBe('README.md:1-4');

  fireEvent.mouseLeave(chip);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

test('keyboard focus shows the location as the chip description, and Escape dismisses it', () => {
  render(<CitedText text="See [S1]." citations={[s1]} />);
  const chip = screen.getByText('S1');

  fireEvent.focus(chip);
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip.textContent).toBe('src/app.py:3-9');
  expect(chip.getAttribute('aria-describedby')).toBe(tooltip.id);

  fireEvent.keyDown(chip, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(chip.hasAttribute('aria-describedby')).toBe(false);

  fireEvent.focus(chip);
  fireEvent.blur(chip);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

test('an invalid marker is flagged and never becomes a chip', () => {
  render(<CitedText text="Real [S1] and made up [S9]." citations={[s1]} />);

  const invalid = screen.getByText('S9');
  expect(invalid.hasAttribute('tabindex')).toBe(false);
  expect(invalid.textContent).toBe('S9 (invalid citation)');
  fireEvent.mouseEnter(invalid);
  expect(screen.queryByRole('tooltip')).toBeNull();

  expect(screen.getByText('S1').getAttribute('tabindex')).toBe('0');
});

test('text without markers renders unchanged', () => {
  const { container } = render(<CitedText text={'No sources [here].\nSecond line'} citations={[s1]} />);

  expect(container.textContent).toBe('No sources [here].\nSecond line');
  expect(screen.queryAllByText(/^S\d$/)).toEqual([]);
});
