package io.southbound.endpoint;

import java.util.UUID;
import java.util.function.LongSupplier;

/** A single-use, short-lived reference to an observed page. */
final class UiObservation {
    static final long TTL_MS = 90000;
    private final LongSupplier clock;
    private String id = "", fingerprint = "";
    private long issuedAt;

    UiObservation(LongSupplier clock) { this.clock = clock; }
    synchronized String issue(String page) {
        id = UUID.randomUUID().toString(); fingerprint = page; issuedAt = clock.getAsLong();
        return id;
    }
    synchronized boolean consume(String reference, String page) {
        long age = clock.getAsLong() - issuedAt;
        boolean valid = !id.isEmpty() && id.equals(reference) && fingerprint.equals(page)
                && age >= 0 && age <= TTL_MS;
        clear(); // A rejected write also invalidates its reference.
        return valid;
    }
    synchronized void clear() { id = ""; fingerprint = ""; }
}
