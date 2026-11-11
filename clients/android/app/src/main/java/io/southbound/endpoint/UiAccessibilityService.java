package io.southbound.endpoint;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.app.KeyguardManager;
import android.content.Intent;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Android execution only. No model, task planning, text matching or automatic retries. */
public final class UiAccessibilityService extends AccessibilityService {
    private static volatile UiAccessibilityService instance;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final UiObservation observation = new UiObservation(SystemClock::elapsedRealtime);
    private UiConsent.Progress progress;
    static boolean connected() { return instance != null; }

    @Override protected void onServiceConnected() { instance = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { /* Read only on explicit commands. */ }
    @Override public void onInterrupt() { invalidate(); }
    @Override public boolean onUnbind(Intent intent) { instance = null; invalidate(); return super.onUnbind(intent); }
    @Override public void onDestroy() { instance = null; invalidate(); super.onDestroy(); }
    private void invalidate() { observation.clear(); }

    static JSONObject execute(String capability, JSONObject args, UiConsent.Progress progress) throws Exception {
        UiAccessibilityService service = instance;
        if (service == null) throw new IllegalStateException("accessibility_unavailable: 请在手机开启 Southbound 无障碍服务");
        return service.run(capability, args, progress);
    }

    private void ready() {
        if (instance != this || !ReceiverService.running || !ReceiverService.enabled(this)
                || !getSharedPreferences("receiver", MODE_PRIVATE).getBoolean("ui_enabled", false)) {
            invalidate();
            throw new IllegalStateException("ui_disabled: 请在手机允许界面操作并开启接收");
        }
        if (!getSystemService(PowerManager.class).isInteractive()
                || getSystemService(KeyguardManager.class).isKeyguardLocked()) {
            invalidate();
            throw new IllegalStateException("device_locked: 请解锁手机后重新观察");
        }
    }

    private boolean allowed(String pkg) {
        return UiConsent.allowed(this, pkg);
    }

    private synchronized JSONObject run(String capability, JSONObject args, UiConsent.Progress progress) throws Exception {
        this.progress = progress;
        ready();
        if ("app.launch".equals(capability)) {
            invalidate();
            String pkg = args.getString("package");
            UiConsent.ensureAllowed(this, pkg, progress);
            ready();
            Intent intent = "com.android.settings".equals(pkg) ? new Intent(Settings.ACTION_SETTINGS).setPackage(pkg)
                    : getPackageManager().getLaunchIntentForPackage(pkg);
            if (intent == null) throw new IllegalStateException("app_unavailable: " + pkg);
            startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return new JSONObject().put("performed", true).put("effect", "launch_requested").put("package", pkg);
        }
        try (Tree tree = readTree()) {
            String current = tree.digest();
            if ("ui.observe".equals(capability)) {
                return tree.json.put("observation_id", observation.issue(current)).put("valid_for_ms", UiObservation.TTL_MS);
            }
            boolean fresh = observation.consume(args.optString("observation_id"), current);
            if (!fresh) throw new IllegalStateException("stale_observation: 页面已变化或引用已过期，请重新读取");
            ready();
            if ("ui.back".equals(capability)) {
                if (!performGlobalAction(GLOBAL_ACTION_BACK)) throw new IllegalStateException("action_rejected");
                return performed("back_requested");
            }
            if ("ui.swipe".equals(capability)) {
                int sx = args.getInt("start_x"), sy = args.getInt("start_y");
                int ex = args.getInt("end_x"), ey = args.getInt("end_y");
                int duration = args.optInt("duration_ms", 450);
                if (duration < 100 || duration > 1500 || !tree.bounds.contains(sx, sy) || !tree.bounds.contains(ex, ey))
                    throw new IllegalArgumentException("invalid_gesture: coordinates must be inside observed app bounds");
                gesture(sx, sy, ex, ey, duration, tree.json.getInt("window_id"), tree.json.getString("package"));
                return performed("gesture_completed");
            }
            AccessibilityNodeInfo node = tree.refs.get(args.getString("element_id"));
            if (node == null || !node.refresh() || !node.isVisibleToUser() || !node.isEnabled() || node.isPassword())
                throw new IllegalStateException("element_unavailable");
            if ("ui.tap".equals(capability)) {
                if (!node.isClickable() || !node.performAction(AccessibilityNodeInfo.ACTION_CLICK))
                    throw new IllegalStateException("action_rejected: select a clickable element");
                return performed("click_requested");
            }
            if ("ui.input".equals(capability)) {
                String value = args.getString("text");
                if (!node.isEditable() || value.length() > 2000) throw new IllegalArgumentException("invalid_input");
                Bundle bundle = new Bundle(); bundle.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
                if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, bundle)) throw new IllegalStateException("action_rejected");
                boolean verified = node.refresh() && value.contentEquals(node.getText() == null ? "" : node.getText());
                return performed("text_set_requested").put("text_verified", verified);
            }
            throw new IllegalArgumentException("unsupported_ui_action");
        }
    }

    private JSONObject performed(String effect) throws Exception {
        return new JSONObject().put("performed", true).put("effect", effect).put("requires_observation", true);
    }

    private void gesture(int sx, int sy, int ex, int ey, int duration, int windowId, String pkg) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicBoolean finished = new AtomicBoolean(), expired = new AtomicBoolean();
        Path path = new Path(); path.moveTo(sx, sy); path.lineTo(ex, ey);
        GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, duration)).build();
        Runnable dispatch = () -> {
            if (expired.get()) { done.countDown(); return; }
            try {
                ready();
                AccessibilityNodeInfo root = getRootInActiveWindow();
                if (root == null) { done.countDown(); return; }
                try {
                    if (root.getWindowId() != windowId || !pkg.contentEquals(root.getPackageName()) || !allowed(pkg)) {
                        done.countDown(); return;
                    }
                } finally { root.recycle(); }
                if (!dispatchGesture(gesture, new GestureResultCallback() {
                    @Override public void onCompleted(GestureDescription g) { finished.set(true); done.countDown(); }
                    @Override public void onCancelled(GestureDescription g) { done.countDown(); }
                }, main)) done.countDown();
            } catch (RuntimeException exc) { done.countDown(); }
        };
        main.post(dispatch);
        if (!done.await(4, TimeUnit.SECONDS)) {
            expired.set(true); main.removeCallbacks(dispatch);
            throw new IllegalStateException("gesture_outcome_unknown: observe before any further action");
        }
        if (!finished.get()) throw new IllegalStateException("gesture_outcome_unknown: cancelled or rejected; observe before any further action");
    }

    private Tree readTree() throws Exception {
        ready();
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new IllegalStateException("screen_unavailable");
        String initialPackage = String.valueOf(root.getPackageName());
        if (!allowed(initialPackage)) {
            root.recycle(); root = null;
            UiConsent.ensureAllowed(this, initialPackage, progress);
            // Let the separate consent task close and reveal the original app.
            for (int attempt = 0; attempt < 40; attempt++) {
                ready(); root = getRootInActiveWindow();
                if (root != null && !getPackageName().contentEquals(root.getPackageName())) break;
                if (root != null) { root.recycle(); root = null; }
                SystemClock.sleep(50);
            }
            if (root == null) throw new IllegalStateException("screen_unavailable_after_consent");
        }
        Tree tree = new Tree();
        try {
            String pkg = String.valueOf(root.getPackageName());
            if (!allowed(pkg)) throw new IllegalStateException("app_not_allowed: " + pkg);
            root.getBoundsInScreen(tree.bounds);
            tree.json.put("package", pkg).put("window_id", root.getWindowId())
                    .put("width", getResources().getDisplayMetrics().widthPixels)
                    .put("height", getResources().getDisplayMetrics().heightPixels)
                    .put("bounds", rect(tree.bounds)).put("nodes", tree.nodes);
            walk(root, "0", "", tree, 0);
            tree.json.put("truncated", tree.truncated);
            ready();
            return tree;
        } catch (Exception exc) { tree.close(); throw exc; }
        finally { root.recycle(); }
    }

    private void walk(AccessibilityNodeInfo node, String id, String parent, Tree tree, int depth) throws Exception {
        if (++tree.visited > 240 || depth > 20) { tree.truncated = true; return; }
        if (node.isVisibleToUser()) {
            Rect bounds = new Rect(); node.getBoundsInScreen(bounds);
            JSONObject item = new JSONObject().put("id", id).put("parent", parent).put("bounds", rect(bounds))
                    .put("clickable", node.isClickable()).put("editable", node.isEditable())
                    .put("scrollable", node.isScrollable()).put("enabled", node.isEnabled());
            if (node.getViewIdResourceName() != null) item.put("resource_id", clipped(node.getViewIdResourceName()));
            if (node.isPassword()) item.put("password", true); // Never expose text/description of password nodes.
            else {
                item.put("text", clipped(node.getText())).put("description", clipped(node.getContentDescription()));
            }
            int bytes = item.toString().getBytes(StandardCharsets.UTF_8).length + 1;
            if (tree.nodes.length() >= 80 || tree.bytes + bytes > 13000) { tree.truncated = true; return; }
            tree.bytes += bytes; tree.nodes.put(item);
            tree.refs.put(id, AccessibilityNodeInfo.obtain(node));
        }
        if (node.isPassword()) return;
        for (int i = 0; i < node.getChildCount(); i++) {
            if (tree.truncated) break;
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            try { walk(child, id + "." + i, id, tree, depth + 1); }
            finally { child.recycle(); }
        }
    }

    private static String clipped(CharSequence text) {
        String value = text == null ? "" : text.toString();
        return value.length() > 100 ? value.substring(0, 100) : value;
    }
    private static JSONArray rect(Rect rect) throws Exception { return new JSONArray(new int[]{rect.left, rect.top, rect.right, rect.bottom}); }
    private static final class Tree implements AutoCloseable {
        final JSONObject json = new JSONObject();
        final JSONArray nodes = new JSONArray();
        final Map<String, AccessibilityNodeInfo> refs = new HashMap<>();
        final Rect bounds = new Rect();
        int bytes, visited;
        boolean truncated;
        String digest() throws Exception {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(json.toString().getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder();
            for (byte b : hash) out.append(String.format("%02x", b));
            return out.toString();
        }
        @Override public void close() { for (AccessibilityNodeInfo node : refs.values()) node.recycle(); refs.clear(); }
    }
}
