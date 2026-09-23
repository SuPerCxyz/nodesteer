import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en'
import zh from './locales/zh'

// 默认中文；用户切换后持久化到 localStorage
const saved = localStorage.getItem('nodesteer_lang')
const lang = saved === 'en' || saved === 'zh' ? saved : 'zh'

i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: lang,
  fallbackLng: 'zh',
  interpolation: { escapeValue: false },
})

export function setLang(l: 'zh' | 'en') {
  localStorage.setItem('nodesteer_lang', l)
  i18n.changeLanguage(l)
  syncHtmlLang(l)
}

/** 语言与 <html lang> 保持一致（可访问性与 SEO）。 */
export function syncHtmlLang(l: string) {
  if (typeof document !== 'undefined') {
    document.documentElement.lang = l === 'en' ? 'en' : 'zh-CN'
  }
}

syncHtmlLang(lang)

export default i18n
