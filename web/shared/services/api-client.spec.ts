import { HttpErrorResponse } from '@angular/common/http';

import { ApiError, newIdempotencyKey, toApiError, toParams } from './api-client';

describe('toApiError', () => {
  it('reads the API error envelope', () => {
    const err = toApiError(
      new HttpErrorResponse({
        status: 422,
        error: { error: { code: 'insufficient_points', message: 'Insufficient demonstration points', details: { balance: 3 }, request_id: 'r1' } },
      }),
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe('insufficient_points');
    expect(err.status).toBe(422);
    expect(err.details['balance']).toBe(3);
    expect(err.requestId).toBe('r1');
  });

  it('maps validation errors to field messages', () => {
    const err = toApiError(
      new HttpErrorResponse({
        status: 422,
        error: { error: { code: 'validation_error', message: 'Invalid', details: { errors: [{ loc: ['body', 'username'], msg: 'too short' }] }, request_id: null } },
      }),
    );
    expect(err.fieldErrors).toEqual({ username: 'too short' });
  });

  it('describes network failures', () => {
    expect(toApiError(new HttpErrorResponse({ status: 0 })).code).toBe('network_error');
  });
});

describe('toParams', () => {
  it('drops empty values and repeats arrays', () => {
    const params = toParams({ page: 2, q: '', sort: null, status: ['live', 'scheduled'] });
    expect(params.get('page')).toBe('2');
    expect(params.has('q')).toBe(false);
    expect(params.has('sort')).toBe(false);
    expect(params.getAll('status')).toEqual(['live', 'scheduled']);
  });
});

describe('newIdempotencyKey', () => {
  it('produces unique keys the API accepts', () => {
    const a = newIdempotencyKey('xfer');
    expect(a).toMatch(/^[A-Za-z0-9_\-:.]{8,128}$/);
    expect(a).not.toBe(newIdempotencyKey('xfer'));
  });
});
