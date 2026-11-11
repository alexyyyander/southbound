package io.southbound.endpoint;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.SystemClock;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import org.json.JSONObject;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** A single receiver owns polling, command execution and the durable outbox. */
final class TerminalEngine {
    interface Listener { void status(String value); void unauthorized(); }
    static final String CHANNEL = "southbound.commands";
    private final Context context;
    private final Listener listener;
    private final CredentialStore credentials;
    private final CommandJournal journal;
    private final TextToSpeech tts;
    private volatile boolean ttsReady;
    private volatile boolean stopped;
    private boolean ticking;
    private long nextHello;

    TerminalEngine(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        credentials = new CredentialStore(context);
        journal = new CommandJournal(context);
        tts = new TextToSpeech(context, status -> ttsReady = status == TextToSpeech.SUCCESS);
    }
    void stop() {
        synchronized (this) {
            stopped = true;
            if (!ticking) journal.close();
        }
        tts.stop();
        tts.shutdown();
    }
    private void status(String value) { listener.status(value); }
    private JSONObject post(JSONObject config, String route, JSONObject body) throws Exception {
        return ProtocolClient.post(config.getString("base_url"), "/api/terminal/" + route,
                config.getString("token"), body);
    }

    boolean tick() {
        synchronized (this) {
            if (stopped) return false;
            ticking = true;
        }
        try { return receive(); }
        finally {
            synchronized (this) {
                ticking = false;
                if (stopped) journal.close();
            }
        }
    }

    private boolean receive() {
        try {
            JSONObject config = credentials.load();
            if (config == null) { listener.unauthorized(); return false; }
            if (SystemClock.elapsedRealtime() >= nextHello) {
                try {
                    JSONObject hello=post(config,"hello",new JSONObject().put("protocol_version",1).put("capabilities",TerminalCapabilities.supported()));
                    if (!config.getJSONArray("capabilities").toString().equals(hello.getJSONArray("capabilities").toString())) {
                        config.put("capabilities",hello.getJSONArray("capabilities"));credentials.save(config);
                    }
                    boolean screen=false;
                    for (int i=0;i<config.getJSONArray("capabilities").length();i++) screen |= "capture_screen".equals(config.getJSONArray("capabilities").getString(i));
                    context.getSharedPreferences("receiver",Context.MODE_PRIVATE).edit().putBoolean("capture_granted",screen).apply();
                } catch (ProtocolClient.HttpError exc) {if (exc.status!=404) throw exc;}
                nextHello=SystemClock.elapsedRealtime()+60000;
            }
            flush(config);
            long pollStarted = SystemClock.elapsedRealtime();
            JSONObject delivery = post(config, "poll", new JSONObject());
            if (delivery.isNull("command")) { status("已连接 · " + config.getString("device_id")); return true; }
            JSONObject command = delivery.getJSONObject("command");
            String id = command.getString("command_id");
            if (command.getInt("protocol_version") != 1
                    || !config.getString("device_id").equals(command.getString("device_id")))
                throw new IllegalStateException("命令版本或目标设备不匹配");
            JSONObject receipt;
            if (!journal.begin(config.getString("scope"), id)) {
                receipt = journal.receipt(id);
                if (receipt == null) receipt = receipt(id, "unknown",
                        new JSONObject().put("error", "Client restarted during execution; action was not repeated"));
            } else if (stopped) {
                receipt = receipt(id, "failed", new JSONObject().put("error", "Client is no longer active"));
            } else if (command.getDouble("expires_at") - delivery.getDouble("server_time")
                    <= (SystemClock.elapsedRealtime() - pollStarted) / 1000.0) {
                receipt = receipt(id, "failed", new JSONObject().put("error", "Command expired"));
            } else {
                try {
                    boolean granted = false;
                    for (int i = 0; i < config.getJSONArray("capabilities").length(); i++)
                        granted |= command.getString("capability").equals(config.getJSONArray("capabilities").getString(i));
                    if (!granted) throw new IllegalArgumentException("Capability not granted");
                    receipt = receipt(id, "succeeded", execute(command, config));
                } catch (Exception exc) {
                    String outcome = exc.toString().contains("outcome_unknown") ? "unknown" : "failed";
                    receipt = receipt(id, outcome, new JSONObject().put("error", exc.toString()));
                }
            }
            journal.finish(id, receipt);
            flush(config);
            status("任务 " + command.getString("capability") + "：" + receipt.getString("status"));
            return true;
        } catch (ProtocolClient.HttpError exc) {
            if (exc.status == 401) listener.unauthorized();
            status(exc.status == 401 ? "设备授权已失效，请重新配对" : "连接失败，将重试：" + exc.getMessage());
        } catch (Exception exc) { status("连接失败，将重试：" + exc.getMessage()); }
        return false;
    }

