package com.moneybook.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebView
import java.util.Calendar

/**
 * 常驻服务：让网页逻辑一直活着，从而做到
 *  1) 通知一到就解析入库（实时）
 *  2) 每天定点生成本地账单并弹通知（不需要服务器）
 * 它只占一个空闲的 WebView，不联网也不刷 CPU。
 */
class CaptureService : Service() {

    companion object {
        private const val TAG = "MoneybookCapture"
        const val ACTION_CHECK_BILL = "com.moneybook.app.CHECK_BILL"
        private const val ALARM_REQUEST = 1001

        fun start(ctx: Context) {
            val i = Intent(ctx, CaptureService::class.java)
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i) else ctx.startService(i)
            } catch (e: Exception) {
                Log.w(TAG, "启动服务失败：" + e.message)
            }
        }

        /** 每天定点唤醒一次（不精确闹钟，不需要特殊权限，差几分钟无所谓）。 */
        fun scheduleDailyAlarm(ctx: Context, hour: Int = 12, minute: Int = 0) {
            val am = ctx.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
            val intent = Intent(ctx, CaptureService::class.java).setAction(ACTION_CHECK_BILL)
            var flags = PendingIntent.FLAG_UPDATE_CURRENT
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags = flags or PendingIntent.FLAG_IMMUTABLE
            val pi = PendingIntent.getService(ctx, ALARM_REQUEST, intent, flags)
            val cal = Calendar.getInstance()
            cal.set(Calendar.HOUR_OF_DAY, hour)
            cal.set(Calendar.MINUTE, minute)
            cal.set(Calendar.SECOND, 0)
            if (cal.timeInMillis <= System.currentTimeMillis()) cal.add(Calendar.DAY_OF_YEAR, 1)
            try {
                am.setInexactRepeating(AlarmManager.RTC_WAKEUP, cal.timeInMillis, AlarmManager.INTERVAL_DAY, pi)
            } catch (e: Exception) {
                Log.w(TAG, "设置定时失败：" + e.message)
            }
        }
    }

    private var web: WebView? = null
    private val handler = Handler(Looper.getMainLooper())

    private val pump = object : Runnable {
        override fun run() {
            try {
                val items = NotifyQueue.drain(this@CaptureService)
                if (items.length() > 0) {
                    val js = "window.MB && window.MB.ingestQueue(" + items.toString() + ")"
                    web?.evaluateJavascript(js, null)
                }
            } catch (e: Exception) {
                Log.w(TAG, "取队列失败：" + e.message)
            }
            handler.postDelayed(this, 5000)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifications.ensureChannels(this)
        try {
            startForeground(Notifications.RUNNING_ID, Notifications.running(this))
        } catch (e: Exception) {
            Log.w(TAG, "前台通知失败：" + e.message)
        }
        web = WebApp.create(this, Bridge(), "MoneybookNative")
        web?.loadUrl(WebApp.START_URL + "?bg=1")   // bg=1：由后台负责定时账单
        handler.postDelayed(pump, 6000)
        scheduleDailyAlarm(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CHECK_BILL) {
            web?.evaluateJavascript("window.MB && window.MB.checkDaily && window.MB.checkDaily()", null)
        }
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(pump)
        try {
            web?.destroy()
        } catch (e: Exception) {
            // 忽略
        }
        web = null
        super.onDestroy()
    }

    /** 暴露给网页调用 */
    inner class Bridge {
        @JavascriptInterface
        fun notify(title: String, body: String) {
            Notifications.post(this@CaptureService, title, body)
        }

        @JavascriptInterface
        fun hasNotificationAccess(): Boolean = NotifyQueue.hasAccess(this@CaptureService)

        @JavascriptInterface
        fun openNotificationSettings() = NotifyQueue.openSettings(this@CaptureService)

        @JavascriptInterface
        fun pendingCount(): Int = NotifyQueue.size(this@CaptureService)

        @JavascriptInterface
        fun version(): String = "android-app"
    }
}
