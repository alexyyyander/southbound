package io.southbound.endpoint;

public final class SettingsNavigationTest {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) {
        int[] launches = {0};
        check(SettingsNavigation.open(() -> launches[0]++, () -> { throw new AssertionError("unexpected fallback"); }));
        check(launches[0] == 1);
        check(SettingsNavigation.open(() -> { throw new SecurityException("permission denied"); }, () -> launches[0]++));
        check(launches[0] == 2);
        check(SettingsNavigation.open(() -> { throw new IllegalStateException("no handler"); }, () -> launches[0]++));
        check(launches[0] == 3);
        check(!SettingsNavigation.open(() -> { throw new SecurityException(); }, () -> { throw new SecurityException(); }));
        System.out.println("SettingsNavigation: denied and unavailable settings handled without crashing");
    }
}
