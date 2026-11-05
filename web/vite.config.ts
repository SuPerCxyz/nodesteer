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
      '@radix-ui/react-direction',
      '@tanstack/react-router',
      'react-top-loading-bar',
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
    // 应用代码在保存成功后会调用 window.location.assign/replace（真实导航）。
    // 浏览器模式下文件共享同一页面/iframe：任一文件触发真实导航会让当前或后续文件
    // 报 "Cannot connect to the iframe" / "Failed to fetch dynamically imported module"
    // 而随机失败（非产品缺陷）。setup 文件用 Navigation API 取消跨文档导航；
    // npm test 由 scripts/run-tests.mjs 按文件分批重启 Vitest 会话，避免单会话累计
    // 运行多个文件后出现基础设施 flake。fileParallelism 保留为额外隔离。
    setupFiles: ['./src/test-utils/setup.ts'],
    fileParallelism: false,
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