    private JSONObject receipt(String id, String status, JSONObject result) throws Exception {
        return new JSONObject().put("command_id", id).put("status", status).put("result", result);
    }

    private void flush(JSONObject config) throws Exception {
        for (String id : journal.pending(config.getString("scope"), false)) {
            try { post(config, "receipt", journal.receipt(id)); }
            catch (ProtocolClient.HttpError exc) {
                if (exc.status != 409 && exc.status != 404) throw exc;
                // Expired/cancelled commands remain recorded locally; do not replay effects.
            }
            journal.mark(id, "sent");
        }
        for (String id : journal.pending(config.getString("scope"), true)) {
            try { post(config, "notification-click", new JSONObject().put("command_id", id)); }
            catch (ProtocolClient.HttpError exc) {
                if (exc.status != 409 && exc.status != 404) throw exc;
            }
            journal.mark(id, "click_sent");
        }
    }

    private JSONObject execute(JSONObject command, JSONObject config) throws Exception {
        String id = command.getString("command_id");
        JSONObject args = command.getJSONObject("arguments");
        switch (command.getString("capability")) {
            case "ui.observe": case "app.launch": case "ui.tap": case "ui.input": case "ui.swipe": case "ui.back":
                return UiAccessibilityService.execute(command.getString("capability"), args, (phase, message) -> {
                    if (stopped) throw new IllegalStateException("Client is no longer active");
                    post(config, "progress", new JSONObject().put("command_id", id).put("phase", phase).put("message", message));
                    status(message);
                });
            case "device_info": return DeviceInfo.read(context);
            case "capture_screen": return ProtocolClient.uploadImage(config.getString("base_url"),config.getString("token"),id,ScreenCaptureService.capture());
            case "notify": {
                NotificationManager manager = context.getSystemService(NotificationManager.class);
                if ((Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                        != PackageManager.PERMISSION_GRANTED) || !manager.areNotificationsEnabled()
                        || manager.getNotificationChannel(CHANNEL).getImportance() == NotificationManager.IMPORTANCE_NONE)
                    throw new IllegalStateException("Notification permission/channel is disabled");
                Intent intent = new Intent(context, MainActivity.class)
                        .setData(Uri.parse("southbound://notification/" + id)).putExtra("command_id", id);
                PendingIntent click = PendingIntent.getActivity(context, 0, intent,
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
                Notification notification = new Notification.Builder(context, CHANNEL)
                        .setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(args.getString("title"))
                        .setContentText(args.getString("text"))
                        .setStyle(new Notification.BigTextStyle().bigText(args.getString("text")))
                        .setContentIntent(click).setAutoCancel(true).build();
                manager.notify(id, 1, notification);
                return new JSONObject().put("published", true);
            }
            case "read_battery": {
                Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
                if (battery == null) throw new IllegalStateException("Battery unavailable");
                int level = battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
                int scale = battery.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
                if (level < 0 || scale <= 0) throw new IllegalStateException("Battery sample invalid");
                int state = battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
                return new JSONObject().put("level", Math.round(100f * level / scale))
                        .put("charging", state == BatteryManager.BATTERY_STATUS_CHARGING
                                || state == BatteryManager.BATTERY_STATUS_FULL);
            }
            case "show_message": {
                if (!TerminalUi.display(args.getString("text")))
                    throw new IllegalStateException("show_message requires an unlocked, visible client; use notify while locked");
                return new JSONObject().put("displayed", true);
            }
            case "speak": {
                if (!ttsReady) throw new IllegalStateException("TTS engine not ready");
                CountDownLatch done = new CountDownLatch(1);
                AtomicReference<String> error = new AtomicReference<>();
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String utteranceId) {}
                    @Override public void onDone(String utteranceId) { if (id.equals(utteranceId)) done.countDown(); }
                    @Override public void onError(String utteranceId) {
                        if (id.equals(utteranceId)) { error.set("TTS failed"); done.countDown(); }
                    }
                });
                if (tts.speak(args.getString("text"), TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.ERROR)
                    throw new IllegalStateException("TTS request rejected");
                if (!done.await(20, TimeUnit.SECONDS)) { tts.stop(); throw new IllegalStateException("TTS completion timeout"); }
                if (error.get() != null) throw new IllegalStateException(error.get());
                return new JSONObject().put("completed", true);
            }
            default: throw new IllegalArgumentException("Unsupported capability");
        }
    }

}
