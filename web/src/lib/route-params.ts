/**
 * Route segments arrive as strings and are used as database ids, so they are
 * checked here rather than passed through and hoped for.
 */

/** A positive integer id, or null if the segment was not one. */
export function readId(value: string | undefined): number | null {
  if (value === undefined || !/^\d+$/.test(value)) {
    return null;
  }
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
