package com.moneybook.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

/** 通知渠道与本地通知：每天 12:00 的账单就是用它弹出来的（不需要任何服务器）。 */
object Notifications {

    const val CHANNEL_RUNNING = "moneybook_running"
    const val CHANNEL_BILL = "moneybook_bill"
    const val RUNNING_ID = 1001

    fun ensureChannels(ctx: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        if (nm.getNotificationChannel(CHANNEL_RUNNING) == null) {
            val c = NotificationChannel(CHANNEL_RUNNING, ctx.getString(R.string.channel_running),
                NotificationManager.IMPORTANCE_MIN)
            c.setShowBadge(false)
            nm.createNotificationChannel(c)
        }
        if (nm.getNotificationChannel(CHANNEL_BILL) == null) {
            val c = NotificationChannel(CHANNEL_BILL, ctx.getString(R.string.channel_bill),
                NotificationManager.IMPORTANCE_DEFAULT)
            nm.createNotificationChannel(c)
        }
    }

    private fun openApp(ctx: Context): PendingIntent {
        val i = Intent(ctx, MainActivity::class.java)
        i.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        var flags = PendingIntent.FLAG_UPDATE_CURRENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags = flags or PendingIntent.FLAG_IMMUTABLE
        return PendingIntent.getActivity(ctx, 0, i, flags)
    }

    /** 常驻的低打扰通知（前台服务需要）。 */
    fun running(ctx: Context): Notification =
        NotificationCompat.Builder(ctx, CHANNEL_RUNNING)
            .setSmallIcon(android.R.drawable.ic_menu_agenda)
            .setContentTitle(ctx.getString(R.string.running_title))
            .setContentText(ctx.getString(R.string.running_text))
            .setOngoing(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setContentIntent(openApp(ctx))
            .build()

    /** 每天定点弹出的账单。 */
    fun post(ctx: Context, title: String, body: String) {
        ensureChannels(ctx)
        val n = NotificationCompat.Builder(ctx, CHANNEL_BILL)
            .setSmallIcon(android.R.drawable.ic_menu_agenda)
            .setContentTitle(title)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(openApp(ctx))
            .build()
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        try {
            nm.notify((System.currentTimeMillis() % 100000).toInt() + 2000, n)
        } catch (e: Exception) {
            // 没有通知权限时忽略
        }
    }
}
