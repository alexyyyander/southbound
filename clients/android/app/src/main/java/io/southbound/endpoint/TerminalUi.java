package io.southbound.endpoint;

import android.app.KeyguardManager;
import android.os.Handler;
import android.os.Looper;
import java.lang.ref.WeakReference;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** UI-only actions never unlock the phone or launch an activity from background. */
final class TerminalUi {
    private static WeakReference<MainActivity> active = new WeakReference<>(null);
    static void attach(MainActivity activity) { active = new WeakReference<>(activity); }
    static void detach(MainActivity activity) { if (active.get() == activity) active.clear(); }
    static boolean display(String message) throws InterruptedException {
        CountDownLatch done = new CountDownLatch(1);
        AtomicBoolean shown = new AtomicBoolean(false);
        AtomicBoolean expired = new AtomicBoolean(false);
        new Handler(Looper.getMainLooper()).post(() -> {
            MainActivity activity = active.get();
            if (!expired.get() && activity != null && !activity.isDestroyed()
                    && activity.hasWindowFocus()
                    && !activity.getSystemService(KeyguardManager.class).isKeyguardLocked()) {
                activity.showTerminalMessage(message);
                shown.set(true);
            }
            done.countDown();
        });
        if (!done.await(3, TimeUnit.SECONDS)) expired.set(true);
        return shown.get();
    }
}
