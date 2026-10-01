// Node >= 25 ships its own (unconfigured, undefined) `localStorage` global,
// which shadows jsdom's. Use jsdom's Storage so components behave as in a browser.
const dom = (globalThis as unknown as { jsdom?: { window: Window } }).jsdom
if (dom && !globalThis.localStorage) {
  Object.defineProperty(globalThis, 'localStorage', {
    value: dom.window.localStorage,
    configurable: true,
  })
}
