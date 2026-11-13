import { useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { fetchModels } from '../../api/query';

export const modelsQueryKey = ['models'] as const;

/** Load the models a question may use; the server's default is the first choice. */
export function useModels() {
  return useQuery({ queryKey: modelsQueryKey, queryFn: ({ signal }) => fetchModels(signal) });
}

interface ModelSelectProps {
  models: string[];
  value: string;
  disabled: boolean;
  onChange: (model: string) => void;
}

export function ModelSelect({ models, value, disabled, onChange }: ModelSelectProps) {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="text-sm text-slate-600 dark:text-slate-300">
        Model
      </label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="rounded border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800"
      >
        {models.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
    </div>
  );
}
