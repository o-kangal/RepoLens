import { type KeyboardEvent, type ReactNode, useId, useState } from 'react';
import type { Source } from '../../api/query';

// The marker grammar of backend/repolens/generation/citations.py: [S1], [S1, S3], [S1,S2].
const MARKER = /\[(S\d+(?:\s*,\s*S\d+)*)\]/g;

function location(source: Source): string {
  return `${source.path}:${source.start_line}-${source.end_line}`;
}

/**
 * Render an answer with each `[Sx]` marker as a chip for its cited source. A label that
 * matches no citation is never shown as a chip; it stays visibly flagged as invalid.
 */
export function CitedText({ text, citations }: { text: string; citations: Source[] }) {
  const byLabel = new Map(citations.map((citation) => [citation.label, citation]));
  const parts: ReactNode[] = [];
  let last = 0;

  for (const match of text.matchAll(MARKER)) {
    parts.push(text.slice(last, match.index));
    const labels = (match[1] ?? '').split(',').map((label) => label.trim());
    labels.forEach((label, i) => {
      const source = byLabel.get(label);
      parts.push(
        source ? (
          <CitationChip key={`${match.index}-${i}`} source={source} />
        ) : (
          <InvalidMarker key={`${match.index}-${i}`} label={label} />
        ),
      );
    });
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));

  return <p className="whitespace-pre-wrap">{parts}</p>;
}

function CitationChip({ source }: { source: Source }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') setOpen(false);
  };

  return (
    <span className="relative mx-0.5 inline-block">
      <span
        tabIndex={0}
        aria-describedby={open ? tooltipId : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        className="cursor-default rounded bg-sky-100 px-1.5 text-xs font-medium text-sky-900 focus:ring-2 focus:ring-sky-500 focus:outline-none dark:bg-sky-900 dark:text-sky-100"
      >
        {source.label}
      </span>
      {open && (
        <span
          id={tooltipId}
          role="tooltip"
          className="absolute bottom-full left-0 z-10 mb-1 rounded bg-slate-900 px-2 py-1 font-mono text-xs whitespace-nowrap text-white dark:bg-slate-100 dark:text-slate-900"
        >
          {location(source)}
        </span>
      )}
    </span>
  );
}

function InvalidMarker({ label }: { label: string }) {
  return (
    <span
      title="This citation matches no retrieved source."
      className="mx-0.5 rounded px-1 text-xs text-red-700 line-through dark:text-red-400"
    >
      {label}
      <span className="sr-only"> (invalid citation)</span>
    </span>
  );
}
