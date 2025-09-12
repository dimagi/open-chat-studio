/**
 * Translation utilities for the chat widget
 */

import en from '../assets/translations/en.json';
import es from '../assets/translations/es.json';
import fr from '../assets/translations/fr.json';

export type TranslationStrings = typeof en;

// Default (English) translations
export const defaultTranslations: TranslationStrings = en as TranslationStrings;

// Store for loaded translations
const translationCache: Map<string, TranslationStrings> = new Map();

// Available translations map
const translationFiles: Record<string, TranslationStrings> = {
  en: en as TranslationStrings,
  es: es as TranslationStrings,
  fr: fr as TranslationStrings,
};


export function getBrowserLanguage(): string {
  if (typeof navigator !== 'undefined') {
    const lang = navigator.language || (navigator as any).userLanguage;
    if (lang) {
      return lang.split('-')[0].toLowerCase();
    }
  }
  return 'en';
}

export function resolveLanguage(langProp?: string): string {
  if (langProp) {
    return langProp.toLowerCase();
  }
  return getBrowserLanguage();
}

export async function loadTranslations(language: string): Promise<TranslationStrings> {
  if (translationCache.has(language)) {
    return translationCache.get(language)!;
  }

  const base = translationFiles[language] || defaultTranslations;
  const merged = { ...defaultTranslations, ...base };

  translationCache.set(language, merged);
  return merged;
}

/**
 * Overrides matching keys
 */
export function mergeTranslations(
  baseTranslations: TranslationStrings,
  customTranslations: Partial<TranslationStrings>
): TranslationStrings {
  return { ...baseTranslations, ...customTranslations };
}

export class TranslationManager {
  private translations: TranslationStrings = defaultTranslations;
  private language: string = 'en';

  constructor(language?: string, customTranslations?: Partial<TranslationStrings>) {
    this.language = resolveLanguage(language);
    this.loadTranslations(customTranslations);
  }

  private async loadTranslations(customTranslations?: Partial<TranslationStrings>) {
    try {
      const baseTranslations = await loadTranslations(this.language);
      this.translations = customTranslations
        ? mergeTranslations(baseTranslations, customTranslations)
        : baseTranslations;
    } catch (error) {
      console.error('Failed to load translations:', error);
      this.translations = customTranslations
        ? mergeTranslations(defaultTranslations, customTranslations)
        : defaultTranslations;
    }
  }

  get(key: keyof TranslationStrings): string {
    const value = this.translations[key] || defaultTranslations[key];
    if (Array.isArray(value)) {
      return value[0] || '';
    }
    return value || key;
  }

  getAll(): TranslationStrings {
    return this.translations;
  }

  getArray(key: keyof TranslationStrings): string[] {
    const value = this.translations[key] || defaultTranslations[key];
    if (Array.isArray(value)) {
      return value;
    }
    if (typeof value === 'string') {
      return [value];
    }
    return [];
  }

  getLanguage(): string {
    return this.language;
  }
}
