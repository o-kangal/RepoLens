import { useQuery } from '@tanstack/react-query';
import { fetchRepository } from '../../api/repos';

export const POLL_INTERVAL_MS = 2000;

/**
 * Follow one repository's index status, polling while indexing runs. Polling stops once
 * indexing ends or a poll fails.
 */
export function useRepository(id: string | null) {
  return useQuery({
    queryKey: ['repository', id],
    queryFn: ({ signal }) => fetchRepository(id ?? '', signal),
    enabled: id !== null,
    refetchInterval: (query) =>
      query.state.status === 'success' && query.state.data?.status === 'running' ? POLL_INTERVAL_MS : false,
  });
}
