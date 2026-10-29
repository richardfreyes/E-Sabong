import { Directive, TemplateRef, ViewContainerRef, effect, inject, input } from '@angular/core';

import { AuthStore } from '../services/auth.store';

/**
 * Renders content only when the user holds any of the given permissions.
 * `<button *appHasPermission="'users.manage'">` or `*appHasPermission="['a', 'b']"`.
 * Purely cosmetic: the API enforces every permission independently.
 */
@Directive({ selector: '[appHasPermission]' })
export class HasPermissionDirective {
  readonly appHasPermission = input.required<string | string[]>();
  private readonly auth = inject(AuthStore);
  private readonly tpl = inject(TemplateRef);
  private readonly vcr = inject(ViewContainerRef);
  private shown = false;

  constructor() {
    effect(() => {
      const needed = this.appHasPermission();
      const list = Array.isArray(needed) ? needed : [needed];
      const allowed = this.auth.hasAnyPermission(...list);
      if (allowed && !this.shown) {
        this.vcr.createEmbeddedView(this.tpl);
        this.shown = true;
      } else if (!allowed && this.shown) {
        this.vcr.clear();
        this.shown = false;
      }
    });
  }
}
