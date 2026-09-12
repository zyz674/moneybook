package com.moneybook.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.util.Base64
import android.util.Log
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.widget.Toast
import org.json.JSONObject

/** 主界面：就是一个 WebView，装的是现成的网页版记账本。 */
class MainActivity : Activity() {

    companion object {
        private const val TAG = "MoneybookMain"
        private const val REQ_PICK_FILE = 2001
    }

    private var web: WebView? = null
    private var pendingFileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingImportUri: Uri? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.ensureChannels(this)

        val chrome = object : WebChromeClient() {
            // 网页版「导入账单」用的就是 <input type="file">，WebView 必须自己接住
            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>,
                fileChooserParams: FileChooserParams
            ): Boolean {
                pendingFileCallback?.onReceiveValue(null)
                pendingFileCallback = filePathCallback
                val intent: Intent = try {
                    fileChooserParams.createIntent()
                } catch (e: Exception) {
                    Intent(Intent.ACTION_GET_CONTENT).apply { type = "*/*" }
                }
                intent.addCategory(Intent.CATEGORY_OPENABLE)
                if (intent.type == null) intent.type = "*/*"
                return try {
                    startActivityForResult(intent, REQ_PICK_FILE)
                    true
                } catch (e: Exception) {
                    pendingFileCallback = null
                    Toast.makeText(this@MainActivity, "没有找到文件选择器，请从文件管理器里「分享」到记账本", Toast.LENGTH_LONG).show()
                    false
                }
            }
        }

        val view = WebApp.create(this, Bridge(), "MoneybookNative", chrome) { _, _ ->
            // 页面加载完再处理「分享进来」的文件
            val uri = pendingImportUri
            pendingImportUri = null
            if (uri != null) importUri(uri)
        }
        setContentView(view)
        web = view
        view.loadUrl(WebApp.START_URL)

        CaptureService.start(this)          // 保证后台记录一直开着
        askNotificationPermission()
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /** 处理「分享到记账本」和「用记账本打开账单文件」 */
    private fun handleIntent(intent: Intent?) {
        val action = intent?.action ?: return
        val uri: Uri? = when (action) {
            Intent.ACTION_SEND -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri
                }
            }
            Intent.ACTION_VIEW -> intent.data
            else -> null
        } ?: return
        if (web == null || web?.url == null) {
            pendingImportUri = uri
        } else {
            importUri(uri)
        }
    }

    /** 读文件 -> base64 -> 交给网页里的导入逻辑 */
    private fun importUri(uri: Uri) {
        Toast.makeText(this, "正在导入账单…", Toast.LENGTH_SHORT).show()
        Thread {
            try {
                val name = queryName(uri) ?: "账单.csv"
                val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
                if (bytes == null || bytes.isEmpty()) throw IllegalStateException("文件是空的")
                val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                val js = "window.MB && window.MB.importBase64(" + JSONObject.quote(name) + "," + JSONObject.quote(b64) + ")"
                runOnUiThread { web?.evaluateJavascript(js, null) }
            } catch (e: Exception) {
                Log.e(TAG, "导入失败", e)
                runOnUiThread { Toast.makeText(this, "导入失败：" + e.message, Toast.LENGTH_LONG).show() }
            }
        }.start()
    }

    private fun queryName(uri: Uri): String? {
        try {
            contentResolver.query(uri, null, null, null, null)?.use { c ->
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && c.moveToFirst()) return c.getString(idx)
            }
        } catch (e: Exception) {
            // 忽略，用默认名字
        }
        return uri.lastPathSegment
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
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == REQ_PICK_FILE) {
            val cb = pendingFileCallback
            pendingFileCallback = null
            var result: Array<Uri>? = null
            if (resultCode == RESULT_OK && data != null) {
                val clip = data.clipData
                if (clip != null && clip.itemCount > 0) {
                    result = Array(clip.itemCount) { clip.getItemAt(it).uri }
                } else if (data.data != null) {
                    result = arrayOf(data.data!!)
                }
            }
            cb?.onReceiveValue(result)
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
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
