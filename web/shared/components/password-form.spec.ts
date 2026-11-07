import { FormControl } from '@angular/forms';

import { PASSWORD_MIN_LENGTH, passwordPolicy } from './password-form';

describe('passwordPolicy', () => {
  const check = (v: string) => passwordPolicy(new FormControl(v));

  it('needs only 6 characters of any kind', () => {
    expect(PASSWORD_MIN_LENGTH).toBe(6);
    for (const ok of ['abcdef', 'ABCDEF', '123456', 'abc123', 'a1b2c3', '@#$%^&']) expect(check(ok)).toBeNull();
  });

  it('rejects shorter passwords with the single length message', () => {
    expect(check('abc12')).toEqual({ server: 'Password must be at least 6 characters.' });
  });

  it('leaves empty values to Validators.required', () => {
    expect(check('')).toBeNull();
  });
});
