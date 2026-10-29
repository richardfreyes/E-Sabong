import { defineConfig } from 'vitest/config';

// Loaded by the Angular unit-test builder (runnerConfig: true).
export default defineConfig({
  test: {
    server: {
      deps: {
        // Ships extensionless ESM imports that Node's resolver rejects; let Vite transform it.
        inline: [/@material\/material-color-utilities/],
      },
    },
  },
});
