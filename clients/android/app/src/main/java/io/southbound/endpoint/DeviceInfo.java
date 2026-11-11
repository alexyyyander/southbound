package io.southbound.endpoint;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.hardware.Sensor;
import android.hardware.SensorManager;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.BatteryManager;
import android.os.Build;
import android.os.StatFs;
import android.os.Vibrator;
import org.json.JSONArray;
import org.json.JSONObject;

/** Inventory only: no camera/audio/location access, sensor listeners or identifiers. */
final class DeviceInfo {
    static JSONObject read(Context context) throws Exception {
        JSONObject device = new JSONObject().put("manufacturer", Build.MANUFACTURER).put("model", Build.MODEL)
                .put("android_version", Build.VERSION.RELEASE).put("api_level", Build.VERSION.SDK_INT)
                .put("security_patch", Build.VERSION.SECURITY_PATCH).put("cpu_cores", Runtime.getRuntime().availableProcessors())
                .put("abis", new JSONArray(Build.SUPPORTED_ABIS));
        if (Build.VERSION.SDK_INT >= 31) device.put("soc_manufacturer", Build.SOC_MANUFACTURER).put("soc_model", Build.SOC_MODEL);
        ActivityManager.MemoryInfo info = new ActivityManager.MemoryInfo();
        context.getSystemService(ActivityManager.class).getMemoryInfo(info);
        JSONObject memory = new JSONObject().put("total_bytes", info.totalMem).put("available_bytes", info.availMem).put("low_memory", info.lowMemory);
        StatFs fs = new StatFs(context.getFilesDir().getAbsolutePath());
        JSONObject storage = new JSONObject().put("volume", "app_data_volume").put("total_bytes", fs.getTotalBytes()).put("available_bytes", fs.getAvailableBytes());
        ConnectivityManager cm = context.getSystemService(ConnectivityManager.class);
        NetworkCapabilities caps = cm.getNetworkCapabilities(cm.getActiveNetwork());
        JSONArray transports = new JSONArray();
        if (caps != null) {
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) transports.put("wifi");
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) transports.put("cellular");
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) transports.put("ethernet");
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) transports.put("vpn");
        }
        JSONObject network = new JSONObject().put("connected", caps != null).put("transports", transports)
                .put("validated", caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))
                .put("metered", cm.isActiveNetworkMetered());
        PackageManager pm = context.getPackageManager();
        JSONObject hardware = new JSONObject().put("camera", pm.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY))
                .put("microphone", pm.hasSystemFeature(PackageManager.FEATURE_MICROPHONE))
                .put("gps", pm.hasSystemFeature(PackageManager.FEATURE_LOCATION_GPS))
                .put("bluetooth", pm.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH))
                .put("nfc", pm.hasSystemFeature(PackageManager.FEATURE_NFC))
                .put("vibrator", context.getSystemService(Vibrator.class).hasVibrator());
        JSONArray sensors = new JSONArray();
        var list = context.getSystemService(SensorManager.class).getSensorList(Sensor.TYPE_ALL);
        for (Sensor s : list) {
            if (sensors.length() >= 32) break;
            sensors.put(new JSONObject().put("type", s.getType()).put("name", bounded(s.getName(), 80))
                    .put("vendor", bounded(s.getVendor(), 40)).put("wake_up", s.isWakeUpSensor()));
        }
        hardware.put("sensors", sensors).put("sensor_count", list.size()).put("sensor_list_truncated", list.size() > 32);
        var display = context.getResources().getDisplayMetrics();
        JSONObject screen = new JSONObject().put("width_pixels", display.widthPixels).put("height_pixels", display.heightPixels)
                .put("density_dpi", display.densityDpi).put("capture_authorized", ScreenCaptureService.active());
        Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        JSONObject batteryInfo = new JSONObject();
        if (battery != null) {
            int scale=battery.getIntExtra(BatteryManager.EXTRA_SCALE,-1), level=battery.getIntExtra(BatteryManager.EXTRA_LEVEL,-1);
            if (scale>0 && level>=0) batteryInfo.put("level", Math.round(100f*level/scale));
            batteryInfo.put("plugged", battery.getIntExtra(BatteryManager.EXTRA_PLUGGED,0)!=0)
                    .put("temperature_celsius", battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE,0)/10.0)
                    .put("voltage_mv", battery.getIntExtra(BatteryManager.EXTRA_VOLTAGE,0));
        }
        return new JSONObject().put("device",device).put("memory",memory).put("storage",storage).put("network",network)
                .put("hardware",hardware).put("display",screen).put("battery",batteryInfo).put("sampled_at",System.currentTimeMillis()/1000.0);
    }
    private static String bounded(String value, int limit) {return value == null ? "" : value.substring(0,Math.min(value.length(),limit));}
}
