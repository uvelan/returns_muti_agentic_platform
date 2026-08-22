/**
 * A return record's status, as something an operator can read.
 *
 * `ReturnRecordProjection.status` is `string | null`, and both screens that
 * showed one rendered it bare. A null therefore produced an empty pill: a badge
 * shaped like a status, containing nothing. That reads as "this return has no
 * status", when what it means is that the platform does not know one -- an
 * older record written before the vocabulary existed, or a projection that has
 * not converged yet.
 *
 * `UNKNOWN` is the frozen answer for that, and specifically never `ISSUED`: a
 * return must not present as issued because nobody recorded that it was not.
 */
export const UNKNOWN_STATUS = "UNKNOWN";

export function readReturnStatus(status: string | null | undefined): string {
  if (status === null || status === undefined || status.trim() === "") {
    return UNKNOWN_STATUS;
  }
  return status;
}
