export function parseApiUtcTimestamp(value: string): number {
  const includesTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(includesTimezone ? value : `${value}Z`).getTime();
}
