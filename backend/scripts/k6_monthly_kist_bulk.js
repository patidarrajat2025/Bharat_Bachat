/*
 * Save All Kist staging benchmark.
 *
 * SAFETY: This endpoint writes real financial records. Run only against an
 * isolated staging database with disposable test members/shares. Prepare a
 * fresh monthly period or reset fixtures between runs; otherwise most repeated
 * iterations will exercise already-paid/idempotent paths rather than first-time
 * posting. Do not point this script at production.
 *
 * Example:
 *   K6_ALLOW_FINANCIAL_WRITES=YES \
 *   BASE_URL=https://staging.example.com TENANT_ID=<id> TOKEN=<jwt> \
 *   PAYLOAD_FILE=./bulk-kist-payload.json k6 run backend/scripts/k6_monthly_kist_bulk.js
 *
 * PAYLOAD_FILE should contain a valid BulkMonthlyKistCreate JSON body with test
 * members and active share IDs. The script overwrites idempotency_key per VU/
 * iteration to distinguish requests. Keep allocation amounts and fixture reset
 * strategy appropriate for the test database.
 */
import http from 'k6/http';
import { check, fail } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const requestDuration = new Trend('save_all_kist_duration', true);
const requestErrors = new Rate('save_all_kist_errors');

export const options = {
  scenarios: {
    save_all_kist: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 2),
      duration: __ENV.DURATION || '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'],
    save_all_kist_errors: ['rate<0.02'],
  },
};

const baseUrl = (__ENV.BASE_URL || '').replace(/\/$/, '');
const tenantId = __ENV.TENANT_ID || '';
const token = __ENV.TOKEN || '';
const payloadPath = __ENV.PAYLOAD_FILE || '';

if (__ENV.K6_ALLOW_FINANCIAL_WRITES !== 'YES') {
  fail('Refusing to run: set K6_ALLOW_FINANCIAL_WRITES=YES only for an isolated staging database.');
}
if (!baseUrl || !tenantId || !token || !payloadPath) {
  fail('Required env vars: BASE_URL, TENANT_ID, TOKEN, PAYLOAD_FILE.');
}

const template = JSON.parse(open(payloadPath));

export default function () {
  const payload = JSON.parse(JSON.stringify(template));
  payload.idempotency_key = `k6-save-all-kist-${__VU}-${__ITER}-${Date.now()}`;
  const response = http.post(
    `${baseUrl}/group/${tenantId}/monthly-kist-bulk`,
    JSON.stringify(payload),
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      timeout: __ENV.REQUEST_TIMEOUT || '120s',
    },
  );
  requestDuration.add(response.timings.duration);
  const ok = check(response, {
    'bulk kist returned 2xx': (r) => r.status >= 200 && r.status < 300,
    'bulk kist response is JSON': (r) => (r.headers['Content-Type'] || '').includes('application/json'),
  });
  requestErrors.add(!ok);
}
