# ADR 0001: Single named caregiver ownership

Status: Accepted

Production uses one named Cognito caregiver account per journey. The caregiver is the
owner, records consent for submitted traveller data, and approves or rejects plans and
material cost changes. Credentials are never shared.

Separate traveller identities, delegation, household accounts, payments and bookings are
out of scope for this release. Adding them requires a product and security review because
it changes ownership, consent, and approval semantics.

The API derives `owner_principal_id` and consent references from verified claims and the
server-side consent store. Clients cannot submit identity, role, ownership, consent
references, or an itinerary. The deterministic validator remains the final authority for
every accepted plan and replan.
