package com.moneybook.relay

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.preference.PreferenceManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

/** 一个极简的配置界面：填服务器地址 + 口令，授权通知访问，发一条测试。 */
class MainActivity : Activity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = PreferenceManager.getDefaultSharedPreferences(this)

        val pad = (resources.displayMetrics.density * 16).toInt()
        val root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setPadding(pad, pad, pad, pad)

        root.addView(TextView(this).apply {
            text = "记账本 · 通知转发"
            textSize = 20f
        })
        root.addView(TextView(this).apply {
            text = "把支付宝 / 微信 / 银行的通知转发到你的记账本服务器。\n" +
                   "服务器地址形如 http://192.168.1.100:8787"
            textSize = 13f
            setPadding(0, pad / 2, 0, pad)
        })

        val urlInput = EditText(this).apply {
            hint = "服务器地址"
            setText(prefs.getString("server_url", ""))
        }
        root.addView(urlInput)

        val tokenInput = EditText(this).apply {
            hint = "访问口令（没设就留空）"
            setText(prefs.getString("token", ""))
        }
        root.addView(tokenInput)

        root.addView(Button(this).apply {
            text = "保存"
            setOnClickListener {
                prefs.edit()
                    .putString("server_url", urlInput.text.toString().trim().trimEnd('/'))
                    .putString("token", tokenInput.text.toString().trim())
                    .apply()
                Toast.makeText(this@MainActivity, "已保存", Toast.LENGTH_SHORT).show()
            }
        })

        root.addView(Button(this).apply {
            text = "① 打开通知访问权限设置"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            }
        })

        root.addView(Button(this).apply {
            text = "② 查看授权状态"
            setOnClickListener {
                val flat = Settings.Secure.getString(contentResolver, "enabled_notification_listeners") ?: ""
                val ok = flat.contains(packageName)
                Toast.makeText(this@MainActivity, if (ok) "已授权，可以收到通知了" else "还没授权", Toast.LENGTH_LONG).show()
            }
        })

        root.addView(Button(this).apply {
            text = "③ 发送一条测试通知"
            setOnClickListener { test() }
        })

        setContentView(root)
    }

    private fun test() {
        val base = prefs.getString("server_url", "") ?: ""
        if (base.isEmpty()) {
            Toast.makeText(this, "请先填写服务器地址并保存", Toast.LENGTH_SHORT).show()
            return
        }
        val token = prefs.getString("token", "") ?: ""
        val url = if (token.isEmpty()) base else base + "?token=" + token
        Thread {
            var conn: HttpURLConnection? = null
            var msg = "测试失败"
            try {
                conn = (URL(url + "/api/ingest").openConnection() as HttpURLConnection)
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                val body = "{\"text\":\"微信支付：已支付¥0.01，收款方测试商户\"}"
                val out: OutputStream = conn.outputStream
                out.write(body.toByteArray(Charsets.UTF_8))
                out.flush()
                out.close()
                msg = "服务器返回 " + conn.responseCode
            } catch (e: Exception) {
                msg = "连不上：" + e.message
            } finally {
                conn?.disconnect()
            }
            val finalMsg = msg
            runOnUiThread { Toast.makeText(this, finalMsg, Toast.LENGTH_LONG).show() }
        }.start()
    }
}
