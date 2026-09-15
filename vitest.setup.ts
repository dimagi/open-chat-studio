import '@testing-library/jest-dom/vitest';
import {afterEach} from 'vitest';
import {cleanup} from '@testing-library/react';

// RTL only auto-registers this via a bare global `afterEach`, which requires `test.globals:
// true` in vitest.config.mjs. That's off here (kept scoped, no implicit globals), so without
// this, DOM from every earlier test in a file accumulates in document.body and queries like
// getByRole can silently match a stale element from a previous test instead of the current one.
afterEach(cleanup);
