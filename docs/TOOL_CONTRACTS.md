# Tool Contracts

## Provenance

Every tool result uses `ToolResult[T]` and carries a typed payload, source,
source timestamp, freshness status, fixture/live flag and safe error fields.
The planner may use only typed payload values. A failed or malformed provider
response becomes `ToolUnavailable`; it is never converted into a live claim.

## Freshness policy

| Data | Freshness window |
| --- | ---: |
| Route | 15 minutes |
| Weather | 60 minutes |
| PSI | 60 minutes |
| Flood alerts | 15 minutes |
| Train disruptions | 5 minutes |
| Curated venue data | 30 days after review |

Fixture values use `fixture` status even when generated moments ago. They are
safe for the deterministic demo but must not be described as live observations.

## Route and cost policy

Route adapters report the provider or fixture's estimated duration, total
distance and first/last-mile walking distance. The user's walking limit is an
acceptance constraint and never caps the reported distance. Public-transport
routes without explicit walking legs are rejected. Demo transport costs use a
versioned deterministic policy: public transport is S$2.00, walking is S$0.00,
and taxi is S$4.80 plus S$1.25 per route kilometre.

## Accessibility policy

Accessibility is `verified` only when a source is present. Missing evidence is
`unverified` and is excluded when wheelchair access is mandatory. Curated venue
records are demonstration data; OneMap BFA is used only when separately
approved and enabled.

## Fixture coverage

`src/adaptsg/data/fixtures/` contains normal, over-limit, public-transport and
environment fixtures. The demo adapters remain the executable fixture path so
CI and the recorded demonstration require no credentials or network.