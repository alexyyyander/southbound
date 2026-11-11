package io.southbound.endpoint;

/** Settings availability and access restrictions vary across Android vendors. */
final class SettingsNavigation {
    static boolean open(Runnable preferred, Runnable fallback) {
        try { preferred.run(); return true; }
        catch (RuntimeException unavailableOrDenied) {
            try { fallback.run(); return true; }
            catch (RuntimeException fallbackUnavailableOrDenied) { return false; }
        }
    }
}
