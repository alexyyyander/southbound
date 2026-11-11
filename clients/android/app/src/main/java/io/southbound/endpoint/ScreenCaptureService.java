package io.southbound.endpoint;

import android.app.Activity;
import android.app.KeyguardManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.util.DisplayMetrics;
import java.io.ByteArrayOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Short, visible screen-sharing session. No persisted/reused consent token. */
public final class ScreenCaptureService extends Service {
    private static final String STOP="io.southbound.endpoint.STOP_SCREEN";
    private static volatile ScreenCaptureService current;
    private final Handler main=new Handler(Looper.getMainLooper());
    private MediaProjection projection;
    private VirtualDisplay display;
    private ImageReader reader;
    private Image latest;
    private int width,height,density;
    private boolean closing;
    static boolean active() {return current!=null;}

    @Override public int onStartCommand(Intent intent,int flags,int startId) {
        if (intent==null || STOP.equals(intent.getAction())) {stopSelf();return START_NOT_STICKY;}
        if (projection!=null) return START_NOT_STICKY;
        Intent consent=intent.getParcelableExtra("consent");
        if (intent.getIntExtra("result",Activity.RESULT_CANCELED)!=Activity.RESULT_OK || consent==null) {stopSelf();return START_NOT_STICKY;}
        try {
            NotificationManager nm=getSystemService(NotificationManager.class);
            nm.createNotificationChannel(new NotificationChannel("southbound.screen","屏幕共享",NotificationManager.IMPORTANCE_LOW));
            PendingIntent stop=PendingIntent.getService(this,20,new Intent(this,ScreenCaptureService.class).setAction(STOP),PendingIntent.FLAG_IMMUTABLE);
            PendingIntent open=PendingIntent.getActivity(this,21,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE);
            Notification notice=new Notification.Builder(this,"southbound.screen").setSmallIcon(android.R.drawable.ic_menu_camera)
                    .setContentTitle("Southbound 屏幕共享已开启").setContentText("Agent 可请求截图；5 分钟后自动停止")
                    .setContentIntent(open).setOngoing(true).addAction(new Notification.Action.Builder(null,"停止共享",stop).build()).build();
            if (Build.VERSION.SDK_INT>=29) startForeground(110,notice,ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
            else startForeground(110,notice);
            projection=getSystemService(MediaProjectionManager.class).getMediaProjection(Activity.RESULT_OK,consent);
            projection.registerCallback(new MediaProjection.Callback() {
                @Override public void onStop() {closing=true;if (current==ScreenCaptureService.this) current=null;stopSelf();}
                @Override public void onCapturedContentResize(int w,int h) {if (!closing && display!=null) resize(w,h);}
            },main);
            DisplayMetrics metrics=getResources().getDisplayMetrics();density=metrics.densityDpi;
            setSize(metrics.widthPixels,metrics.heightPixels);createReader();
            // Android 14: exactly one createVirtualDisplay per consent token.
            display=projection.createVirtualDisplay("Southbound screenshot",width,height,density,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,reader.getSurface(),null,main);
            current=this;
            main.postDelayed(this::stopSelf,5*60*1000L);
        } catch (Exception exc) {
            getSharedPreferences("receiver",MODE_PRIVATE).edit().putString("screen_error","屏幕共享启动失败，请重新授权："+exc.getClass().getSimpleName()).apply();
            stopSelf();
        }
        return START_NOT_STICKY;
    }
    private void setSize(int w,int h) {
        double scale=Math.min(1.0,1280.0/Math.max(w,h));
        width=Math.max(1,(int)(w*scale));height=Math.max(1,(int)(h*scale));
    }
    private void createReader() {
        reader=ImageReader.newInstance(width,height,PixelFormat.RGBA_8888,3);
        reader.setOnImageAvailableListener(source -> {
            if (closing || source!=reader) return;
            Image image;
            try {image=source.acquireLatestImage();} catch (IllegalStateException exc) {return;}
            if (image!=null) {if (latest!=null) latest.close();latest=image;}
        },main);
    }
    private void resize(int w,int h) {
        if (w<=0 || h<=0) return;
        int oldW=width,oldH=height;setSize(w,h);
        if (oldW==width && oldH==height) return;
        ImageReader old=reader;
        if (latest!=null) {latest.close();latest=null;}
        createReader();display.resize(width,height,density);display.setSurface(reader.getSurface());old.close();
    }
    static byte[] capture() throws Exception {
        ScreenCaptureService service=current;
        if (service==null) throw new IllegalStateException("SCREEN_AUTH_REQUIRED: 请在手机 App 点击允许屏幕共享，完成系统授权后重新请求截图");
        CountDownLatch done=new CountDownLatch(1);
        byte[][] result=new byte[1][];Exception[] error=new Exception[1];
        service.main.post(() -> {
            try {
                if (service.closing || current!=service || service.getSystemService(KeyguardManager.class).isKeyguardLocked()
                        || !service.getSystemService(PowerManager.class).isInteractive())
                    throw new IllegalStateException("SCREEN_UNAVAILABLE: 请解锁手机并重新授权屏幕共享");
                if (service.latest==null) throw new IllegalStateException("SCREEN_NOT_READY: 共享画面尚未就绪，请稍后重新请求");
                Image image=service.latest;Image.Plane plane=image.getPlanes()[0];
                int rowWidth=plane.getRowStride()/plane.getPixelStride();
                Bitmap padded=Bitmap.createBitmap(rowWidth,image.getHeight(),Bitmap.Config.ARGB_8888);
                Bitmap cropped=null;
                try {
                    var buffer=plane.getBuffer().duplicate();buffer.rewind();
                    if (buffer.remaining()<padded.getByteCount()) {
                        var aligned=java.nio.ByteBuffer.allocate(padded.getByteCount());aligned.put(buffer);aligned.rewind();buffer=aligned;
                    }
                    padded.copyPixelsFromBuffer(buffer);
                    cropped=Bitmap.createBitmap(padded,0,0,image.getWidth(),image.getHeight());
                    ByteArrayOutputStream bytes=new ByteArrayOutputStream();
                    for (int quality:new int[]{80,60,40,20}) {
                        bytes.reset();cropped.compress(Bitmap.CompressFormat.JPEG,quality,bytes);
                        if (bytes.size()<=512*1024) {result[0]=bytes.toByteArray();break;}
                    }
                    if (result[0]==null) throw new IllegalStateException("SCREEN_TOO_LARGE: 截图超出上传大小限制");
                } finally {if (cropped!=null && cropped!=padded) cropped.recycle();padded.recycle();}
            } catch (Exception exc) {error[0]=exc;} finally {done.countDown();}
        });
        if (!done.await(8,TimeUnit.SECONDS)) throw new IllegalStateException("SCREEN_TIMEOUT: 截图超时");
        if (error[0]!=null) throw error[0];
        return result[0];
    }
    @Override public IBinder onBind(Intent intent) {return null;}
    @Override public void onDestroy() {
        closing=true;if (current==this) current=null;main.removeCallbacksAndMessages(null);
        if (display!=null) display.release();
        if (latest!=null) latest.close();
        if (reader!=null) reader.close();
        if (projection!=null) projection.stop();
        stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();
    }
}
