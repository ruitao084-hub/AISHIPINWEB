/**
 * Shared types between the web app and the HTTP API.
 *
 * Taskbook §5.2 makes the FastAPI OpenAPI document the single source of truth
 * for the HTTP contract. From P2-T08 this package re-exports types GENERATED
 * from that document (`src/generated/`), and CI fails when the checked-in
 * output drifts from the live schema.
 *
 * Until P2-T08 lands there is deliberately nothing here: hand-written request
 * and response shapes are exactly the drift the taskbook forbids.
 */

/** Contract revision of the hand-maintained (non-generated) surface. */
export const SHARED_TYPES_CONTRACT_VERSION = "0.0.0" as const;
