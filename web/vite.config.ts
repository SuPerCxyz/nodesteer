/// <reference types="vitest/config" />
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import { playwright } from '@vitest/browser-playwright'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    // 测试与前端共用依赖：预先纳入，避免运行中途重新预构建触发页面重载导致浏览器测试挂起
    include: [
      'i18next',
      'react',
      'react-dom',
      'react-dom/client',
      'react/jsx-runtime',
      'react-hook-form',
      '@hookform/resolvers/zod',
      'zod',
      '@tanstack/react-router',
      'zustand',
      'lucide-react',
      'sonner',
      'clsx',
      'tailwind-merge',
      'vitest-browser-react',
    ],
  },
  test: {
    silent: 'passed-only',
    unstubEnvs: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    browser: {
      enabled: true,
      provider: playwright(),
      instances: [{ browser: 'chromium' }],
    },
    coverage: {
      // include: ['src/**/*.{js,jsx,ts,tsx}'], // Uncomment to expand the report to all src/**/* so untested modules appear as 0% coverage.
      exclude: [
        'src/components/ui/**',
        'src/assets/**',
        'src/tanstack-table.d.ts',
        'src/routeTree.gen.ts',
        'src/test-utils/**',
        'src/routes/**',
      ],
    },
  },
})
