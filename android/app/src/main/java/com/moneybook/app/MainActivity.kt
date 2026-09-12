package com.moneybook.app

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebView

/** 主界面：就是一个 WebView，装的是现成的网页版记账本。 */
class MainActivity : Activity() {

    private var web: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.ensureChannels(this)
        val view = WebApp.create(this, Bridge(), "MoneybookNative")
        setContentView(view)
        web = view
        view.loadUrl(WebApp.START_URL)

        CaptureService.start(this)          // 保证后台记录一直开着
        askNotificationPermission()
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) return
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        try {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
        } catch (e: Exception) {
            // 忽略
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        val w = web
        if (w != null && w.canGoBack()) {
            w.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
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
            Notifications.post(this@MainActivity, title, body)
        }

        @JavascriptInterface
        fun hasNotificationAccess(): Boolean = NotifyQueue.hasAccess(this@MainActivity)

        @JavascriptInterface
        fun openNotificationSettings() = NotifyQueue.openSettings(this@MainActivity)

        @JavascriptInterface
        fun pendingCount(): Int = NotifyQueue.size(this@MainActivity)

        @JavascriptInterface
        fun version(): String = "android-app"
    }
}
