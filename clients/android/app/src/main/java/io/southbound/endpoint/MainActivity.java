package io.southbound.endpoint;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.os.PowerManager;
import android.media.projection.MediaProjectionManager;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONObject;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** One connection action, guided OS permissions, and a device-scoped smoke test. */
public final class MainActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private CredentialStore credentials;
    private CommandJournal journal;
    private EditText urlField, codeField;
    private TextView statusView, receiverView, messageView, testView, uiStatus;
    private Button primary, testButton, screenButton, uiButton;
    private boolean paired, busy, flow, batteryAsked, notificationAsked, resumed, checking, uiSetup;
    private int settingsReturn;
    private String testRequest = "", testScope = "";
    private JSONObject testResult;
    private long resultAt;
    private long nextCheck;
    private final Runnable refresh = new Runnable() {
        @Override public void run() {
            primary.setEnabled(!busy);
            primary.setText(busy ? "正在连接…" : ReceiverService.running ? "停止接收" : paired ? "开启接收" : "连接并启用");
            receiverView.setText(ReceiverService.running ? "接收中 · " + ReceiverService.lastStatus(MainActivity.this) : paired ? "已连接 · 接收已停止" : "等待连接");
            screenButton.setText(ScreenCaptureService.active() ? "停止屏幕共享" : "允许屏幕共享（5 分钟）");
            boolean uiAllowed = getSharedPreferences("receiver",MODE_PRIVATE).getBoolean("ui_enabled",false);
            uiButton.setText(uiAllowed ? "停止界面操作" : "启用手机控制");
            uiButton.setContentDescription(uiButton.getText() + (UiAccessibilityService.connected() ? "，无障碍已连接" : "，无障碍未开启"));
            uiStatus.setText(!uiAllowed ? "界面操作已关闭" : UiAccessibilityService.connected()
                    ? "无障碍已连接 · 保持解锁后可执行界面命令" : "请在系统无障碍设置中开启 Southbound 界面操作");
            boolean pending = testResult != null && ("running".equals(testResult.optString("status")) || "approved".equals(testResult.optString("status")));
            testButton.setEnabled(ReceiverService.running && !busy && !pending && !checking);
            showTest();
            if (!testRequest.isEmpty() && !checking && System.currentTimeMillis() >= nextCheck) checkTest(false);
            main.postDelayed(this, 1000);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        credentials = new CredentialStore(this);
        journal = new CommandJournal(this);
        if (state != null) {
            flow = state.getBoolean("flow"); batteryAsked = state.getBoolean("batteryAsked");
            notificationAsked = state.getBoolean("notificationAsked"); settingsReturn = state.getInt("settingsReturn");
            uiSetup = state.getBoolean("uiSetup");
        }
        buildUi();
        if (state != null && !paired) {
            urlField.setText(state.getString("pendingUrl", ""));
            codeField.setText(state.getString("pendingCode", ""));
        }
        handleIntent(getIntent());
    }
    private TextView text(String value, int size) {
        TextView v = new TextView(this); v.setText(value); v.setTextSize(size);
        v.setTextColor(Color.rgb(25,48,45)); v.setPadding(0,12,0,12); return v;
    }
    private Button button(LinearLayout parent, String value, View.OnClickListener listener) {
        Button v = new Button(this); v.setText(value); v.setOnClickListener(listener); parent.addView(v); return v;
    }
    private void buildUi() {
        LinearLayout content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL);
        int pad = (int)(24 * getResources().getDisplayMetrics().density);
        content.setPadding(pad,pad,pad,pad); content.setBackgroundColor(Color.rgb(244,247,246));
        content.addView(text("Southbound",30)); content.addView(text("你的手机，随时接收任务。",18));
        receiverView=text("等待连接",20); content.addView(receiverView);
        statusView=text("请先扫描电脑上的接入二维码，再从安装页打开 App。",15); content.addView(statusView);
        primary=button(content,"连接并启用",v -> connectAndEnable());
        content.addView(text("首次使用会引导通知和锁屏联网授权。接收时会显示常驻通知，并增加耗电。",14));
        testButton=button(content,"测试锁屏任务",v -> startTest()); testButton.setEnabled(false);
        testView=text("点击测试后，30 秒内锁屏。手机会先发通知，再播报语音。请调高媒体音量。",16); content.addView(testView);
        screenButton=button(content,"允许屏幕共享（5 分钟）",v -> {
            if (ScreenCaptureService.active()) {stopService(new Intent(this,ScreenCaptureService.class));return;}
            if (!ReceiverService.running) {status("请先开启接收。");return;}
            if (!getSharedPreferences("receiver",MODE_PRIVATE).getBoolean("capture_granted",false)) {status("需主人启用截图能力；更新 App 后请稍等能力同步。");return;}
            startActivityForResult(getSystemService(MediaProjectionManager.class).createScreenCaptureIntent(),20);
        });
        content.addView(text("屏幕共享开启时，Agent 可请求截图。锁屏或系统停止共享后需重新授权；受保护页面可能显示黑屏。",14));
        uiButton=button(content,"启用手机控制",v -> {
            boolean enabled=getSharedPreferences("receiver",MODE_PRIVATE).getBoolean("ui_enabled",false);
            if (!enabled && !paired) {status("请先连接手机，再启用手机控制。");return;}
            getSharedPreferences("receiver",MODE_PRIVATE).edit().putBoolean("ui_enabled",!enabled).apply();
            if (enabled) {UiConsent.clear();uiSetup=false;status("界面操作已停止。");}
            else {uiSetup=true;flow=true;batteryAsked=false;continueEnable();}
        });
        button(content,"开启 Southbound 无障碍服务",v -> openAccessibilitySettings());
        uiStatus=text("界面操作已关闭",15);content.addView(uiStatus);
        content.addView(text("界面操作由电脑 Agent 规划。首次访问其他 App 会弹窗确认，无需填写包名。锁屏或停止接收时不操作。",14));
        button(content,"查看本机硬件信息",v -> {
            worker.execute(() -> {
                try {String info=DeviceInfo.read(this).toString(2);main.post(() -> {if (!isDestroyed()) messageView.setText(info);});}
                catch (Exception exc) {status("硬件信息暂不可用："+exc.getClass().getSimpleName());}
            });
        });
        LinearLayout advanced = new LinearLayout(this); advanced.setOrientation(LinearLayout.VERTICAL); advanced.setVisibility(View.GONE);
        button(content,"连接详情与手动设置",v -> advanced.setVisibility(advanced.getVisibility()==View.VISIBLE ? View.GONE : View.VISIBLE));
        urlField=new EditText(this); urlField.setSingleLine(true); urlField.setHint("服务地址");
        urlField.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI); advanced.addView(urlField);
        codeField=new EditText(this); codeField.setSingleLine(true); codeField.setHint("配对码（扫码会自动填写）");
        codeField.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD); advanced.addView(codeField);
        button(advanced,"检查通知与锁屏权限",v -> { if (paired) { flow=true; batteryAsked=false; continueEnable(); } else status("请先连接手机"); });
        EditText uiPackages=new EditText(this);
        uiPackages.setHint("允许操作的 App 包名，逗号分隔");
        uiPackages.setText(getSharedPreferences("receiver",MODE_PRIVATE).getString("ui_packages","com.android.settings"));
        advanced.addView(uiPackages);
        button(advanced,"保存允许操作的 App",v -> {
            String packages=uiPackages.getText().toString().trim();
            if (packages.length()>2000 || !packages.matches("[a-zA-Z0-9_.,，\\s]*")) {status("请输入 App 包名，用逗号分隔。");return;}
            getSharedPreferences("receiver",MODE_PRIVATE).edit().putString("ui_packages",packages).apply();
            UiConsent.clear();
            status("允许列表已保存；空列表表示不允许操作任何 App。");
        });
        content.addView(advanced);
        content.addView(text("终端消息",18));
        messageView=text(getSharedPreferences("receiver",MODE_PRIVATE).getString("message","暂无消息"),16); content.addView(messageView);
        ScrollView scroll=new ScrollView(this); scroll.addView(content); setContentView(scroll);
        scroll.setOnApplyWindowInsetsListener((v,insets) -> {v.setPadding(0,insets.getSystemWindowInsetTop(),0,insets.getSystemWindowInsetBottom());return insets;});
        try {
            JSONObject saved=credentials.load(); paired=saved!=null;
            if (paired) {
                urlField.setText(saved.getString("base_url")); status("已连接到 " + saved.getString("base_url"));
                testScope=saved.getString("scope");
                var prefs=getSharedPreferences("self_test",MODE_PRIVATE);
                if (testScope.equals(prefs.getString("scope",""))) testRequest=prefs.getString("request","");
            }
        } catch (Exception exc) { status("凭据不可用，请在电脑撤销旧设备后重新接入。"); }
    }
    private void openAccessibilitySettings() {
        // ACCESSIBILITY_DETAILS_SETTINGS is a system-only API, not an app permission shortcut.
        boolean opened = SettingsNavigation.open(
                () -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)),
                () -> startActivity(new Intent(Settings.ACTION_SETTINGS)));
        status(opened ? "请在系统无障碍列表中开启 Southbound 界面操作。"
                : "系统未允许打开设置；请手动进入设置 → 无障碍，开启 Southbound 界面操作。");
    }
    private void status(String value) { main.post(() -> {if (!isDestroyed()) statusView.setText(value);}); }
    private void handleIntent(Intent intent) {
        if (intent == null) return;
        String id=intent.getStringExtra("command_id");
        if (id!=null && id.matches("[a-f0-9]{32}")) {journal.mark(id,"clicked");intent.removeExtra("command_id");}
        Uri uri=intent.getData();
        if (uri==null || !"southbound".equals(uri.getScheme()) || !"connect".equals(uri.getHost())) return;
        try {
            String base=ProtocolClient.normalizeUrl(uri.getQueryParameter("server"));
            String code=uri.getQueryParameter("code");
            if (code==null || !code.matches("[A-Za-z0-9_-]{40,128}")) throw new IllegalArgumentException();
            if (paired) {
                status("已连接到 " + credentials.load().getString("base_url") + "。可直接开启接收。");
            } else if (!busy) {
                urlField.setText(base);codeField.setText(code);
                status("将连接到 " + base + "。点击“连接并启用”继续。");
            }
        } catch (Exception exc) { status("接入链接无效，请重新扫描电脑上的二维码。"); }
        intent.setData(null); // Do not retain a consumed grant in the Activity intent.
    }
    private void connectAndEnable() {
        if (busy) return;
        if (ReceiverService.running) {
            flow=false; ReceiverService.setEnabled(this,false);stopService(new Intent(this,ReceiverService.class));
            status("已停止接收。已经开始的任务可能仍会完成。");return;
        }
        flow=true;batteryAsked=false;
        if (paired) {continueEnable();return;}
        final String base,code=codeField.getText().toString().trim();
        try {
            base=ProtocolClient.normalizeUrl(urlField.getText().toString());
            if (code.isEmpty()) throw new IllegalArgumentException();
        } catch (Exception exc) {flow=false;status("请从安装页面点击连接，或展开手动设置填写连接信息。");return;}
        busy=true;primary.setEnabled(false);
        worker.execute(() -> {
            try {
                JSONObject caps=TerminalCapabilities.supported();
                JSONObject saved=ProtocolClient.post(base,"/api/terminal/pair","",new JSONObject()
                        .put("protocol_version",1).put("pairing_code",code).put("platform","android").put("capabilities",caps));
                saved.put("base_url",base).put("scope",UUID.randomUUID().toString());credentials.save(saved);
                main.post(() -> {
                    if (isDestroyed()) return;
                    busy=false;paired=true;testScope=saved.optString("scope");codeField.setText("");
                    status("已连接到 " + base);continueEnable();
                });
            } catch (Exception exc) {
                main.post(() -> {busy=false;flow=false;});
                status("连接失败。请检查同一 Wi-Fi 和链接有效期；若已配对但响应丢失，需在电脑撤销后重试。");
            }
        });
    }
    private void continueEnable() {
        if (!flow || !resumed || isDestroyed()) return;
        if (!getSystemService(NotificationManager.class).areNotificationsEnabled()) {
            if (Build.VERSION.SDK_INT>=33 && !notificationAsked && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED) {
                notificationAsked=true;requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},1);
            } else {
                settingsReturn=1;
                startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE,getPackageName()));
            }
            return;
        }
        if (!getSystemService(PowerManager.class).isIgnoringBatteryOptimizations(getPackageName()) && !batteryAsked) {
            batteryAsked=true;settingsReturn=2;
            try {startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,Uri.parse("package:"+getPackageName())));}
            catch (ActivityNotFoundException exc) {startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}
            return;
        }
        try {
            if (!ReceiverService.setEnabled(this,true)) throw new IllegalStateException();
            startForegroundService(new Intent(this,ReceiverService.class));
            status(getSystemService(PowerManager.class).isIgnoringBatteryOptimizations(getPackageName()) ? "已开启接收。可以测试锁屏任务了。" : "已开启接收；未允许锁屏联网，深度休眠时可能延迟。可在权限设置中补开。");
        } catch (Exception exc) {ReceiverService.setEnabled(this,false);status("暂时无法开启接收，请回到 App 重试。");}
        flow=false;
        if (uiSetup) {
            uiSetup=false;
            if (!UiAccessibilityService.connected()) openAccessibilitySettings();
            else status("手机控制已就绪；访问新应用时会弹窗确认。");
        }
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request,permissions,results);
        if (request==1) {
            if (results.length>0 && results[0]==PackageManager.PERMISSION_GRANTED) main.post(this::continueEnable);
            else {flow=false;status("需要通知权限才能显示接收状态。准备好后点击开启接收继续。");}
        }
    }
    @Override protected void onActivityResult(int request,int result,Intent data) {
        super.onActivityResult(request,result,data);
        if (request==20) {
            if (result==RESULT_OK && data!=null) {
                try {startForegroundService(new Intent(this,ScreenCaptureService.class).putExtra("result",result).putExtra("consent",data));
                    status("正在开启屏幕共享。切到需要截图的页面，再让 Agent 请求截图。");}
                catch (RuntimeException exc) {status("屏幕共享未启动，请重新授权。");}
            } else status("未授权屏幕共享，Agent 无法截图。");
        }
    }
    private void startTest() {
        if (checking || !ReceiverService.running) return;
        // Retry the SAME request after an ambiguous HTTP failure, never make a duplicate test.
        boolean finished=testResult!=null && !"running".equals(testResult.optString("status")) && !"approved".equals(testResult.optString("status"));
        if (testRequest.isEmpty() || finished) testRequest=UUID.randomUUID().toString().replace("-","");
        if (!getSharedPreferences("self_test",MODE_PRIVATE).edit().putString("scope",testScope).putString("request",testRequest).commit()) {
            status("无法保存测试记录，请稍后重试。");return;
        }
        testResult=null;testView.setText("正在安排测试…");checkTest(true);
    }
    private void checkTest(boolean start) {
        if (checking) return;
        checking=true;
        String request=testRequest;
        worker.execute(() -> {
            try {
                JSONObject saved=credentials.load();
                JSONObject result=ProtocolClient.post(saved.getString("base_url"),start ? "/api/terminal/self-test" : "/api/terminal/self-test/status",
                        saved.getString("token"),new JSONObject().put("request_id",request));
                main.post(() -> {
                    if (isDestroyed()) return;
                    checking=false;testResult=result;resultAt=android.os.SystemClock.elapsedRealtime();
                    boolean pending="running".equals(result.optString("status")) || "approved".equals(result.optString("status"));
                    nextCheck=pending ? System.currentTimeMillis()+2000 : Long.MAX_VALUE;showTest();
                });
            } catch (Exception exc) {
                main.post(() -> {
                    if (isDestroyed()) return;
                    checking=false;nextCheck=System.currentTimeMillis()+5000;
                    testView.setText(exc instanceof ProtocolClient.HttpError && ((ProtocolClient.HttpError)exc).status==409
                        ? "已有测试在运行，或距离上次测试不足一分钟。稍后重试。"
                        : "尚未取得测试结果，请检查网络。可点击测试重试，同一次请求不会重复执行。");
                });
            }
        });
    }
    private void showTest() {
        if (testResult==null) return;
        String state=testResult.optString("status");
        if ("verified".equals(state)) testView.setText("✓ 通知已发布\n✓ 播报已完成\n请确认你实际看见通知、听见播报。");
        else if ("failed".equals(state) || "cancelled".equals(state)) testView.setText(getString(R.string.test_failed,testResult.optString("error",state)));
        else {
            long remaining=(long)Math.ceil(testResult.optDouble("scheduled_at",0)-testResult.optDouble("server_time",0)-(android.os.SystemClock.elapsedRealtime()-resultAt)/1000.0);
            testView.setText(remaining>0 ? "请现在锁屏\n"+remaining+" 秒后发送通知并播报。" : "测试正在进行，等待手机回执…");
        }
    }
    void showTerminalMessage(String message) {messageView.setText(message);getSharedPreferences("receiver",MODE_PRIVATE).edit().putString("message",message).apply();}
    @Override public void onNewIntent(Intent intent) {super.onNewIntent(intent);setIntent(intent);handleIntent(intent);}
    @Override public void onResume() {
        super.onResume();resumed=true;TerminalUi.attach(this);main.post(refresh);
        if (paired && !flow && ReceiverService.enabled(this) && !ReceiverService.running) {
            // Resume only the receiver the user previously enabled, while this Activity is visible.
            try { startForegroundService(new Intent(this,ReceiverService.class)); }
            catch (RuntimeException exc) { status("接收服务尚未恢复，请点击开启接收。"); }
        }
        if (settingsReturn!=0) {
            int returning=settingsReturn;settingsReturn=0;
            if (returning==1 && !getSystemService(NotificationManager.class).areNotificationsEnabled()) {flow=false;status("通知尚未允许，准备好后再点击开启接收。");}
            else main.post(this::continueEnable);
        } else if (flow && paired) main.post(this::continueEnable);
    }
    @Override public void onPause() {resumed=false;TerminalUi.detach(this);main.removeCallbacks(refresh);super.onPause();}
    @Override public void onSaveInstanceState(Bundle state) {
        state.putBoolean("uiSetup",uiSetup);
        state.putBoolean("flow",flow);state.putBoolean("batteryAsked",batteryAsked);state.putBoolean("notificationAsked",notificationAsked);state.putInt("settingsReturn",settingsReturn);
        if (!paired) {state.putString("pendingUrl",urlField.getText().toString());state.putString("pendingCode",codeField.getText().toString());}
        super.onSaveInstanceState(state);
    }
    @Override public void onDestroy() {worker.shutdownNow();main.removeCallbacks(refresh);journal.close();super.onDestroy();}
}
