# Payment Service Runbook

## Overview
The payment-service processes all payment transactions via Stripe.

## Common Issues
### 5xx Errors
- Check connection pool settings in `config/pool.yaml`.
- Verify Stripe API key is valid and not rate-limited.
- Inspect CloudWatch logs for `PoolExhausted` exceptions.

### High Latency
- Check database connection pool utilisation.
- Review recent deployments for regression.
- Verify downstream Stripe endpoint health at https://status.stripe.com.

## Escalation
If unresolved within 15 minutes, page the payments-oncall rotation.
