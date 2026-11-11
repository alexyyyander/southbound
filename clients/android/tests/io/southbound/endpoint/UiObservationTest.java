package io.southbound.endpoint;

/** Runs on JDK 17 without an emulator or third-party test dependencies. */
public final class UiObservationTest {
    private static long now;
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) {
        UiObservation gate = new UiObservation(() -> now);
        String first = gate.issue("settings");
        check(gate.consume(first, "settings"));
        check(!gate.consume(first, "settings")); // No replay after success.
        String second = gate.issue("settings");
        check(!gate.consume(second, "payment")); // Page changed before dispatch.
        check(!gate.consume(second, "settings")); // No retry after returning to old page.
        String third = gate.issue("settings");
        now += UiObservation.TTL_MS + 1;
        check(!gate.consume(third, "settings"));
        String old = gate.issue("settings");
        String latest = gate.issue("settings");
        check(!old.equals(latest));
        check(!gate.consume(old, "settings")); // Wrong reference invalidates even the new one.
        check(!gate.consume(latest, "settings"));
        String active = gate.issue("settings");
        gate.clear();
        check(!gate.consume(active, "settings")); // Revocation/service interruption.
        System.out.println("UiObservation: stale page, expiry, revocation and replay checks passed");
    }
}
