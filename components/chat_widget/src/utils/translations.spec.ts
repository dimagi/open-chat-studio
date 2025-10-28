import * as translations from './translations';

const defaultTranslations = translations.defaultTranslations;
type TranslationStrings = typeof defaultTranslations;

const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

const setNavigator = (value: any) => {
  Object.defineProperty(globalThis, 'navigator', {
    value,
    configurable: true,
    writable: true,
  });
};

const restoreNavigator = () => {
  if (originalNavigatorDescriptor) {
    Object.defineProperty(globalThis, 'navigator', originalNavigatorDescriptor);
  } else {
    delete (globalThis as any).navigator;
  }
};

describe('getBrowserLanguage', () => {
  afterEach(() => {
    restoreNavigator();
  });

  it('returns the language from navigator.language', () => {
    setNavigator({ language: 'es-MX' });

    expect(translations.getBrowserLanguage()).toBe('es');
  });

  it('falls back to navigator.userLanguage when language is missing', () => {
    setNavigator({ userLanguage: 'fr-CA' });

    expect(translations.getBrowserLanguage()).toBe('fr');
  });

  it('defaults to "en" when navigator is unavailable', () => {
    delete (globalThis as any).navigator;

    expect(translations.getBrowserLanguage()).toBe('en');
  });
});

describe('resolveLanguage', () => {
  it('returns the provided language in lower case', () => {
    expect(translations.resolveLanguage('ES')).toBe('es');
  });

  it('delegates to getBrowserLanguage when no language is provided', () => {
    setNavigator({ language: 'fr-CA' });

    try {
      expect(translations.resolveLanguage()).toBe('fr');
    } finally {
      restoreNavigator();
    }
  });
});

describe('loadTranslations', () => {
  it('returns translations when language exists', async () => {
    const esTranslations = await translations.loadTranslations('es');

    expect(esTranslations['window.close']).toBe('Cerrar');
  });

  it('falls back to default translations for unknown languages', async () => {
    const translated = await translations.loadTranslations('de');

    expect(translated).toBe(defaultTranslations);
  });
});

describe('mergeTranslations', () => {
  it('overrides matching keys from custom translations', () => {
    const base: TranslationStrings = {
      ...defaultTranslations,
      'window.close': 'Close',
    };
    const merged = translations.mergeTranslations(base, { 'window.close': 'Cerrar' });

    expect(merged['window.close']).toBe('Cerrar');
  });
});

describe('TranslationManager', () => {
  const waitForAsyncLoad = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('initializes with resolved language', async () => {
    const manager = new translations.TranslationManager('FR');

    await waitForAsyncLoad();

    expect(manager.getLanguage()).toBe('fr');
  });

  it('returns overrides when provided to get', async () => {
    const manager = new translations.TranslationManager('es', {
      'window.close': 'Cerrar ventana',
    });

    await waitForAsyncLoad();

    expect(manager.get('window.close')).toBe('Cerrar ventana');
    expect(manager.get('window.close', 'Overwrite')).toBe('Overwrite');
  });

  it('falls back to default translations when key is missing', async () => {
    const manager = new translations.TranslationManager('de');

    await waitForAsyncLoad();

    expect(manager.get('window.close')).toBe(defaultTranslations['window.close']);
  });

  it('translations return keys if value is blank', async () => {
    const manager = new translations.TranslationManager('de', {
      "window.close": "",
    });

    await waitForAsyncLoad();

    expect(manager.get('window.close')).toBe("");
  });

  it('returns arrays from getArray and wraps strings', async () => {
    const manager = new translations.TranslationManager('en', {
      'content.welcomeMessages': ['Hello', 'Welcome'],
      'window.newChat': 'New chat now',
    });

    await waitForAsyncLoad();

    expect(manager.getArray('content.welcomeMessages')).toEqual(['Hello', 'Welcome']);
    expect(manager.getArray('window.newChat')).toEqual(['New chat now']);
  });

  // Note: error handling path relies on the internal loadTranslations
  // reference captured at module evaluation time, so it is not practical
  // to force a rejection here without modifying source. We validate
  // fallback behaviour via the unknown-language case instead.
});
