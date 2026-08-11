# Tests

Suites that span more than one app. Unit tests live next to the code they cover
(`apps/*/tests`, `packages/*/tests`).

| Directory   | Contents                                                                        |
| ----------- | ------------------------------------------------------------------------------- |
| `e2e/`      | Playwright specs driving the browser against mock providers (§92, PHASE 15)     |
| `fixtures/` | Shared sample media: product image, generated shot, voice clip, BGM, SRT (§121) |

## Fixture licensing (§121)

Every fixture must be self-generated or carry a documented licence permitting
use here. Each fixture is listed in `fixtures/README.md` with its origin. Do not
add media whose provenance you cannot state.

## Running

```bash
make test              # unit
make test-integration  # needs `make infra-up`
make test-e2e          # PHASE 15
```
