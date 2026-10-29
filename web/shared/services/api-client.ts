import { HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import type { ApiErrorBody, PageQuery } from '@shared/models';

export const API_BASE = '/api/v1';

/** Normalised error raised by every API call. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
  ) {
    super(message);
  }

  /** Field errors from a 422 validation response, keyed by the last path segment. */
  get fieldErrors(): Record<string, string> {
    const errors = (this.details['errors'] as { loc: (string | number)[]; msg: string }[] | undefined) ?? [];
    return Object.fromEntries(errors.map((e) => [String(e.loc[e.loc.length - 1]), e.msg]));
  }
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (err instanceof HttpErrorResponse) {
    const body = err.error as Partial<ApiErrorBody> | null;
    if (body && typeof body === 'object' && body.error) {
      const e = body.error;
      return new ApiError(err.status, e.code, e.message, e.details ?? {}, e.request_id ?? null);
    }
    if (err.status === 0) return new ApiError(0, 'network_error', 'Cannot reach the server. Check your connection.');
    return new ApiError(err.status, 'http_error', err.statusText || 'Request failed');
  }
  return new ApiError(0, 'unknown_error', err instanceof Error ? err.message : 'Unexpected error');
}

export function toParams(query?: PageQuery | Record<string, unknown>): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => (params = params.append(key, String(v))));
    else params = params.set(key, String(value));
  }
  return params;
}

@Injectable({ providedIn: 'root' })
export class ApiClient {
  private readonly http = inject(HttpClient);

  get<T>(path: string, query?: PageQuery | Record<string, unknown>): Observable<T> {
    return this.http.get<T>(API_BASE + path, { params: toParams(query) });
  }

  post<T>(path: string, body: unknown = {}, headers?: Record<string, string>): Observable<T> {
    return this.http.post<T>(API_BASE + path, body, { headers: headers ? new HttpHeaders(headers) : undefined });
  }

  put<T>(path: string, body: unknown): Observable<T> {
    return this.http.put<T>(API_BASE + path, body);
  }

  patch<T>(path: string, body: unknown): Observable<T> {
    return this.http.patch<T>(API_BASE + path, body);
  }

  delete<T>(path: string): Observable<T> {
    return this.http.delete<T>(API_BASE + path);
  }

  upload<T>(path: string, file: File): Observable<T> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<T>(API_BASE + path, form);
  }

  download(path: string, query?: Record<string, unknown>): Observable<Blob> {
    return this.http.get(API_BASE + path, { params: toParams(query), responseType: 'blob' });
  }
}

export function newIdempotencyKey(prefix = 'web'): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
