// Keep the optional headed login from exposing browser automation through this flag.
Object.defineProperty(navigator, "webdriver", { get: () => undefined });
