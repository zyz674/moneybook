package com.moneybook.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** 开机（或升级）后把记录服务重新拉起来。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action ?: return
        if (context == null) return
        if (action == Intent.ACTION_BOOT_COMPLETED || action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            CaptureService.start(context)
            CaptureService.scheduleDailyAlarm(context)
        }
    }
}
