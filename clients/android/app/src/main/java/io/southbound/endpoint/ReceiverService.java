package io.southbound.endpoint;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Explicitly enabled, user-visible local terminal receiver, including screen-off. */
public final class ReceiverService extends Service implements TerminalEngine.Listener {
    static final String STOP = "io.southbound.endpoint.STOP";
    private static final String CHANNEL = "southbound.receiver";
    private static final int NOTIFICATION_ID = 100;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor();
    private PowerManager.WakeLock wakeLock;
    private TerminalEngine engine;
    private volatile boolean stopping;
    private boolean started;
    private int failures;
    static volatile boolean running;

    static boolean enabled(Context context) {
        return context.getSharedPreferences("receiver", MODE_PRIVATE).getBoolean("enabled", false);
    }
    static boolean setEnabled(Context context, boolean value) {
        return context.getSharedPreferences("receiver", MODE_PRIVATE).edit().putBoolean("enabled", value).commit();
    }
    static String lastStatus(Context context) {
        return context.getSharedPreferences("receiver", MODE_PRIVATE).getString("status", "尚未启动");
    }

    @Override public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "后台任务接收", NotificationManager.IMPORTANCE_LOW));
        manager.createNotificationChannel(new NotificationChannel(TerminalEngine.CHANNEL, "Southbound 提醒", NotificationManager.IMPORTANCE_DEFAULT));
        wakeLock = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,
                "Southbound:terminalReceiver");
        wakeLock.setReferenceCounted(false);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if ((intent != null && STOP.equals(intent.getAction())) || !enabled(this)) {
            setEnabled(this, false);
            status("后台接收已停止");
            stopSelf();
            return START_NOT_STICKY;
        }
        try {
            Notification notification = notification("正在连接；锁屏后继续接收任务");
            if (Build.VERSION.SDK_INT >= 34) startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            else startForeground(NOTIFICATION_ID, notification);
        } catch (RuntimeException exc) {
            setEnabled(this, false);
            status("系统拒绝后台服务，请回到 App 重新开启");
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!started) {
            started = true;
            running = true;
            engine = new TerminalEngine(this, this);
            // The explicit low-latency mode keeps CPU available during screen-off.
            // A timeout bounds accidental leaks; the live worker renews it below.
            wakeLock.acquire(10 * 60 * 1000L);
            worker.execute(this::receive);
        }
        return START_STICKY;
    }

    private void receive() {
        synchronized (this) {
            if (stopping || !enabled(this)) return;
            wakeLock.acquire(10 * 60 * 1000L);
        }
        try {
            failures = engine.tick() ? 0 : Math.min(failures + 1, 5);
        } catch (RuntimeException exc) {
            failures = Math.min(failures + 1, 5);
            status("接收失败，将重试：" + exc.getClass().getSimpleName());
        }
        synchronized (this) {
            if (!stopping && enabled(this)) worker.schedule(this::receive,
                    Math.min(60, 2L << failures), TimeUnit.SECONDS);
        }
    }

    private Notification notification(String message) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, ReceiverService.class).setAction(STOP), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_popup_sync)
                .setContentTitle("Southbound 后台接收中").setContentText(message)
                .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .addAction(new Notification.Action.Builder(null, "停止接收", stop).build()).build();
    }

    @Override public void status(String message) {
        if (!message.equals(lastStatus(this)))
            getSharedPreferences("receiver", MODE_PRIVATE).edit().putString("status", message).apply();
        // Connection state changes are visible in-app; keep the ongoing notice stable.
    }
    @Override public void unauthorized() {
        setEnabled(this, false);
        main.post(this::stopSelf);
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public void onDestroy() {
        UiConsent.clear();
        stopService(new Intent(this, ScreenCaptureService.class));
        synchronized (this) {
            stopping = true;
            running = false;
            worker.shutdownNow();
            if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        }
        if (engine != null) engine.stop();
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }
}
