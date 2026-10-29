import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'pp-page-head',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 0.75rem 1rem; margin-bottom: 1.25rem; }
    h1 { margin: 0; font-size: clamp(2rem, 1.6rem + 1.6vw, 2.75rem); color: var(--pp-chalk); }
    p { margin: 0.35rem 0 0; color: var(--pp-ash); font-size: 0.9rem; max-width: 60ch; }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
  `,
  template: `
    <header>
      <div class="min-w-0">
        <ng-content select="[back]" />
        <h1 class="pp-display">{{ title() }}</h1>
        @if (subtitle()) { <p>{{ subtitle() }}</p> }
      </div>
      <div class="actions"><ng-content /></div>
    </header>
  `,
})
export class PageHeadComponent {
  readonly title = input.required<string>();
  readonly subtitle = input<string | null>(null);
}
