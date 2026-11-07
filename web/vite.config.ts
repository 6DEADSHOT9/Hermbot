import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';

export default defineConfig({
  build: { target: 'es2022' },
  plugins: [react(), tailwind()],
  resolve: { alias: [{find: /^lucide-react$/, replacement: new URL('./src/icons.ts', import.meta.url).pathname}] },
  server: { proxy: { '/api': 'http://api:8000', '/health': 'http://api:8000' } },
});
