package io.southbound.endpoint;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;

/** Not exported. No remote endpoint can press these buttons or supply a package to grant. */
public final class UiConsentActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private UiConsent.Request request;
    private AlertDialog dialog;
    private final Runnable expiry = new Runnable() {
        @Override public void run() {
            if (request == null || UiConsent.request(request.id) == null || request.decided) finishAndRemoveTask();
            else main.postDelayed(this, 500);
        }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        request = UiConsent.request(getIntent().getStringExtra("request_id"));
        if (request == null) { finishAndRemoveTask(); return; }
        dialog = new AlertDialog.Builder(this).setTitle("允许访问“" + request.label + "”？")
                .setMessage("电脑 Agent 的当前任务需要读取和操作这个应用。\n" + request.pkg
                        + "\n\n允许后任务继续；拒绝则停止这一步。")
                .setPositiveButton("允许 10 分钟", (d, which) -> choose(true, false))
                .setNeutralButton("始终允许", (d, which) -> choose(true, true))
                .setNegativeButton("不允许", (d, which) -> choose(false, false))
                .setOnCancelListener(d -> choose(false, false)).create();
        dialog.setCanceledOnTouchOutside(false);
        dialog.show();
        for (int button : new int[]{AlertDialog.BUTTON_POSITIVE, AlertDialog.BUTTON_NEUTRAL, AlertDialog.BUTTON_NEGATIVE})
            dialog.getButton(button).setFilterTouchesWhenObscured(true);
        main.post(expiry);
    }
    private void choose(boolean allow, boolean remember) {
        if (request != null) UiConsent.decide(this, request, allow, remember);
        finishAndRemoveTask();
    }
    @Override public void onBackPressed() { choose(false, false); }
    @Override public void onDestroy() {
        main.removeCallbacks(expiry);
        if (dialog != null) dialog.dismiss();
        if (request != null && !isChangingConfigurations()) {
            if (!request.decided) UiConsent.decide(this, request, false, false);
            request.closed.countDown();
        }
        super.onDestroy();
    }
}
