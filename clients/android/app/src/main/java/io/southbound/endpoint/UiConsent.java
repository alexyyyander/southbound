package io.southbound.endpoint;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Local, explicit user decisions. The server cannot grant application access. */
final class UiConsent {
    interface Progress { void update(String phase, String message) throws Exception; }
    private static final String CHANNEL = "southbound.consent";
    private static final int NOTICE = 103;
    private static final Map<String, Long> temporary = new HashMap<>();
    private static Request pending;
    static final class Request {
        final String id = UUID.randomUUID().toString(), pkg, label;
        final long deadline = SystemClock.elapsedRealtime() + 60000;
        final CountDownLatch closed = new CountDownLatch(1);
        volatile boolean approved, decided;
        Request(String pkg, String label) { this.pkg = pkg; this.label = label; }
    }

    static boolean protectedPackage(Context context, String pkg) {
        return pkg.isEmpty() || pkg.equals(context.getPackageName()) || pkg.equals("com.android.systemui")
                || pkg.contains("permissioncontroller") || pkg.contains("packageinstaller");
    }
    static synchronized boolean allowed(Context context, String pkg) {
        if (protectedPackage(context, pkg)) return false;
        if (temporary.getOrDefault(pkg, 0L) > SystemClock.elapsedRealtime()) return true;
        String packages = context.getSharedPreferences("receiver", Context.MODE_PRIVATE)
                .getString("ui_packages", "com.android.settings");
        for (String value : packages.split("[,，\\s]+")) if (pkg.equals(value)) return true;
        return false;
    }
    static synchronized Request request(String id) {
        return pending != null && pending.id.equals(id) && SystemClock.elapsedRealtime() < pending.deadline ? pending : null;
    }
    static synchronized void decide(Context context, Request request, boolean allow, boolean remember) {
        if (pending != request || request.decided || SystemClock.elapsedRealtime() >= request.deadline) return;
        if (!ReceiverService.running || !ReceiverService.enabled(context)
                || !context.getSharedPreferences("receiver", Context.MODE_PRIVATE).getBoolean("ui_enabled", false)) allow = false;
        if (allow) {
            if (remember) {
                String packages = context.getSharedPreferences("receiver", Context.MODE_PRIVATE).getString("ui_packages", "com.android.settings");
                if (!context.getSharedPreferences("receiver", Context.MODE_PRIVATE).edit()
                        .putString("ui_packages", packages + "," + request.pkg).commit()) allow = false;
            } else temporary.put(request.pkg, SystemClock.elapsedRealtime() + 10 * 60 * 1000L);
        }
        request.approved = allow; request.decided = true;
    }
    static synchronized void clear() {
        temporary.clear();
        if (pending != null) { pending.approved = false; pending.decided = true; pending.closed.countDown(); }
    }

    static void ensureAllowed(UiAccessibilityService service, String pkg, Progress progress) throws Exception {
        if (allowed(service, pkg)) return;
        if (protectedPackage(service, pkg)) throw new IllegalStateException("app_not_allowed: " + pkg);
        String label = pkg;
        try { label = service.getPackageManager().getApplicationLabel(service.getPackageManager().getApplicationInfo(pkg, 0)).toString(); }
        catch (Exception ignored) { /* Package remains visible as the authoritative identity. */ }
        Request request = new Request(pkg, label);
        synchronized (UiConsent.class) {
            if (pending != null) throw new IllegalStateException("consent_busy");
            pending = request;
        }
        NotificationManager manager = service.getSystemService(NotificationManager.class);
        try {
            progress.update("waiting_for_user", "请在手机确认访问：" + label);
            Intent intent = new Intent(service, UiConsentActivity.class).putExtra("request_id", request.id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS);
            manager.createNotificationChannel(new NotificationChannel(CHANNEL, "应用访问确认", NotificationManager.IMPORTANCE_HIGH));
            PendingIntent open = PendingIntent.getActivity(service, 103, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            manager.notify(NOTICE, new Notification.Builder(service, CHANNEL)
                    .setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle("任务等待你的确认")
                    .setContentText("允许访问“" + label + "”？").setContentIntent(open).setAutoCancel(true)
                    .setVisibility(Notification.VISIBILITY_PRIVATE).build());
            new Handler(Looper.getMainLooper()).post(() -> {
                if (request(request.id) == null) return;
                try { service.startActivity(intent); }
                catch (RuntimeException ignored) { /* Notification remains as the user-initiated entry point. */ }
            });
            while (SystemClock.elapsedRealtime() < request.deadline) {
                if (request.closed.await(Math.min(10000, Math.max(1, request.deadline - SystemClock.elapsedRealtime())), TimeUnit.MILLISECONDS)) break;
                if (!ReceiverService.running || !ReceiverService.enabled(service)
                        || !service.getSharedPreferences("receiver", Context.MODE_PRIVATE).getBoolean("ui_enabled", false))
                    throw new IllegalStateException("ui_disabled");
                progress.update("waiting_for_user", "请在手机确认访问：" + label);
            }
            if (!request.decided || !request.approved || SystemClock.elapsedRealtime() >= request.deadline)
                throw new IllegalStateException("app_access_not_granted: 已拒绝或确认超时");
            // Server checks cancellation, expiry and capability revocation before we continue.
            progress.update("executing", "手机已确认应用访问");
        } finally {
            synchronized (UiConsent.class) { if (pending == request) pending = null; }
            manager.cancel(NOTICE);
        }
    }
}
