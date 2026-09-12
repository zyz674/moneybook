package com.moneybook.relay

import android.app.Notification
import android.content.SharedPreferences
import android.preference.PreferenceManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import org.json.JSONObject
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 通知监听 -> 转发到记账本。
 *
 * 只读取通知的标题与正文，拼成一行文本 POST 给 /api/ingest，
 * 由服务端负责判断"这是不是一笔交易"以及金额、商户、方向。
 */
class NotifyRelayService : NotificationListenerService() {

    companion object {
        const val TAG = "MoneybookRelay"
        // 只关心这几个来源，避免把无关通知也发出去
        val WATCHED = setOf(
            "com.tencent.mm",                    // 微信
            "com.eg.android.AlipayGphone",       // 支付宝
            "com.android.mms",                   // 短信
            "com.google.android.apps.messaging",
            "com.samsung.android.messaging",
            "com.miui.smsextra"
        )
    }

    private lateinit var prefs: SharedPreferences

    override fun onCreate() {
        super.onCreate()
        prefs = PreferenceManager.getDefaultSharedPreferences(this)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val notification = sbn?.notification ?: return
        val pkg = sbn.packageName ?: return
        // 短信类应用包名千奇百怪，这里用"是否在名单里 或 是否是短信"双条件
        if (pkg !in WATCHED && !looksLikeSms(pkg)) return

        val extras = notification.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
        val big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
        val body = if (big.isNotEmpty()) big else text
        if (body.isEmpty() && title.isEmpty()) return

        val full = (title + " " + body).trim()
        Log.i(TAG, "通知: " + pkg + " -> " + full)
        send(full, pkg, sbn.postTime)
    }

    private fun looksLikeSms(pkg: String): Boolean {
        return pkg.contains("mms") || pkg.contains("messaging") || pkg.contains("sms")
    }

    /** 在后台线程里 POST，避免阻塞通知回调。 */
    private fun send(text: String, pkg: String, postTime: Long) {
        val base = prefs.getString("server_url", "") ?: ""
        if (base.isEmpty()) {
            Log.w(TAG, "还没配置服务器地址")
            return
        }
        val token = prefs.getString("token", "") ?: ""
        val url = if (token.isEmpty()) base else base + (if (base.contains("?")) "&" else "?") + "token=" + token
        val ts = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA).format(Date(postTime))

        Thread {
            var conn: HttpURLConnection? = null
            try {
                val payload = JSONObject()
                payload.put("text", text)
                payload.put("package", pkg)
                payload.put("ts", ts)
                payload.put("device", android.os.Build.MODEL)

                conn = (URL(url).openConnection() as HttpURLConnection)
                conn.requestMethod = "POST"
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                val out: OutputStream = conn.outputStream
                out.write(payload.toString().toByteArray(Charsets.UTF_8))
                out.flush()
                out.close()
                val code = conn.responseCode
                Log.i(TAG, "POST " + code)
            } catch (e: Exception) {
                Log.e(TAG, "转发失败: " + e.message)
            } finally {
                conn?.disconnect()
            }
        }.start()
    }
}
