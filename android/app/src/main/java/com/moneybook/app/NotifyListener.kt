package com.moneybook.app

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * 通知监听：微信 / 支付宝 / 银行的付款通知一进来就塞进本地队列。
 * 这是安卓系统官方提供的能力，不需要 root，也不需要任何服务器。
 */
class NotifyListener : NotificationListenerService() {

    companion object {
        private const val TAG = "MoneybookNotify"

        /** 只关心这些来源 */
        private val WATCHED = setOf(
            "com.tencent.mm",                     // 微信
            "com.eg.android.AlipayGphone",        // 支付宝
            "com.unionpay",                       // 云闪付
            "com.android.mms",
            "com.google.android.apps.messaging",
            "com.samsung.android.messaging",
            "com.miui.smsextra",
            "com.android.messaging"
        )

        /** 包名不认识时，看内容像不像一笔交易 */
        private val MONEY_WORDS = listOf("支出", "消费", "付款", "支付", "到账", "收入", "扣款", "退款", "人民币", "元")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val notification = sbn?.notification ?: return
        val pkg = sbn.packageName ?: return
        val extras = notification.extras ?: return

        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
        val big = extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
        val body = if (big.isNotEmpty()) big else text
        if (body.isEmpty() && title.isEmpty()) return

        val full = (title + " " + body).trim()
        if (!shouldCapture(pkg, full)) return

        Log.i(TAG, pkg + " -> " + full)
        NotifyQueue.add(this, full, pkg)

        // 顺手让常驻服务尽快来取（服务没起来就等下次开机/打开 App）
        try {
            CaptureService.start(this)
        } catch (e: Exception) {
            Log.w(TAG, "启动记录服务失败：" + e.message)
        }
    }

    private fun shouldCapture(pkg: String, text: String): Boolean {
        if (WATCHED.contains(pkg)) return true
        if (pkg.contains("mms") || pkg.contains("messaging") || pkg.contains("sms")) return true
        // 认不出来源的，只有明确像交易才收
        val looksMoney = MONEY_WORDS.any { text.contains(it) }
        val hasAmount = text.contains("¥") || text.contains("￥") || text.contains("元")
        return looksMoney && hasAmount
    }
}
