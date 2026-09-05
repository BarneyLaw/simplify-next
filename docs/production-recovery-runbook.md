# Production recovery runbook

Use this procedure only with an approved operator and a recorded incident.

1. Restore the DynamoDB point-in-time recovery backup to a new table. Do not overwrite the
   existing table.
2. Verify the restored table has schema version `1`, TTL on `expires_at`, encryption,
   point-in-time recovery, and deletion protection.
3. Update the Lambda alias configuration to the restored table name and deploy the alias
   change through the normal review path.
4. Run owner/read, owner/unauthorized-read, idempotency replay, and per-resource audit
   chain canaries. Confirm no care text, token, credential, or idempotency key appears in
   telemetry.
5. Keep the old table until the canaries and a representative journey/replan verification
   are recorded as successful. Only then retire the old table under the retention policy.

If audit append or provider verification fails, retain the current plan and disclose no
new provider result. Never use a restored table to bypass consent, ownership, or itinerary
validation.
