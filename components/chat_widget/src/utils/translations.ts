/**
 * Translation utilities for the chat widget
 */

import ar from '../assets/translations/ar.json';
import en from '../assets/translations/en.json';
import es from '../assets/translations/es.json';
import fr from '../assets/translations/fr.json';
import hi from '../assets/translations/hi.json';
import it from '../assets/translations/ita.json';
import pt from '../assets/translations/por.json';
import sw from '../assets/translations/sw.json';
import uk from '../assets/translations/uk.json';

export type TranslationStrings = typeof en;

// Default (English) translations
export const defaultTranslations: TranslationStrings = en as TranslationStrings;

// Available translations map
const translationFiles: Record<string, TranslationStrings> = {
  ar: ar as TranslationStrings,
  en: en as TranslationStrings,
  es: es as TranslationStrings,
  fr: fr as TranslationStrings,
  hi: hi as TranslationStrings,
  it: it as TranslationStrings,
  pt: pt as TranslationStrings,
  sw: sw as TranslationStrings,
  uk: uk as TranslationStrings,
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
  return translationFiles[language] || defaultTranslations;
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
    let baseTranslations: TranslationStrings;
    try {
      baseTranslations = await loadTranslations(this.language);
    } catch (error) {
      console.error('Failed to load translations:', error);
      baseTranslations = defaultTranslations;
    }

    this.translations = customTranslations
      ? mergeTranslations(baseTranslations, customTranslations)
      : baseTranslations;
  }

  get(key: keyof TranslationStrings, override?: string | null): string | undefined {
    if (override !== undefined && override !== null) {
      return override;
    }

    const value = this.translations[key] ?? defaultTranslations[key];
    if (Array.isArray(value)) {
      return value.length > 0 ? value[0] : undefined;
    }
    return value ?? undefined;
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
